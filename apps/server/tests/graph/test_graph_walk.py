"""一场对话上图怎么走：几条短检查，一条只钉一件看得见的事。"""

from __future__ import annotations

import json

from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs.stop_queue import enqueue_stop, take_stops
from agentcore.tools.builtin.graph.tool import GraphTool
from agentcore.tools.registry import ToolRegistry
from tests.delegate.conftest import NestingProvider, Provider, ctx, nesting_tool, tool


async def test_two_segments_with_no_order_both_come_back():
    """没有先后的两段一起做完，两句结论都回到这扇窗。"""
    t = tool(Provider(["成本已经查清", "基准已经对齐"]))
    result = await t.execute(
        {
            "nodes": [
                {"id": "查成本", "goal": "查近 7 日成本"},
                {"id": "对基准", "goal": "对齐历史基准"},
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert "成本已经查清" in result.output
    assert "基准已经对齐" in result.output


async def test_next_segment_waits_and_sees_only_the_conclusion():
    """下一段等上一段封口，窗口里看见的是那句结论。"""
    provider = Provider(["成本已经查清", "摘要写好了"])
    t = tool(provider)
    result = await t.execute(
        {
            "nodes": [
                {"id": "查成本", "goal": "查近 7 日成本"},
                {"id": "写摘要", "goal": "根据结论写摘要", "depends_on": ["查成本"]},
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert "摘要写好了" in result.output
    users = [
        next((m.content or "" for m in request.messages if m.role == "user"), "")
        for request in provider.requests
    ]
    assert len(users) == 2
    assert "成本已经查清" not in users[0]
    assert "成本已经查清" in users[1]


async def test_one_more_layer_stops_at_the_depth_cap():
    """一段还可以再开下一层。开到上限，叶子不再往下拆。"""

    class DeepProvider(NestingProvider):
        async def stream(self, request):
            system = next((m.content or "" for m in request.messages if m.role == "system"), "")
            user = next((m.content or "" for m in request.messages if m.role == "user"), "")
            is_captain = self.CAPTAIN_MARK in user or self.CAPTAIN_MARK in system
            has_result = any(m.role == "tool" for m in request.messages)
            if is_captain and not has_result:
                self.delegate_calls += 1
                args = json.dumps(
                    {
                        "conclusion": "这一层先封口。",
                        "nodes": [{"id": "下一段", "goal": "接着做"}],
                    }
                )
                yield LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="sub-tc",
                            function_name="graph",
                            arguments_delta=args,
                        )
                    ]
                )
            elif is_captain:
                yield LLMChunk(delta_content="这一层做完了")
            else:
                yield LLMChunk(delta_content="叶子做完了")

    provider = DeepProvider()
    t = nesting_tool(provider, EventSink())
    result = await t.execute(
        {
            "conclusion": "先拆开。",
            "nodes": [{"id": "先拆", "goal": "再开下一层"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert provider.delegate_calls == 2


async def test_one_layer_rejects_opening_too_many_at_once():
    """再开下一层时，一次开得太多会被拦住。"""
    t = GraphTool(
        llm=Provider([]),
        sink=EventSink(),
        system_prompt="SYS",
        user_message="原始请求",
        history=[],
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        captain_run_id="cap_1",
        depth=1,
        folder_id="test_birth",
        approval_gate=None,
    )
    result = await t.execute(
        {
            "conclusion": "再拆。",
            "nodes": [{"id": f"段{i}", "goal": f"做{i}"} for i in range(5)],
        },
        ctx(),
    )
    assert result.success is False
    assert "扇出已达上限" in (result.error or "")


class _StopOnStartSink(EventSink):
    def __init__(self) -> None:
        super().__init__()
        self._sent = False
        self.cancelled_reasons: list[str] = []

    def emit(self, event) -> None:  # noqa: ANN001
        if event.type is EventType.RUN_CANCELLED:
            self.cancelled_reasons.append(str(event.payload.get("reason") or ""))
        if not self._sent and event.type is EventType.RUN_STARTED:
            enqueue_stop(execution_id="e", run_id=None, conversation_id="c")
            self._sent = True
        super().emit(event)


async def test_stopping_the_turn_cancels_segments_still_running():
    """停下这一轮，还在进行的段一起停，不会自己再开一段。"""

    class _Slow:
        async def stream(self, request):  # noqa: ANN001
            import asyncio

            await asyncio.sleep(0.3)
            yield LLMChunk(delta_content="还在写")

    take_stops("e")
    sink = _StopOnStartSink()
    t = tool(_Slow(), sink)
    result = await t.execute(
        {
            "nodes": [
                {"id": "查成本", "goal": "查近 7 日成本"},
                {"id": "对基准", "goal": "对齐历史基准"},
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success is True
    assert "user_stop" in sink.cancelled_reasons
    followups = [
        event
        for event in sink._history
        if event.type is EventType.RUN_STARTED
        and (
            str(event.payload.get("run_id") or "").endswith("_redir")
            or event.payload.get("replaces_run_id")
            or event.payload.get("continues_run_id")
        )
    ]
    assert followups == []
