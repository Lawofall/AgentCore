"""Regression: CEO team_gate retired — no tool strip / no post-gate content_reset."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import react_loop
from agentcore.runtime.events import EventSink
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
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


class _StubTool:
    def __init__(self, name: str = "search") -> None:
        self._name = name
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=ToolCategory.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
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


@pytest.mark.asyncio
async def test_many_investigation_rounds_do_not_strip_tools_or_gate():
    """Many recon rounds: tools stay available; no hard-stop nudge."""
    search = _StubTool(name="search")
    n = 8
    provider = _ScriptedProvider(
        [[_tool_chunk("search", f'{{"q": "{i}"}}')] for i in range(1, n + 1)]
        + [[_content_chunk("ok")]]
    )
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(search),
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=n + 3),
        turn_model="m",
        role="captain",
        approval_gate=None,
    )
    assert content == "ok"
    assert search.calls == n
    assert not any(
        m.role == "user" and m.content and "探路已达硬上限" in m.content for m in messages
    )


@pytest.mark.asyncio
async def test_long_answer_after_investigation_not_discarded():
    long = "甲" * 500
    search = _StubTool(name="search")
    provider = _ScriptedProvider(
        [[_tool_chunk("search", f'{{"q": "{i}"}}', call_id=f"c{i}")] for i in range(7)]
        + [[_content_chunk(long)]]
    )
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=_registry(search),
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=12),
        turn_model="m",
        role="captain",
        approval_gate=None,
    )
    assert content == long
    assert not any(
        m.role == "user" and m.content and "草稿已丢弃" in m.content for m in messages
    )
