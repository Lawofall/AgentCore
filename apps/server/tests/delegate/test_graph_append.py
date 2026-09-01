"""协作图身份：prev 链 / 同回合合入 / 宿主解析（divert 已退役）。"""

from __future__ import annotations

from typing import Any

import pytest

from agentcore.runtime.delegate.graph_append import (
    build_recent_graph_context,
    clear_graph_host_registry,
    format_recent_graph_worker_facts,
    parse_host_captain_run_id,
    peek_graph_host,
    register_graph_host,
    render_recent_graph_context,
)
from agentcore.runtime.events import EventSink, graph_append, run_plan
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.protocol import ToolResult
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
    """跨回合新图：无 host_message_id，新 eid 插 team；旧 graph_append 仅兼容回放。"""
    sink2 = EventSink(message_id="m2", conversation_id="c")
    # 旧 journal 回放：graph_append 仍可落 process 锚点
    sink2.emit(
        graph_append(
            execution_id="exec1",
            host_message_id="m1",
            append_message_id="m2",
            added_count=1,
            roles=["写"],
            added_run_ids=["r2"],
        )
    )
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
    assert any(s.get("kind") == "graph_append" and s.get("added_count") == 1 for s in process)
    assert any(s.get("kind") == "team" and s.get("execution_id") == "exec2" for s in process)
    assert peek_graph_host("exec2") == "m2"


def test_legacy_host_message_id_run_plan_skips_team_marker():
    """旧 divert 生长帧：带 host_message_id 的 run_plan 不插新 team。"""
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
    assert not any(s.get("kind") == "team" for s in process)


@pytest.mark.asyncio
async def test_delegate_cross_turn_append_mints_prev_chain(monkeypatch):
    """跨回合已收口图：新 eid + prev_execution_id，不发 graph_append。"""
    t = tool(Provider([]))
    t._base_tool_context.execution_id = None
    t._message_id = "m2"
    t._conversation_id = "conv-1"
    t._captain_run_id = "cap-2"
    emitted: list[Any] = []
    t._sink.emit = lambda ev: emitted.append(ev)  # type: ignore[method-assign]

    async def fake_resolve(*, conversation_id: str, execution_id: str):
        assert conversation_id == "conv-1"
        assert execution_id == "exec-old"
        return "m-host"

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        fake_resolve,
    )
    monkeypatch.setattr("agentcore.runtime.plan_only.is_plan_only", lambda: True)

    result = await t.execute(
        {
            "tasks": [{"role": "撰写员", "task": "写"}],
            "append_to_execution_id": "exec-old",
            "coordinate": False,
        },
        ctx(),
    )
    assert result.success
    plans = [e for e in emitted if getattr(e.type, "value", e.type) == "run_plan"]
    assert len(plans) == 1
    payload = plans[0].payload
    assert payload.get("prev_execution_id") == "exec-old"
    assert payload.get("execution_id") != "exec-old"
    assert "host_message_id" not in payload
    assert "exec-old" not in (result.output or "")
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
            "tasks": [{"role": "撰写员", "task": "写"}],
            "append_to_execution_id": "latest",
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


def test_format_recent_graph_worker_facts_includes_status():
    """Accident regression: next-turn CEO must see prior worker terminal states."""
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", role="调研员", task="搜集竞品资料"),
            RunSpec(run_id="w2", role="撰写员", task="写大纲"),
        ]
    )
    completed = {
        "w1": RunState(phase=RunPhase.CANCELLED),
        "w2": RunState(phase=RunPhase.COMPLETED, content="ok"),
    }
    facts = format_recent_graph_worker_facts(plan, completed)
    assert "workers=2：" in facts
    assert "run_id=w1" in facts
    assert "role=调研员; status=cancelled; task=搜集竞品资料" in facts
    assert "role=撰写员; status=completed; task=写大纲" in facts


def test_format_recent_graph_worker_facts_missing_seed_is_running():
    plan = RunPlan(nodes=[RunSpec(run_id="w1", role="执行员", task="还在跑")])
    facts = format_recent_graph_worker_facts(plan, {})
    assert "role=执行员; status=running; task=还在跑" in facts


