"""P0-B ratchet: mid-coordination ask_user soft-stop → cold claim → settle rebuilds session.

Covers the end-to-end path that unit snapshot round-trips do not:

1. CEO coordinates (≥2 workers) and writes a synthesis draft
2. CEO ``ask_user`` while the session is still active → snapshot lands in the turn journal
3. Soft-stop clears the in-process session (as turn-end / process restart would)
4. Cold claim re-hydrates ``journal_entries`` the way ``claim_paused_turn`` does
5. ``settle_resumed_suspension`` CONTINUE rebuilds ``CoordinationSession`` (draft / completed / budget)
6. Restored session accepts further coordination (``update_synthesis`` + event consume)
7. STOP settle must not attach ``active_coordination`` or ``run_started`` unfinished workers

By-design boundary (not asserted here): ``resume_plan`` (plan_review wave-boundary
resume) hardcodes ``coordinate=False`` — post-checkpoint tails stay on the blocking
path and do not re-enter coordination.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, NamedTuple

import agentcore.runtime.coordination.wait as coord_wait
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.coordination.journal import coordination_from_journal
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    active_coordination,
    clear_active_coordination,
    current_execution_id,
)
from agentcore.runtime.coordination.tools import CancelWorkerTool, UpdateSynthesisTool
from agentcore.runtime.coordination.wait import await_coordination_injection
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.runtime.facts import FactKind, TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.pipeline.resume import settle_resumed_suspension
from agentcore.runtime.suspension import (
    AskUserSuspension,
    captain_transcript,
    suspension_from_json,
)
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.protocol import ToolContext, ToolEffect
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import _upstream_body
from tests.llm_helpers import make_profile_params

EXEC_ID = "e-coord-ask-resume"
DRAFT_TEXT = "进展草稿：研究员方向已对齐，写手待完成。"


class _SlowSecondWorker:
    """First worker instant; second delayed so CEO can ask_user mid-wave."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        idx = self.calls
        self.calls += 1
        if idx >= 1:
            # Long enough that CEO can ask_user after the first completion + synthesis
            # while r2 is still in flight (mid-wave soft-stop).
            await asyncio.sleep(5.0)
        yield LLMChunk(
            delta_content=_upstream_body("AOUT" if idx == 0 else "BOUT")
        )


class _CoordAskCeoProvider:
    """CEO script: delegate → update_synthesis → ask_user (pause before all_completed)."""

    def __init__(self) -> None:
        self.delegate_calls = 0
        self.synth_calls = 0
        self.ask_calls = 0

    async def stream(self, request):  # noqa: ANN001
        tool_msgs = [m for m in request.messages if m.role == "tool"]
        last_tool = (tool_msgs[-1].content or "") if tool_msgs else ""
        coord_injected = any(
            m.role == "user" and m.content and "团队协调事件" in m.content
            for m in request.messages
        )
        # Require a real completion — idle_timeout patrol also carries「团队协调事件」
        # and must not drive synthesis while completed_run_ids is still empty.
        worker_completed = any(
            m.role == "user" and m.content and "worker_completed" in m.content
            for m in request.messages
        )
        all_done = any(
            m.role == "user" and m.content and "all_completed" in m.content
            for m in request.messages
        )
        if not tool_msgs:
            self.delegate_calls += 1
            args = json.dumps(
                {
                    "tasks": [
                        {"id": "r1", "role": "研究员", "task": "做A"},
                        {
                            "id": "r2",
                            "role": "写手",
                            "task": "做B",
                            "depends_on": ["r1"],
                        },
                    ],
                }
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="ceo-dc1",
                        function_name="delegate",
                        arguments_delta=args,
                    )
                ]
            )
        elif "已更新合成草稿" in last_tool:
            self.ask_calls += 1
            args = json.dumps(
                {
                    "message": "写手方向是否按方案 A 继续？",
                    "questions": [
                        {
                            "prompt": "写手方向是否按方案 A 继续？",
                            "kind": "choice",
                            "options": ["按 A 继续", "改方案 B"],
                        }
                    ],
                }
            )
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="ceo-ask1",
                        function_name="ask_user",
                        arguments_delta=args,
                    )
                ]
            )
        elif (
            coord_injected
            and worker_completed
            and self.synth_calls == 0
            and not all_done
        ):
            self.synth_calls += 1
            args = json.dumps({"draft": DRAFT_TEXT})
            yield LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="ceo-syn1",
                        function_name="update_synthesis",
                        arguments_delta=args,
                    )
                ]
            )
        else:
            # Should not reach a free-text finalize before ask_user suspends.
            yield LLMChunk(delta_content="意外收口")


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id=EXEC_ID,
        run_id="cap",
        agent_id="cap",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c-coord-ask",
    )


