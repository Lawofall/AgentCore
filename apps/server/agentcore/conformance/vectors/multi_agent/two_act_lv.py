"""批 R2 · LV 案量级两幕向量（幕级 LOD 验收素材）。

幕1 多视角调研**含子队**：4 透镜并行，其中「法律视角」是 lead，自己扇出 3 人子队
（合同条款 / 判例检索 / 监管咨询，``parent_run_id``=法律透镜，同 ``execution_id`` 合并子图）→
汇总分析师交叉验证。幕2 辩论**含证人 + 补派**：正反两轮对抗（第 2 轮为续派/补派）+ 每轮质询
折进宿主 + 结辩 + 主持人点名幕1「法律」证人答问。约 18 节点（幕1 八 · 幕2 十），供内嵌 + 全屏
``shoot:graph-probe`` 验单屏可读；**只增不改**既有向量/golden。

跨回合两幕同图：m1 完成 MLR → m2 开辩挂同一 ``execution_id``，``graph_append`` 锚点 synthesizer，
``run_plan.act`` = act-2 / debate / authorized_by=stage_card。
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
    _side_continue,
)

_TOPIC = "品牌是否应立即终止争议代言联名"
_EXEC_MLR = "exec_two_act_lv"
_EXEC_DEBATE = "exec_two_act_lv_act2"
_CAPTAIN_2 = "c2"
# Compat alias for MLR frames still using _EXEC in builders
_EXEC = _EXEC_MLR
_MOD = "debate_mod_lv"
_PRO = f"{_MOD}_r1_pro"
_CON = f"{_MOD}_r1_con"
_PRO_CX = f"{_MOD}_r1_cx_pro"
_PRO_R2 = f"{_MOD}_r2_pro"
_CON_R2 = f"{_MOD}_r2_con"
_PRO_CLOSE = f"{_MOD}_closing_pro"
_CON_CLOSE = f"{_MOD}_closing_con"
_WIT_SEAT = f"{_MOD}_wit_lens_0"
_WIT_ANS = f"{_MOD}_r1_wit_lens_0"

# 幕1 法律透镜的子队（lead → 3 子研究员）。
_LAW_SUB = (
    ("law_sub_0", "合同条款", "梳理第十二条解除权原文与触发要件"),
    ("law_sub_1", "判例检索", "检索同类联名解约判例与赔付区间"),
    ("law_sub_2", "监管咨询", "确认广告法/代言人新规的合规红线"),
)


_LENSES = (
    ("lens_0", "法律视角", "统筹法律尽调（下放子队分工）"),
    ("lens_1", "品牌商业视角", "评估营收冲击"),
    ("lens_2", "舆情公关视角", "盘点声量窗口"),
    ("lens_3", "文化社会视角", "分析圈层冲突"),
)


def _lens_agents_runs() -> tuple[list[dict], list[dict]]:
    agents = [
        {
            "id": rid,
            "role": role,
            "thinking": True,
        }
        for rid, role, _t in _LENSES
    ]
    runs = [
        {"id": rid, "agent_id": rid, "task": task, "depends_on": []}
        for rid, _r, task in _LENSES
    ]
    return agents, runs


def _synth_agent_run() -> tuple[list[dict], list[dict]]:
    agents = [
        {
            "id": "synthesizer",
            "role": "汇总分析师",
            "thinking": True,
        }
    ]
    runs = [
        {
            "id": "synthesizer",
            "agent_id": "synthesizer",
            "task": f"交叉验证综述 · {_TOPIC}",
            "depends_on": [rid for rid, _r, _t in _LENSES],
        }
    ]
    return agents, runs


def _law_subteam_agents_runs() -> tuple[list[dict], list[dict]]:
    agents = [
        {
            "id": rid,
            "role": role,
            "thinking": True,
        }
        for rid, role, _t in _LAW_SUB
    ]
    runs = [
        {
            "id": rid,
            "agent_id": rid,
            "task": task,
            "depends_on": [],
            "parent_run_id": "lens_0",
        }
        for rid, _r, task in _LAW_SUB
    ]
    return agents, runs


def _act1_events(act1: dict) -> list[SSEEvent]:
    """幕1：4 透镜并行（法律透镜含 3 人子队）→ 汇总分析师。

    ``run_started`` 顺序严格对齐 ``run_plan`` 声明顺序（透镜 → 子队 → 汇总），使两端 fold
    的 agents[] 与 oracle golden 同序：法律透镜（lead）先起并保持 running，扇出子队后再收口，
    汇总分析师单独一支后置 plan（依赖四透镜）。
    """
    lens_agents, lens_runs = _lens_agents_runs()
    sub_agents, sub_runs = _law_subteam_agents_runs()
    synth_agents, synth_runs = _synth_agent_run()
    events: list[SSEEvent] = [
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
            agents=lens_agents,
            runs=lens_runs,
            act=act1,
        ),
        # 法律透镜（lead）先起并保持 running（稍后扇出子队），run_started 序 = 声明序。
        run_started("lens_0", "lens_0"),
    ]
    for rid in ("lens_1", "lens_2", "lens_3"):
        events += [
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
        ]
    # 法律透镜自己扇出子队（同 execution_id 合并子图，子节点挂 lens_0 下）。
    events.append(
        run_plan(
            execution_id=_EXEC,
            plan_type="multi_agent",
            task_summary="法律尽调子团队",
            agents=sub_agents,
            runs=sub_runs,
            act=act1,
        )
    )
    for rid, _role, _t in _LAW_SUB:
        events += [
            run_started(rid, rid, parent_run_id="lens_0"),
            run_output_delta(rid, rid, f"{rid} 子调研完成"),
            run_completed(
                rid,
                rid,
                output_summary=f"{rid} 完成",
                duration_ms=600,
                role="member",
                model="deepseek-v4-flash",
                usage=_USAGE,
                cost=_COST,
            ),
        ]
    events += [
        run_output_delta("lens_0", "lens_0", "整合子队：解除权成立但严重度存疑。"),
        run_completed(
            "lens_0",
            "lens_0",
            output_summary="法律尽调完成（含子队）",
            duration_ms=1800,
            role="member",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        # 汇总分析师后置 plan（依赖四透镜），run_started 序在子队之后 = 声明序。
        run_plan(
            execution_id=_EXEC,
            plan_type="multi_agent",
            task_summary=f"交叉验证综述：{_TOPIC}",
            agents=synth_agents,
            runs=synth_runs,
            act=act1,
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
        content_delta("调研已呈报，建议开辩。"),
        message_end(FinishReason.END_TURN, input_tokens=6200, output_tokens=1100, cost=_COST),
    ]
    return events


def _round1_payload() -> dict:
    return {
        "round_no": 1,
        "focus": "解除条款是否成立",
        "summary": "双方对合同第十二条效力各执一词；证人澄清原文后争点收敛到严重度门槛。",
        "verdict": {
            "real_clash": True,
            "new_arguments": True,
            "converged": False,
            "stop_reason": "",
            "rationale": "尚有严重度门槛待辩，续辩。",
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
                "answer_run_id": _PRO_CX,
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
    }


def _round2_payload() -> dict:
    return {
        "round_no": 2,
        "focus": "严重度门槛与赔付敞口",
        "summary": "补派续辩聚焦严重度认定与赔付区间，争点收敛。",
        "verdict": {
            "real_clash": True,
            "new_arguments": False,
            "converged": True,
            "stop_reason": "无新论据。",
            "rationale": "可收场。",
        },
        "sides": [
            {"key": "pro", "name": "立即终止方", "run_id": _PRO_R2, "ok": True},
            {"key": "con", "name": "冷静观望方", "run_id": _CON_R2, "ok": True},
        ],
        "clashes": [],
        "cross_exam": [],
        "scores": {},
    }


def _debate_payload() -> dict:
    return {
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
        "rounds": [_round1_payload(), _round2_payload()],
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
        "closings": [
            {"key": "pro", "name": "立即终止方", "run_id": _PRO_CLOSE, "ok": True},
            {"key": "con", "name": "冷静观望方", "run_id": _CON_CLOSE, "ok": True},
        ],
        "brief": {
            "crux": "第十二条严重度门槛是否满足",
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
    }


def _act2_events(act2: dict) -> list[SSEEvent]:
    """幕2：辩论同图生长（证人答问 + 两轮补派对抗 + 结辩）。"""
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
    cx1 = [
        _ctx_block("task", "质询环节", "请正面回答"),
        _ctx_block(
            "cross_exam",
            "第 1 轮 · 质询（必须正面回答）",
            "- 你凭哪条主张可立即解除？",
        ),
    ]
    wit_ctx = [
        _ctx_block("task", "证人答问", "请据幕1 调研记忆作答"),
        _ctx_block(
            "witness_exam",
            "第 1 轮 · 证人答问（事实性问题）",
            "- 合同第十二条原文如何表述解除条件？",
        ),
    ]
    r2_ctx = [
        _ctx_block("task", "第 2 轮任务", "针对严重度门槛补派续辩"),
        _ctx_block("round_focus", "第 2 轮 · 本轮焦点", "严重度门槛与赔付敞口"),
    ]
    closing_ctx = [
        _ctx_block("task", "结辩环节", "只讲胜负手，不得引入新论据"),
        _ctx_block("closing", "结辩环节", "本场辩论已充分交锋，现请做结辩陈词。"),
    ]
    return [
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
            act=act2,
        ),
        run_started(_MOD, _MOD, parent_run_id=_CAPTAIN_2),
        # 证人席位（辩论幕内）：声明后即开跑占位。
        run_plan(
            execution_id=_EXEC_DEBATE,
            plan_type="debate",
            task_summary="",
            agents=witness_agents,
            runs=witness_runs,
            prev_execution_id=_EXEC_MLR,
            act=act2,
        ),
        run_started(_WIT_SEAT, _WIT_SEAT, parent_run_id=_MOD, group="debate:witness"),
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
            act=act2,
        ),
        run_started(_PRO, "d_pro", parent_run_id=_MOD, stance="pro", group="debate:debate", round_no=1),
        run_output_delta(_PRO, "d_pro", "第十二条可解除【已核实·#e2】，应立即终止。"),
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
        run_started(_CON, "d_con", parent_run_id=_MOD, stance="con", group="debate:debate", round_no=1),
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
        # 第 1 轮质询（折进宿主 pro）。
        *_side_continue(
            _PRO_CX,
            parent=_MOD,
            continues_run_id=_PRO,
            stance="pro",
            round_no=1,
            context_blocks=cx1,
            delta="依据合同第十二条解除权。",
            output_summary="正方质询作答",
            duration_ms=400,
        ),
        # 证人答问：continues=席位根（辩论幕），parent=席位。
        run_started(
            _WIT_ANS,
            _WIT_ANS,
            parent_run_id=_WIT_SEAT,
            continues_run_id=_WIT_SEAT,
            group="debate:witness",
            round_no=1,
            side_key="witness:lens_0",
        ),
        run_context(_WIT_ANS, _WIT_ANS, wit_ctx),
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
        debate_round(execution_id=_EXEC_DEBATE, moderator_run_id=_MOD, payload=_round1_payload()),
        # 第 2 轮补派续辩（正反各续一拍）。
        debate_round_started(
            execution_id=_EXEC_DEBATE,
            moderator_run_id=_MOD,
            round_no=2,
            focus="严重度门槛与赔付敞口",
            cross_exam_enabled=False,
        ),
        *_side_continue(
            _PRO_R2,
            parent=_MOD,
            continues_run_id=_PRO,
            stance="pro",
            round_no=2,
            context_blocks=r2_ctx,
            delta="续辩：声誉损害已达严重度，应立即切割。",
            output_summary="立即终止方第2轮",
            duration_ms=700,
        ),
        *_side_continue(
            _CON_R2,
            parent=_MOD,
            continues_run_id=_CON,
            stance="con",
            round_no=2,
            context_blocks=r2_ctx,
            delta="续辩：严重度未达门槛，仓促解约赔付敞口大。",
            output_summary="冷静观望方第2轮",
            duration_ms=720,
        ),
        debate_round(execution_id=_EXEC_DEBATE, moderator_run_id=_MOD, payload=_round2_payload()),
        # 结辩。
        *_side_continue(
            _PRO_CLOSE,
            parent=_MOD,
            continues_run_id=_PRO,
            stance="pro",
            round_no=2,
            context_blocks=closing_ctx,
            delta="结辩：原文授权 + 严重度已足，应立即终止。",
            output_summary="立即终止方结辩",
            duration_ms=500,
        ),
        *_side_continue(
            _CON_CLOSE,
            parent=_MOD,
            continues_run_id=_CON,
            stance="con",
            round_no=2,
            context_blocks=closing_ctx,
            delta="结辩：严重度未定前不宜仓促解约。",
            output_summary="冷静观望方结辩",
            duration_ms=510,
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
            output_summary="2 轮·收敛",
            duration_ms=3000,
            role="主持人",
            model="deepseek-v4-pro",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(execution_id=_EXEC_DEBATE, moderator_run_id=_MOD, payload=_debate_payload()),
        tool_use_end("db1", "debate", success=True, output="辩论完成。"),
        content_delta("辩论收束，决策简报已呈。"),
        message_end(FinishReason.END_TURN, input_tokens=7200, output_tokens=1400, cost=_COST),
    ]


def _multi_agent_two_act_lv() -> list[SSEEvent]:
    act1 = {"act_id": "act-1", "kind": "multi_agent", "title": "多视角调研"}
    act2 = {
        "act_id": "act-2",
        "kind": "debate",
        "title": "辩论对抗",
        "anchor_run_id": "synthesizer",
        "authorized_by": "stage_card",
    }
    return [*_act1_events(act1), *_act2_events(act2)]
