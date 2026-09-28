"""Multi-agent escalation and CEO arbitration vectors."""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    escalation_raised,
    escalation_required,
    escalation_resolved,
    message_end,
    message_start,
    run_completed,
    run_output_delta,
    run_plan,
    run_started,
)

from .._common import _CONV, _COST, _ESC_A, _ESC_Q, _USAGE
from ._builders import _blocking_escalate_team


def _multi_agent_escalation() -> list[SSEEvent]:
    """多 Agent：worker ``escalate(reason=scope)`` 实时可见。被委派的 r1 发现活派偏了，
    调 ``escalate``——执行器在【调用瞬间】emit ``run_escalation``（run 级），
    三端 fold + oracle 把它折到 r1 的 ``escalations``（协作图标记），r2 的
    ``escalations`` 恒空。r1 自己这份做完并 COMPLETED（升级的持久副本另走
    RunState.escalations → CEO 综述）。"""
    agents = [
        {
            "id": "w1",
            "role": "研究员",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "撰写员",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研选型", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写建议", "depends_on": ["r1"]},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="选型并给建议",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        # 调用瞬间 emit：升级先于 r1 的产出/完成（实时，非收场 harvest）。
        escalation_raised(
            "r1",
            "w1",
            question="数据库选 Postgres 还是 MySQL？这关系到后续所有选型。",
            assumption="暂按 Postgres 推进",
            kind="adjust",
            # 固定 id 保 golden 稳定（缺省会随机 uuid，导出不幂等）。
            escalation_id="esc1",
        ),
        run_output_delta("r1", "w1", "已按 Postgres 完成选型调研"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成选型调研（含 1 条升级）",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "基于选型给出建议"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成建议",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 团队已完成（有 1 条待你拍板的升级）。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]

def _multi_agent_blocking_escalate() -> list[SSEEvent]:
    """多 Agent：``escalate(reason=wait)`` 答复路径。经典路径
    （coordinate=false / 用户直挂；本向量无 tool_use，wire 从 run_plan 起）。r1 撞到「猜错
    就作废」的岔路，调 escalate(reason=wait) 原地挂起 → 执行器 emit ``escalation_required``
    （run 级，``escalation_id`` 键给 resolve 端点），三端 fold + oracle 把它折成 r1 的一条 pending
    升级（``status="pending"``）。关键：wait 升级【不】把回合翻 paused——兄弟仍可跑（区别于 approval/
    ask_user/plan_review 的 halting gate），故 ``pendingInteraction`` 恒 None。用户答复 →
    ``escalation_resolved(status="resolved", answer)``（单一发射者：仅挂起的工具发）→ 该项翻
    ``{status:"resolved", answer}``，r1 据答续跑并 COMPLETED。"""
    agents, plan_runs = _blocking_escalate_team()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="选型并给建议",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        # 阻塞挂起：把问题直接送到用户（CEO 停在 delegate、够不到用户）。
        escalation_required("r1", "w1", escalation_id="esc1", question=_ESC_Q, assumption=_ESC_A),
        # 用户答复 → 该项翻 resolved，r1 据答续跑（以用户答复为准）。
        escalation_resolved(
            "r1", "w1", escalation_id="esc1", status="resolved", answer="用 Postgres。"
        ),
        run_output_delta("r1", "w1", "已确认 Postgres，完成选型调研"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成选型调研（用户已拍板 Postgres）",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "基于选型给出建议"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成建议",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]

def _multi_agent_blocking_escalate_timeout() -> list[SSEEvent]:
    """多 Agent：阻塞式求决策 — 墙钟超时降级（``timed_out``）。

    r1 阻塞挂起 → 时限内未答 → ``escalation_resolved(status=timed_out)`` → 投影
    ``{status:timed_out, answer:null}``，worker 回落 assumption。显式「按假设继续」走
    ``assumed``（见其它向量），二者不得糊成同一 ``timeout`` 标签。
    """
    agents, plan_runs = _blocking_escalate_team()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="选型并给建议",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        escalation_required("r1", "w1", escalation_id="esc1", question=_ESC_Q, assumption=_ESC_A),
        escalation_resolved("r1", "w1", escalation_id="esc1", status="timed_out", answer=""),
        run_output_delta("r1", "w1", "未获答复，按 Postgres 假设完成选型调研"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成选型调研（超时按假设 Postgres 续跑）",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "基于选型给出建议"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成建议",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]

def _multi_agent_blocking_escalate_pending() -> list[SSEEvent]:
    """多 Agent：阻塞式求决策 — 进行中（卡片 live / 重载 dormant）。r1 阻塞挂起 emit
    ``escalation_required`` 后流到此为止（无 ``escalation_resolved`` / ``message_end``，镜像
    ``approval_paused`` 的「挂起态快照」）。关键断言：回合 ``status`` 仍为 ``running`` 且
    ``pendingInteraction`` 恒 None——阻塞升级【非 halting gate】，不把回合翻 paused（并行兄弟 r2 仍
    照常起跑），这正是它区别于 approval/ask_user/plan_review 的核心。r1 的升级 ``status="pending"``
    （即 live 应答卡；断连重载时同形为 dormant 记录）。"""
    agents, plan_runs = _blocking_escalate_team()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="选型并给建议",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        # 并行兄弟 r2 照常起跑——证明阻塞升级不挡其它 worker、不挂起整波。
        run_started("r2", "w2"),
        escalation_required("r1", "w1", escalation_id="esc1", question=_ESC_Q, assumption=_ESC_A),
    ]

