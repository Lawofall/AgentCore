"""两幕链：幕 1 MLR multi_agent + 幕 2 debate 新图 + prev（批 A2）。

跨回合：m1 完成四透镜+汇总 → m2 开辩 mint 新 ``execution_id``，``prev_execution_id``
链到幕 1；``run_plan.act`` = act-2 / debate / anchor=synthesizer。不再 divert /
``graph_append`` / 同 eid。oracle 最终投影以 m2 图为准（新 eid 重置 slot）。
"""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    debate_result,
    debate_round_started,
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
from ..debate._builders import (
    _moderator_agents_runs,
    _pro_con_debater_agents,
    _pro_con_debater_runs,
)

_TOPIC = "品牌是否应立即终止争议代言联名"
_EXEC_MLR = "exec_mlr_debate"
_EXEC_DEBATE = "exec_mlr_debate_act2"
_CAPTAIN_2 = "c2"
_MOD = "debate_mod_act2"
_PRO = f"{_MOD}_r1_pro"
_CON = f"{_MOD}_r1_con"

def _mlr_agents_runs() -> tuple[list[dict], list[dict]]:
    lenses = (
        ("lens_0", "法律视角", "梳理解约条款"),
        ("lens_1", "品牌商业视角", "评估营收冲击"),
        ("lens_2", "舆情公关视角", "盘点声量窗口"),
        ("lens_3", "文化社会视角", "分析圈层冲突"),
    )
    agents = [
        {
            "id": rid,
            "role": role,
            "thinking": True,
        }
        for rid, role, _t in lenses
    ]
    agents.append(
        {
            "id": "synthesizer",
            "role": "汇总分析师",
            "thinking": True,
        }
    )
    runs = [
        {"id": rid, "agent_id": rid, "task": task, "depends_on": []}
        for rid, _r, task in lenses
    ]
    runs.append(
        {
            "id": "synthesizer",
            "agent_id": "synthesizer",
            "task": f"交叉验证综述 · {_TOPIC}",
            "depends_on": [rid for rid, _r, _t in lenses],
        }
    )
    return agents, runs


