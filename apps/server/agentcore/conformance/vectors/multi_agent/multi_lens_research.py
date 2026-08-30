"""多视角深度调研（幕 1）端到端合成向量。

对齐历史多维调研图（录制时具名 ``lens_crosscheck``）：CEO delegate →
4 异质透镜并行 → 汇总分析师 depends_on 四路（handoff 携 ``motion_card``）→
CEO 收尾呈报「建议开辩」（开辩入口是 stage_card，不是 followups chips）。

流式中间态刻意留足：四路并行推进中、汇总未开跑、汇总进行中。
"""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    message_end,
    message_start,
    run_completed,
    run_output_delta,
    run_plan,
    run_progress,
    run_reasoning_delta,
    run_started,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE

_TOPIC = "某消费品牌是否应在争议代言人事件后继续联名"

_MOTION_CARD = {
    "motion": "品牌是否应立即终止与该代言人的联名合作",
    "sides": [
        {"key": "terminate", "name": "立即终止方", "stance": "应立刻切割止损"},
        {"key": "hold", "name": "冷静观望方", "stance": "证据未定不宜仓促解约"},
    ],
    "fact_pointers": ["#r1", "#r3", "notes/endorsement.md"],
    "rationale": "法律风险与品牌声誉的取舍无法靠继续取证收敛，必须对抗检验价值判断。",
    "form": "debate",
}

_LENSES = (
    ("lens_0", "法律视角", "梳理代言合同解约条款、连带责任与监管口径"),
    ("lens_1", "品牌商业视角", "评估联名营收贡献、库存与渠道契约成本"),
    ("lens_2", "舆情公关视角", "盘点舆论热度、声量拐点与危机公关窗口"),
    ("lens_3", "文化社会视角", "分析代言人象征意义与圈层价值冲突"),
)


def _agents() -> list[dict]:
    agents = [
        {
            "id": rid,
            "role": role,
            "thinking": True,
        }
        for rid, role, _task in _LENSES
    ]
    agents.append(
        {
            "id": "synthesizer",
            "role": "汇总分析师",
            "thinking": True,
        }
    )
    return agents


def _plan_runs() -> list[dict]:
    runs = [
        {"id": rid, "agent_id": rid, "task": task, "depends_on": []}
        for rid, _role, task in _LENSES
    ]
    lens_ids = [rid for rid, _role, _task in _LENSES]
    runs.append(
        {
            "id": "synthesizer",
            "agent_id": "synthesizer",
            "task": f"交叉验证综述（共识/冲突/分歧；必要时附建议开辩命题卡）· {_TOPIC}",
            "depends_on": lens_ids,
        }
    )
    return runs


