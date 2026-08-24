"""P0-B ratchet: mid-coordination ask_user journal → cold claim → settle rebuilds session.

Covers the durable path that unit snapshot round-trips do not. The pause
journal is a static fixture (live react_loop cannot stably hang mid-wave:
routine ``worker_completed`` no longer wakes the CEO). Shape matches what
the live path would persist:

1. CEO coordinated (≥2 workers) and wrote a synthesis draft
2. CEO ``ask_user`` while the session was still active → snapshot in the turn journal
3. Soft-stop / process restart would clear the in-process session
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
from pathlib import Path
from typing import Any, NamedTuple

from agentcore.llm.provider.protocol import LLMChunk
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.coordination.journal import (
    CoordinationSnapshotFact,
    coordination_from_journal,
)
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSnapshot,
    active_coordination,
    clear_active_coordination,
)
from agentcore.runtime.coordination.tools import UpdateSynthesisTool
from agentcore.runtime.coordination.wait import await_coordination_injection
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import FactKind, LlmCallFact, RoundBoundaryFact, TurnStartedFact
from agentcore.runtime.pipeline.resume import settle_resumed_suspension
from agentcore.runtime.runs import RunPlan, RunSpec
from agentcore.runtime.runs.serialize import plan_snapshot_fact, run_final_fact
from agentcore.runtime.runs.types import RunPhase, RunState
from agentcore.runtime.suspension import AskUserSuspension, suspension_from_json
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.protocol import ToolContext, ToolEffect
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import _upstream_body

EXEC_ID = "e-coord-ask-resume"
CAP_RUN_ID = "cap"
RUN_R1 = "del_fixture_r1"
RUN_R2 = "del_fixture_r2"
DRAFT_TEXT = "进展草稿：研究员方向已对齐，写手待完成。"


class _SlowSecondWorker:
    """Delayed second worker for settle re-drive of unfinished r2."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        idx = self.calls
        self.calls += 1
        if idx >= 1:
            await asyncio.sleep(0.05)
        yield LLMChunk(delta_content=_upstream_body("BOUT"))


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


def _mid_coord_ask_pause_fixture() -> _PausedMidCoordAsk:
    """Cold-claimable ask_user pause mid-coordination (journal is the authority).

    Live react_loop mid-wave pause is nondeterministic under coordination wake
    policy (routine worker_completed no longer wakes CEO). This fixture mirrors
    the journal-at-pause shape the live path would persist: turn_started, captain
    rounds through delegate → synthesis → ask_user, plan + coordination snapshots.
    """
    system_prompt = "你是 CEO。"
    user_message = "请协调团队并行完成 A 和 B"
    plan = RunPlan(
        nodes=[
            RunSpec(run_id=RUN_R1, agent_id=RUN_R1, role="研究员", task="做A"),
            RunSpec(
                run_id=RUN_R2,
                agent_id=RUN_R2,
                role="写手",
                task="做B",
                depends_on=[RUN_R1],
            ),
        ]
    )
    snap = CoordinationSnapshot(
        execution_id=EXEC_ID,
        draft=DRAFT_TEXT,
        conversation_id="c-coord-ask",
        completed_run_ids=[RUN_R1],
        total_workers=2,
        active=True,
        saw_first_completion=True,
    )
    r1_done = RunState(phase=RunPhase.COMPLETED, content="AOUT")
    journal_entries = [
        TurnStartedFact(
            system_prompt=system_prompt, user_message=user_message, model_profile="m"
        )
        .to_fact()
        .entry(),
        RoundBoundaryFact(round_idx=0, run_id=CAP_RUN_ID, role="captain").to_fact().entry(),
        LlmCallFact(
            run_id=CAP_RUN_ID,
            round_idx=0,
            tool_calls=[
                {
                    "id": "ceo-dc1",
                    "type": "function",
                    "function": {"name": "delegate", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        {
            "kind": EventType.RUN_PLAN.value,
            "payload": {"execution_id": EXEC_ID},
            "ts": "t0",
        },
        plan_snapshot_fact(plan).entry(),
        run_final_fact(RUN_R1, r1_done).entry(),
        RoundBoundaryFact(round_idx=1, run_id=CAP_RUN_ID, role="captain").to_fact().entry(),
        LlmCallFact(
            run_id=CAP_RUN_ID,
            round_idx=1,
            tool_calls=[
                {
                    "id": "ceo-syn1",
                    "type": "function",
                    "function": {
                        "name": "update_synthesis",
                        "arguments": '{"draft": "' + DRAFT_TEXT + '"}',
                    },
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        CoordinationSnapshotFact(snapshot=snap.to_dict()).to_fact().entry(),
        RoundBoundaryFact(round_idx=2, run_id=CAP_RUN_ID, role="captain").to_fact().entry(),
        LlmCallFact(
            run_id=CAP_RUN_ID,
            round_idx=2,
            tool_calls=[
                {
                    "id": "ceo-ask1",
                    "type": "function",
                    "function": {"name": "ask_user", "arguments": "{}"},
                }
            ],
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry(),
        {"kind": EventType.CHECKPOINT_REQUIRED.value, "payload": {}, "ts": "t"},
    ]

    frame = AskUserSuspension(
        message_id="m-coord-ask",
        conversation_id="c-coord-ask",
        user_id="u",
        captain_run_id=CAP_RUN_ID,
        checkpoint_id="ck-coord-ask",
        tool_call_id="ceo-ask1",
        base_system_prompt=system_prompt,
        user_message=user_message,
        transcript=[],
        question="写手方向是否按方案 A 继续？",
        questions=[
            {
                "prompt": "写手方向是否按方案 A 继续？",
                "kind": "choice",
                "options": ["按 A 继续", "改方案 B"],
                "multiple": False,
                "default": "",
            }
        ],
    )
    frame.journal_entries = list(journal_entries)

    snap_from_journal = coordination_from_journal(journal_entries)
    assert snap_from_journal is not None
    assert snap_from_journal.active is True
    assert snap_from_journal.execution_id == EXEC_ID
    assert snap_from_journal.draft == DRAFT_TEXT
    assert snap_from_journal.total_workers == 2
    assert len(snap_from_journal.completed_run_ids) == 1
    assert len(snap_from_journal.completed_run_ids) < snap_from_journal.total_workers
    assert any(
        (e.get("kind") or "") == FactKind.COORDINATION_SNAPSHOT.value for e in journal_entries
    )
    assert any((e.get("kind") or "") == FactKind.PLAN_SNAPSHOT.value for e in journal_entries)

    restored = _cold_claim(frame, journal_entries)
    return _PausedMidCoordAsk(restored, journal_entries, snap, user_message)


async def _pause_mid_coord_ask(_monkeypatch) -> _PausedMidCoordAsk:
    """Mid-coordination ask_user pause → cold-claim frame (shared continue/stop fixture)."""
    return _mid_coord_ask_pause_fixture()


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
