"""End-to-end: ``react_loop`` records execution-level facts (§18.3 Phase 1).

Drives the real ReAct loop with a scripted fake provider + a stub tool, with a
:class:`~agentcore.runtime.facts.TurnFactLog` bound to the ambient
``current_fact_log`` — exactly how the pipeline binds it per turn. Asserts the
loop emits the execution facts (``round_boundary`` / ``llm_call`` / ``note``)
into that single ordered log, INTERLEAVED with the display facts the sink
forwards (``tool_use_start`` / ``tool_use_end``) — the「单一有序日志」the durable
journal projects from. ``message_final`` / ``turn_started`` are recorded by the
executor / pipeline, not the bare loop, so they are out of scope here.
"""

from pathlib import Path

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import (
    FactKind,
    MessageFinalFact,
    RoundBoundaryFact,
    TurnFactLog,
    TurnStartedFact,
    current_fact_log,
)
from agentcore.runtime.journal import runs_from_entries, window_from_journal
from agentcore.runtime.pipeline import _journal_entries_for_turn
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _tool_chunk(name: str, args: str, *, call_id: str = "c1") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


def _reasoning_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_reasoning=text)


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
    def __init__(
        self, name: str = "search", *, category: ToolCategory = ToolCategory.SEARCH
    ) -> None:
        self._name = name
        self._category = category
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=self._category,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(tool_call_id="", success=True, output="result")


class _CitingTool:
    """A research tool whose result carries a web citation (drives the CEO annotate path).

    The CEO path folds the source's card-aligned number into the tool message AFTER the
    ``tool_use_end`` event fires, so the forwarded display result is the un-annotated text
    — the 边界① divergence the dedicated ``tool_call`` fact closes.
    """

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="search",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        return ToolResult(
            tool_call_id="",
            success=True,
            output="found",
            citations=[{"url": "https://example.com/a", "title": "A"}],
        )


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def _run(provider: _ScriptedProvider, tool: _StubTool, *, max_rounds: int = 20):
    """Run the loop with a fresh fact log bound, returning (recorded_facts, content)."""
    reg = ToolRegistry()
    reg.register(tool)
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    profile = make_profile_params(max_rounds=max_rounds)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        content, _reasoning, _usage, _rounds = await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=EventSink(),
            tool_context=_context(),
            profile=profile,
            turn_model="m",
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        current_fact_log.reset(token)
    return log.entries(), content, messages


async def test_loop_records_round_boundary_and_llm_call_per_round():
    # Round 0 issues a tool call; round 1 answers with text → two rounds, so two
    # round_boundary + two llm_call facts, with the tool's display facts between them.
    provider = _ScriptedProvider([[_tool_chunk("search", '{"q": "x"}')], [_content_chunk("done")]])
    facts, content, _messages = await _run(provider, _StubTool())

    assert content == "done"

    boundaries = [f for f in facts if f["kind"] == FactKind.ROUND_BOUNDARY]
    assert len(boundaries) == 2
    assert [b["payload"]["round_idx"] for b in boundaries] == [0, 1]
    # run_id / role scope every round to the captain (multi-agent turns split per run).
    assert all(
        b["payload"] == {"round_idx": i, "run_id": "cap", "role": "captain"}
        for i, b in enumerate(boundaries)
    )

    calls = [f for f in facts if f["kind"] == FactKind.LLM_CALL]
    assert len(calls) == 2

    # Round 0: only the output is stored — the tool call (serialized id/type/function),
    # no content, finish_reason "tool_calls". The input window is NOT duplicated.
    tool_round = calls[0]["payload"]
    assert tool_round["content"] == ""
    assert tool_round["finish_reason"] == "tool_calls"
    assert tool_round["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}}
    ]

    # Round 1: the textual answer, no tool calls, finish_reason "stop".
    text_round = calls[1]["payload"]
    assert text_round["content"] == "done"
    assert text_round["tool_calls"] == []
    assert text_round["finish_reason"] == "stop"


async def test_llm_call_fact_preserves_upstream_length_finish_reason():
    """Engine must not rewrite upstream finish_reason=length to stop on LlmCallFact."""
    provider = _ScriptedProvider([[LLMChunk(delta_content="半截", finish_reason="length")]])
    facts, content, _messages = await _run(provider, _StubTool())
    assert content == "半截"
    calls = [f for f in facts if f["kind"] == FactKind.LLM_CALL]
    assert len(calls) == 1
    assert calls[0]["payload"]["finish_reason"] == "length"


