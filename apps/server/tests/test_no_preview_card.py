"""组队 / 开辩不再挂编制确认卡。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agentcore.core.types import ToolEffect, WorkspaceBoundary

_AUTO_AXES = WorkspaceBoundary.FOLDER
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import TurnFactLog, current_fact_log
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.debate import DebateTool
from agentcore.tools.registry import ToolRegistry
from tests.delegate.conftest import Provider, ctx, tool_durable


async def _drain_coord(execution_id: str = "e") -> None:
    from agentcore.runtime.graph.session.session import (
        active_coordination,
        clear_active_coordination,
    )

    session = active_coordination(execution_id)
    if session is not None and session.drive_task is not None:
        await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination()


async def _fake_debate_run(_config, _usage_metadata):
    return SimpleNamespace(
        tool_call_id="",
        success=True,
        output="ok",
        effect=ToolEffect.CONTINUE,
        metadata={},
    )


async def test_confirmed_ask_does_not_skip_debate_team_preview():
    """同回合 ask continue 后顶层 debate 也不再挂编制确认卡。"""
    registry = InteractionRegistry()
    sink = EventSink()
    sink.seed_journal(
        [
            {
                "type": EventType.CHECKPOINT_REQUIRED.value,
                "payload": {"checkpoint_id": "ask1"},
                "timestamp": "t0",
            },
            {
                "type": EventType.CHECKPOINT_RESOLVED.value,
                "payload": {"checkpoint_id": "ask1", "decision": "continue"},
                "timestamp": "t1",
            },
        ]
    )
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    tool = _debate_tool(sink, registry, _save, _drop)
    tool._run_moderator = _fake_debate_run  # type: ignore[method-assign]
    transcript = [
        LLMMessage(role="user", content="辩一下"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_debate",
                    function=ToolCallFunction(name="debate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog()
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await tool.execute(
            {
                "motion": "该不该上四天工作制？",
                "form": "debate",
                "sides": [
                    {"key": "pro", "name": "正方", "stance": "应推广"},
                    {"key": "con", "name": "反方", "stance": "暂缓"},
                ],
            },
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)


def _debate_tool(
    sink: EventSink,
    registry: InteractionRegistry,
    save,
    drop,
    *,
    permission_axes=None,
) -> DebateTool:
    if permission_axes is None:
        permission_axes = _AUTO_AXES
    return DebateTool(
        llm=Provider([]),
        sink=sink,
        system_prompt="sys",
        user_message="辩一下",
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        conversation_id="c",
        ambient_armed=True,
        message_id="m1",
        suspension_saver=save,
        suspension_deleter=drop,
        permission_axes=permission_axes,
        registry=registry,
        captain_run_id="ceo",
        approval_gate=None,
    )


async def test_debate_top_level_does_not_hang_preview():
    """顶层 debate 不再 await team_preview；直接开跑。"""
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    tool = _debate_tool(sink, registry, _save, _drop)
    tool._run_moderator = _fake_debate_run  # type: ignore[method-assign]
    transcript = [
        LLMMessage(role="user", content="辩一下"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_debate",
                    function=ToolCallFunction(name="debate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog()
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await tool.execute(
            {
                "motion": "该不该上四天工作制？",
                "form": "debate",
                "sides": [
                    {"key": "pro", "name": "正方", "stance": "应推广"},
                    {"key": "con", "name": "反方", "stance": "暂缓"},
                ],
            },
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)


async def test_debate_full_auto_does_not_hang_preview():
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    tool = _debate_tool(
        sink, registry, _save, _drop, permission_axes=WorkspaceBoundary.FOLDER
    )
    # full_auto must not suspend before moderator.
    # Without LLM we can't finish moderator; patch _run_moderator.
    async def _fake_run(config, usage_metadata):
        return SimpleNamespace(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
            metadata={},
        )

    tool._run_moderator = _fake_run  # type: ignore[method-assign]
    result = await tool.execute(
        {
            "motion": "命题",
            "form": "debate",
            "sides": [
                {"key": "pro", "name": "正方", "stance": "a"},
                {"key": "con", "name": "反方", "stance": "b"},
            ],
        },
        ctx(),
    )
    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
