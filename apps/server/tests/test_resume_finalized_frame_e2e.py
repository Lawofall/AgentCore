"""End-to-end: a frame finalized by 挂起即收口 (②) is cold-resumable to completion.

Increment 3 keystone. Two existing guarantees BRACKET this one:
- the finalize path (flag ON) persists a frame BYTE-IDENTICAL to the blocking pause
  (test_pause_conformance + test_checkpoint_finalize_*): ``window_from_journal(journal)``
  == the captain transcript, with the suspended call left pending.
- ``resume_chat_pipeline`` rebuilds + continues a HAND-BUILT frame
  (test_resume_consult_e2e).

This test CLOSES the loop between them. It drives the REAL captain loop to the finalize
pause under the flag (the ② producer), serializes the captured frame through the REAL
save→claim JSON cycle (``suspension_from_json(frame.to_json())`` — dropping
transcript/plan/completed exactly as the ``paused_turns`` row does, 执行级事件溯源 Phase 2
⑤/⑥), re-hydrates ``journal_entries`` / ``journal`` the way ``claim_paused_turn`` does off
``turn_journal``, then feeds it to the REAL ``resume_chat_pipeline`` (the same entry the
cloud ``POST .../resume`` route AND the Sidecar call) and asserts the turn CONTINUES to
``END_TURN`` — the suspended call closed with the settled result, the CEO loop run on to
its reply.

So a regression ANYWHERE along producer → serialize → claim → rebuild → continue surfaces
HERE: it proves the single cold-resume path ② collapses everything onto actually carries a
②-finalized turn home, for BOTH suspend points — ask_user (no plan tail) and plan_review
(the harder shape: the gated worker ``s2`` is re-driven from the journal-projected plan +
``completed`` seed, NOT from any serialized blob).
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcore.config import settings
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.memory.store import FileMemoryStore
from agentcore.runtime import pipeline
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.facts import TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.suspension import (
    AskUserSuspension,
    PlanReviewSuspension,
    TurnSuspension,
    captain_transcript,
    suspension_from_json,
)
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import _TEST_BIRTH_FOLDER_ID, _upstream_body
from tests.llm_helpers import make_profile_params


class _ScriptedProvider:
    """Fake LLM: one scripted round of chunks per ``stream`` call, recording each request.

    Shared by the resumed CEO loop AND (on the plan_review path) the re-driven worker tail —
    ``resume_chat_pipeline`` builds ONE provider and threads it into both. Past the script it
    answers tool-free so the loop always finalizes (never hangs)."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.requests: list = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the engine loop
        self.requests.append(request)
        chunks = (
            self._rounds[self.calls]
            if self.calls < len(self._rounds)
            else [LLMChunk(delta_content="收尾")]
        )
        self.calls += 1
        for chunk in chunks:
            yield chunk

    async def close(self) -> None:  # resume_chat_pipeline awaits llm.close() in finally
        return None


class _ExplodingBridge:
    """A ClientRequestBridge whose ``suspend`` must NEVER be reached on the finalize path.

    The whole point of 挂起即收口 is that the producing turn ENDS in place — no in-memory
    Future. If the producer ever parks on this bridge the test fails loudly, not hangs."""

    async def suspend(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("finalize path must not touch the suspend bridge")


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="cap",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
    )


def _backend() -> ServerWorkspace:
    # The resumed continuation here never touches the workspace; a "." root keeps it inert.
    return ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox())


def _seed_loop(system_prompt: str, user_message: str) -> tuple[list[LLMMessage], TurnFactLog]:
    """The captain seed the live pipeline builds before the loop: messages + a fact log
    whose head is ``turn_started`` (so the persisted journal folds back to a real window)."""
    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(
            system_prompt=system_prompt, user_message=user_message, model_profile="m"
        ).to_fact()
    )
    return messages, log


def _cold_claim(frame: TurnSuspension, journal_entries: list[dict]) -> TurnSuspension:
    """Simulate the REAL durable round-trip: ``save`` (``frame.to_json`` → ``paused_turns``)
    then ``claim_paused_turn`` (``suspension_from_json`` + ``turn_journal`` hydration).

    The JSON cycle DROPS ``transcript`` / ``plan`` / ``completed`` (执行级事件溯源 Phase 2 ⑤/⑥
    — they are not serialized), so the resumed window + plan tail MUST be rebuilt purely from
    the hydrated journal, exactly as a cold process (server restart / fresh worker) does — the
    strongest proof the finalize frame is self-sufficient."""
    restored = suspension_from_json(frame.to_json())
    assert not restored.transcript, "the cold-claimed frame must carry no in-memory transcript"
    # claim_paused_turn re-hydrates the raw fact stream (唯一权威载体) off the turn_journal rows;
    # the display-event resume seed (``.journal``) is a DERIVED property of it (P0-B Phase 3).
    restored.journal_entries = list(journal_entries)
    return restored


