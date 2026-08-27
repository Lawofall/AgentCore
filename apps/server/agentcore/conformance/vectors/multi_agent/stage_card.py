"""批 B：阶段推进卡 — 展示 → 点开辩 → 辩论幕生长（authorized_by=stage_card）+ orphaned。"""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    debate_result,
    debate_round_started,
    interaction_orphaned,
    message_end,
    message_start,
    run_completed,
    run_output_delta,
    run_plan,
    run_started,
    stage_card_required,
    stage_card_resolved,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE
from ..debate._builders import (
    _moderator_agents_runs,
    _pro_con_debater_agents,
    _pro_con_debater_runs,
)

_TOPIC = "品牌是否应立即终止争议代言联名"
_EXEC_MLR = "exec_stage_card"
_EXEC_DEBATE = "exec_stage_card_act2"
_CAPTAIN_2 = "c2"
# Compat alias for MLR frames still using _EXEC in builders
_EXEC = _EXEC_MLR
_MOD = "debate_mod_sc"
_PRO = f"{_MOD}_r1_pro"
_CON = f"{_MOD}_r1_con"
_CARD = "sc_stage_1"

_SIDES = [
    {"key": "pro", "name": "立即终止方", "stance": "应立刻切割止损"},
    {"key": "con", "name": "冷静观望方", "stance": "证据未定不宜仓促解约"},
]


def _mlr_agents_runs() -> tuple[list[dict], list[dict]]:
    agents = [
        {
            "id": "lens_0",
            "role": "法律视角",
            "thinking": True,
        },
        {
            "id": "synthesizer",
            "role": "汇总分析师",
            "thinking": True,
        },
    ]
    runs = [
        {"id": "lens_0", "agent_id": "lens_0", "task": "梳理解约条款", "depends_on": []},
        {
            "id": "synthesizer",
            "agent_id": "synthesizer",
            "task": f"交叉验证综述 · {_TOPIC}",
            "depends_on": ["lens_0"],
        },
    ]
    return agents, runs