def _cold_claim(frame: AskUserSuspension, journal_entries: list[dict]) -> AskUserSuspension:
    restored = suspension_from_json(frame.to_json())
    assert isinstance(restored, AskUserSuspension)
    assert not restored.transcript
    # journal_entries is the唯一权威载体; the display ``journal`` seed derives from it (P0-B Phase 3).
    restored.journal_entries = list(journal_entries)
    return restored


class _PausedMidCoordAsk(NamedTuple):
    restored: AskUserSuspension
    journal_entries: list[dict]
    snap: Any
    user_message: str


def _resume_delegate(user_message: str) -> tuple[EventSink, DelegateTool, ToolContext]:
    resume_sink = EventSink()
    resume_sink.seed_journal(
        [{"type": EventType.CHECKPOINT_REQUIRED.value, "payload": {}, "timestamp": "t"}]
    )
    resume_ctx = _ctx()
    resume_delegate = DelegateTool(
        llm=_SlowSecondWorker(),
        sink=resume_sink,
        system_prompt="SYS",
        user_message=user_message,
        history=[],
        tools=ToolRegistry(),
        base_tool_context=resume_ctx,
        captain_run_id="cap",
        folder_id="test_birth",
        approval_gate=None,
    )
    return resume_sink, resume_delegate, resume_ctx


async def _cancel_live_drive(execution_id: str = EXEC_ID) -> None:
    live = active_coordination(execution_id)
    if live is not None and live.drive_task is not None and not live.drive_task.done():
        live.drive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await live.drive_task
    clear_active_coordination(execution_id)
    clear_active_coordination()


