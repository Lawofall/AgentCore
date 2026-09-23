"""Behavioral paths for ``react_loop`` + ``execute_tools`` (happy + key error paths).

Complements ``test_engine_governance`` / ``test_tool_exec`` with a small matrix of
public outcomes: content-only turn, tool→answer, unknown tool recovery, and
hard LLM failure after a tool round. No CEO toolset / assemble structure asserts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentcore.core.types import ToolEffect, ToolFace
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
)
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
            face=ToolFace.SEARCH,
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
    messages: list[LLMMessage] | None = None,
    deliverable_only: bool = False,
    on_reset=None,
):
    return await react_loop(
        messages=messages or [LLMMessage(role="user", content="go")],
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
        deliverable_only=deliverable_only,
        on_reset=on_reset,
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


async def test_captain_window_prompt_follows_each_round():
    """Composer occupancy is the latest CEO request, not the peak of the turn."""
    tool = _StubTool(name="search", output="found-it")
    provider = _ScriptedProvider(
        [
            [
                _tool_chunk("search", '{"q":"x"}'),
                LLMChunk(
                    usage=TokenUsage(
                        input_tokens=8_000,
                        output_tokens=10,
                        last_prompt_tokens=8_000,
                    ),
                    finish_reason="tool_calls",
                ),
            ],
            [
                _content_chunk("done"),
                LLMChunk(
                    usage=TokenUsage(
                        input_tokens=3_000,
                        output_tokens=4,
                        last_prompt_tokens=3_000,
                    ),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    sink = EventSink()
    content, _reasoning, usage, rounds = await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(tool),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=8),
        turn_model="primary",
        role="captain",
        run_id="run-1",
        approval_gate=None,
    )
    prompts = [
        e.payload["last_prompt_tokens"]
        for e in sink.history_snapshot()
        if e.type is EventType.WINDOW_PROMPT
    ]
    assert content == "done"
    assert rounds == 2
    assert prompts == [8_000, 3_000]
    assert usage.last_prompt_tokens == 3_000
    assert usage.input_tokens == 11_000
    journal = sink.execution_journal() or []
    assert EventType.WINDOW_PROMPT.value not in [e["type"] for e in journal]


async def test_worker_loop_does_not_emit_window_prompt():
    provider = _ScriptedProvider(
        [
            [
                _content_chunk("worker"),
                LLMChunk(
                    usage=TokenUsage(input_tokens=500, last_prompt_tokens=500),
                    finish_reason="stop",
                ),
            ]
        ]
    )
    sink = EventSink()
    await react_loop(
        messages=[LLMMessage(role="user", content="go")],
        llm=provider,
        tools=_registry(),
        sink=sink,
        tool_context=_context(),
        profile=make_profile_params(max_rounds=8),
        turn_model="primary",
        role="worker",
        run_id="run-w",
        approval_gate=None,
    )
    assert not any(e.type is EventType.WINDOW_PROMPT for e in sink.history_snapshot())


async def test_react_loop_replays_unmatched_trailing_tools_before_llm():
    from agentcore.tools.write_replay import is_write_replay

    class _ReplayAware(_StubTool):
        def __init__(self) -> None:
            super().__init__(name="search", output="replayed")
            self.replay_flags: list[bool] = []

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            self.replay_flags.append(is_write_replay())
            return await super().execute(arguments, context)

    tool = _ReplayAware()
    provider = _ScriptedProvider([[_content_chunk("基于补跑结果的答复")]])
    messages = [
        LLMMessage(role="user", content="go"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    function=ToolCallFunction(name="search", arguments="{}"),
                )
            ],
        ),
    ]
    content, _r, _u, rounds = await _run_loop(provider, _registry(tool), messages=messages)

    assert tool.calls == 1
    assert tool.replay_flags == [True]
    assert is_write_replay() is False
    assert provider.calls == 1
    assert content == "基于补跑结果的答复"
    assert rounds == 1
    assert any(m.role == "tool" and m.tool_call_id == "c1" for m in messages)
    asst_tools = [m for m in messages if m.role == "assistant" and m.tool_calls]
    assert len(asst_tools) == 1


async def test_react_loop_does_not_replay_when_continue_user_follows_tools():
    tool = _StubTool(name="search", output="should-not-run")
    provider = _ScriptedProvider([[_content_chunk("续干后的答复")]])
    messages = [
        LLMMessage(role="user", content="go"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    function=ToolCallFunction(name="search", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="user", content="## 续干指令\n继续"),
    ]
    content, _r, _u, rounds = await _run_loop(provider, _registry(tool), messages=messages)
    assert tool.calls == 0
    assert content == "续干后的答复"
    assert rounds == 1


async def test_react_loop_replay_resets_worker_narration():
    from agentcore.tools.write_replay import is_write_replay

    class _ReplayAware(_StubTool):
        def __init__(self) -> None:
            super().__init__(name="search", output="replayed")
            self.replay_flags: list[bool] = []

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            self.replay_flags.append(is_write_replay())
            return await super().execute(arguments, context)

    resets: list[str] = []
    tool = _ReplayAware()
    provider = _ScriptedProvider([[_content_chunk("成稿")]])
    messages = [
        LLMMessage(role="user", content="go"),
        LLMMessage(
            role="assistant",
            content="我先查一下资料。",
            tool_calls=[
                ToolCall(
                    id="c1",
                    function=ToolCallFunction(name="search", arguments="{}"),
                )
            ],
        ),
    ]
    content, _r, _u, rounds = await _run_loop(
        provider,
        _registry(tool),
        messages=messages,
        role="worker",
        deliverable_only=True,
        on_reset=resets.append,
    )
    assert tool.calls == 1
    assert tool.replay_flags == [True]
    assert content == "成稿"
    assert rounds == 1
    assert resets == ["narration"]
    assert "我先查一下" not in content


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
    """Hallucinated old names (file_write / file_read / str_replace) are unknown.

    Did-you-mean may suggest write / read / edit — message only, no auto-exec.
    """
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    pens = (
        ("file_write", "write"),
        ("file_read", "read"),
        ("str_replace", "edit"),
    )
    for old_name, live_name in pens:
        live_tool = _StubTool(live_name)
        reg = ToolRegistry()
        reg.register(live_tool)
        sink = EventSink()
        messages, terminal, attempts = await execute_tools(
            [ToolCall(id="c1", function=ToolCallFunction(name=old_name, arguments="{}"))],
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
        assert live_tool.calls == 0
        if "你是否想用" in content:
            assert live_name in content
            assert f"你是否想用：{old_name}" not in content


async def test_execute_tools_retired_md_export_names_suggest_no_exec():
    """废名 md_to_docx / md_to_pdf 只 did-you-mean，永不自动改写执行。"""
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    export_tool = _StubTool("md_export")
    reg = ToolRegistry()
    reg.register(export_tool)
    for retired in ("md_to_docx", "md_to_pdf"):
        sink = EventSink()
        messages, terminal, attempts = await execute_tools(
            [ToolCall(id="c1", function=ToolCallFunction(name=retired, arguments="{}"))],
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
        assert "你是否想用：md_export" in content
    assert export_tool.calls == 0


async def test_execute_tools_unknown_file_append_suggests_str_replace_no_exec():
    """废名 file_append 只 did-you-mean，永不自动改写执行。"""
    from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction

    replace_tool = _StubTool("edit")
    reg = ToolRegistry()
    reg.register(replace_tool)
    sink = EventSink()
    messages, terminal, attempts = await execute_tools(
        [ToolCall(id="c1", function=ToolCallFunction(name="file_append", arguments="{}"))],
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
    assert "你是否想用：edit" in content
    assert replace_tool.calls == 0


def test_registry_suggest_names_alias_and_close_match():
    reg = ToolRegistry()
    reg.register(_StubTool("web_search"))
    reg.register(_StubTool("web_fetch"))
    reg.register(_StubTool("run"))
    reg.register(_StubTool("write"))
    reg.register(_StubTool("read"))
    reg.register(_StubTool("edit"))
    reg.register(_StubTool("file_list"))
    reg.register(_StubTool("glob"))

    reg.register(_StubTool("md_export"))

    assert reg.suggest_names("fetch") == ["run"]
    assert reg.suggest_names("wget") == ["run"]
    assert reg.suggest_names("curl") == ["run"]
    assert reg.suggest_names("file_append") == ["edit"]
    assert reg.suggest_names("md_to_docx") == ["md_export"]
    assert reg.suggest_names("md_to_pdf") == ["md_export"]
    assert reg.suggest_names("ls") == ["file_list"]
    assert reg.suggest_names("list_dir") == ["file_list"]
    assert reg.suggest_names("find") == ["glob"]
    assert reg.suggest_names("glob_file_search") == ["glob"]
    assert "web_search" in reg.suggest_names("web_serch")  # typo → close match
    assert reg.suggest_names("totally_unknown_zzzz") == []
    # Old product names are not aliases and are not registered.
    from agentcore.tools.registry import _KNOWN_TOOL_ALIASES

    assert "file_write" not in _KNOWN_TOOL_ALIASES
    assert "file_read" not in _KNOWN_TOOL_ALIASES
    assert "str_replace" not in _KNOWN_TOOL_ALIASES
    assert "file_write" not in reg.names
    assert "file_read" not in reg.names
    assert "str_replace" not in reg.names
    assert "write" in reg.suggest_names("file_write")
    assert "read" in reg.suggest_names("file_read")


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
