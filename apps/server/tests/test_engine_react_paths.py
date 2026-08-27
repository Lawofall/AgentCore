"""Behavioral paths for ``react_loop`` + ``execute_tools`` (happy + key error paths).

Complements ``test_engine_governance`` / ``test_tool_exec`` with a small matrix of
public outcomes: content-only turn, tool→answer, unknown tool recovery, and
hard LLM failure after a tool round. No CEO toolset / assemble structure asserts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _tool_chunk(name: str, args: str = "{}", *, call_id: str = "c1") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _FailingProvider:
    def __init__(self, rounds: list[list[LLMChunk]], *, fail_on: set[int]) -> None:
        self._rounds = rounds
        self._fail_on = fail_on
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        idx = self.calls
        self.calls += 1
        if idx in self._fail_on:
            raise RuntimeError("provider boom")
        chunks = self._rounds[idx] if idx < len(self._rounds) else []
        for chunk in chunks:
            yield chunk


class _StubTool:
    def __init__(
        self,
        name: str = "search",
        *,
        output: str = "ok",
        success: bool = True,
        terminal: bool = False,
    ) -> None:
        self._name = name
        self._output = output
        self._success = success
        self._terminal = terminal
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        if not self._success:
            return ToolResult(tool_call_id="", success=False, output="", error="boom")
        return ToolResult(
            tool_call_id="",
            success=True,
            output=self._output,
            effect=ToolEffect.HANDOFF if self._terminal else ToolEffect.CONTINUE,
            final_text=self._output if self._terminal else None,
        )


def _registry(*tools: _StubTool) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool)
    return reg


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def _run_loop(
    provider,
    tools: ToolRegistry,
    *,
    finish_override_sink: list[FinishReason] | None = None,
    role: str = "",
):
    return await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=tools,
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=8),
        turn_model="primary",
        out=(
            None
            if finish_override_sink is None
            else ReactLoopOut(finish_override=finish_override_sink)
        ),
        role=role,
        run_id="run-1",
        approval_gate=None,
    )


async def test_react_loop_content_only_returns_answer():
    provider = _ScriptedProvider([[_content_chunk("你好世界")]])
    content, _reasoning, usage, rounds = await _run_loop(provider, _registry())

    assert content == "你好世界"
    assert rounds == 1
    assert provider.calls == 1
    assert usage.input_tokens >= 0


async def test_react_loop_sanitizes_protocol_markers_in_final_content():
    """统一出口清洗：正文里的供应商协议标记（<longcat_tool_call> 等）在返回值中已被剥离。"""
    provider = _ScriptedProvider(
        [[_content_chunk("结论如下<longcat_tool_call>勿泄漏</longcat_tool_call>完。")]]
    )
    content, _reasoning, _usage, rounds = await _run_loop(provider, _registry())

    assert "<longcat" not in content
    assert "longcat_tool_call" not in content
    assert "结论如下" in content
    assert "完。" in content
    assert rounds == 1


async def test_react_loop_tool_then_answer():
    tool = _StubTool(name="search", output="found-it")
    provider = _ScriptedProvider(
        [
            [_tool_chunk("search", '{"q":"x"}')],
            [_content_chunk("基于工具结果的答复")],
        ]
    )
    content, _r, _u, rounds = await _run_loop(provider, _registry(tool))

    assert tool.calls == 1
    assert content == "基于工具结果的答复"
    assert rounds == 2


async def test_react_loop_unknown_tool_recovers_with_final_answer():
    """Unknown tool name must not kill the turn — model gets an error message and continues."""
    provider = _ScriptedProvider(
        [
            [_tool_chunk("no_such_tool", "{}")],
            [_content_chunk("换一种方式作答")],
        ]
    )
    content, _r, _u, rounds = await _run_loop(provider, _registry(_StubTool("search")))

    assert content == "换一种方式作答"
    assert rounds == 2


async def test_react_loop_hard_llm_failure_after_tool_keeps_partial():
    tool = _StubTool(name="search", output="partial-ctx")
    provider = _FailingProvider(
        [[_content_chunk("前半段"), _tool_chunk("search", "{}")], []],
        fail_on={1},
    )
    finish: list[FinishReason] = []
    content, _r, _u, rounds = await _run_loop(
        provider, _registry(tool), finish_override_sink=finish
    )

    assert tool.calls == 1
    assert content == "前半段"
    assert rounds == 2
    assert finish == [FinishReason.DEGRADED]


async def test_execute_tools_unknown_tool_returns_error_message():
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    reg = _registry(_StubTool("search"))
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [ToolCall(id="c1", function=ToolCallFunction(name="ghost", arguments="{}"))],
        reg,
        _context(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert terminal is None
    assert len(messages) == 1
    assert "not found" in (messages[0].content or "")
    assert attempts[0].success is False
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1
    assert ends[0].payload["status"] == "error"


async def test_execute_tools_unknown_tool_suggests_alias():
    """Hallucinated names (web_read / write) get did-you-mean — message only, no auto-exec."""
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    read_tool = _StubTool("read_url")
    write_tool = _StubTool("file_write")
    reg = ToolRegistry()
    reg.register(read_tool)
    reg.register(write_tool)
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [ToolCall(id="c1", function=ToolCallFunction(name="web_read", arguments="{}"))],
        reg,
        _context(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert terminal is None
    assert attempts[0].success is False
    content = messages[0].content or ""
    assert "not found" in content
    assert "你是否想用：read_url" in content
    # Alias suggestion must not silently execute the target tool.
    assert read_tool.calls == 0
    assert write_tool.calls == 0


async def test_execute_tools_wait_not_found_no_fuzzy_to_unrelated():
    """协调闸 wait 未装配：诚实文案，勿 fuzzy 成 git 等无关工具。"""
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    git_tool = _StubTool("git")
    reg = ToolRegistry()
    reg.register(git_tool)
    # "wait" is close to nothing useful; without the gate, difflib may still
    # suggest unrelated short names depending on registry contents.
    reg.register(_StubTool("web_search"))
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [ToolCall(id="c1", function=ToolCallFunction(name="wait", arguments="{}"))],
        reg,
        _context(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert terminal is None
    assert attempts[0].success is False
    content = messages[0].content or ""
    assert "wait" in content
    assert "未装配" in content
    assert "你是否想用" not in content
    assert "git" not in content.lower()
    assert git_tool.calls == 0


def test_registry_suggest_names_alias_and_close_match():
    reg = ToolRegistry()
    reg.register(_StubTool("web_search"))
    reg.register(_StubTool("read_url"))
    reg.register(_StubTool("download_url"))
    reg.register(_StubTool("file_write"))
    reg.register(_StubTool("file_list"))
    reg.register(_StubTool("glob"))

    assert reg.suggest_names("web_read") == ["read_url"]
    assert reg.suggest_names("fetch") == ["download_url"]
    assert reg.suggest_names("fetch_url") == ["download_url"]
    assert reg.suggest_names("web_fetch") == ["download_url"]
    assert reg.suggest_names("write") == ["file_write"]
    assert reg.suggest_names("ls") == ["file_list"]
    assert reg.suggest_names("list_dir") == ["file_list"]
    assert reg.suggest_names("find") == ["glob"]
    assert reg.suggest_names("glob_file_search") == ["glob"]
    assert "web_search" in reg.suggest_names("web_serch")  # typo → close match
    assert reg.suggest_names("totally_unknown_zzzz") == []


async def test_execute_tools_happy_path_emits_start_and_end():
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    tool = _StubTool("search", output="alpha")
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [ToolCall(id="c1", function=ToolCallFunction(name="search", arguments="{}"))],
        _registry(tool),
        _context(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert terminal is None
    assert tool.calls == 1
    assert messages[0].content == "alpha"
    assert attempts[0].success is True
    types = [e.type for e in sink._history]  # noqa: SLF001
    assert EventType.TOOL_USE_START in types
    assert EventType.TOOL_USE_END in types


async def test_react_loop_handoff_terminal_returns_tool_final_text():
    tool = _StubTool(name="handoff_like", output="交付正文", terminal=True)
    provider = _ScriptedProvider([[_tool_chunk("handoff_like", "{}")]])
    content, _r, _u, rounds = await _run_loop(provider, _registry(tool))

    assert tool.calls == 1
    assert content == "交付正文"
    assert rounds == 1