def _multi_agent_mlr_debate_acts() -> list[SSEEvent]:
    """幕1 MLR 完成 → 幕2 辩论新图 + prev（act-2 / anchor=synthesizer）。"""
    mlr_agents, mlr_runs = _mlr_agents_runs()
    mod_agents, mod_runs = _moderator_agents_runs(
        _MOD, _CAPTAIN_2, f"主持正反辩论：{_TOPIC}"
    )
    # 新图：parent = 本回合 captain（幕因果经 act.anchor + prev）。
    mod_runs = [
        {
            **mod_runs[0],
            "parent_run_id": _CAPTAIN_2,
        }
    ]
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
        "opening": "先从法律风险与舆情窗口切入。",
        "narrative_first": False,
        "sides": [
            {
                "key": "pro",
                "name": "立即终止方",
                "stance": "应立刻切割止损",
                "is_subject": False,
            },
            {
                "key": "con",
                "name": "冷静观望方",
                "stance": "证据未定不宜仓促解约",
                "is_subject": False,
            },
        ],
        "rounds": [
            {
                "round_no": 1,
                "focus": "立即终止 vs 观望",
                "summary": "双方争点收敛到举证门槛与沉默成本。",
                "verdict": {
                    "real_clash": True,
                    "new_arguments": False,
                    "converged": True,
                    "stop_reason": "核心分歧已充分暴露。",
                    "rationale": "继续无新增信息。",
                },
                "sides": [
                    {
                        "key": "pro",
                        "name": "立即终止方",
                        "run_id": _PRO,
                        "ok": True,
                    },
                    {
                        "key": "con",
                        "name": "冷静观望方",
                        "run_id": _CON,
                        "ok": True,
                    },
                ],
                "clashes": [],
                "cross_exam": [],
                "scores": {},
            }
        ],
        "brief": {
            "crux": "立即终止联名是否必要",
            "strongest_points": {"pro": "沉默即纵容", "con": "举证门槛高"},
            "handoffs": [
                {"kind": "value", "text": "声誉 vs 财务"},
            ],
            "leaning": "视举证强度",
            "confidence": "中",
            "recommendation": "先核合同再定",
            "decisive": "举证门槛决定是否立即切割",
        },
        "closings": [],
    }
    return [
        # ── 回合 1：幕 1 MLR ──
        message_start("m1", conversation_id=_CONV),
        content_delta(f"先做多视角调研：{_TOPIC}。"),
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
            execution_id=_EXEC_MLR,
            plan_type="multi_agent",
            task_summary=f"多视角深度调研：{_TOPIC}",
            agents=mlr_agents,
            runs=mlr_runs,
            act={
                "act_id": "act-1",
                "kind": "multi_agent",
                "title": "多视角调研",
            },
        ),
        *[
            ev
            for rid in ("lens_0", "lens_1", "lens_2", "lens_3")
            for ev in (
                run_started(rid, rid),
                run_output_delta(rid, rid, f"{rid} 要点就绪"),
                run_completed(
                    rid,
                    rid,
                    output_summary=f"{rid} 完成",
                    duration_ms=800,
                    role="member",
                    model="deepseek-v4-flash",
                    usage=_USAGE,
                    cost=_COST,
                ),
            )
        ],
        run_started("synthesizer", "synthesizer"),
        run_output_delta("synthesizer", "synthesizer", "交叉验证完成，建议开辩。"),
        run_completed(
            "synthesizer",
            "synthesizer",
            output_summary="汇总完成并附命题卡",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="多视角调研完成。"),
        content_delta("调研已呈报，建议开辩。"),
        message_end(FinishReason.END_TURN, input_tokens=5000, output_tokens=900, cost=_COST),
        # ── 回合 2：幕 2 辩论新图 + prev ──
        message_start("m2", conversation_id=_CONV),
        content_delta("按命题卡开辩。"),
        tool_use_start(
            "db1",
            "debate",
            {
                "motion": _TOPIC,
                "form": "debate",
                "sides": [
                    {"key": "pro", "name": "立即终止方", "stance": "应立刻切割止损"},
                    {"key": "con", "name": "冷静观望方", "stance": "证据未定不宜仓促解约"},
                ],
            },
        ),
        run_plan(
            execution_id=_EXEC_DEBATE,
            plan_type="debate",
            task_summary=f"正反辩论：{_TOPIC}",
            agents=mod_agents,
            runs=mod_runs,
            prev_execution_id=_EXEC_MLR,
            act={
                "act_id": "act-2",
                "kind": "debate",
                "title": "辩论对抗",
                "anchor_run_id": "synthesizer",
                "authorized_by": "preview",
            },
        ),
        run_started(_MOD, _MOD, parent_run_id=_CAPTAIN_2),
        debate_round_started(
            execution_id=_EXEC_DEBATE,
            moderator_run_id=_MOD,
            round_no=1,
            focus="立即终止 vs 观望",
            cross_exam_enabled=False,
            opening="先从法律风险与舆情窗口切入。",
            form="debate",
        ),
        run_plan(
            execution_id=_EXEC_DEBATE,
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
            prev_execution_id=_EXEC_MLR,
            act={
                "act_id": "act-2",
                "kind": "debate",
                "title": "辩论对抗",
                "anchor_run_id": "synthesizer",
                "authorized_by": "preview",
            },
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
        run_output_delta(_CON, _CON, "举证不足不宜仓促解约。"),
        run_completed(
            _CON,
            _CON,
            output_summary="冷静观望方立论完成",
            duration_ms=950,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            _MOD,
            _MOD,
            output_summary="1 轮·收敛",
            duration_ms=2000,
            role="主持人",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(execution_id=_EXEC_DEBATE, moderator_run_id=_MOD, payload=debate_payload),
        tool_use_end("db1", "debate", success=True, output="辩论完成。"),
        content_delta("辩论收束，决策简报已呈。"),
        message_end(FinishReason.END_TURN, input_tokens=6000, output_tokens=1100, cost=_COST),
    ]
