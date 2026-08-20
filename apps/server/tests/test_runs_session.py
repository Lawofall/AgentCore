"""RunSession capture + continue_run (统一「续干」原语).

Proves a finished worker is kept alive as a recoverable RunSession and that
``continue_run`` recalls the SAME author: it sees its prior transcript + the
appended instruction, extends the transcript, and emits continuation graph
events with ``continues_run_id`` = session root and ``parent_run_id`` = true parent.
"""

import asyncio
from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
)
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs import (
    RunPhase,
    RunSession,
    RunSpec,
    build_agent_executor,
    build_run_plan,
    continue_run,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.sessions import SessionStore
from agentcore.runtime.terminal import RUN_CLOSE_EVENT_TYPES
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


class _ContentProvider:
    """Fake LLM: one scripted content chunk per call; records full requests."""

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0
        self.requests: list[list[tuple[str, str]]] = []

    async def stream(self, request):
        self.requests.append([(m.role, m.content or "") for m in request.messages])
        text = self._contents[self.calls] if self.calls < len(self._contents) else "done"
        self.calls += 1
        yield LLMChunk(delta_content=text)


class _RecordingProvider:
    """Fake LLM that keeps full ``LLMMessage`` lists so reasoning strip can be asserted."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.requests: list[list[LLMMessage]] = []

    async def stream(self, request):  # noqa: ANN001
        self.requests.append(list(request.messages))
        chunks = (
            self._rounds[self.calls]
            if self.calls < len(self._rounds)
            else [LLMChunk(delta_content="done")]
        )
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _StubSearchTool:
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="search",
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        return ToolResult(tool_call_id="", success=True, output="ok")


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _executor(plan: RunPlan, provider: _ContentProvider, sink: EventSink):
    return build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=sink,
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )


async def _make_session(provider: _ContentProvider, *, run_id: str = "t_1") -> RunSession:
    """Run one worker through the executor and snapshot it as a RunSession."""
    from agentcore.runtime.runs import WaveScheduler

    plan, _ = build_run_plan(
        [{"role": "A", "task": "做A"}], id_prefix="t", parent_run_id="CEO"
    )
    res = await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    state = res[run_id]
    return RunSession(
        run_id=run_id,
        spec=plan.by_id(run_id),
        transcript=state.transcript,
        content=state.content,
    )


def _session_with_reasoning(*, run_id: str = "t_1") -> RunSession:
    """Hand-built session whose prior-beat assistants still carry reasoning drafts."""
    transcript = [
        LLMMessage(role="system", content="SYS"),
        LLMMessage(role="user", content="做A"),
        LLMMessage(
            role="assistant",
            content="",
            reasoning_content="历史 beat 工具链思考",
            tool_calls=[
                ToolCall(
                    id="tc_hist",
                    function=ToolCallFunction(name="search", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content="ok", tool_call_id="tc_hist"),
        LLMMessage(
            role="assistant",
            content="第一版产出",
            reasoning_content="历史 beat 终稿思考草稿",
        ),
    ]
    return RunSession(
        run_id=run_id,
        spec=RunSpec(run_id=run_id, agent_id=run_id, role="A", task="做A"),
        transcript=transcript,
        content="第一版产出",
    )


def test_session_store_put_get_and_miss():
    store = SessionStore()
    assert store.get("nope") is None
    assert "nope" not in store
    spec = RunSpec(run_id="r1", agent_id="r1", role="A", task="t")
    sess = RunSession(run_id="r1", spec=spec, transcript=[], content="x")
    store.put(sess)
    assert store.get("r1") is sess
    assert "r1" in store
    assert len(store) == 1


async def test_continue_run_revises_from_transcript_and_extends_it():
    provider = _ContentProvider(["第一版", "修订版"])
    session = await _make_session(provider)
    original_len = len(session.transcript)

    state = await continue_run(
        session=session,
        feedback="把语气改正式",
        continuation_run_id="t_1_rev1",
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        execution_id="e",
        approval_gate=None,
    )

    assert state.phase is RunPhase.COMPLETED
    assert state.content == "修订版"
    rev_request = provider.requests[-1]
    assert any(role == "assistant" and content == "第一版" for role, content in rev_request)
    assert any(role == "user" and "把语气改正式" in content for role, content in rev_request)
    assert len(state.transcript) > original_len
    assert state.transcript[-1].role == "assistant"
    assert state.transcript[-1].content == "修订版"


async def test_continue_run_strips_historical_reasoning_but_keeps_current_beat_echo():
    """跨 beat 续写：历史上行不含 reasoning；本 beat 工具链仍回传思考。"""
    session = _session_with_reasoning()
    hist_before = [
        (m.role, m.content, m.reasoning_content)
        for m in session.transcript
        if m.role == "assistant"
    ]
    assert any(r for _, _, r in hist_before)

    tools = ToolRegistry()
    tools.register(_StubSearchTool())
    provider = _RecordingProvider(
        [
            [
                LLMChunk(delta_reasoning="本 beat 工具思考"),
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="tc_now",
                            function_name="search",
                            arguments_delta="{}",
                        )
                    ]
                ),
            ],
            [LLMChunk(delta_content="续写终稿")],
        ]
    )

    state = await continue_run(
        session=session,
        feedback="下一 beat 继续",
        continuation_run_id="t_1_b2",
        llm=provider,
        tools=tools,
        sink=EventSink(),
        base_tool_context=_ctx(),
        execution_id="e",
        approval_gate=None,
    )

    assert state.phase is RunPhase.COMPLETED
    assert state.content == "续写终稿"
    assert len(provider.requests) == 2

    # First uplink: every prior-beat assistant has reasoning stripped.
    for m in provider.requests[0]:
        if m.role == "assistant":
            assert m.reasoning_content is None

    # Stored session unchanged until commit (strip copies, does not mutate).
    assert [
        (m.role, m.content, m.reasoning_content)
        for m in session.transcript
        if m.role == "assistant"
    ] == hist_before

    # Second uplink (same beat after tool): historical still stripped; this beat's
    # tool-call turn keeps its reasoning for DeepSeek echo.
    hist_contents = {"", "第一版产出"}
    for m in provider.requests[1]:
        if m.role != "assistant":
            continue
        if m.content in hist_contents or (
            m.tool_calls and m.tool_calls[0].id == "tc_hist"
        ):
            assert m.reasoning_content is None
        elif m.tool_calls and m.tool_calls[0].id == "tc_now":
            assert m.reasoning_content == "本 beat 工具思考"

    # Continuation transcript written for session commit has history stripped.
    hist_in_result = [
        m
        for m in state.transcript
        if m.role == "assistant"
        and (m.content in hist_contents or (m.tool_calls and m.tool_calls[0].id == "tc_hist"))
    ]
    assert hist_in_result
    assert all(m.reasoning_content is None for m in hist_in_result)


async def test_continue_run_does_not_mutate_stored_transcript_until_committed():
    provider = _ContentProvider(["第一版", "修订版"])
    session = await _make_session(provider)
    before = list(session.transcript)
    await continue_run(
        session=session,
        feedback="改",
        continuation_run_id="t_1_rev1",
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        execution_id="e",
        approval_gate=None,
    )
    assert session.transcript == before


async def test_continue_run_emits_continues_run_id_and_true_parent():
    provider = _ContentProvider(["第一版", "修订版"])
    session = await _make_session(provider)
    sink = EventSink()
    await continue_run(
        session=session,
        feedback="改",
        continuation_run_id="t_1_rev1",
        llm=provider,
        tools=ToolRegistry(),
        sink=sink,
        base_tool_context=_ctx(),
        execution_id="e",
        approval_gate=None,
        parent_run_id="CEO",
    )
    sink.close()
    events = [e async for e in sink]
    started = [e for e in events if e.type == EventType.RUN_STARTED]
    assert len(started) == 1
    assert started[0].payload["run_id"] == "t_1_rev1"
    assert started[0].payload["continues_run_id"] == "t_1"
    assert started[0].payload["parent_run_id"] == "CEO"
    assert "revision" not in started[0].payload
    completed = [e for e in events if e.type == EventType.RUN_COMPLETED]
    assert completed and completed[0].payload["run_id"] == "t_1_rev1"
    assert completed[0].payload["role"] == "member"


async def test_continue_run_failure_returns_failed_state():
    provider = _ContentProvider(["第一版"])
    session = await _make_session(provider)

    class _Boom:
        async def stream(self, request):
            raise RuntimeError("provider down")
            yield  # pragma: no cover - async generator

    sink = EventSink()
    state = await continue_run(
        session=session,
        feedback="改",
        continuation_run_id="t_1_rev1",
        llm=_Boom(),
        tools=ToolRegistry(),
        sink=sink,
        base_tool_context=_ctx(),
        execution_id="e",
        approval_gate=None,
    )
    assert state.phase is RunPhase.FAILED
    assert "provider down" in state.error
    sink.close()
    assert EventType.RUN_FAILED in [e.type async for e in sink]


async def test_continue_run_mid_cancel_emits_terminal_frame():
    """中途取消续写：``run_started`` 之后必有终态帧（CancelledError 不再穿过去留洞）。"""
    session = _session_with_reasoning()
    sink = EventSink()

    class _Hang:
        async def stream(self, request):  # noqa: ANN001, ARG002
            yield LLMChunk(delta_content="半成品")
            await asyncio.sleep(30)
            yield LLMChunk(delta_content="…")

    task = asyncio.create_task(
        continue_run(
            session=session,
            feedback="把语气改正式",
            continuation_run_id="t_1_rev1",
            llm=_Hang(),
            tools=ToolRegistry(),
            sink=sink,
            base_tool_context=_ctx(),
            execution_id="e",
            approval_gate=None,
        )
    )
    for _ in range(200):
        if any(
            e.type is EventType.RUN_STARTED and e.payload.get("run_id") == "t_1_rev1"
            for e in sink._history  # noqa: SLF001
        ):
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pytest.fail("continue_run 从未发出 run_started")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = list(sink._history)  # noqa: SLF001
    started = [
        e
        for e in events
        if e.type is EventType.RUN_STARTED and e.payload.get("run_id") == "t_1_rev1"
    ]
    assert started, "续写必须先发 run_started"
    terminals = [
        e
        for e in events
        if e.type in RUN_CLOSE_EVENT_TYPES and e.payload.get("run_id") == "t_1_rev1"
    ]
    assert len(terminals) == 1
    assert terminals[0].type is EventType.RUN_CANCELLED
    assert terminals[0].payload.get("reason") == "stop"