def _multi_agent_blocking_escalate_multi() -> list[SSEEvent]:
    """多 Agent：阻塞式求决策 — 同一 worker 串行多次升级（多升级向量）。r1 先后撞到两个「只有用户
    能定」的关键岔路：第一次得到答复（resolved），续跑后第二次超时降级（timeout）。验证 ``escalations``
    可承载一个 run 的多条阻塞升级、且「找首个 pending」折叠在串行场景逐条对位结算——第二次 resolve
    命中的是当时唯一的 pending（esc2，esc1 已 resolved）。worker 串行 ⇒ 任一时刻至多一个 pending。"""
    agents, plan_runs = _blocking_escalate_team()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="选型并给建议",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        # 第一个关键岔路：阻塞挂起 → 用户答复 → resolved。
        escalation_required("r1", "w1", escalation_id="esc1", question=_ESC_Q, assumption=_ESC_A),
        escalation_resolved(
            "r1", "w1", escalation_id="esc1", status="resolved", answer="用 Postgres。"
        ),
        # 据首答续跑后又撞到第二个只有用户能定的点 → 再次阻塞挂起（此时 esc1 已结算，唯一 pending）。
        escalation_required(
            "r1",
            "w1",
            escalation_id="esc2",
            question="部署用 Docker 还是裸机？这关系到运维方案。",
            assumption="暂按 Docker 推进",
        ),
        # 第二次墙钟超时降级（answer 空）→ r1 回落到该假设续跑。
        escalation_resolved("r1", "w1", escalation_id="esc2", status="timed_out", answer=""),
        run_output_delta("r1", "w1", "已确认 Postgres + Docker，完成选型调研"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成选型调研（2 次升级）",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "基于选型给出建议"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成建议",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]

def _multi_agent_ceo_arbitrate_escalate() -> list[SSEEvent]:
    """多 Agent·协调模式 D1：worker 阻塞 escalate → CEO 用 replan tell 把决定说回来。

    ``escalation_required(awaiting=ceo)`` 初始不可答；``escalation_resolved(arbitrated_by=ceo,
    via_user=false)`` 后 worker 恢复。回合不 paused。
    """
    agents, plan_runs = _blocking_escalate_team()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排协调团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="选型并给建议",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        escalation_required(
            "r1",
            "w1",
            escalation_id="esc1",
            question=_ESC_Q,
            assumption=_ESC_A,
            awaiting="ceo",
        ),
        escalation_resolved(
            "r1",
            "w1",
            escalation_id="esc1",
            status="resolved",
            answer="用 Postgres。",
            arbitrated_by="ceo",
            via_user=False,
        ),
        run_output_delta("r1", "w1", "已按主管裁决确认 Postgres，完成选型调研"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成选型调研（CEO 已仲裁 Postgres）",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "基于选型给出建议"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成建议",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]

def _multi_agent_ceo_arbitrate_escalate_via_user() -> list[SSEEvent]:
    """多 Agent·协调模式 D1：CEO 经 ask_user 转交用户后再用 replan tell（via_user=true）。

    事件序列只钉裁决可见性（arbitrated_by=ceo + via_user）；ask_user 卡本身是回合级 gate，
    不在本向量展开。
    """
    agents, plan_runs = _blocking_escalate_team()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排协调团队。"),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="选型并给建议",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        escalation_required(
            "r1",
            "w1",
            escalation_id="esc1",
            question=_ESC_Q,
            assumption=_ESC_A,
            awaiting="ceo",
        ),
        escalation_resolved(
            "r1",
            "w1",
            escalation_id="esc1",
            status="resolved",
            answer="用 Postgres（用户确认）。",
            arbitrated_by="ceo",
            via_user=True,
        ),
        run_output_delta("r1", "w1", "已按经用户确认的主管裁决完成选型调研"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成选型调研（CEO 经用户仲裁）",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("r2", "w2"),
        run_output_delta("r2", "w2", "基于选型给出建议"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成建议",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]