def _patch_resume_seams(monkeypatch, provider: _ScriptedProvider, store: FileMemoryStore) -> None:
    """Swap ONLY the LLM provider + the memory store; keep the REAL toolset assembly, settle,
    window rebuild and CEO loop. ``resume_chat_pipeline`` reads ``build_turn_router``
    off the package facade; the real assembly reads ``default_memory_store`` as a
    module local."""

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)
    monkeypatch.setattr("agentcore.runtime.resolve.prepare.default_memory_store", lambda: store)
    # No live client on a resume test → keep the resumed loop free of the approval gate.
    monkeypatch.setattr(settings, "approval_gate_enabled", False)


# --- Phase 1 producers: drive the REAL loop to a ②-finalized pause -----------------


async def _finalize_ask_user() -> tuple[AskUserSuspension, list[dict]]:
    """Drive the REAL captain loop to the ask_user finalize pause (flag ON); return the
    captured frame + the journal the face persisted. The producer must end on PAUSED with
    the bridge untouched, else there is no ②-finalized frame to resume."""
    system_prompt = "你是 CEO。"
    user_message = "A 还是 B?"
    captured: dict = {}

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        captured["frame"] = frame
        captured["journal_entries"] = list(frame.journal_entries)

    async def deleter(_message_id: str) -> None:
        return None

    sink = EventSink()
    ask_tool = AskUserTool(
        sink=sink,
        conversation_id="c1",
        timeout_seconds=1.0,
        captain_run_id="cap",
        base_system_prompt=system_prompt,
        user_message=user_message,
        message_id="m1",
        suspension_saver=saver,
        suspension_deleter=deleter,
    )
    reg = ToolRegistry()
    reg.register(ask_tool)

    provider = _ScriptedProvider(
        [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="call_ask",
                            function_name="ask_user",
                            arguments_delta=(
                                f'{{"message": "{user_message}", '
                                f'"assumptions": [{{"label": "默认", "value": "A"}}]}}'
                            ),
                        )
                    ]
                )
            ]
        ]
    )
    messages, log = _seed_loop(system_prompt, user_message)
    profile = make_profile_params(max_rounds=5)
    finish_override: list[FinishReason] = []
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=sink,
            tool_context=_ctx(),
            profile=profile,
            turn_model="m",
            out=ReactLoopOut(finish_override=finish_override),
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert finish_override == [FinishReason.PAUSED], "producer must ② finalize on PAUSED"
    return captured["frame"], captured["journal_entries"]


