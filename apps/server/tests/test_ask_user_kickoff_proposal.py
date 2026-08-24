"""ask_user：纯 message 短问可过（提案体硬闸已拆除）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.suspension import AskUserSuspension, captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c-ask-clarify",
    )


def _tool(*, message_id: str | None = "m1") -> AskUserTool:
    async def _save(_frame: AskUserSuspension) -> None:
        return None

    async def _drop(_mid: str) -> None:
        return None

    return AskUserTool(
        sink=EventSink(),
        conversation_id="c-ask-clarify",
        timeout_seconds=1.0,
        message_id=message_id,
        suspension_saver=_save if message_id else None,
        suspension_deleter=_drop if message_id else None,
        captain_run_id="cap",
        base_system_prompt="sys",
        user_message="写调研报告",
    )


def _assistant_tool(name: str, args: dict, *, call_id: str) -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                function=ToolCallFunction(name=name, arguments=json.dumps(args)),
            )
        ],
    )


@pytest.mark.asyncio
async def test_message_only_ask_suspends():
    """纯 message 短问可过（非专用 card）。"""
    tool = _tool()
    token = captain_transcript.set([LLMMessage(role="user", content="写一份竞品调研报告")])
    try:
        res = await tool.execute({"message": "你是想要简报还是完整报告？"}, _ctx())
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND
    assert any(e.type is EventType.CHECKPOINT_REQUIRED for e in tool.sink._history)
    cp = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert cp.payload["intent"] == "decision"


@pytest.mark.asyncio
async def test_empty_assumptions_and_questions_still_ok():
    tool = _tool()
    token = captain_transcript.set([LLMMessage(role="user", content="写调研报告")])
    try:
        res = await tool.execute(
            {"message": "复述目标确认一下？", "assumptions": [], "questions": []},
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND


@pytest.mark.asyncio
async def test_ask_with_assumptions_still_suspends():
    tool = _tool()
    token = captain_transcript.set([LLMMessage(role="user", content="写一份竞品调研报告")])
    try:
        res = await tool.execute(
            {
                "message": "按这份起步计划开做",
                "assumptions": [{"label": "范围", "value": "国内三家"}],
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND


@pytest.mark.asyncio
async def test_after_delegate_message_only_is_decision():
    """途中短问：仅 message 仍可挂起，intent=decision。"""
    tool = _tool()
    transcript = [
        LLMMessage(role="user", content="写调研报告"),
        _assistant_tool("delegate", {"tasks": []}, call_id="d1"),
        LLMMessage(role="tool", content="ok", tool_call_id="d1"),
        _assistant_tool("ask_user", {"message": "终稿交哪？"}, call_id="a1"),
    ]
    token = captain_transcript.set(transcript)
    try:
        res = await tool.execute({"message": "终稿交哪？"}, _ctx())
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND
    cp = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert cp.payload["intent"] == "decision"


@pytest.mark.asyncio
async def test_team_preview_resolved_does_not_block_ask():
    """team_preview 拍板后仍可短问（勿再开开工提案拒调已拆除）。"""
    sink = EventSink()
    sink.seed_journal(
        [
            {
                "type": "team_preview_required",
                "payload": {"checkpoint_id": "tp1"},
                "timestamp": "t0",
            },
            {
                "type": "team_preview_resolved",
                "payload": {"checkpoint_id": "tp1", "decision": "continue"},
                "timestamp": "t1",
            },
        ]
    )

    async def _save(_frame: AskUserSuspension) -> None:
        return None

    async def _drop(_mid: str) -> None:
        return None

    tool = AskUserTool(
        sink=sink,
        conversation_id="c-ask-clarify",
        timeout_seconds=1.0,
        message_id="m1",
        suspension_saver=_save,
        suspension_deleter=_drop,
        captain_run_id="cap",
        base_system_prompt="sys",
        user_message="继续",
    )
    token = captain_transcript.set([LLMMessage(role="user", content="继续")])
    try:
        res = await tool.execute({"message": "交付形态再确认一下？"}, _ctx())
    finally:
        captain_transcript.reset(token)
    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND
    assert "勿再开开工提案卡" not in (res.error or "")