async def test_single_ordered_log_interleaves_display_and_execution_facts():
    # The sink's display facts (tool_use_start/end) and the engine's execution facts
    # land in ONE log, in emission order: rb0, llm0(tool), tool_use_start/end, rb1, llm1.
    provider = _ScriptedProvider([[_tool_chunk("search", "{}")], [_content_chunk("ok")]])
    facts, _content, _messages = await _run(provider, _StubTool())

    kinds = [f["kind"] for f in facts]
    # Execution facts are present...
    assert kinds.count(FactKind.ROUND_BOUNDARY) == 2
    assert kinds.count(FactKind.LLM_CALL) == 2
    # ...and so are the forwarded display facts (the single-log proof).
    assert "tool_use_start" in kinds
    assert "tool_use_end" in kinds
    # Ordering: round 0's tool call is recorded before its tool runs, which is before
    # round 1 starts — so the tool_use facts sit between the two round boundaries.
    first_boundary = kinds.index(FactKind.ROUND_BOUNDARY)
    second_boundary = kinds.index(FactKind.ROUND_BOUNDARY, first_boundary + 1)
    tool_start = kinds.index("tool_use_start")
    assert first_boundary < tool_start < second_boundary


async def test_loop_records_note_fact_on_nudge():
    # Three identical tool calls trip the stuck-loop detector → NUDGE logs only
    # (no [系统提示] in the window). The 4th round answers.
    same = _tool_chunk("compute", '{"q": "x"}')
    provider = _ScriptedProvider([[same], [same], [same], [_content_chunk("final")]])
    facts, content, messages = await _run(
        provider, _StubTool(name="compute", category=ToolCategory.EXECUTION)
    )

    assert content == "final"
    notes = [f for f in facts if f["kind"] == FactKind.NOTE]
    assert notes == []
    assert not any(
        m.role == "user" and m.content and "[系统提示]" in m.content
        for m in messages
    )


def test_journal_entries_for_turn_gates_on_plain_sink():
    # Persistence cutover: a plain chat turn (nothing surfaced, no process) writes NO
    # journal, so storage + the None-gate match the pre-cutover behavior even though
    # the fact log accumulated.
    from agentcore.runtime.events import FinishReason

    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(system_prompt="s", user_message="hi", model_profile="m").to_fact()
    )
    log.record_fact(RoundBoundaryFact(round_idx=0, run_id="cap", role="captain").to_fact())
    sink = EventSink()
    assert _journal_entries_for_turn(log, sink=sink, finish=FinishReason.END_TURN) is None


def test_journal_entries_for_turn_composes_log_plus_tail_and_projects_gated():
    # A surfaced turn: durable = the fact log (execution + forwarded display facts) +
    # the process/turn_end tail read off the sink. runs.events is NOT re-appended (it
    # already rides the log); the read-side projection re-gates to the team graph.
    from agentcore.runtime.events import FinishReason

    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(system_prompt="s", user_message="go", model_profile="m").to_fact()
    )
    log.record_fact(RoundBoundaryFact(round_idx=0, run_id="cap", role="captain").to_fact())
    # The forwarded display events that would ride the log (surfaced: run_plan present).
    from agentcore.runtime.facts import Fact

    log.record_fact(Fact(kind="run_plan", payload={"execution_id": "e1"}, ts="t0"))
    log.record_fact(Fact(kind="run_completed", payload={"run_id": "w1"}, ts="t1"))
    log.record_fact(MessageFinalFact(run_id="cap", content="done").to_fact())

    sink = EventSink()
    sink.seed_journal([{"type": "run_plan", "payload": {"execution_id": "e1"}, "timestamp": "t0"}])
    durable = _journal_entries_for_turn(log, sink=sink, finish=FinishReason.END_TURN)

    # Tail = just turn_end (no process); the log's own entries come first verbatim.
    assert durable[-1] == {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None}
    assert durable[: len(log.entries())] == log.entries()

    # Projecting the durable journal back yields the gated team graph (exec facts
    # skipped, run_plan surfaces so events are kept).
    assert runs_from_entries(durable) == {
        "events": [
            {"type": "run_plan", "payload": {"execution_id": "e1"}, "timestamp": "t0"},
            {"type": "run_completed", "payload": {"run_id": "w1"}, "timestamp": "t1"},
        ],
        "finish_reason": "end_turn",
        # Display fold synthesizes ``team`` from ``run_plan`` when process_team absent.
        "process": [{"kind": "team", "execution_id": "e1"}],
    }


