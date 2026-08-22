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

from agentcore.core.types import ToolCategory  # noqa: F401 — parity with engine-facts harness
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import react_loop
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.journal import (
    completed_from_journal,
    plan_from_journal,
    runs_from_entries,
    window_from_journal,
)
from agentcore.runtime.runs.serialize import plan_to_json, state_map_to_json
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
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
            category=ToolCategory.SEARCH,
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
                                f'{{"message": "{user_message}", '
                                f'"assumptions": [{{"label": "默认", "value": "A"}}]}}'
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
                                f'{{"message": "{user_message}", '
                                f'"assumptions": [{{"label": "默认", "value": "A"}}]}}'
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


# --- plan_review / delegate suspend golden (the harder pause shape) ----------------


async def test_plan_review_pause_journal_projects_to_captain_transcript():
    # The delegate counterpart of the ask_user golden — the harder shape: drive the REAL
    # captain loop to a `delegate` whose plan checkpoints after s1, suspending the
    # WaveScheduler at the wave boundary. The journal-at-pause interleaves the captain's
    # facts with the WORKER's own (different run_id), and the suspended delegate has NO tool
    # result yet. The fold must still reproduce the captain transcript byte-for-byte —
    # gating the Phase 2 resume cutover for the plan_review suspend point.
    system_prompt = "你是 CEO。"
    user_message = "调研并撰写"

    captured: dict[str, object] = {}

    async def saver(frame) -> None:  # noqa: ANN001 - TurnSuspension
        # The fact-log snapshot _save_pause_journal will persist (the pause instant — the
        # delegate's tool result is not yet recorded) AND the captain_transcript snapshot.
        captured["transcript"] = list(frame.transcript)
        captured["journal_entries"] = list(frame.journal_entries)
        # frame.completed = the scheduler's finished-worker seed at the checkpoint (s1) —
        # the blob Phase 2 ⑥ replaces with a journal projection.
        captured["completed"] = dict(frame.completed)
        # frame.plan (frozen as JSON at the pause instant) = the DAG blob Phase 2 replaces
        # with the ``plan_snapshot`` journal projection. Frozen now since the live object
        # could be steered later.
        captured["plan_json"] = plan_to_json(frame.plan)

    async def deleter(_message_id: str) -> None:
        captured["deleted"] = True

    sink = EventSink()
    registry = InteractionRegistry()
    # The captain's delegate runs its workers on this scripted provider: s1 → padded S1OUT
    # (then the plan checkpoints), s2 → padded S2OUT (after resume). Separate from the captain
    # provider. Pad past MIN_UPSTREAM_BODY_CHARS so handoff accepts.
    s1_body = _upstream_body("S1OUT")
    s2_body = _upstream_body("S2OUT")
    worker_provider = _ScriptedProvider(
        [[LLMChunk(delta_content=s1_body)], [LLMChunk(delta_content=s2_body)]]
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
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(messages)
    try:
        # ②: the delegate checkpoint finalizes the turn at the wave boundary (frame saved) —
        # react_loop ends on PAUSED in place, with no parked plan_review to resolve. The saver
        # snapshotted the pause frame under test before the boundary yielded.
        await react_loop(
            messages=messages,
            llm=captain_provider,
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

    assert "transcript" in captured, "the delegate checkpoint must have captured a frame"
    persisted = captured["journal_entries"]  # type: ignore[assignment]

    # THE GOLDEN: the window folded from the persisted journal == the captain transcript
    # the frame snapshotted. Both end at the assistant issuing the suspended delegate (no
    # tool result — the wave is parked), the interleaved worker facts (run_id="s1"…) excluded.
    assert window_from_journal(persisted) == captured["transcript"]

    # DISPLAY whole: the richer execution stream still surfaces the plan_review card.
    runs = runs_from_entries(persisted)
    assert runs is not None
    assert any(e["type"] == "plan_review_required" for e in runs["events"])

    # The shape: system, user, assistant(delegate) — no tool message for the suspended call.
    transcript = captured["transcript"]
    assert [m.role for m in transcript] == ["system", "user", "assistant"]  # type: ignore[union-attr]
    assert transcript[2].tool_calls[0].function.name == "delegate"  # type: ignore[index]
    assert all(m.role != "tool" for m in transcript)  # type: ignore[union-attr]
    # And the journal really did interleave a worker's facts (proving run-scope isolation).
    assert any(
        e.get("kind") == "llm_call" and (e.get("payload") or {}).get("run_id") != "cap"
        for e in persisted  # type: ignore[union-attr]
    )

    # THE COMPLETED GOLDEN (Phase 2 ⑥): the worker run-final facts fold back to the EXACT
    # seed map the frame snapshotted — so a resume re-seeds finished nodes (s1) from the
    # journal, gating the drop of frame.completed. Compared through the shared serializer
    # (state_map_to_json) since both sides drop the heavy transcript → byte-for-byte equal.
    projected = completed_from_journal(persisted)
    # Same finished run_ids (only s1 — its minted id — ran before the checkpoint; s2 is
    # downstream and runs post-resume) AND byte-for-byte equal seed RunStates.
    assert set(projected) == set(captured["completed"])  # type: ignore[arg-type]
    assert len(projected) == 1
    assert state_map_to_json(projected) == state_map_to_json(captured["completed"])  # type: ignore[arg-type]
    (s1_run_id,) = projected
    assert s1_run_id.endswith("_s1")
    assert projected[s1_run_id].content == s1_body

    # THE PLAN GOLDEN (执行级事件溯源 Phase 2, frame.plan 退场): the ``plan_snapshot`` fact folds
    # back to the EXACT DAG the frame snapshotted — so a resume rebuilds the plan (its minted
    # run_ids matching the seed map above) from the journal, gating the drop of frame.plan.
    # Compared through the shared serializer (plan_to_json) → byte-for-byte equal.
    projected_plan = plan_from_journal(persisted)
    assert projected_plan is not None
    assert plan_to_json(projected_plan) == captured["plan_json"]
    # Both nodes (s1 ran, s2 pending) survive with their minted ids + dependency edge.
    assert [n.run_id for n in projected_plan.nodes] == [
        n["run_id"]
        for n in captured["plan_json"]["nodes"]  # type: ignore[index]
    ]
    assert projected_plan.nodes[1].depends_on == [projected_plan.nodes[0].run_id]
