"""同回合二次委派的 seed：只带上一段 drive 的真实终态。

首批失败 / 让出后未跑的节点绝不冒充完成——否则二次批次的各段结果会把失败算成
交付，未跑的尾节点也会被当成已派发而永不调度。
"""

from __future__ import annotations

import asyncio

from agentcore.runtime.graph.session.session import (
    active_coordination,
    clear_active_coordination,
)
from agentcore.runtime.runs import RunPhase
from agentcore.runtime.runs.types import RunState
from tests.delegate.conftest import Provider, ctx, tool


async def test_second_delegate_seed_keeps_failed_node_failed(monkeypatch):
    """首批一人失败 → 二次委派后名册仍点名该失败、失败计数非 0。"""
    executed: list[str] = []

    async def _exec(spec, completed):  # noqa: ANN001 — matches build_agent_executor
        executed.append(spec.task)
        if spec.task == "撰写初稿":
            # error_retryable=False：确定性失败，波调度器不再 infra 重跑，派发序列唯一。
            return RunState(phase=RunPhase.FAILED, error="boom", content="", error_retryable=False)
        return RunState(phase=RunPhase.COMPLETED, content=f"{spec.role}_OUT")

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    t = tool(Provider([]))
    first = await t.execute(
        {
            "nodes": [
                {"id": "研究员", "goal": "调研竞品"},
                {"id": "写手", "goal": "撰写初稿"},
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert first.success is True

    writer_id = next(n.run_id for n in t._last_graph_plan.nodes if n.task == "撰写初稿")
    seed = t._last_graph_seed or {}
    assert seed[writer_id].phase is RunPhase.FAILED
    assert seed[writer_id].error == "boom"

    second = await t.execute(
        {"nodes": [{"id": "校对", "goal": "复核初稿"}], "coordinate": False},
        ctx(),
    )

    assert second.success is True
    assert "失败 1" in second.output
    assert "写手" in second.output and "boom" in second.output
    assert "不能当成全部完成" in second.output
    # 已终结的首批节点仍不重派：seed 带真相，不等于放行重跑。
    assert executed == ["调研竞品", "撰写初稿", "复核初稿"]


async def test_second_delegate_seed_omits_yielded_untouched_tail(monkeypatch):
    """让出边界后从未跑的尾节点不进 seed → 二次委派仍会调度它。"""
    executed: list[str] = []

    async def _exec(spec, completed):  # noqa: ANN001 — matches build_agent_executor
        executed.append(spec.task)
        if spec.task == "调研真实需求":
            return RunState(
                phase=RunPhase.COMPLETED,
                content="AOUT",
                escalations=[{"reason": "adjust", "question": "真问题是X", "assumption": "暂按X"}],
            )
        return RunState(phase=RunPhase.COMPLETED, content=f"{spec.role}_OUT")

    monkeypatch.setattr("agentcore.runtime.runs.build_agent_executor", lambda **kw: _exec)
    t = tool(Provider([]))
    first = await t.execute(
        {
            "nodes": [
                {"id": "研究员", "goal": "调研真实需求"},
                {"id": "写手", "goal": "撰写最终报告", "depends_on": ["研究员"]},
            ],
            "coordinate": False,
        },
        ctx(),
    )
    assert first.success is True
    assert t._supervised is not None
    assert executed == ["调研真实需求"]

    nodes = {n.task: n.run_id for n in t._last_graph_plan.nodes}
    seed = t._last_graph_seed or {}
    assert seed[nodes["调研真实需求"]].phase is RunPhase.COMPLETED
    assert nodes["撰写最终报告"] not in seed

    second = await t.execute(
        {"nodes": [{"id": "校对", "goal": "复核初稿"}], "coordinate": False},
        ctx(),
    )

    assert second.success is True
    assert "撰写最终报告" in executed
    assert (t._last_graph_seed or {})[nodes["撰写最终报告"]].phase is RunPhase.COMPLETED


async def test_coordinated_drive_stamps_seed_after_workers_finish():
    """协调态 kickoff 不把未完成人写入 seed；drive 收口后必须 stamp，避免同构闸谎报 0/N。"""
    t = tool(Provider(["AOUT", "BOUT"]))
    first = await t.execute(
        {
            "nodes": [
                {"id": "研究员", "goal": "做A调研"},
                {"id": "写手", "goal": "做B撰写"},
            ],
            "coordinate": True,
        },
        ctx(),
    )
    assert first.success is True
    session = active_coordination("e")
    assert session is not None
    try:
        await asyncio.wait_for(session.drive_task, timeout=10)
        seed = t._last_graph_seed or {}
        assert len(seed) == 2
        assert all(state.phase is RunPhase.COMPLETED for state in seed.values())

        second = await t.execute(
            {
                "nodes": [{"id": "写手", "goal": "做B撰写完善"}],
                "coordinate": True,
            },
            ctx(),
        )
        blob = f"{second.error or ''}{second.output or ''}"
        assert "已完成 0/" not in blob
    finally:
        clear_active_coordination("e")
