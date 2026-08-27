"""批 D1 · 证人模式：幕1 MLR + 幕2 辩论同图，主持人点名证人答问。

向量形状对齐 ``mlr_debate_acts`` / ``multi_agent_debate``：两幕同图 + 证人席位节点
+ ``witness_exam`` 进 ``debate_round`` / ``debate_result``；答问 run 挂席位下
（``continues_run_id``=席位根，``parent_run_id``=席位），不把拍挂到幕1 透镜。
"""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    debate_result,
    debate_round,
    debate_round_started,
    message_end,
    message_start,
    run_completed,
    run_context,
    run_output_delta,
    run_plan,
    run_started,
    tool_use_end,
    tool_use_start,
)

from .._common import _CONV, _COST, _USAGE, _ctx_block
from ..debate._builders import (
    _moderator_agents_runs,
    _pro_con_debater_agents,
    _pro_con_debater_runs,
)

_TOPIC = "品牌是否应立即终止争议代言联名"
_EXEC_MLR = "exec_mlr_debate_witness"
_EXEC_DEBATE = "exec_mlr_debate_witness_act2"
_CAPTAIN_2 = "c2"
# Compat alias for MLR frames still using _EXEC in builders
_EXEC = _EXEC_MLR
_MOD = "debate_mod_wit"
_PRO = f"{_MOD}_r1_pro"
_CON = f"{_MOD}_r1_con"
_WIT_SEAT = f"{_MOD}_wit_lens_0"
_WIT_ANS = f"{_MOD}_r1_wit_lens_0"


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


