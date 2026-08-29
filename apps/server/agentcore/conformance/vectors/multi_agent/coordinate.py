"""Multi-agent coordinate synthesis vectors."""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    coordination_wait,
    message_end,
    message_start,
    run_completed,
    run_output_delta,
    run_plan,
    run_progress,
    run_started,
    team_synthesis_preview,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE


def _multi_agent_coordinate() -> list[SSEEvent]:
    """多 Agent·CEO 协调模式：≥2 worker 并行 + 合成草稿预览 + 收束。

    Wire 形状对齐 coordinate=true 路径的可见事件（非阻塞 delegate 立即返回后 CEO 继续
    ReAct）。单 worker 协调见 ``multi_agent_solo_coordinate_interjection``（无
    team_synthesis_preview）。本向量钉并行两队员、update_synthesis 推送的
    team_synthesis_preview（P2 DURABLE——fold 同 key 保最新进
    ProjectedTurn.teamSynthesisPreview）、完成后 CEO 终稿。亦作「刷新重建」钉：golden
    断言末次 preview 文案。
    """
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
        {"id": "r1", "agent_id": "w1", "task": "调研接口", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写文档", "depends_on": []},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排团队并行推进，并在波内协调。"),
        # 默认协调路径（省略 coordinate 等价于 true）；显式 true 钉本向量意图。
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
            task_summary="协调模式：并行调研 + 撰写",
            agents=agents,
            runs=plan_runs,
        ),
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output="【团队已启动·协调模式】已派出 2 名队员（研究员、撰写员）。",
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        # CEO update_synthesis → team_synthesis_preview（草稿正文在 text；workers 可空）。
        # P2 DURABLE：三端 fold 同 key 保最新 → ProjectedTurn.teamSynthesisPreview。
        team_synthesis_preview(
            execution_id="exec1",
            completed=0,
            total=2,
            headline="合成草稿更新 · 已完成 0/2",
            text="两边刚起步；接口方向按开局共识对齐。",
            workers=[],
            in_progress=True,
        ),
        run_output_delta("r1", "w1", "调研结论"),
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
        run_progress(1, 2),
        # 确定性进度预览（drive._progress）：有 worker 摘要行。
        team_synthesis_preview(
            execution_id="exec1",
            completed=1,
            total=2,
            headline="已完成 1/2：✅ 研究员 ⏳ 撰写员",
            text="已完成 1/2：✅ 研究员 ⏳ 撰写员\n· 研究员：完成调研",
            workers=[
                {
                    "run_id": "r1",
                    "role": "研究员",
                    "status": "completed",
                    "summary": "完成调研",
                },
                {
                    "run_id": "r2",
                    "role": "撰写员",
                    "status": "pending",
                    "summary": "",
                },
            ],
            in_progress=True,
        ),
        run_output_delta("r2", "w2", "成稿"),
        run_completed(
            "r2",
            "w2",
            output_summary="完成撰写",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_progress(2, 2),
        # all_completed 后 CEO 用 content_delta 写终稿（不再走 update_synthesis）。
        content_delta(" 团队已完成：接口与文档对齐，按方案 A 定稿。"),
        message_end(FinishReason.END_TURN, input_tokens=4300, output_tokens=900, cost=_COST),
    ]


def _multi_agent_coordination_wait() -> list[SSEEvent]:
    """多 Agent·协调等待 UX：CEO 空等团队事件时 ``coordination_wait``（EPHEMERAL）。

    挂起态快照——1/2 worker 已完成、CEO 进入等待；无 message_end，回合仍 running。
    用于 #/preview / ``pnpm shoot`` 自检 StatusStrip 只报 1/2（成员细节在协作图节点）。
    """
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
        {"id": "r1", "agent_id": "w1", "task": "调研接口", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "撰写文档", "depends_on": []},
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
            task_summary="协调等待：并行调研 + 撰写",
            agents=agents,
            runs=plan_runs,
        ),
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output="【团队已启动·协调模式】已派出 2 名队员（研究员、撰写员）。",
        ),
        run_started("r1", "w1"),
        run_started("r2", "w2"),
        run_output_delta("r1", "w1", "调研结论"),
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
        run_progress(1, 2),
        team_synthesis_preview(
            execution_id="exec1",
            completed=1,
            total=2,
            headline="已完成 1/2：✅ 研究员 ⏳ 撰写员",
            text="已完成 1/2：✅ 研究员 ⏳ 撰写员\n· 研究员：完成调研",
            workers=[
                {
                    "run_id": "r1",
                    "role": "研究员",
                    "status": "completed",
                    "summary": "完成调研",
                },
                {
                    "run_id": "r2",
                    "role": "撰写员",
                    "status": "pending",
                    "summary": "",
                },
            ],
            in_progress=True,
        ),
        # CEO 进入 await_coordination_injection 空等——前端应显示等待指示。
        coordination_wait(
            execution_id="exec1",
            waiting=True,
            completed=1,
            total=2,
        ),
    ]
