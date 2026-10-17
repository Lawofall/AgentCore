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
transcript exactly as the ``paused_turns`` row does, 执行级事件溯源 Phase 2
⑤/⑥), re-hydrates ``journal_entries`` / ``journal`` the way ``claim_paused_turn`` does off
``turn_journal``, then feeds it to the REAL ``resume_chat_pipeline`` (the same entry the
cloud ``POST .../resume`` route AND the Sidecar call) and asserts the turn CONTINUES to
``END_TURN`` — the suspended call closed with the settled result, the CEO loop run on to
its reply.

So a regression ANYWHERE along producer → serialize → claim → rebuild → continue surfaces
HERE: it proves the single cold-resume path ② collapses everything onto actually carries a
②-finalized ask_user turn home (no plan tail). Leftover ``plan_review`` frames are an
unknown kind (from_json ValueError / list skip / resume 410) and never enter this path.
"""

from __future__ import annotations

from pathlib import Path

from agentcore.config import settings
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime import pipeline
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.facts import TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.suspension import (
    AskUserSuspension,
    TurnSuspension,
    captain_transcript,
    suspension_from_json,
)
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


class _ScriptedProvider:
    """Fake LLM: one scripted round of chunks per ``stream`` call, recording each request.

    Shared by the resumed CEO loop. Past the script it answers tool-free so the loop
    always finalizes (never hangs)."""

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

    The JSON cycle DROPS ``transcript`` (执行级事件溯源 Phase 2 ⑤/⑥
    — it is not serialized), so the resumed window MUST be rebuilt purely from
    the hydrated journal, exactly as a cold process (server restart / fresh worker) does — the
    strongest proof the finalize frame is self-sufficient."""
    restored = suspension_from_json(frame.to_json())
    assert not restored.transcript, "the cold-claimed frame must carry no in-memory transcript"
    # claim_paused_turn re-hydrates the raw fact stream (唯一权威载体) off the turn_journal rows;
    # the display-event resume seed (``.journal``) is a DERIVED property of it (P0-B Phase 3).
    restored.journal_entries = list(journal_entries)
    return restored


def _patch_resume_seams(monkeypatch, provider: _ScriptedProvider) -> None:
    """Swap ONLY the LLM provider; keep the REAL toolset assembly, settle,
    window rebuild and CEO loop. ``resume_chat_pipeline`` reads ``build_turn_router``
    off the package facade."""

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)
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
                                f'{{"questions": [{{"prompt": "{user_message}"}}]}}'
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


# --- the round-trip: a finalized frame resumes through the cold path to END_TURN ----


async def test_finalized_ask_user_frame_resumes_to_completion(monkeypatch):
    # ② producer: a real ask_user pause finalized to PAUSED.
    frame, journal_entries = await _finalize_ask_user()
    restored = _cold_claim(frame, journal_entries)
    assert isinstance(restored, AskUserSuspension)

    # The cold resume only needs the CEO to wrap up — one tool-free round → END_TURN.
    provider = _ScriptedProvider([[LLMChunk(delta_content="最终答复")]])
    _patch_resume_seams(monkeypatch, provider)

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