def _multi_agent_mlr_debate_witness() -> list[SSEEvent]:
    """幕1 MLR → 幕2 辩论 + 证人点名答问（批 D1）。"""
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
    witness_agents = [
        {
            "id": _WIT_SEAT,
            "role": "证人·法律",
            "thinking": True,
        }
    ]
    witness_runs = [
        {
            "id": _WIT_SEAT,
            "agent_id": _WIT_SEAT,
            "task": "来自幕1·法律；不占辩席，仅在主持人点名时回答事实性问题。",
            "depends_on": [],
            "parent_run_id": _MOD,
            "group": "debate:witness",
        }
    ]
    round_payload = {
        "round_no": 1,
        "focus": "解除条款是否成立",
        "summary": "双方对合同第十二条效力各执一词；证人澄清原文后争点收敛。",
        "verdict": {
            "real_clash": True,
            "new_arguments": False,
            "converged": True,
            "stop_reason": "核心事实已澄清。",
            "rationale": "证人答问闭合条款原文争议。",
        },
        "sides": [
            {"key": "pro", "name": "立即终止方", "run_id": _PRO, "ok": True},
            {"key": "con", "name": "冷静观望方", "run_id": _CON, "ok": True},
        ],
        "clashes": [],
        "cross_exam": [
            {
                "target": "pro",
                "questioner": "",
                "exchanges": [
                    {
                        "question": "你凭哪条主张可立即解除？",
                        "answer": "依据合同第十二条解除权。",
                    }
                ],
                "answer_run_id": f"{_MOD}_r1_cx_pro",
            }
        ],
        "witness_exam": [
            {
                "witness_key": "lens_0",
                "lens_run_id": "lens_0",
                "seat_run_id": _WIT_SEAT,
                "name": "证人·法律",
                "origin_caption": "来自幕1·法律",
                "exchanges": [
                    {
                        "question": "合同第十二条原文如何表述解除条件？",
                        "answer": "第十二条写明「严重损害品牌声誉时可单方解除」。",
                    }
                ],
                "answer_run_id": _WIT_ANS,
            }
        ],
        "scores": {},
        "evidence_ledger_delta": [
            {
                "id": "#e1",
                "url": "",
                "title": "证人·法律：合同第十二条原文如何表述解除条件？",
                "snippet": "第十二条写明「严重损害品牌声誉时可单方解除」。",
                "site": "",
                "date": "",
                "tier": "unknown",
                "side_key": "witness:lens_0",
            },
            {
                "id": "#e2",
                "url": "https://court.example/contract",
                "title": "法律 · #r1",
                "snippet": "约定文档 AgentCore/文档/research/法律透镜报告.md · 幕1 #r1",
                "site": "法律",
                "date": "",
                "tier": "unknown",
                "side_key": "dossier",
                "dossier_path": "AgentCore/文档/research/法律透镜报告.md",
                "origin_id": "#r1",
                "dossier_label": "法律",
            },
        ],
    }
    debate_payload = {
        "form": "debate",
        "motion": _TOPIC,
        "stop_reason": "converged",
        "opening": "先核清解除条款事实再谈立场。",
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
        "rounds": [{k: v for k, v in round_payload.items() if k != "evidence_ledger_delta"}],
        "witnesses": [
            {
                "key": "lens_0",
                "name": "证人·法律",
                "lens_run_id": "lens_0",
                "seat_run_id": _WIT_SEAT,
                "lens_label": "法律",
                "origin_caption": "来自幕1·法律",
            }
        ],
        "brief": {
            "crux": "第十二条是否覆盖本案",
            "strongest_points": {
                "pro": "原文含声誉损害解除权",
                "con": "「严重」门槛未满足",
            },
            "handoffs": [{"kind": "value", "text": "声誉阈值由谁定"}],
            "leaning": "条款存在但严重度仍须拍板",
            "confidence": "中",
            "recommendation": "引用证人澄清的原文，再由用户定严重度",
            "decisive": "证人闭合了原文争议",
        },
        "closings": [],
        "evidence_ledger": round_payload["evidence_ledger_delta"],
    }
    cx_pro = f"{_MOD}_r1_cx_pro"
    return [
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
            execution_id=_EXEC,
            plan_type="multi_agent",
            task_summary=f"多视角深度调研：{_TOPIC}",
            agents=mlr_agents,
            runs=mlr_runs,
            act={"act_id": "act-1", "kind": "multi_agent", "title": "多视角调研"},
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
                "authorized_by": "stage_card",
            },
        ),
        run_started(_MOD, _MOD, parent_run_id=_CAPTAIN_2),
        # 证人席位（辩论幕内）：声明后即开跑占位，避免收场被标 skipped、两端 fold 序漂移。
        run_plan(
            execution_id=_EXEC_DEBATE,
            plan_type="debate",
            task_summary="",
            agents=witness_agents,
            runs=witness_runs,
            prev_execution_id=_EXEC_MLR,
            act={
                "act_id": "act-2",
                "kind": "debate",
                "title": "辩论对抗",
                "anchor_run_id": "synthesizer",
                "authorized_by": "stage_card",
            },
        ),
        run_started(
            _WIT_SEAT,
            _WIT_SEAT,
            parent_run_id=_MOD,
            group="debate:witness",
        ),
        debate_round_started(
            execution_id=_EXEC_DEBATE,
            moderator_run_id=_MOD,
            round_no=1,
            focus="解除条款是否成立",
            cross_exam_enabled=True,
            opening="先核清解除条款事实再谈立场。",
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
                "authorized_by": "stage_card",
            },
        ),
        # 辩手 agent_id 用 plan 卡 id（d_pro/d_con），与 multi_agent_debate 同形——
        # 避免桌面 fold 为 run_id 再铸一份 agent、打乱 agents[] 序。
        run_started(
            _PRO,
            "d_pro",
            parent_run_id=_MOD,
            stance="pro",
            group="debate:debate",
            round_no=1,
        ),
        run_output_delta(
            _PRO,
            "d_pro",
            "第十二条可解除【已核实·#e2】，应立即终止。",
        ),
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
            _CON,
            "d_con",
            parent_run_id=_MOD,
            stance="con",
            group="debate:debate",
            round_no=1,
        ),
        run_output_delta(_CON, "d_con", "第十二条不适用本案。"),
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
        # 质询作答（辩手）
        run_started(
            cx_pro,
            cx_pro,
            parent_run_id=_MOD,
            continues_run_id=_PRO,
            stance="pro",
            group="debate:debate",
            round_no=1,
            side_key="pro",
        ),
        run_context(
            cx_pro,
            cx_pro,
            [
                _ctx_block("task", "质询环节", "请正面回答"),
                _ctx_block(
                    "cross_exam",
                    "第 1 轮 · 质询（必须正面回答）",
                    "- 你凭哪条主张可立即解除？",
                ),
            ],
        ),
        run_output_delta(cx_pro, cx_pro, "依据合同第十二条解除权。"),
        run_completed(
            cx_pro,
            cx_pro,
            output_summary="正方质询作答",
            duration_ms=400,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        # 证人答问：continues=席位根（辩论幕），parent=席位
        run_started(
            _WIT_ANS,
            _WIT_ANS,
            parent_run_id=_WIT_SEAT,
            continues_run_id=_WIT_SEAT,
            group="debate:witness",
            round_no=1,
            side_key="witness:lens_0",
        ),
        run_context(
            _WIT_ANS,
            _WIT_ANS,
            [
                _ctx_block("task", "证人答问", "请据幕1 调研记忆作答"),
                _ctx_block(
                    "witness_exam",
                    "第 1 轮 · 证人答问（事实性问题）",
                    "- 合同第十二条原文如何表述解除条件？",
                ),
            ],
        ),
        run_output_delta(
            _WIT_ANS,
            _WIT_ANS,
            "### 质询一\n第十二条写明「严重损害品牌声誉时可单方解除」。",
        ),
        run_completed(
            _WIT_ANS,
            _WIT_ANS,
            output_summary="证人·法律答问完成",
            duration_ms=500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_round(
            execution_id=_EXEC_DEBATE,
            moderator_run_id=_MOD,
            payload=round_payload,
        ),
        run_completed(
            _WIT_SEAT,
            _WIT_SEAT,
            output_summary="证人·法律（来自幕1·法律）",
            duration_ms=100,
            role="证人·法律",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            _MOD,
            _MOD,
            output_summary="1 轮·收敛",
            duration_ms=2200,
            role="主持人",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(execution_id=_EXEC_DEBATE, moderator_run_id=_MOD, payload=debate_payload),
        tool_use_end("db1", "debate", success=True, output="辩论完成。"),
        content_delta("辩论收束；证人答问已进证据台账。"),
        message_end(FinishReason.END_TURN, input_tokens=6200, output_tokens=1200, cost=_COST),
    ]