def test_render_recent_graph_context_keeps_append_channel():
    block = render_recent_graph_context(
        execution_id="exec-1",
        worker_facts="workers=1：\n- run_id=w1; role=A; status=cancelled; task=x",
    )
    assert "<近期团队图>" in block
    assert "exec-1" not in block
    assert "run_id=w1" in block
    assert "status=cancelled" in block
    assert "本对话最近一张协作图" in block
    assert "append_to_execution_id" not in block
    assert "continue_from" not in block
    assert "replaces_run_id" not in block
    assert "prev_execution_id" not in block


@pytest.mark.asyncio
async def test_build_recent_graph_context_mentions_prev(monkeypatch):
    async def fake_latest(*, conversation_id: str, exclude_message_id: str | None = None):
        return "exec-recent"

    async def fake_host(**_k):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_latest_appendable_execution",
        fake_latest,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        fake_host,
    )
    note = await build_recent_graph_context(conversation_id="c1")
    assert "exec-recent" not in note
    assert "本对话最近一张协作图" in note
    assert "append_to_execution_id" not in note
    assert "continue_from" not in note
    assert "prev_execution_id" not in note


@pytest.mark.asyncio
async def test_build_recent_graph_context_includes_worker_status_facts(monkeypatch):
    """New-turn volatile tail must carry cancelled/completed worker facts from host journal."""

    async def fake_latest(*, conversation_id: str, exclude_message_id: str | None = None):
        return "exec-cancelled"

    async def fake_host(*, conversation_id: str, execution_id: str):
        assert execution_id == "exec-cancelled"
        return "host-msg-1"

    async def fake_plan_completed(host_message_id: str):
        assert host_message_id == "host-msg-1"
        plan = RunPlan(
            nodes=[
                RunSpec(run_id="del_1", role="研究员", task="查资料"),
            ]
        )
        completed = {"del_1": RunState(phase=RunPhase.CANCELLED)}
        return plan, completed

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_latest_appendable_execution",
        fake_latest,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        fake_host,
    )
    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.load_host_plan_and_completed",
        fake_plan_completed,
    )
    note = await build_recent_graph_context(conversation_id="c1")
    assert "exec-cancelled" not in note
    assert "workers=1：" in note
    assert "run_id=del_1" in note
    assert "role=研究员; status=cancelled; task=查资料" in note
    assert "append_to_execution_id" not in note
    assert "continue_from" not in note


@pytest.mark.asyncio
async def test_delegate_new_turn_live_prev_does_not_reuse_eid(monkeypatch):
    """上一张仍在跑：本回合 mint 新 eid + prev，不把新人并进旧图。"""
    from agentcore.runtime.coordination.session import (
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
                "tasks": [{"role": "撰写员", "task": "写"}],
                "coordinate": False,
            },
            ctx(),
        )
        assert result.success
        plans = [e for e in emitted if getattr(e.type, "value", e.type) == "run_plan"]
        assert len(plans) == 1
        payload = plans[0].payload
        assert payload.get("execution_id") == "exec-new"
        assert payload.get("prev_execution_id") == "exec-old"
        assert "exec-old" not in (result.output or "")
        assert "exec-new" not in (result.output or "")
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()


@pytest.mark.asyncio
async def test_delegate_missing_prev_host_rejects(monkeypatch):
    t = tool(Provider([]))
    t._base_tool_context.execution_id = None
    t._message_id = "m2"
    t._conversation_id = "conv-1"

    async def miss(**_k):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.delegate.graph_append.resolve_host_message_id",
        miss,
    )
    result = await t.execute(
        {
            "tasks": [{"role": "撰写员", "task": "写"}],
            "append_to_execution_id": "missing-eid",
            "coordinate": False,
        },
        ctx(),
    )
    assert isinstance(result, ToolResult)
    assert not result.success
    assert "找不到" in (result.error or "")
