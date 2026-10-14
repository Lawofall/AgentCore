"""协作图身份：prev 链 / 同回合合入 / 宿主解析（divert 已退役）。"""

from __future__ import annotations

from typing import Any

import pytest

from agentcore.runtime.graph.shape.graph_append import (
    clear_graph_host_registry,
    parse_host_captain_run_id,
    peek_graph_host,
    register_graph_host,
)
from agentcore.runtime.events import EventSink, run_plan
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from tests.delegate.conftest import Provider, ctx, tool


@pytest.fixture(autouse=True)
def _clean_graph_host_registry():
    clear_graph_host_registry()
    yield
    clear_graph_host_registry()


def test_register_graph_host_first_wins():
    register_graph_host("exec-a", "m1")
    register_graph_host("exec-a", "m2")
    assert peek_graph_host("exec-a") == "m1"


def test_parse_host_captain_run_id_prefers_original_host_frame():
    entries = [
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "runs": [
                    {"id": "host-cap", "kind": "captain", "agent_id": "host-cap"},
                    {"id": "r1", "agent_id": "w1", "task": "x", "depends_on": []},
                ],
            },
        },
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "host_message_id": "m-host",
                "runs": [
                    {"id": "turn-cap", "kind": "captain", "agent_id": "turn-cap"},
                    {"id": "r2", "agent_id": "w2", "task": "y", "depends_on": []},
                ],
            },
        },
    ]
    assert parse_host_captain_run_id(entries) == "host-cap"


def test_parse_host_captain_run_id_fallback_append_frame_only():
    entries = [
        {
            "type": "run_plan",
            "payload": {
                "host_message_id": "m-host",
                "runs": [{"id": "legacy-cap", "kind": "captain"}],
            },
        }
    ]
    assert parse_host_captain_run_id(entries) == "legacy-cap"


def test_parse_host_captain_run_id_empty():
    assert parse_host_captain_run_id(None) is None
    assert parse_host_captain_run_id([]) is None
    assert parse_host_captain_run_id([{"kind": "run_plan", "payload": {"runs": []}}]) is None


def test_sink_registers_host_and_team_marker():
    sink = EventSink(message_id="m1", conversation_id="c")
    sink.emit(
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="t",
            agents=[{"id": "w1", "role": "研", "thinking": True}],
            runs=[{"id": "r1", "agent_id": "w1", "task": "x", "depends_on": []}],
        )
    )
    assert peek_graph_host("exec1") == "m1"
    process = sink.process_timeline() or []
    assert any(s.get("kind") == "team" and s.get("execution_id") == "exec1" for s in process)


def test_sink_prev_run_plan_inserts_team_on_new_execution():
    """跨回合新图：无 host_message_id，新 eid 插 team；不再发 graph_append。"""
    sink2 = EventSink(message_id="m2", conversation_id="c")
    sink2.emit(
        run_plan(
            execution_id="exec2",
            plan_type="multi_agent",
            task_summary="t2",
            agents=[{"id": "w3", "role": "写", "thinking": True}],
            runs=[{"id": "r3", "agent_id": "w3", "task": "y", "depends_on": []}],
            prev_execution_id="exec1",
        )
    )
    process = sink2.process_timeline() or []
    assert not any(s.get("kind") == "graph_append" for s in process)
    assert any(s.get("kind") == "team" and s.get("execution_id") == "exec2" for s in process)
    assert peek_graph_host("exec2") == "m2"


def test_host_message_id_does_not_suppress_the_team_marker():
    """旧字段不再把生长写回上一轮：本回合照样记一张图。"""
    sink = EventSink(message_id="m2", conversation_id="c")
    sink.emit(
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="t",
            agents=[{"id": "w1", "role": "研", "thinking": True}],
            runs=[{"id": "r1", "agent_id": "w1", "task": "x", "depends_on": []}],
            host_message_id="m1",
        )
    )
    process = sink.process_timeline() or []
    assert any(s.get("kind") == "team" and s.get("execution_id") == "exec1" for s in process)


