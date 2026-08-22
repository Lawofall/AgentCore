"""挂起即收口 (②): the plan_review (delegate) 收口 slice.

The delegate counterpart of the ask_user finalize. When a delegate plan checkpoints after
a step (``checkpoint_after``), the wave boundary PERSISTS the plan_review frame and then
ENDS the turn (``FinishReason.PAUSED``) at the boundary instead of parking
the scheduler on the in-memory interaction Future — so EVERY resolution (even in-session)
flows through the one cold ``POST .../resume`` path.

Unlike test_pause_conformance's plan_review golden — which needs a CONCURRENT resolver to
un-park the blocking wait — this drives the captain loop to completion with NO resolver:
the finalize path proves the turn ENDS on its own at the checkpoint, and the interaction
bridge is never parked. The journal the face persisted must still fold back to the captain
transcript ending at the assistant issuing the suspended delegate (no tool result) — the
resume window source, byte-for-byte the blocking shape.
"""

import json
from pathlib import Path

from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.facts import FactKind, TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.journal import runs_from_entries, window_from_journal
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import _TEST_BIRTH_FOLDER_ID, _upstream_body
from tests.llm_helpers import make_profile_params


class _ScriptedProvider:
    """Yields one pre-scripted chunk list per ``stream`` call (one call per round)."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="cap",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def test_loop_finalizes_plan_review_to_paused():
    # Drive the REAL captain loop with a REAL DelegateTool whose plan checkpoints after s1.
    # The wave boundary persists the frame and ENDS the turn at the boundary — no resolver,
    # no parked bridge. The loop must finish on FinishReason.PAUSED with the delegate call
    # PENDING, and the persisted journal must fold to the captain transcript.
    system_prompt = "你是 CEO。"
    user_message = "调研并撰写"
    captured: dict[str, object] = {}

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        captured["transcript"] = list(frame.transcript)
        captured["journal_entries"] = list(frame.journal_entries)
        captured["completed"] = dict(frame.completed)

    async def deleter(_message_id: str) -> None:
        captured["deleted"] = True

    sink = EventSink()
    registry = InteractionRegistry()
    # The delegate runs its workers on this scripted provider: s1 → long enough body
    # (≥ MIN_UPSTREAM_BODY_CHARS) so handoff accepts and the plan checkpoints; s2 would
    # run only after a resume.
    s1_body = _upstream_body("S1OUT")
    worker_provider = _ScriptedProvider(
        [[LLMChunk(delta_content=s1_body)], [LLMChunk(delta_content="S2OUT")]]
    )
    delegate = DelegateTool(
        llm=worker_provider,
        sink=sink,
        system_prompt=system_prompt,
        user_message=user_message,
        history=[],
        tools=ToolRegistry(),
        base_tool_context=_context(),
        conversation_id="c1",
        registry=registry,
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
            [LLMChunk(delta_content="最终答复")],
        ]
    )

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    profile = make_profile_params(max_rounds=5)
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(
            system_prompt=system_prompt, user_message=user_message, model_profile="m"
        ).to_fact()
    )
    finish_override: list[FinishReason] = []
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        # No concurrent resolver — the loop must end ON ITS OWN at the checkpoint.
        content, _reasoning, _usage, _rounds = await react_loop(
            messages=messages,
            llm=captain_provider,
            tools=reg,
            sink=sink,
            tool_context=_context(),
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

    # The turn ended ON PAUSED with no answer text — the engine mapped the delegate's
    # SUSPEND to FinishReason.PAUSED.
    assert finish_override == [FinishReason.PAUSED]
    assert content == ""
    # s2 never ran (only s1 before the checkpoint); the captain's 2nd round never fired.
    assert worker_provider.calls == 1
    assert captain_provider.calls == 1

    # The interaction bridge was NEVER parked — the finalize path persists + ends instead
    # of suspending on the in-memory Future (the whole point of ②).
    assert registry.list_pending("c1") == []

    # The suspended delegate call is pending: the transcript ends at the assistant issuing
    # delegate, with NO tool result message.
    assert [m.role for m in messages] == ["system", "user", "assistant"]
    assert messages[-1].tool_calls[0].function.name == "delegate"
    assert all(m.role != "tool" for m in messages)

    # No §8.3 tool_call fact for the suspended delegate call (would inject a phantom result).
    assert all(
        not (f["kind"] == FactKind.TOOL_CALL and f["payload"].get("name") == "delegate")
        for f in log.entries()
    )

    # THE GOLDEN: the window folded from the PERSISTED journal == the snapshotted captain
    # transcript == the live transcript — so a cold resume rebuilds the exact pre-pause
    # window. The interleaved worker (s1) facts are excluded from the captain window.
    persisted = captured["journal_entries"]  # type: ignore[assignment]
    assert window_from_journal(persisted) == captured["transcript"]
    assert window_from_journal(persisted) == messages

    # The frame seeded the finished worker (s1) for the resume drive.
    assert any(rid.endswith("_s1") for rid in captured["completed"])  # type: ignore[union-attr]

    # DISPLAY whole: the richer execution stream still surfaces the plan_review card.
    # CEO 评审前置：payload 必带把关摘要（LLM 不可用时为确定性回落）。
    runs = runs_from_entries(persisted)
    assert runs is not None
    review_events = [e for e in runs["events"] if e["type"] == "plan_review_required"]
    assert review_events
    ceo_review = review_events[0]["payload"].get("ceo_review")
    assert isinstance(ceo_review, dict)
    assert ceo_review.get("conclusion")
    assert ceo_review.get("risks")
    assert ceo_review.get("suggestions")
    # 无 LLM 时回落确定性摘要
    assert ceo_review.get("source") == "deterministic"
