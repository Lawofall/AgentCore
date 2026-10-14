"""Conformance golden: a paused turn's PERSISTED journal projects back to its transcript.

This is the machine judge gating the Phase 2 resume cutover (执行级事件溯源 §18.3，
conformance golden 闸). It drives the REAL :class:`AskUserTool` to its suspend point and
asserts that ``window_from_journal`` folded over the exact stream the face persists
(``frame.journal_entries`` — the ``current_fact_log`` snapshot ``_save_pause_journal``
writes to ``turn_journal``) reproduces, byte for byte, the ``captain_transcript`` the
same face snapshots into the (now in-memory only) frame — i.e.
``window_from_journal(persisted) == frame.transcript``. Green gated the cutover: resume now
rebuilds the CEO window from the journal (Phase 2 ④) and the旁路 ``paused_turns.frame``
transcript column is GONE (Phase 2 ⑤ — no longer serialized; the second source of truth
disappeared). ``frame.transcript`` here is the live in-memory capture the face still makes
off ``captain_transcript`` for exactly this comparison.

It also guards the DISPLAY side stayed whole: ``runs_from_entries(persisted)`` still
surfaces the ``checkpoint_required`` card, so persisting the richer execution stream did
not cost the reload its prompt card.

Why a real drive, not a hand-built journal: the whole risk is that the engine's actual
emission order / message shaping diverges from the fold (a missing ``reasoning_content``
回灌, a phantom tool message for the suspended call → resume 400s = a lost turn). Only
exercising the live loop + the live capture pins that. The棘轮: when a real pause shape
breaks the fold, add it here FIRST, then fix the projection.
"""

import json
from pathlib import Path

from agentcore.core.types import ToolFace  # noqa: F401 — parity with engine-facts harness
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import react_loop
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.journal import (
    runs_from_entries,
    window_from_journal,
)
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.builtin.graph import GraphTool
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


class _StubTool:
    """A benign non-terminal tool that completes with a fixed output (no citations).

    No citations on purpose: the CEO path annotates citation numbers into the tool
    message AFTER emitting ``tool_use_end``, a known Phase-1 divergence the journal does
    not yet capture (执行级事件溯源 §18.3 投影边界①). Keeping this tool citation-free
    means the completed tool round it produces folds back faithfully, so this gate pins
    the multi-round pause shape WITHOUT tripping that separate, documented gap.
    """

    def __init__(self, name: str = "search") -> None:
        self._name = name

    @property
    def schema(self):  # noqa: ANN201 - duck-typed for the registry
        from agentcore.tools.protocol import ToolSchema

        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
        )

    async def execute(self, arguments, context):  # noqa: ANN001
        from agentcore.tools.protocol import ToolResult

        return ToolResult(tool_call_id="", success=True, output="found it")


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="cap",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def test_pause_journal_projects_to_captain_transcript():
    # Drive the captain loop to the ask_user suspend point and assert the journal-at-
    # pause folds back to the snapshotted captain transcript — the conformance gate.
    system_prompt = "你是 CEO。"
    user_message = "A 还是 B?"

    captured: dict[str, object] = {}

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        # Snapshot the frame the suspending face built: the live captain_transcript AND
        # the journal_entries it will persist to turn_journal (the fact-log snapshot at
        # the pause — before the tool resolves, so the ask_user call's tool_use_end is
        # not yet recorded). Asserting on journal_entries (not the live log) pins the
        # EXACT bytes _save_pause_journal writes — the resume's future window source.
        captured["transcript"] = list(frame.transcript)
        captured["journal_entries"] = list(frame.journal_entries)

    async def deleter(_message_id: str) -> None:
        captured["deleted"] = True

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

    # The live captain seeds messages = system + user and publishes them on
    # captain_transcript for the suspending face; the pipeline binds the fact log and
    # records turn_started (the head) before the loop — seed both here to match.
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
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=sink,
            tool_context=_context(),
            profile=profile,
            turn_model="m",
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert "transcript" in captured, "the suspending face must have captured a frame"
    persisted = captured["journal_entries"]  # type: ignore[assignment]

    # THE GOLDEN: the window folded from the PERSISTED journal == the transcript the
    # frame snapshotted. Both end at the assistant message issuing the suspended ask_user
    # call (no tool result — it is still pending), so resume can append the settled one.
    assert window_from_journal(persisted) == captured["transcript"]

    # DISPLAY whole: the richer execution stream still surfaces the checkpoint card.
    runs = runs_from_entries(persisted)
    assert runs is not None
    assert any(e["type"] == "checkpoint_required" for e in runs["events"])

    # Guard the shape so a future regression can't make BOTH sides wrongly empty/equal.
    transcript = captured["transcript"]
    assert isinstance(transcript, list) and len(transcript) == 3
    assert transcript[0].role == "system" and transcript[0].content == system_prompt
    assert transcript[1].role == "user" and transcript[1].content == user_message
    assert transcript[2].role == "assistant"
    assert transcript[2].tool_calls[0].function.name == "ask_user"
    # And no phantom tool result rode in for the suspended call.
    assert all(m.role != "tool" for m in transcript)


async def test_pause_journal_after_completed_tool_round():
    # A pause AFTER a completed tool round: round 0 runs `search` (completes →
    # tool_use_end), round 1 issues ask_user (suspends). The fold must keep the
    # completed round's assistant+tool pair AND end at the suspended ask_user with no
    # tool result — the harder, realistic resume shape (prior context before the fork).
    system_prompt = "你是 CEO。"
    user_message = "查一下再问我"

    captured: dict[str, object] = {}

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        captured["transcript"] = list(frame.transcript)
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
    reg.register(_StubTool("search"))
    reg.register(ask_tool)

    provider = _ScriptedProvider(
        [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0, id="c_search", function_name="search", arguments_delta="{}"
                        )
                    ]
                )
            ],
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
            ],
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
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=sink,
            tool_context=_context(),
            profile=profile,
            turn_model="m",
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    persisted = captured["journal_entries"]  # type: ignore[assignment]
    assert window_from_journal(persisted) == captured["transcript"]
    # DISPLAY whole: the completed search tool + the checkpoint card both survive.
    runs = runs_from_entries(persisted)
    assert runs is not None
    assert any(e["type"] == "checkpoint_required" for e in runs["events"])
    # The shape: system, user, assistant(search), tool(found), assistant(ask_user).
    transcript = captured["transcript"]
    assert [m.role for m in transcript] == [  # type: ignore[union-attr]
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert transcript[3].content == "found it"  # type: ignore[index]
    assert transcript[4].tool_calls[0].function.name == "ask_user"  # type: ignore[index]


# --- leftover plan_review: extra checkpoint_after runs through (no pause) --------


def test_fold_skips_leftover_plan_review_required():
    from agentcore.runtime.journal.pending_interactions import (
        fold_interactions,
        fold_pending_interactions,
    )

    entries = [
        {
            "type": "plan_review_required",
            "payload": {
                "checkpoint_id": "cp_1",
                "conversation_id": "c",
                "steps": [{"run_id": "r1", "role": "调研", "summary": "方案就绪"}],
                "pending": [{"run_id": "r2", "role": "执行"}],
            },
        }
    ]
    assert fold_pending_interactions(entries, message_id="m1") == []
    assert fold_interactions(entries) == []
