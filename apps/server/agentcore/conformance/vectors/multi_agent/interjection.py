"""Multi-agent mid-flight user interjection vectors (协调插话 → CEO 路由)."""

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
    run_started,
    tool_use_end,
    tool_use_start,
    user_interjection,
)

from .._common import _CONV, _COST, _USAGE


def _agents_and_plan() -> tuple[list[dict], list[dict]]:
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
        {"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写", "depends_on": []},
    ]
    return agents, plan_runs


def _multi_agent_user_interjection_handled() -> list[SSEEvent]:
    """插话留在本回合：received → injected。CEO 在终稿里承接，不再另发状态。"""
    agents, plan_runs = _agents_and_plan()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队并行推进。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}]},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="并行调研 + 撰写",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        user_interjection(
            interjection_id="inj1",
            execution_id="exec1",
            content="补充一点：结论里请点明成本对比。",
            status="received",
        ),
        user_interjection(
            interjection_id="inj1",
            execution_id="exec1",
            content="补充一点：结论里请点明成本对比。",
            status="injected",
        ),
        tool_use_start(
            "syn1",
            "update_synthesis",
            {"draft": "已收到补充：成品会点明成本对比。"},
        ),
        tool_use_end(
            "syn1",
            "update_synthesis",
            success=True,
            output="已更新合成草稿（18 字），用户可见「进展中」预览。",
        ),
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已完成。"),
        content_delta("团队已按你的补充交稿。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_user_interjection_stays() -> list[SSEEvent]:
    """插话留在本回合：received → injected。不改排、不另开下一回合。"""
    agents, plan_runs = _agents_and_plan()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队并行推进。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}]},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="并行调研 + 撰写",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        user_interjection(
            interjection_id="inj2",
            execution_id="exec1",
            content="另外帮我写一封生日贺卡，跟这个项目无关。",
            status="received",
        ),
        user_interjection(
            interjection_id="inj2",
            execution_id="exec1",
            content="另外帮我写一封生日贺卡，跟这个项目无关。",
            status="injected",
        ),
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已完成。"),
        content_delta("当前任务已收口。贺卡那句留在这一轮。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_user_interjection_failed() -> list[SSEEvent]:
    """插话失败终态：received → injected → failed（此前无向量覆盖）。"""
    agents, plan_runs = _agents_and_plan()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队并行推进。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}]},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="并行调研 + 撰写",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        user_interjection(
            interjection_id="inj-fail",
            execution_id="exec1",
            content="请顺便帮我订机票。",
            status="received",
        ),
        user_interjection(
            interjection_id="inj-fail",
            execution_id="exec1",
            content="请顺便帮我订机票。",
            status="injected",
        ),
        user_interjection(
            interjection_id="inj-fail",
            execution_id="exec1",
            content="请顺便帮我订机票。",
            status="failed",
            note="未能排队，请重试或再说一次",
        ),
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已完成。"),
        content_delta("当前任务已收口。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_user_interjection_with_attachments() -> list[SSEEvent]:
    """带附件插话：received SSE 携带 attachments 元数据（名字 + 路径 + 二进制标记）。"""
    agents, plan_runs = _agents_and_plan()
    att_meta = [
        {
            "name": "成本表.xlsx",
            "workspace_path": "attachments/成本表.xlsx",
            "binary": True,
        },
        {
            "name": "补充说明.md",
            "workspace_path": "attachments/补充说明.md",
            "binary": False,
        },
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队并行推进。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}]},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="并行调研 + 撰写",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        user_interjection(
            interjection_id="inj-att",
            execution_id="exec1",
            content="请对照附件里的成本表再核一遍。",
            status="received",
            attachments=att_meta,
        ),
        user_interjection(
            interjection_id="inj-att",
            execution_id="exec1",
            content="请对照附件里的成本表再核一遍。",
            status="injected",
            attachments=att_meta,
        ),
        tool_use_start(
            "syn1",
            "update_synthesis",
            {"draft": "已收到附件补充：会对照成本表核验。"},
        ),
        tool_use_end(
            "syn1",
            "update_synthesis",
            success=True,
            output="已更新合成草稿，用户可见「进展中」预览。",
        ),
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已完成。"),
        content_delta("团队已按附件补充交稿。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_user_interjection_with_mentions() -> list[SSEEvent]:
    """带点名插话：received SSE 携带 agent_mentions（软芯片，非硬路由）。"""
    agents, plan_runs = _agents_and_plan()
    mentions = [{"agent_id": "agent_research", "role": "研究员"}]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队并行推进。"),
        tool_use_start(
            "dc1",
            "delegate",
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}]},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="并行调研 + 撰写",
            agents=agents,
            runs=plan_runs,
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        user_interjection(
            interjection_id="inj-mention",
            execution_id="exec1",
            content="请让研究员再核一遍成本。",
            status="received",
            agent_mentions=mentions,
        ),
        user_interjection(
            interjection_id="inj-mention",
            execution_id="exec1",
            content="请让研究员再核一遍成本。",
            status="injected",
            agent_mentions=mentions,
        ),
        tool_use_start(
            "syn1",
            "update_synthesis",
            {"draft": "已收到点名补充：会让研究员核验成本。"},
        ),
        tool_use_end(
            "syn1",
            "update_synthesis",
            success=True,
            output="已更新合成草稿，用户可见「进展中」预览。",
        ),
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="团队已完成。"),
        content_delta("团队已按你的点名补充交稿。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_solo_coordinate_interjection() -> list[SSEEvent]:
    """单 worker + 协调：非阻塞开跑 → 执行期插话可达 → CEO cancel_worker。

    钉「solo 也进协调」组合：leftover team_preview / team_synthesis_preview 仍不发（相邻 ≥2 闸不变），
    但 delegate 立即返回『团队已启动』，CEO 自由；用户中途「把它停止」经
    user_interjection 注入后由 cancel_worker 终止队员。
    """
    agents = [
        {
            "id": "w1",
            "role": "工程师",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "实现功能", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我派一名工程师去做。"),
        tool_use_start(
            "dc1",
            "delegate",
            {
                "tasks": [{"role": "工程师", "task": "实现功能"}],
                "coordinate": True,
            },
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="单人协调：实现功能",
            agents=agents,
            runs=plan_runs,
        ),
        # 协调臂：delegate 立即返回；无 team_synthesis_preview（solo 预览闸仍 ≥2）。
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output="【团队已启动·协调模式】已派出 1 名队员（工程师）。",
        ),
        run_started("r1", "w1"),
        user_interjection(
            interjection_id="inj-solo-stop",
            execution_id="exec1",
            content="把它停止",
            status="received",
        ),
        user_interjection(
            interjection_id="inj-solo-stop",
            execution_id="exec1",
            content="把它停止",
            status="injected",
        ),
        content_delta("收到，正在停止这名工程师。"),
        tool_use_start(
            "cw1",
            "cancel_worker",
            {"run_id": "r1", "reason": "用户要求停止"},
        ),
        tool_use_end(
            "cw1",
            "cancel_worker",
            success=True,
            output="已请求取消队员 r1（工程师）。",
        ),
        run_cancelled("r1", "w1", reason="stop", execution_id="exec1"),
        content_delta("已按你的要求停下。"),
        message_end(FinishReason.END_TURN, input_tokens=1800, output_tokens=320, cost=_COST),
    ]


def _multi_agent_user_interjection_delegate_append() -> list[SSEEvent]:
    """插话留在本回合：received → injected → CEO 二次 delegate 追加队员。"""
    batch1_agents = [
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
    batch1_runs = [
        {"id": "r1", "agent_id": "w1", "task": "调研", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写", "depends_on": []},
    ]
    batch2_agents = [
        {
            "id": "w3",
            "role": "校对员",
            "thinking": True,
        },
    ]
    batch2_runs = [
        {"id": "r3", "agent_id": "w3", "task": "校对把关", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队并行推进。"),
        tool_use_start(
            "dc1",
            "delegate",
            {
                "tasks": [{"role": "研究员"}, {"role": "撰写员"}],
                "coordinate": True,
            },
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="并行调研 + 撰写",
            agents=batch1_agents,
            runs=batch1_runs,
        ),
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output="【团队已启动·协调模式】已派出 2 名队员（研究员、撰写员）。",
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        user_interjection(
            interjection_id="inj-append",
            execution_id="exec1",
            content="再加一个校对员把关。",
            status="received",
        ),
        user_interjection(
            interjection_id="inj-append",
            execution_id="exec1",
            content="再加一个校对员把关。",
            status="injected",
        ),
        content_delta("收到，追加一名校对员。"),
        tool_use_start(
            "dc2",
            "delegate",
            {"tasks": [{"role": "校对员", "task": "校对把关"}]},
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="追加校对",
            agents=batch2_agents,
            runs=batch2_runs,
        ),
        tool_use_end(
            "dc2",
            "delegate",
            success=True,
            output="【队员已追加·协调模式】已追加 1 名队员（校对员）。",
        ),
        run_started("r3", "w3"),
        run_completed(
            "r1",
            "w1",
            output_summary="完成调研",
            duration_ms=1000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            "r3",
            "w3",
            output_summary="完成校对",
            duration_ms=600,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta("已按你的要求追加校对并收口。"),
        message_end(FinishReason.END_TURN, input_tokens=2200, output_tokens=450, cost=_COST),
    ]
