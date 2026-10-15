"""graph called while a thought is open must attach that thought, not crash."""

from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.graph.shape.conversation_graph import fold_conversation_graph
from agentcore.runtime.graph.shape.opening_segment import OPENING_CONCLUSION_REQUIRED
from agentcore.runtime.graph.shape.step_segment import (
    bind_step_chain,
    current_step_chain,
    open_think,
    reset_step_chain,
)
from agentcore.tools.protocol import ToolResult
from tests.graph.conftest import tool


class _LLM:
    async def stream(self, request):  # noqa: ANN001
        if False:
            yield None


def _journal(events):
    return [
        {"kind": event.type.value, "payload": event.payload}
        for event in events
        if event is not None
    ]


async def _drive_stopped(monkeypatch):
    captured: dict = {}

    async def fake_drive(_tool, plan, **kwargs):
        captured["plan"] = plan
        captured["seed"] = kwargs.get("seed_completed")
        return ToolResult(tool_call_id="", success=True, output="已挂上")

    monkeypatch.setattr("agentcore.tools.builtin.graph.tool.drive", fake_drive)
    return captured


async def test_open_think_graph_attaches_the_thought(monkeypatch):
    captured = await _drive_stopped(monkeypatch)
    sink = EventSink()
    graph = tool(_LLM(), sink)
    graph._captain_run_id = "cap"
    token = bind_step_chain("e", ["seg_prev"], goal="测试下graph功能")
    try:
        chain = current_step_chain()
        assert chain is not None
        opened = open_think(chain, run_id="cap", round_idx=0)
        result = await graph.execute(
            {
                "conclusion": "先看通路。",
                "nodes": [{"id": "a", "goal": "回一句"}],
                "coordinate": False,
            },
            graph._base_tool_context,
        )
    finally:
        reset_step_chain(token)

    assert result.success is True
    assert result.error in (None, "")
    plan = captured["plan"]
    think_id = opened[0].payload["runs"][0]["id"]
    assert think_id in {node.run_id for node in plan.nodes}
    workers = [node for node in plan.nodes if node.run_id != think_id]
    assert workers and workers[0].depends_on == [think_id]
    assert captured["seed"][think_id].content == "先看通路。"

    framed = [
        event
        for event in sink.history_snapshot()
        if event.type in (EventType.RUN_PLAN, EventType.RUN_COMPLETED)
    ]
    graph_view = fold_conversation_graph([_journal([*opened, *framed])])
    by_id = {segment.id: segment for segment in graph_view.segments}
    assert by_id[think_id].depends_on == ("seg_prev",)
    assert think_id not in {
        run["id"]
        for event in framed
        if event.type is EventType.RUN_PLAN
        for run in event.payload["runs"]
    }


async def test_open_think_without_conclusion_is_a_contract_miss(monkeypatch):
    captured = await _drive_stopped(monkeypatch)
    graph = tool(_LLM(), EventSink())
    graph._captain_run_id = "cap"
    token = bind_step_chain("e", [], goal="测试")
    try:
        chain = current_step_chain()
        assert chain is not None
        open_think(chain, run_id="cap", round_idx=0)
        result = await graph.execute(
            {"nodes": [{"id": "a", "goal": "回一句"}]},
            graph._base_tool_context,
        )
    finally:
        reset_step_chain(token)

    assert result.success is False
    assert result.contract_failure is True
    assert result.error == OPENING_CONCLUSION_REQUIRED
    assert captured == {}


async def test_captain_without_a_thought_still_seals_the_opening(monkeypatch):
    captured = await _drive_stopped(monkeypatch)
    sink = EventSink()
    graph = tool(_LLM(), sink)
    graph._captain_run_id = "cap"
    result = await graph.execute(
        {
            "conclusion": "先看通路。",
            "nodes": [{"id": "a", "goal": "回一句"}],
            "coordinate": False,
        },
        graph._base_tool_context,
    )
    assert result.success is True
    assert "seg_cap" in {node.run_id for node in captured["plan"].nodes}
    assert captured["seed"]["seg_cap"].content == "先看通路。"
    declared = [
        run["id"]
        for event in sink.history_snapshot()
        if event.type is EventType.RUN_PLAN
        for run in event.payload["runs"]
    ]
    assert "seg_cap" in declared


async def test_empty_words_do_not_read_a_missing_chain_field():
    sink = EventSink()
    graph = tool(_LLM(), sink)
    graph._captain_run_id = "cap"
    token = bind_step_chain("e", [], goal="测试")
    try:
        await graph.seal_opening_at_turn_end("")
    finally:
        reset_step_chain(token)
    kinds = [event.type for event in sink.history_snapshot()]
    assert EventType.RUN_COMPLETED in kinds
