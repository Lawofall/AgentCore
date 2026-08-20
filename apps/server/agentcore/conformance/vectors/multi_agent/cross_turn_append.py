"""跨回合协作图续接 conformance 向量。

第一回合建图完成 → 第二回合 ``append_to_execution_id`` → **新** ``execution_id`` +
``prev_execution_id`` 链到旧图（不再 divert / ``graph_append`` / 同 eid merge）。
消费端契约：(a) 新回合 ``run_plan`` 锚当前 message，带 ``prev_execution_id``；
(b) 进度分母只含本图节点；(c) 旧 ``graph_append`` / ``host_message_id`` 生长帧不再出现。
"""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    execution_detached,
    message_end,
    message_start,
    run_completed,
    run_output_delta,
    run_plan,
    run_started,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE

_CAPTAIN_1 = "c1"
_CAPTAIN_2 = "c2"


def _captain_agent(captain_id: str) -> dict:
    """Roster card — mirrors ``plan_events.captain_card``."""
    return {"id": captain_id, "role": "CEO", "thinking": True}


def _captain_run(captain_id: str) -> dict:
    """Plan-time captain root — mirrors ``plan_events.plan_event`` insert."""
    return {
        "id": captain_id,
        "agent_id": captain_id,
        "task": "",
        "depends_on": [],
        "parent_run_id": None,
        "kind": "captain",
    }


def _worker_run(
    run_id: str,
    agent_id: str,
    task: str,
    *,
    parent: str,
    depends_on: list[str] | None = None,
) -> dict:
    """Worker plan node under the turn captain."""
    return {
        "id": run_id,
        "agent_id": agent_id,
        "task": task,
        "depends_on": list(depends_on or []),
        "parent_run_id": parent,
    }


def _multi_agent_cross_turn_append() -> list[SSEEvent]:
    """跨回合续接：m1 建图完成 → m2 新图 + prev=exec1 → 新批完成。"""
    batch1_agents = [
        _captain_agent(_CAPTAIN_1),
        {"id": "w1", "role": "研究员", "thinking": True},
        {"id": "w2", "role": "分析师", "thinking": True},
    ]
    batch1_runs = [
        _captain_run(_CAPTAIN_1),
        _worker_run("r1", "w1", "调研素材", parent=_CAPTAIN_1),
        _worker_run("r2", "w2", "分析结论", parent=_CAPTAIN_1, depends_on=["r1"]),
    ]
    # 第二回合：仅本图节点（撰写员），经 prev 链到 exec1；本回合 captain。
    batch2_agents = [
        _captain_agent(_CAPTAIN_2),
        {"id": "w3", "role": "撰写员", "thinking": True},
    ]
    batch2_runs = [
        _captain_run(_CAPTAIN_2),
        _worker_run("r3", "w3", "撰写文稿", parent=_CAPTAIN_2),
    ]
    return [
        # ── 回合 1：建图并完成 ──
        message_start("m1", conversation_id=_CONV),
        content_delta("先组队调研分析。"),
        tool_use_start(
            "dc1",
            "delegate",
            {
                "tasks": [{"role": "研究员"}, {"role": "分析师"}],
                "coordinate": False,
            },
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研分析",
            agents=batch1_agents,
            runs=batch1_runs,
        ),
        run_started("r1", "w1", parent_run_id=_CAPTAIN_1),
        run_output_delta("r1", "w1", "素材就绪"),
        run_completed(
            "r1",
            "w1",
            output_summary="调研完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2", parent_run_id=_CAPTAIN_1),
        run_output_delta("r2", "w2", "分析定稿"),
        run_completed(
            "r2",
            "w2",
            output_summary="分析完成",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队完成 2 项任务。"),
        content_delta(" 第一轮结论已汇总。"),
        message_end(FinishReason.END_TURN, input_tokens=4000, output_tokens=700, cost=_COST),
        # ── 回合 2：新图 + prev 链 ──
        message_start("m2", conversation_id=_CONV),
        content_delta("再往上一张图加一位撰写员。"),
        tool_use_start(
            "dc2",
            "delegate",
            {
                "tasks": [{"role": "撰写员", "task": "撰写文稿"}],
                "append_to_execution_id": "exec1",
                "coordinate": False,
            },
        ),
        run_plan(
            execution_id="exec2",
            plan_type="multi_agent",
            task_summary="撰写文稿",
            agents=batch2_agents,
            runs=batch2_runs,
            prev_execution_id="exec1",
        ),
        run_started("r3", "w3", parent_run_id=_CAPTAIN_2),
        run_output_delta("r3", "w3", "成稿"),
        run_completed(
            "r3",
            "w3",
            output_summary="撰写完成",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end(
            "dc2",
            "delegate",
            success=True,
            output="【协作图·续接】本回合新开团队执行，经 prev 链到上一张图。",
        ),
        content_delta(" 已新开撰写队，接续上一张图。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_cross_turn_live_prev() -> list[SSEEvent]:
    """上一轮后台仍在跑 + 新回合再派人：新人进新图，不进旧图。"""
    batch1_agents = [
        _captain_agent(_CAPTAIN_1),
        {"id": "w1", "role": "研究员", "thinking": True},
    ]
    batch1_runs = [
        _captain_run(_CAPTAIN_1),
        _worker_run("r1", "w1", "调研素材", parent=_CAPTAIN_1),
    ]
    batch2_agents = [
        _captain_agent(_CAPTAIN_2),
        {"id": "w3", "role": "撰写员", "thinking": True},
    ]
    batch2_runs = [
        _captain_run(_CAPTAIN_2),
        _worker_run("r3", "w3", "撰写文稿", parent=_CAPTAIN_2),
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("先组队调研，我先收口。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "研究员"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="调研素材",
            agents=batch1_agents,
            runs=batch1_runs,
        ),
        run_started("r1", "w1", parent_run_id=_CAPTAIN_1),
        execution_detached(
            execution_id="exec1",
            conversation_id=_CONV,
            completed=0,
            total=1,
            reason="turn_released",
            host_turn_id="m1",
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已启动"),
        content_delta(" 人已派出。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
        message_start("m2", conversation_id=_CONV),
        content_delta("再加一位撰写员。"),
        tool_use_start(
            "dc2",
            "delegate",
            {"tasks": [{"role": "撰写员", "task": "撰写文稿"}]},
        ),
        run_plan(
            execution_id="exec2",
            plan_type="multi_agent",
            task_summary="撰写文稿",
            agents=batch2_agents,
            runs=batch2_runs,
            prev_execution_id="exec1",
        ),
        run_started("r3", "w3", parent_run_id=_CAPTAIN_2),
        run_output_delta("r3", "w3", "成稿"),
        run_completed(
            "r3",
            "w3",
            output_summary="撰写完成",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end(
            "dc2",
            "delegate",
            success=True,
            output="【协作图·续接】本回合新开一队、接续上一张图。",
        ),
        content_delta(" 已新开一队，接续上一张图。"),
        message_end(FinishReason.END_TURN, input_tokens=1800, output_tokens=350, cost=_COST),
    ]