async def _pause_mid_coord_ask(monkeypatch) -> _PausedMidCoordAsk:
    """CEO mid-wave ask_user → soft-stop → cold-claim frame (shared continue/stop fixture)."""
    # Short idle wait so the post-synthesis round wakes (timeout) while r2 still runs.
    monkeypatch.setattr(coord_wait, "_COORD_WAIT_TIMEOUT_S", 0.4)
    clear_active_coordination()

    captured: dict = {}

    async def saver(frame) -> None:  # noqa: ANN001
        captured["frame"] = frame
        captured["journal_entries"] = list(frame.journal_entries)

    async def deleter(_message_id: str) -> None:
        return None

    sink = EventSink()
    base_ctx = _ctx()
    worker_llm = _SlowSecondWorker()
    ceo_llm = _CoordAskCeoProvider()

    delegate = DelegateTool(
        llm=worker_llm,
        sink=sink,
        system_prompt="SYS",
        user_message="请协调团队并行完成 A 和 B",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=base_ctx,
        captain_run_id="cap",
        folder_id="test_birth",
        approval_gate=None,
    )
    ask_tool = AskUserTool(
        sink=sink,
        conversation_id="c-coord-ask",
        timeout_seconds=1.0,
        captain_run_id="cap",
        base_system_prompt="你是 CEO。",
        user_message="请协调团队并行完成 A 和 B",
        message_id="m-coord-ask",
        suspension_saver=saver,
        suspension_deleter=deleter,
    )
    reg = ToolRegistry()
    reg.register(delegate)
    reg.register(UpdateSynthesisTool(sink=sink))
    reg.register(CancelWorkerTool())
    reg.register(ask_tool)

    system_prompt = "你是 CEO。"
    user_message = "请协调团队并行完成 A 和 B"
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(
            system_prompt=system_prompt, user_message=user_message, model_profile="m"
        ).to_fact()
    )
    finish_override: list[FinishReason] = []

    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    exec_token = current_execution_id.set(EXEC_ID)
    try:
        await react_loop(
            messages=messages,
            llm=ceo_llm,
            tools=reg,
            sink=sink,
            tool_context=base_ctx,
            profile=make_profile_params(max_rounds=12),
            turn_model="m",
            out=ReactLoopOut(finish_override=finish_override),
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        current_execution_id.reset(exec_token)
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert finish_override == [FinishReason.PAUSED], "ask_user must ② finalize mid-coordination"
    assert ceo_llm.delegate_calls == 1
    assert ceo_llm.synth_calls == 1
    assert ceo_llm.ask_calls == 1
    assert "frame" in captured

    journal_entries = captured["journal_entries"]
    snap = coordination_from_journal(journal_entries)
    assert snap is not None, "ask_user suspend must journal a coordination_snapshot"
    assert snap.active is True
    assert snap.execution_id == EXEC_ID
    assert snap.draft == DRAFT_TEXT
    assert snap.total_workers == 2
    assert snap.budget_remaining >= 0
    # At least the first worker completed; prefer mid-wave (r2 still unfinished) so
    # settle's try_start_coordination re-drive path is exercised when plan is present.
    assert len(snap.completed_run_ids) >= 1
    assert len(snap.completed_run_ids) < snap.total_workers
    assert any(
        (e.get("kind") or "") == FactKind.COORDINATION_SNAPSHOT.value for e in journal_entries
    )
    assert any((e.get("kind") or "") == FactKind.PLAN_SNAPSHOT.value for e in journal_entries)

    # Soft-stop / process end: drop the live session so resume must rebuild from journal.
    await _cancel_live_drive(EXEC_ID)
    assert active_coordination(EXEC_ID) is None

    frame = captured["frame"]
    assert isinstance(frame, AskUserSuspension)
    restored = _cold_claim(frame, journal_entries)
    return _PausedMidCoordAsk(restored, journal_entries, snap, user_message)


async def test_ask_user_soft_stop_rebuilds_coordination_on_resume(monkeypatch):
    """挂起快照入 journal → claim → settle 重建协调态 → CEO 可续协调。"""
    paused = await _pause_mid_coord_ask(monkeypatch)
    restored, snap, user_message = paused.restored, paused.snap, paused.user_message
    resume_sink, resume_delegate, resume_ctx = _resume_delegate(user_message)

    settled = await settle_resumed_suspension(
        restored,
        decision=CheckpointDecision.CONTINUE,
        note="",
        selected=["按 A 继续"],
        sink=resume_sink,
        delegate_tool=resume_delegate,
        execution_id="e_fresh_mint",
    )
    assert settled.terminal_text is None  # CONTINUE → CEO loop resumes
    assert "按 A 继续" in settled.output

    session = active_coordination(EXEC_ID)
    assert session is not None, "settle must rebuild CoordinationSession from journal"
    assert session.active is True
    assert session.draft == DRAFT_TEXT
    assert session.total_workers == 2
    assert session.budget_remaining == snap.budget_remaining
    assert set(snap.completed_run_ids).issubset(session.completed_run_ids)

    # Behaviour: restored session still accepts progressive synthesis + team events.
    syn = UpdateSynthesisTool(sink=resume_sink)
    syn_result = await syn.execute({"draft": DRAFT_TEXT + "（续跑修订）"}, resume_ctx)
    assert syn_result.success is True
    assert session.draft == DRAFT_TEXT + "（续跑修订）"

    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.WORKER_COMPLETED,
            payload={"run_id": "r2", "role": "写手", "status": "completed", "summary": "BOUT"},
        )
    )
    injected = await await_coordination_injection([])
    assert injected, "restored session must inject team events for the CEO loop"
    assert any("团队协调事件" in (m.content or "") for m in injected)

    # Cleanup background re-drive if settle re-armed unfinished workers.
    await _cancel_live_drive(EXEC_ID)


async def test_ask_user_stop_does_not_rebuild_coordination(monkeypatch):
    """STOP settle must not attach a live drive or re-start unfinished workers."""
    paused = await _pause_mid_coord_ask(monkeypatch)
    resume_sink, resume_delegate, _ = _resume_delegate(paused.user_message)

    try:
        settled = await settle_resumed_suspension(
            paused.restored,
            decision=CheckpointDecision.STOP,
            note="",
            selected=[],
            sink=resume_sink,
            delegate_tool=resume_delegate,
            execution_id="e_fresh_mint",
        )
        assert settled.terminal_text is None
        assert settled.effect is ToolEffect.CONTINUE
        assert "取消了澄清" in settled.output

        session = active_coordination(EXEC_ID)
        assert session is None, "STOP must not set_active_coordination"
        journal = resume_sink.execution_journal() or []
        assert not any(
            e.get("type") == EventType.RUN_STARTED.value for e in journal
        ), "STOP must not run_started unfinished workers"
    finally:
        await _cancel_live_drive(EXEC_ID)