async def test_window_from_journal_reconstructs_live_transcript():
    # End-to-end conformance (the unit-level golden Phase 2 generalizes to paused
    # turns): drive the REAL loop, then assert window_from_journal folds the recorded
    # facts back into the EXACT live transcript the loop fed the model — system + user
    # + assistant(tool_call WITH reasoning_content) + tool(result). This is the window
    # Phase 2 resume will rebuild from the journal instead of the paused_turns frame.
    system_prompt = "You are the CEO."
    provider = _ScriptedProvider(
        [
            [_reasoning_chunk("let me search"), _tool_chunk("search", '{"q": "x"}')],
            [_content_chunk("the answer")],
        ]
    )
    reg = ToolRegistry()
    reg.register(_StubTool())
    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content="go"),
    ]
    profile = make_profile_params(max_rounds=20)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=EventSink(),
            tool_context=_context(),
            profile=profile,
            turn_model="m",
            run_id="cap",
            role="captain",
            approval_gate=None,
        )
    finally:
        current_fact_log.reset(token)

    # The pipeline records turn_started (the head); the bare loop does not, so prepend
    # it to the loop's facts to form the full turn journal, capturing the verbatim
    # system prompt + user message exactly as the executor seeded the transcript.
    facts = [
        TurnStartedFact(system_prompt=system_prompt, user_message="go", model_profile="m")
        .to_fact()
        .entry()
    ] + log.entries()

    # The live transcript after the loop = system, user, assistant(tool+reasoning),
    # tool — the round-1 final answer is returned, not appended. The projection must
    # reproduce it byte-for-byte (reasoning_content included, or a resumed request 400s).
    assert window_from_journal(facts) == messages


async def test_tool_call_fact_captures_post_annotation_text_ceo_path():
    # 边界① cleared: on the CEO path the source's citation number is folded into the tool
    # message AFTER tool_use_end fires, so the forwarded display result is the PRE-annotation
    # text. The tool_call execution fact is recorded after that fold, so the window
    # reproduces the EXACT annotated tool message the next round saw — proving the window no
    # longer reads the diverging tool_use_end text.
    system_prompt = "You are the CEO."
    provider = _ScriptedProvider([[_tool_chunk("search", "{}")], [_content_chunk("done")]])
    reg = ToolRegistry()
    reg.register(_CitingTool())
    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content="go"),
    ]
    profile = make_profile_params(max_rounds=20)
    log = TurnFactLog()
    citations: list[dict] = []
    token = current_fact_log.set(log)
    try:
        await react_loop(
            messages=messages,
            llm=provider,
            tools=reg,
            sink=EventSink(),
            tool_context=_context(),
            profile=profile,
            turn_model="m",
            run_id="cap",
            role="captain",
            out=ReactLoopOut(citations=citations),
            annotate_citations=True,
            approval_gate=None,
        )
    finally:
        current_fact_log.reset(token)

    # The live tool message carries the citation annotation (the post-emit fold)...
    tool_msg = next(m for m in messages if m.role == "tool")
    assert "[来源编号]" in (tool_msg.content or "")
    assert tool_msg.content != "found"  # diverged from the pre-annotation tool_use_end text
    # ...and the tool_call fact captured that SAME annotated text (not "found").
    tool_facts = [f for f in log.entries() if f["kind"] == FactKind.TOOL_CALL]
    assert len(tool_facts) == 1
    assert tool_facts[0]["payload"]["result"] == tool_msg.content
    assert tool_facts[0]["payload"]["run_id"] == "cap"

    # The window folds the annotated text back, reproducing the live transcript exactly.
    facts = [
        TurnStartedFact(system_prompt=system_prompt, user_message="go", model_profile="m")
        .to_fact()
        .entry()
    ] + log.entries()
    assert window_from_journal(facts) == messages


async def test_no_facts_recorded_when_no_log_bound():
    # Outside a turn (no log bound) recording is a no-op — the bare loop runs exactly
    # as before, so binding facts never changes engine behavior where it isn't wanted.
    provider = _ScriptedProvider([[_content_chunk("hi")]])
    reg = ToolRegistry()
    reg.register(_StubTool())
    profile = make_profile_params(max_rounds=5)

    # Sanity: ensure nothing is bound, then run with no TurnFactLog set.
    assert current_fact_log.get() is None
    content, _r, _u, _rounds = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=reg,
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        run_id="cap",
        role="captain",
        approval_gate=None,
    )
    assert content == "hi"