def _multi_agent_multi_lens_research() -> list[SSEEvent]:
    """幕 1 全链路：delegate → 4 透镜并行流式 → 汇总+motion_card → CEO 建议开辩。"""
    agents = _agents()
    plan_runs = _plan_runs()
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta(f"我将按多视角深度调研推进「{_TOPIC}」。"),
        tool_use_start(
            "dc1",
            "delegate",
            {
                "playbook": "lens_crosscheck",
                "playbook_args": {"topic": _TOPIC},
                "coordinate": False,
            },
        ),
        run_plan(
            execution_id="exec_mlr1",
            plan_type="multi_agent",
            task_summary=f"多视角深度调研：{_TOPIC}",
            agents=agents,
            runs=plan_runs,
        ),
        # ── 波 1：四路透镜并行开跑（汇总仍排队）──────────────────────────
        run_started("lens_0", "lens_0"),
        run_started("lens_1", "lens_1"),
        run_started("lens_2", "lens_2"),
        run_started("lens_3", "lens_3"),
        run_reasoning_delta("lens_0", "lens_0", "先核对代言合同解约与连带责任条款…"),
        run_output_delta("lens_0", "lens_0", "【法律】合同含道德条款，解约需举证重大声誉损害；"),
        run_output_delta("lens_1", "lens_1", "【品牌商业】联名贡献约 18% 季度营收，渠道有最低采购约束；"),
        run_output_delta("lens_2", "lens_2", "【舆情公关】负面声量 72h 内翻倍，品牌官号评论区失控；"),
        run_reasoning_delta("lens_3", "lens_3", "代言人象征意义与品牌调性是否仍兼容…"),
        run_output_delta("lens_3", "lens_3", "【文化社会】圈层撕裂明显，年轻用户要求切割，铁粉要求澄清；"),
        # 交错推进：部分完成、部分仍 running（汇总未开）
        run_output_delta("lens_0", "lens_0", "监管口径偏谨慎，仓促解约亦有违约风险。"),
        run_completed(
            "lens_0",
            "lens_0",
            output_summary="法律透镜：解约可行但举证门槛高",
            duration_ms=2100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            debrief={
                "summary": "解约可行但举证门槛高，仓促动作亦有违约风险",
                "key_points": ["道德条款可援引", "举证声誉损害是关键"],
                "next_steps": "汇总时对照舆情时间线评估举证难度",
            },
        ),
        run_progress(1, 5),
        run_output_delta("lens_1", "lens_1", "库存与退货成本估算约两千万量级。"),
        run_completed(
            "lens_1",
            "lens_1",
            output_summary="品牌商业透镜：短期财务代价高",
            duration_ms=2400,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            debrief={
                "summary": "继续联名保营收，终止则短期财务冲击大",
                "key_points": ["联名约占 18% 季度营收", "渠道最低采购约束"],
            },
        ),
        run_progress(2, 5),
        run_output_delta("lens_2", "lens_2", "危机窗口约 5–7 天，沉默会被解读为纵容。"),
        run_completed(
            "lens_2",
            "lens_2",
            output_summary="舆情公关透镜：沉默成本高于表态成本",
            duration_ms=1900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            debrief={
                "summary": "负面声量持续攀升，沉默会被解读为纵容",
                "key_points": ["72h 声量翻倍", "危机窗口约一周"],
            },
        ),
        run_progress(3, 5),
        # 三路已完、lens_3 仍 running、synthesizer 因 depends_on 未开 —— 关键中间态
        run_output_delta(
            "lens_3",
            "lens_3",
            "价值冲突难靠更多事实消解，更接近立场对抗。",
        ),
        run_completed(
            "lens_3",
            "lens_3",
            output_summary="文化社会透镜：价值冲突难靠取证收敛",
            duration_ms=2200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
            debrief={
                "summary": "圈层撕裂对应价值对立，继续取证难以收敛",
                "key_points": ["年轻用户要求切割", "铁粉要求澄清"],
            },
        ),
        run_progress(4, 5),
        # ── 波 2：汇总分析师收口（可产 motion_card）──────────────────────
        run_started("synthesizer", "synthesizer"),
        run_reasoning_delta(
            "synthesizer",
            "synthesizer",
            "四路已齐：标清共识/冲突，判断是否必须对抗开辩…",
        ),
        run_output_delta(
            "synthesizer",
            "synthesizer",
            "### 交叉验证\n**共识**：事件已实质伤害品牌声誉，需在一周内给出清晰立场。\n",
        ),
        run_output_delta(
            "synthesizer",
            "synthesizer",
            "**冲突**：法律侧强调举证与违约风险；舆情侧强调沉默即纵容；商业侧强调短期财务代价。\n",
        ),
        run_output_delta(
            "synthesizer",
            "synthesizer",
            "**分歧**：是否「立即终止联名」是价值判断，继续调研无法收敛。\n",
        ),
        run_completed(
            "synthesizer",
            "synthesizer",
            output_summary="交叉验证完成；核心争议需开辩检验",
            duration_ms=2800,
            role="member",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
            debrief={
                "summary": "四路交叉后，是否立即终止联名是必须对抗检验的核心争议",
                "key_points": [
                    "共识：一周内需清晰立场",
                    "冲突：法律举证 vs 舆情窗口 vs 商业代价",
                    "分歧：立即终止 vs 冷静观望",
                ],
                "assumptions": "争议事实以公开报道与合同摘要为准，未做内部审计",
                "next_steps": "若用户同意，建议就命题开一场正反辩论",
                "motion_card": _MOTION_CARD,
            },
        ),
        run_progress(5, 5),
        tool_use_end(
            "dc1",
            "delegate",
            success=True,
            output="多视角调研完成：4 透镜 + 汇总；汇总员提交了建议开辩命题卡。",
        ),
        # CEO 收尾正文（默认模式：呈报建议开辩，本回合不直接调 debate）
        content_delta(
            "\n\n## 多视角调研结论\n\n"
            f"围绕「{_TOPIC}」，四路透镜与汇总已完成交叉验证。\n\n"
            "**共识**：事件已实质伤害品牌声誉，需在约一周内给出清晰立场。\n"
            "**冲突**：法律侧强调举证与违约风险；舆情侧强调沉默成本；商业侧强调短期财务冲击。\n"
            "**缺口**：内部合同全文与库存明细尚未核验。\n\n"
            "### 建议开辩\n\n"
            f"**命题**：{_MOTION_CARD['motion']}\n"
            "- 立即终止方：应立刻切割止损\n"
            "- 冷静观望方：证据未定不宜仓促解约\n"
            f"**为何必须对抗**：{_MOTION_CARD['rationale']}\n\n"
            "若你同意，可开一场正反辩论；本回合我不会直接开辩，请你拍板。"
        ),
        message_end(FinishReason.END_TURN, input_tokens=9200, output_tokens=1600, cost=_COST),
    ]