def _multi_agent_stage_card_start_debate() -> list[SSEEvent]:
    """推进卡展示 → resolved(start_debate) → 幕2 同图生长 authorized_by=stage_card。"""
    mlr_agents, mlr_runs = _mlr_agents_runs()
    mod_agents, mod_runs = _moderator_agents_runs(
        _MOD, "synthesizer", f"主持正反辩论：{_TOPIC}"
    )
    mod_runs = [{**mod_runs[0], "parent_run_id": _CAPTAIN_2}]
    debater_agents = _pro_con_debater_agents()
    debater_runs = _pro_con_debater_runs(
        _MOD,
        _PRO,
        _CON,
        pro_task="论证应立即终止联名",
        con_task="论证应冷静观望",
    )
    debate_payload = {
        "form": "debate",
        "motion": _TOPIC,
        "stop_reason": "converged",
        "opening": "先从法律风险切入。",
        "narrative_first": False,
        "sides": [
            {**_SIDES[0], "is_subject": False},
            {**_SIDES[1], "is_subject": False},
        ],
        "rounds": [],
        "brief": {
            "crux": "立即终止联名是否必要",
            "strongest_points": {"pro": "沉默即纵容", "con": "举证门槛高"},
            "leaning": "倾向冷静观望",
            "confidence": "中",
            "recommendation": "先核合同再定",
            "decisive": "举证门槛未达立即切割线",
            "handoffs": [{"kind": "value", "text": "声誉 vs 财务"}],
        },
        "closings": [],
    }
    act2 = {
        "act_id": "act-2",
        "kind": "debate",
        "title": "辩论对抗",
        "anchor_run_id": "synthesizer",
        "authorized_by": "stage_card",
    }
    return [
        message_start("m1", conversation_id=_CONV),
        tool_use_start(
            "dc1",
            "delegate",
            {"playbook": "lens_crosscheck", "playbook_args": {"topic": _TOPIC}},
        ),
        run_plan(
            execution_id=_EXEC,
            plan_type="multi_agent",
            task_summary=f"多视角调研：{_TOPIC}",
            agents=mlr_agents,
            runs=mlr_runs,
            act={
                "act_id": "act-1",
                "kind": "multi_agent",
                "title": "多视角调研",
            },
        ),
        run_started("lens_0", "lens_0"),
        run_completed(
            "lens_0",
            "lens_0",
            output_summary="法律视角完成",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("synthesizer", "synthesizer"),
        run_completed(
            "synthesizer",
            "synthesizer",
            output_summary="汇总完成并附命题卡",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
            debrief={
                "summary": "存在真对立轴",
                "motion_card": {
                    "motion": _TOPIC,
                    "sides": _SIDES,
                    "fact_pointers": ["#r1"],
                    "rationale": "各方握同一事实却价值对立，继续取证无效",
                    "form": "debate",
                },
            },
        ),
        tool_use_end("dc1", "delegate", success=True, output="多视角调研完成。"),
        content_delta("调研已呈报。"),
        stage_card_required(
            stage_card_id=_CARD,
            conversation_id=_CONV,
            motion=_TOPIC,
            sides=_SIDES,
            form="debate",
            rationale="各方握同一事实却价值对立，继续取证无效",
            fact_pointers=["#r1"],
            thorough=True,
            max_rounds=5,
            host_execution_id=_EXEC,
            synthesizer_run_id="synthesizer",
            host_message_id="m1",
        ),
        message_end(FinishReason.END_TURN, input_tokens=4000, output_tokens=700, cost=_COST),
        # ── 推进卡裁决 → 新回合机制直起辩论 ──
        message_start("m2", conversation_id=_CONV),
        stage_card_resolved(stage_card_id=_CARD, decision="start_debate", note=""),
        content_delta("按此开辩。"),
        run_plan(
            execution_id=_EXEC_DEBATE,
            plan_type="debate",
            task_summary=f"正反辩论：{_TOPIC}",
            agents=mod_agents,
            runs=mod_runs,
            prev_execution_id=_EXEC_MLR,
            act=act2,
        ),
        run_started(_MOD, _MOD, parent_run_id=_CAPTAIN_2),
        debate_round_started(
            execution_id=_EXEC_DEBATE,
            moderator_run_id=_MOD,
            round_no=1,
            focus="立即终止 vs 观望",
            cross_exam_enabled=False,
            opening="先从法律风险切入。",
            form="debate",
        ),
        run_plan(
            execution_id=_EXEC_DEBATE,
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
            prev_execution_id=_EXEC_MLR,
            act=act2,
        ),
        run_started(_PRO, _PRO, parent_run_id=_MOD, stance="pro", group="debate:debate", round_no=1),
        run_output_delta(_PRO, _PRO, "应立即终止以止损。"),
        run_completed(
            _PRO,
            _PRO,
            output_summary="立即终止方立论完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(_CON, _CON, parent_run_id=_MOD, stance="con", group="debate:debate", round_no=1),
        run_output_delta(_CON, _CON, "宜冷静观望。"),
        run_completed(
            _CON,
            _CON,
            output_summary="冷静观望方立论完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(
            execution_id=_EXEC_DEBATE,
            moderator_run_id=_MOD,
            payload=debate_payload,
        ),
        run_completed(
            _MOD,
            _MOD,
            output_summary="主持完成",
            duration_ms=2000,
            role="member",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        content_delta("## 辩论收报\n倾向冷静观望。"),
        message_end(FinishReason.END_TURN, input_tokens=6000, output_tokens=1200, cost=_COST),
    ]


def _multi_agent_stage_card_orphaned() -> list[SSEEvent]:
    """推进卡展示后下回合未调 debate/未起 MLR → 收尾 interaction_orphaned。"""
    mlr_agents, mlr_runs = _mlr_agents_runs()
    return [
        message_start("m1", conversation_id=_CONV),
        tool_use_start("dc1", "delegate", {"playbook": "lens_crosscheck"}),
        run_plan(
            execution_id=_EXEC_DEBATE,
            plan_type="multi_agent",
            task_summary=f"多视角调研：{_TOPIC}",
            agents=mlr_agents,
            runs=mlr_runs,
            act={"act_id": "act-1", "kind": "multi_agent", "title": "多视角调研"},
        ),
        run_started("lens_0", "lens_0"),
        run_completed(
            "lens_0",
            "lens_0",
            output_summary="法律视角完成",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started("synthesizer", "synthesizer"),
        run_completed(
            "synthesizer",
            "synthesizer",
            output_summary="汇总完成",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="完成"),
        content_delta("调研已呈报。"),
        stage_card_required(
            stage_card_id=_CARD,
            conversation_id=_CONV,
            motion=_TOPIC,
            sides=_SIDES,
            form="debate",
            rationale="真对立轴须对抗检验",
            fact_pointers=["#r1"],
            thorough=True,
            max_rounds=5,
            host_execution_id=_EXEC,
            synthesizer_run_id="synthesizer",
            host_message_id="m1",
        ),
        message_end(FinishReason.END_TURN, input_tokens=3000, output_tokens=500, cost=_COST),
        # 用户发消息不立即 orphan；本回合 CEO 既未调 debate 也未起 MLR → 收尾失效
        message_start("m2", conversation_id=_CONV),
        content_delta("好的，我们换个话题。"),
        message_end(FinishReason.END_TURN, input_tokens=200, output_tokens=40, cost=_COST),
        interaction_orphaned(interaction_id=_CARD, kind="stage_card"),
    ]