async def _finalize_plan_review() -> tuple[PlanReviewSuspension, list[dict]]:
    """Drive the REAL captain loop to the plan_review finalize pause (flag ON): a delegate
    whose plan checkpoints after ``s1`` (``s2`` gated downstream). Return the captured frame
    + persisted journal. Producer must end on PAUSED with the bridge never parked."""
    system_prompt = "你是 CEO。"
    user_message = "调研并撰写"
    captured: dict = {}

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        captured["frame"] = frame
        captured["journal_entries"] = list(frame.journal_entries)

    async def deleter(_message_id: str) -> None:
        return None

    sink = EventSink()
    # The delegate's workers run on this provider: s1 → padded S1OUT (then the plan
    # checkpoints and the turn finalizes). s2 runs only AFTER the resume, on the resume's
    # own provider. Pad past MIN_UPSTREAM_BODY_CHARS so handoff accepts.
    s1_body = _upstream_body("S1OUT")
    worker_provider = _ScriptedProvider(
        [[LLMChunk(delta_content=s1_body)], [LLMChunk(delta_content=_upstream_body("S2OUT"))]]
    )
    delegate = DelegateTool(
        llm=worker_provider,
        sink=sink,
        system_prompt=system_prompt,
        user_message=user_message,
        history=[],
        tools=ToolRegistry(),
        base_tool_context=_ctx(),
        conversation_id="c1",
        registry=_ExplodingBridge(),  # type: ignore[arg-type]
        checkpoint_timeout_seconds=5.0,
        checkpoint_enabled=True,
        message_id="m1",
        suspension_saver=saver,
        suspension_deleter=deleter,
        captain_run_id="cap",
        folder_id=_TEST_BIRTH_FOLDER_ID,
        approval_gate=None,
    )
    reg = ToolRegistry()
    reg.register(delegate)

    dag = [
        {"id": "s1", "role": "研究员", "task": "调研", "checkpoint_after": True},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    ]
    captain_provider = _ScriptedProvider(
        [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="call_del",
                            function_name="delegate",
                            arguments_delta=json.dumps(
                                {"tasks": dag, "coordinate": False}
                            ),
                        )
                    ]
                )
            ],
            [LLMChunk(delta_content="（未被消费的兜底）")],
        ]
    )
    messages, log = _seed_loop(system_prompt, user_message)
    profile = make_profile_params(max_rounds=5)
    finish_override: list[FinishReason] = []
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        await react_loop(
            messages=messages,
            llm=captain_provider,
            tools=reg,
            sink=sink,
            tool_context=_ctx(),
            profile=profile,
            turn_model="m",
            out=ReactLoopOut(finish_override=finish_override),
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert finish_override == [FinishReason.PAUSED], "producer must ② finalize on PAUSED"
    return captured["frame"], captured["journal_entries"]


# --- the round-trip: a finalized frame resumes through the cold path to END_TURN ----


async def test_finalized_ask_user_frame_resumes_to_completion(monkeypatch, tmp_path):
    # ② producer: a real ask_user pause finalized to PAUSED.
    frame, journal_entries = await _finalize_ask_user()
    restored = _cold_claim(frame, journal_entries)
    assert isinstance(restored, AskUserSuspension)

    # The cold resume only needs the CEO to wrap up — one tool-free round → END_TURN.
    provider = _ScriptedProvider([[LLMChunk(delta_content="最终答复")]])
    _patch_resume_seams(monkeypatch, provider, FileMemoryStore(tmp_path / "memory"))

    result = await pipeline.resume_chat_pipeline(
        suspension=restored,
        decision=CheckpointDecision.CONTINUE,
        note="选 A",
        selected=[],
        sink=EventSink(),
        backend=_backend(),
    )

    # The turn CONTINUED off the finalized frame and finished cleanly.
    assert result["finish_reason"] == FinishReason.END_TURN
    assert result.get("error") is None
    assert "最终答复" in (result["content"] or "")
    # The suspended ask_user call was closed with the settled result and fed back: the
    # resumed CEO request is a valid assistant-tool_call → tool-result pair (else it 400s).
    assert provider.requests, "the resumed CEO loop must have called the model"
    tool_msgs = [m for m in provider.requests[-1].messages if m.role == "tool"]
    assert any(m.tool_call_id == restored.tool_call_id for m in tool_msgs)


async def test_finalized_plan_review_frame_resumes_to_completion(monkeypatch, tmp_path):
    # ② producer: a real plan_review pause (s1 done, s2 gated) finalized to PAUSED.
    frame, journal_entries = await _finalize_plan_review()
    restored = _cold_claim(frame, journal_entries)
    assert isinstance(restored, PlanReviewSuspension)
    # The cold-claimed frame dropped the plan + completed blobs — the resume MUST rebuild
    # both from the hydrated journal (执行级事件溯源 Phase 2), then re-drive the tail.
    assert not restored.completed
    assert not restored.plan.nodes

    # The resume's ONE provider serves BOTH the re-driven worker s2 (→ S2OUT) and the CEO's
    # finalize round (→ 最终答复), in that order (settle drives the tail before the loop).
    provider = _ScriptedProvider(
        [
            [LLMChunk(delta_content=_upstream_body("S2OUT"))],
            [LLMChunk(delta_content="最终答复")],
        ]
    )
    _patch_resume_seams(monkeypatch, provider, FileMemoryStore(tmp_path / "memory"))

    result = await pipeline.resume_chat_pipeline(
        suspension=restored,
        decision=CheckpointDecision.CONTINUE,
        note="继续",
        selected=[],
        sink=EventSink(),
        backend=_backend(),
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    assert result.get("error") is None
    assert "最终答复" in (result["content"] or "")
    # The gated tail actually re-drove: the worker s2 round AND the captain finalize round
    # both fired on the resume provider (the worker, then the CEO).
    assert provider.calls >= 2
    # The suspended delegate call was closed with the workers' product and fed back as a
    # valid tool result for the CEO's finalize round.
    tool_msgs = [m for m in provider.requests[-1].messages if m.role == "tool"]
    assert any(m.tool_call_id == restored.tool_call_id for m in tool_msgs)
