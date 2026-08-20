"""Multi-agent team notes and coordinate synthesis vectors."""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    coordination_wait,
    message_end,
    message_start,
    run_completed,
    run_context,
    run_output_delta,
    run_plan,
    run_progress,
    run_started,
    team_note_posted,
    team_synthesis_preview,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE, _ctx_block


def _multi_agent_team_notes() -> list[SSEEvent]:
    """多 Agent·通·便签墙 (§2.2)：并行两队员把【给队友看的】决定 / 提醒 / 认领贴到团队便签墙。

    两个 run 无依赖（同波并行）：研究员先贴一条 ``decision``（定了别人要依赖的接口），撰写员再贴
    一条 ``heads_up``（提个醒一个坑），随后撰写员贴一条 ``claim``（我领了——认领一块活免得撞活，
    WriteCoordinator 的台面化对偶）。三条都 journaled，折到 ProjectedTurn.teamNotes（按贴出顺序、
    按 noteId 去重）——验三端 fold 对 team_note_posted 三类 kind 的投影一致（与图节点正交：notes 不进
    runs/process，只进 teamNotes）。便签是顺手广播，不改回合 paused / pending 态。"""
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
    # Both depend on nothing → they run in the SAME wave (真并行), which is exactly when the
    # note wall matters (siblings can see each other's notes mid-flight).
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
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}], "coordinate": False},
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
        # 研究员 broadcasts a DECISION its teammate must align on (an interface contract).
        team_note_posted(
            execution_id="exec1",
            note_id="n1",
            run_id="r1",
            agent_id="w1",
            role="研究员",
            kind="decision",
            text="接口定了：GET /items 返回 {items:[], next_cursor}",
            ts=1_700_000_000.0,
        ),
        # 撰写员 broadcasts a HEADS-UP (a pitfall it hit) for the others.
        team_note_posted(
            execution_id="exec1",
            note_id="n2",
            run_id="r2",
            agent_id="w2",
            role="撰写员",
            kind="heads_up",
            text="提个醒：示例里的时间一律用 ISO8601，别用本地格式",
            ts=1_700_000_001.0,
        ),
        # 撰写员 CLAIMS a piece of work so a sibling doesn't duplicate it (我领了 — the proactive,
        # visible counterpart of WriteCoordinator's hard file guard).
        team_note_posted(
            execution_id="exec1",
            note_id="n3",
            run_id="r2",
            agent_id="w2",
            role="撰写员",
            kind="claim",
            text="示例文档这部分我来写，别人不用重复",
            ts=1_700_000_002.0,
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
        tool_use_end("dc1", "delegate", success=True, output="团队完成 2 项任务。"),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4200, output_tokens=820, cost=_COST),
    ]


