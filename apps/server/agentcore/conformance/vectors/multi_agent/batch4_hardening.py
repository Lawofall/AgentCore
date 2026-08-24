"""批次 4 内核加固棘轮：stop 门 / 增量 preview / merge 竞态 / gaps / 超时硬收尾。"""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    message_end,
    message_start,
    run_cancelled,
    run_completed,
    run_plan,
    run_progress,
    run_started,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE


def _multi_agent_stop_gate_run_frames() -> list[SSEEvent]:
    """停止诚实过渡态：message_end(cancelled) 后仍有 run_* 级联终态帧，fold 须如实收口。

    对应桌面 turnPhase stopping 放行 run_*；协议层保证终态帧可投影。
    """
    agents = [
        {"id": "w1", "role": "研究员", "thinking": True},
        {"id": "w2", "role": "撰写员", "thinking": True},
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("已派出团队。"),
        tool_use_start(
            "dc1", "delegate", {"tasks": [{"role": "研究员"}, {"role": "撰写员"}]}
        ),
        run_plan(
            execution_id="exec-stop",
            plan_type="multi_agent",
            task_summary="并行任务",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        run_progress(0, 2),
        tool_use_end("dc1", "delegate", success=False, output="已停止"),
        message_end(FinishReason.CANCELLED, input_tokens=800, output_tokens=40, cost=_COST),
        run_cancelled("r1", "w1", reason="stop"),
        run_cancelled("r2", "w2", reason="stop"),
    ]


def _multi_agent_incremental_preview_badge() -> list[SSEEvent]:
    """增量组队：首批仍在跑时二次 delegate（徽标叠加，不压运行态）。"""
    agents = [
        {"id": "w1", "role": "研究员", "thinking": True},
        {"id": "w2", "role": "分析师", "thinking": True},
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "分析", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("先派首批。"),
        tool_use_start(
            "dc1", "delegate", {"tasks": [{"role": "研究员"}, {"role": "分析师"}]}
        ),
        run_plan(
            execution_id="exec-prev",
            plan_type="multi_agent",
            task_summary="首批调研",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        run_progress(0, 2),
        tool_use_end("dc1", "delegate", success=True, output="团队已启动"),
        message_end(FinishReason.PAUSED, input_tokens=1200, output_tokens=80, cost=_COST),
    ]


def _multi_agent_merge_race_secondary_delegate() -> list[SSEEvent]:
    """同回合二次 delegate 合并进同一 execution（第二批只带新增节点）。

    形状对齐 ``multi_agent_multi_batch``（两端 fold 已对齐的增量 merge）；
    本向量钉「工具调用包裹 + 同 execution_id」的 merge 契约面。
    """
    batch1_agents = [{"id": "w1", "role": "研究员", "thinking": True}]
    batch1_runs = [{"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []}]
    batch2_agents = [{"id": "w2", "role": "校对员", "thinking": True}]
    batch2_runs = [{"id": "r2", "agent_id": "w2", "task": "校对", "depends_on": ["r1"]}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("先调研。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "研究员"}]}),
        run_plan(
            execution_id="exec-merge",
            plan_type="multi_agent",
            task_summary="调研",
            agents=batch1_agents,
            runs=batch1_runs,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已启动"),
        run_started("r1", "w1"),
        run_completed(
            "r1",
            "w1",
            output_summary="调研完成",
            duration_ms=500,
            role="member",
            model="test",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 再追加校对。"),
        tool_use_start("dc2", "delegate", {"tasks": [{"role": "校对员"}]}),
        run_plan(
            execution_id="exec-merge",
            plan_type="multi_agent",
            task_summary="追加校对",
            agents=batch2_agents,
            runs=batch2_runs,
        ),
        tool_use_end("dc2", "delegate", success=True, output="已追加校对员"),
        run_started("r2", "w2"),
        run_completed(
            "r2",
            "w2",
            output_summary="校对完成",
            duration_ms=300,
            role="member",
            model="test",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta("都完成了。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=150, cost=_COST),
    ]


def _multi_agent_run_completed_gaps() -> list[SSEEvent]:
    """批次 3 新面：run_completed.gaps 一等化（软放行缺口）。"""
    agents = [{"id": "w1", "role": "撰稿", "thinking": True}]
    plan_runs = [{"id": "r1", "agent_id": "w1", "task": "写报告", "depends_on": []}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("写一份报告。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "撰稿"}]}),
        run_plan(
            execution_id="exec-gaps",
            plan_type="multi_agent",
            task_summary="写报告",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_completed(
            "r1",
            "w1",
            output_summary="正文已交，缺附录",
            duration_ms=900,
            role="member",
            model="test",
            usage=_USAGE,
            cost=_COST,
            gaps=[
                {
                    "role": "撰稿",
                    "description": "附录「数据表」未交付（契约软放行）",
                }
            ],
        ),
        tool_use_end("dc1", "delegate", success=True, output="已收口（有缺口）"),
        content_delta("报告主体完成，附录待补。"),
        message_end(FinishReason.END_TURN, input_tokens=1500, output_tokens=120, cost=_COST),
    ]


def _multi_agent_timeout_hard_gaps() -> list[SSEEvent]:
    """批次 3 新面：超时硬收尾盖章 → run_completed.gaps reason=worker_timeout。"""
    agents = [{"id": "w1", "role": "研究员", "thinking": True}]
    plan_runs = [{"id": "r1", "agent_id": "w1", "task": "深研", "depends_on": []}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("去做深研。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "研究员"}]}),
        run_plan(
            execution_id="exec-to",
            plan_type="multi_agent",
            task_summary="深研",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_completed(
            "r1",
            "w1",
            output_summary="超时强制交卷（部分产出）",
            duration_ms=120_000,
            role="member",
            model="test",
            usage=_USAGE,
            cost=_COST,
            gaps=[
                {
                    "role": "研究员",
                    "description": "墙钟超时硬收尾，交付缩水",
                    "reason": "worker_timeout",
                }
            ],
        ),
        tool_use_end("dc1", "delegate", success=True, output="已收口（超时缩水）"),
        content_delta("队员超时交卷，以下为已有产出。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=160, cost=_COST),
    ]