@pytest.mark.asyncio
async def test_delegate_closed_graph_without_continuation_is_fresh(monkeypatch):
    """已收口图、无续派/补缺口：新 eid、不写 prev。"""
    t = tool(Provider([]))
    t._base_tool_context.execution_id = None
    t._message_id = "m2"
    t._conversation_id = "conv-1"
    t._captain_run_id = "cap-2"
    emitted: list[Any] = []
    t._sink.emit = lambda ev: emitted.append(ev)  # type: ignore[method-assign]
    monkeypatch.setattr("agentcore.runtime.plan_only.is_plan_only", lambda: True)

    result = await t.execute(
        {
            "conclusion": "这段先到这里。",
            "nodes": [{"id": "write", "goal": "写"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success
    plans = [e for e in emitted if getattr(e.type, "value", e.type) == "run_plan"]
    assert len(plans) == 1
    payload = plans[0].payload
    assert "prev_execution_id" not in payload
    assert payload.get("execution_id")
    assert not any(
        getattr(e.type, "value", e.type) == "graph_append" for e in emitted
    )


@pytest.mark.asyncio
async def test_delegate_same_turn_memory_append_keeps_eid(monkeypatch):
    """同回合二次：合入同一 execution_id，不写 prev。"""
    t = tool(Provider([]))
    t._base_tool_context.execution_id = "exec-live"
    t._message_id = "m1"
    t._conversation_id = "conv-1"
    t._captain_run_id = "cap-1"
    t._calls = 1
    t._last_graph_execution_id = "exec-live"
    t._last_graph_plan = RunPlan(
        nodes=[RunSpec(run_id="r1", task="调研", role="研究员", agent_id="w1")]
    )
    t._last_graph_seed = {
        "r1": RunState(phase=RunPhase.COMPLETED, content=""),
    }
    emitted: list[Any] = []
    t._sink.emit = lambda ev: emitted.append(ev)  # type: ignore[method-assign]

    monkeypatch.setattr("agentcore.runtime.plan_only.is_plan_only", lambda: True)

    result = await t.execute(
        {
            "conclusion": "这段先到这里。",
            "nodes": [{"id": "write", "goal": "写"}],
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success
    plans = [e for e in emitted if getattr(e.type, "value", e.type) == "run_plan"]
    if plans:
        assert plans[0].payload.get("execution_id") == "exec-live"
        assert "prev_execution_id" not in plans[0].payload
    assert not any(
        getattr(e.type, "value", e.type) == "graph_append" for e in emitted
    )


@pytest.mark.asyncio
async def test_delegate_new_turn_live_graph_stays_on_this_turn(monkeypatch):
    """上一张仍在跑：新节点记在本回合，不链回旧图，也不并进旧图。"""
    from agentcore.runtime.graph.session.session import (
        CoordinationSession,
        clear_active_coordination,
        current_execution_id,
        set_active_coordination,
    )

    clear_active_coordination()
    live = CoordinationSession(
        execution_id="exec-old",
        total_workers=1,
        conversation_id="conv-1",
        host_turn_id="m1",
    )
    set_active_coordination(live)
    token = current_execution_id.set("exec-old")
    try:
        t = tool(Provider([]))
        t._base_tool_context.execution_id = "exec-new"
        t._message_id = "m2"
        t._conversation_id = "conv-1"
        t._captain_run_id = "cap-2"
        emitted: list[Any] = []
        t._sink.emit = lambda ev: emitted.append(ev)  # type: ignore[method-assign]
        monkeypatch.setattr("agentcore.runtime.plan_only.is_plan_only", lambda: True)

        result = await t.execute(
            {
                "conclusion": "这段先到这里。",
                "nodes": [{"id": "write", "goal": "写"}],
                "coordinate": False,
            },
            ctx(),
        )
        assert result.success
        plans = [e for e in emitted if getattr(e.type, "value", e.type) == "run_plan"]
        assert len(plans) == 1
        payload = plans[0].payload
        assert payload.get("execution_id") == "exec-new"
        assert "prev_execution_id" not in payload
        assert "exec-old" not in (result.output or "")
        assert "exec-new" not in (result.output or "")
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()