def _multi_agent_team_notes_amended() -> list[SSEEvent]:
    """多 Agent·通·便签墙·改写/作废 (§2.2 便签会过期 → supersession)：队员更正自己贴过的便签。

    便签会过期：研究员先贴一条 decision（字段用 password），随后【改写】它（字段改用 pwd）——旧便签 n1
    被标 superseded、新便签 n3 携 ``supersedes=n1`` ``supersede_mode=update``；撰写员贴一条 heads_up 后
    【作废】它——旧便签 n2 被标 voided、撤回便签 n4 携 ``supersedes=n2`` ``supersede_mode=void``。验三端
    fold 一致：amendment 事件把【目标】便签标 superseded/voided（不是把 amendment 自己标），amendment 自己
    active 且携 supersedes 指回来源——这样陈旧决定不会一直误导队友。"""
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
            {"tasks": [{"role": "研究员"}, {"role": "撰写员"}], "coordinate": False},
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
        # 研究员 broadcasts a DECISION, then realizes it changed and 改写s it (password → pwd):
        # n1 becomes superseded, n3 carries the corrected decision + supersedes=n1.
        team_note_posted(
            execution_id="exec1",
            note_id="n1",
            run_id="r1",
            agent_id="w1",
            role="研究员",
            kind="decision",
            text="登录字段用 password",
            ts=1_700_000_000.0,
        ),
        team_note_posted(
            execution_id="exec1",
            note_id="n3",
            run_id="r1",
            agent_id="w1",
            role="研究员",
            kind="decision",
            text="登录字段改用 pwd（替代 password）",
            ts=1_700_000_002.0,
            supersedes="n1",
            supersede_mode="update",
        ),
        # 撰写员 broadcasts a HEADS-UP, then 作废s it (no replacement): n2 becomes voided, n4 is the
        # retraction notice + supersedes=n2 / mode=void.
        team_note_posted(
            execution_id="exec1",
            note_id="n2",
            run_id="r2",
            agent_id="w2",
            role="撰写员",
            kind="heads_up",
            text="示例时间用本地格式",
            ts=1_700_000_001.0,
        ),
        team_note_posted(
            execution_id="exec1",
            note_id="n4",
            run_id="r2",
            agent_id="w2",
            role="撰写员",
            kind="heads_up",
            text="撤回之前那条：示例时间用本地格式",
            ts=1_700_000_003.0,
            supersedes="n2",
            supersede_mode="void",
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
        tool_use_end("dc1", "delegate", success=True, output="团队完成 2 项任务。"),
        content_delta(" 团队已完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4300, output_tokens=860, cost=_COST),
    ]


def _multi_agent_team_notes_ceo_seed() -> list[SSEEvent]:
    """多 Agent·通·便签墙 Phase 2：CEO ``seed_notes`` + ``team_brief``。

    主 Agent 在首波 worker 开跑前播种两条便签（``source=ceo``，``run_id=__ceo_seed__``），并行审查员
    开局 ``run_context`` 还收到回合级 ``team_brief`` 块；其中一名审查员再贴 ``heads_up`` 警示。
    验三端 fold：``teamNotes`` 保留 CEO 来源 + worker 便签顺序；``receivedContext`` 含 ``team_brief``。"""
    agents = [
        {
            "id": "w1",
            "role": "方向审查",
            "thinking": True,
        },
        {
            "id": "w2",
            "role": "事实审查",
            "thinking": True,
        },
    ]
    plan_runs = [
        {"id": "r1", "agent_id": "w1", "task": "审查方向与受众", "depends_on": []},
        {"id": "r2", "agent_id": "w2", "task": "核查事实与引用", "depends_on": []},
    ]
    brief = "受众：技术初学者；篇幅约 1500 字；风格科普向，避免公式堆砌。"
    brief_block = _ctx_block(
        "team_brief",
        "团队共识（主协调为本回合设定，全员遵循）",
        brief,
    )
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我来安排并行审查。"),
        tool_use_start(
            "dc1",
            "delegate",
            {
                "tasks": [{"role": "方向审查"}, {"role": "事实审查"}],
                "coordinate": False,
                "seed_notes": [
                    {"kind": "decision", "text": "整体方向：科普向，不讲推导"},
                    {"kind": "heads_up", "text": "篇幅硬上限 1500 字"},
                ],
                "team_brief": brief,
            },
        ),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="简介稿并行审查",
            agents=agents,
            runs=plan_runs,
        ),
        # CEO seed_notes land before the first worker wave (same batch wall).
        team_note_posted(
            execution_id="exec1",
            note_id="n0",
            run_id="__ceo_seed__",
            agent_id="ceo",
            role="主协调",
            kind="decision",
            text="整体方向：科普向，不讲推导",
            ts=1_699_999_998.0,
            source="ceo",
        ),
        team_note_posted(
            execution_id="exec1",
            note_id="n1",
            run_id="__ceo_seed__",
            agent_id="ceo",
            role="主协调",
            kind="heads_up",
            text="篇幅硬上限 1500 字",
            ts=1_699_999_999.0,
            source="ceo",
        ),
        run_started("r1", "w1"),
        run_context(
            "r1",
            "w1",
            [
                _ctx_block(
                    "request",
                    "原始用户请求（老板交给整个团队的目标，不一定全是你的活；你的具体职责见下方「你的任务」）",
                    "写一篇向量数据库科普简介。",
                ),
                _ctx_block(
                    "team_position",
                    "你在团队中的位置",
                    "并行队友：事实审查（核查事实与引用）。你的产出汇总给老板。",
                ),
                brief_block,
                _ctx_block("task", "你的任务", "审查方向与受众"),
            ],
        ),
        run_started("r2", "w2"),
        run_context(
            "r2",
            "w2",
            [
                _ctx_block(
                    "request",
                    "原始用户请求（老板交给整个团队的目标，不一定全是你的活；你的具体职责见下方「你的任务」）",
                    "写一篇向量数据库科普简介。",
                ),
                _ctx_block(
                    "team_position",
                    "你在团队中的位置",
                    "并行队友：方向审查（审查方向与受众）。你的产出汇总给老板。",
                ),
                brief_block,
                _ctx_block("task", "你的任务", "核查事实与引用"),
            ],
        ),
        team_note_posted(
            execution_id="exec1",
            note_id="n2",
            run_id="r1",
            agent_id="w1",
            role="方向审查",
            kind="heads_up",
            text="提个醒：开篇别堆公式，先讲直觉",
            ts=1_700_000_001.0,
        ),
        run_output_delta("r1", "w1", "方向 OK"),
        run_completed(
            "r1",
            "w1",
            output_summary="方向审查完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_output_delta("r2", "w2", "事实 OK"),
        run_completed(
            "r2",
            "w2",
            output_summary="事实审查完成",
            duration_ms=950,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="并行审查完成。"),
        content_delta(" 审查完成。"),
        message_end(FinishReason.END_TURN, input_tokens=4100, output_tokens=780, cost=_COST),
    ]


def _multi_agent_coordinate() -> list[SSEEvent]:
    """多 Agent·CEO 协调模式：≥2 worker 并行 + 波内便签 + 合成草稿预览 + 收束。

    Wire 形状对齐 coordinate=true 路径的可见事件（非阻塞 delegate 立即返回后 CEO 继续
    ReAct）。单 worker 协调见 ``multi_agent_solo_coordinate_interjection``（无
    team_synthesis_preview）。本向量钉并行两队员、中途 team_note_posted、
    update_synthesis 推送的 team_synthesis_preview（P2 DURABLE——fold 同 key 保最新进
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
        team_note_posted(
            execution_id="exec1",
            note_id="n1",
            run_id="r1",
            agent_id="w1",
            role="研究员",
            kind="decision",
            text="接口定了：GET /items 返回 {items:[], next_cursor}",
            ts=1_700_000_000.0,
        ),
        # CEO update_synthesis → team_synthesis_preview（草稿正文在 text；workers 可空）。
        # P2 DURABLE：三端 fold 同 key 保最新 → ProjectedTurn.teamSynthesisPreview。
        team_synthesis_preview(
            execution_id="exec1",
            completed=0,
            total=2,
            headline="合成草稿更新 · 已完成 0/2",
            text="两边刚起步；接口方向按研究员便签对齐。",
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
