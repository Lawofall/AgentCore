"""同回合两幕同一张图：一条助手消息里先 MLR 再 debate，共用 ``execution_id``。

对照 ``mlr_debate_acts``（跨回合新图 + prev）：本向量钉「一回合一张协作图」在辩论
接续上的同回合分支——``acts[]`` 两幕、单 eid、不写 ``prev_execution_id``。
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
_EXEC = "exec_same_turn_mlr_debate"
_CAPTAIN = "c1"
_MOD = "debate_mod_same"
_PRO = f"{_MOD}_r1_pro"
_CON = f"{_MOD}_r1_con"

_SIDES = [
    {"key": "pro", "name": "立即终止方", "stance": "应立刻切割止损"},
    {"key": "con", "name": "冷静观望方", "stance": "证据未定不宜仓促解约"},
]


def _mlr_agents_runs() -> tuple[list[dict], list[dict]]:
    agents = [
        {"id": "lens_0", "role": "法律视角", "thinking": True},
        {"id": "synthesizer", "role": "汇总分析师", "thinking": True},
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


def _multi_agent_same_turn_mlr_debate() -> list[SSEEvent]:
    """同一条消息：幕1 MLR 完成 → 幕2 辩论合入同一 execution_id。"""
    mlr_agents, mlr_runs = _mlr_agents_runs()
    mod_agents, mod_runs = _moderator_agents_runs(
        _MOD, _CAPTAIN, f"主持正反辩论：{_TOPIC}"
    )
    debater_agents = _pro_con_debater_agents()
    debater_runs = _pro_con_debater_runs(
        _MOD,
        _PRO,
        _CON,
        pro_task="论证应立即终止联名",
        con_task="论证应冷静观望",
    )
    act1 = {"act_id": "act-1", "kind": "multi_agent"}
    act2 = {
        "act_id": "act-2",
        "kind": "debate",
        "title": "正反辩论对抗",
        "anchor_run_id": "synthesizer",
        "authorized_by": "preview",
    }
    debate_payload = {
        "form": "debate",
        "motion": _TOPIC,
        "stop_reason": "converged",
        "opening": "先从法律风险与舆情窗口切入。",
        "narrative_first": False,
        "sides": [
            {**_SIDES[0], "is_subject": False},
            {**_SIDES[1], "is_subject": False},
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
            "handoffs": [{"kind": "value", "text": "声誉 vs 财务"}],
            "leaning": "视举证强度",
            "confidence": "中",
            "recommendation": "先核合同再定",
            "decisive": "举证门槛决定是否立即切割",
        },
        "closings": [],
    }
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta(f"先调研再开辩：{_TOPIC}。"),
        tool_use_start(
            "dc1",
            "delegate",
            {
                "playbook": "multi_lens_research",
                "playbook_args": {"topic": _TOPIC},
                "coordinate": False,
            },
        ),
        run_plan(
            execution_id=_EXEC,
            plan_type="multi_agent",
            task_summary=f"多视角深度调研：{_TOPIC}",
            agents=mlr_agents,
            runs=mlr_runs,
            act=act1,
        ),
        run_started("lens_0", "lens_0"),
        run_output_delta("lens_0", "lens_0", "lens_0 要点就绪"),
        run_completed(
            "lens_0",
            "lens_0",
            output_summary="lens_0 完成",
            duration_ms=800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
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
        content_delta("调研已呈报，本回合直接开辩。"),
        tool_use_start(
            "db1",
            "debate",
            {
                "motion": _TOPIC,
                "form": "debate",
                "sides": list(_SIDES),
            },
        ),
        run_plan(
            execution_id=_EXEC,
            plan_type="debate",
            task_summary=f"正反辩论：{_TOPIC}",
            agents=mod_agents,
            runs=mod_runs,
            act=act2,
        ),
        run_started(_MOD, _MOD, parent_run_id=_CAPTAIN),
        debate_round_started(
            execution_id=_EXEC,
            moderator_run_id=_MOD,
            round_no=1,
            focus="立即终止 vs 观望",
            cross_exam_enabled=False,
            opening="先从法律风险与舆情窗口切入。",
            form="debate",
        ),
        run_plan(
            execution_id=_EXEC,
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
            act=act2,
        ),
        run_started(
            _PRO, "d_pro", parent_run_id=_MOD, stance="pro", group="debate:debate", round_no=1
        ),
        run_output_delta(_PRO, "d_pro", "应立即终止以止损。"),
        run_completed(
            _PRO,
            "d_pro",
            output_summary="立即终止方立论完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(
            _CON, "d_con", parent_run_id=_MOD, stance="con", group="debate:debate", round_no=1
        ),
        run_output_delta(_CON, "d_con", "举证不足不宜仓促解约。"),
        run_completed(
            _CON,
            "d_con",
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
        debate_result(execution_id=_EXEC, moderator_run_id=_MOD, payload=debate_payload),
        tool_use_end("db1", "debate", success=True, output="辩论完成。"),
        content_delta("辩论收束，决策简报已呈。"),
        message_end(FinishReason.END_TURN, input_tokens=5500, output_tokens=1000, cost=_COST),
    ]
