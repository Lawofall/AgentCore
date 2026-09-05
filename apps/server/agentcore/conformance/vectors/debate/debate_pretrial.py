"""庭前取证 conformance：fast 秒过 + Evidence Pack full（调查员舰队向量已退役）。"""

from __future__ import annotations

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    content_delta,
    debate_pretrial_completed,
    debate_pretrial_orders,
    debate_pretrial_started,
    debate_result,
    debate_round_started,
    message_end,
    message_start,
    run_completed,
    run_output_delta,
    run_plan,
    run_started,
)

from .._common import _CONV, _COST, _USAGE
from ._builders import _moderator_agents_runs, _pro_con_debater_agents, _pro_con_debater_runs


def _multi_agent_debate_pretrial_fast() -> list[SSEEvent]:
    """thorough=False：庭前秒过（skip_reason=fast），无取证员。"""
    cap, mod = "captain1", "debate_mod_fast"
    pro_run, con_run = f"{mod}_r1_pro", f"{mod}_r1_con"
    mod_agents, mod_runs = _moderator_agents_runs(mod, cap, "主持正反辩论：快速对碰")
    debater_agents = _pro_con_debater_agents()
    debater_runs = _pro_con_debater_runs(
        mod,
        pro_run,
        con_run,
        pro_task="快速支持",
        con_task="快速反对",
    )
    sides_wire = [
        {"key": "pro", "name": "支持方"},
        {"key": "con", "name": "反对方"},
    ]
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("快速对碰。"),
        run_plan(
            execution_id="exec_fast",
            plan_type="debate",
            task_summary="正反辩论：快速对碰",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        debate_pretrial_started(
            execution_id="exec_fast",
            moderator_run_id=mod,
            thorough=False,
            sides=sides_wire,
            skip_reason="fast",
        ),
        debate_pretrial_completed(
            execution_id="exec_fast",
            moderator_run_id=mod,
            thorough=False,
            sides=sides_wire,
            status="skipped",
            skip_reason="fast",
            orders=[],
            fallback_self_search=False,
            evidence_ready=False,
            completeness="empty",
            incomplete=False,
            evidence_ledger_count=0,
            evidence_ledger_delta=[],
        ),
        debate_round_started(
            execution_id="exec_fast",
            moderator_run_id=mod,
            round_no=1,
            focus="核心一击",
            cross_exam_enabled=False,
            opening="",
            form="debate",
        ),
        run_plan(
            execution_id="exec_fast",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(pro_run, pro_run, parent_run_id=mod, stance="pro", round_no=1),
        run_output_delta(pro_run, pro_run, "支持。"),
        run_completed(
            pro_run,
            pro_run,
            output_summary="支持方",
            duration_ms=400,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(con_run, con_run, parent_run_id=mod, stance="con", round_no=1),
        run_output_delta(con_run, con_run, "反对。"),
        run_completed(
            con_run,
            con_run,
            output_summary="反对方",
            duration_ms=400,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            mod,
            mod,
            output_summary="1 轮·快速",
            duration_ms=1200,
            role="主持人",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(
            execution_id="exec_fast",
            moderator_run_id=mod,
            payload={
                "form": "debate",
                "motion": "快速对碰命题",
                "stop_reason": "converged",
                "opening": "",
                "narrative_first": False,
                "sides": [
                    {
                        "key": "pro",
                        "name": "支持方",
                        "stance": "支持",
                        "is_subject": False,
                    },
                    {
                        "key": "con",
                        "name": "反对方",
                        "stance": "反对",
                        "is_subject": False,
                    },
                ],
                "rounds": [
                    {
                        "round_no": 1,
                        "focus": "核心一击",
                        "summary": "快速交锋。",
                        "verdict": {
                            "real_clash": True,
                            "new_arguments": False,
                            "converged": True,
                            "stop_reason": "converged",
                            "rationale": "单轮即收",
                        },
                        "sides": [
                            {
                                "key": "pro",
                                "name": "支持方",
                                "run_id": pro_run,
                                "ok": True,
                            },
                            {
                                "key": "con",
                                "name": "反对方",
                                "run_id": con_run,
                                "ok": True,
                            },
                        ],
                        "clashes": [],
                        "cross_exam": [],
                        "scores": {},
                    }
                ],
                "closings": [],
                "brief": {
                    "crux": "快速分歧",
                    "strongest_points": {"pro": "支持", "con": "反对"},
                    "handoffs": [],
                    "decisive": "",
                    "leaning": "未决",
                    "confidence": "low",
                    "recommendation": "需要更深入再辩",
                },
                "evidence_ledger": [],
            },
        ),
        message_end(FinishReason.END_TURN, input_tokens=500, output_tokens=80, cost=_COST),
    ]


def _pack_source(
    *,
    source_id: str,
    label: str,
    path: str,
    excerpt: str,
    complete: bool = True,
    failure: str | None = None,
) -> dict:
    return {
        "source_id": source_id,
        "kind": "attachment",
        "label": label,
        "path": path,
        "excerpt": excerpt,
        "complete": complete,
        "failure": failure,
    }


def _multi_agent_debate_pretrial_evidence_pack_full() -> list[SSEEvent]:
    """Evidence Pack 完整：skip 外证、budget/plan=0、completeness=full。"""
    cap, mod = "captain1", "debate_mod_ep_full"
    pro_run, con_run = f"{mod}_r1_pro", f"{mod}_r1_con"
    mod_agents, mod_runs = _moderator_agents_runs(mod, cap, "主持正反辩论：合同条款争议")
    debater_agents = _pro_con_debater_agents()
    debater_runs = _pro_con_debater_runs(
        mod,
        pro_run,
        con_run,
        pro_task="依共享证据包论证支持",
        con_task="依共享证据包论证反对",
    )
    sides_wire = [
        {"key": "pro", "name": "支持方"},
        {"key": "con", "name": "反对方"},
    ]
    pack_wire = {
        "motion": "是否续签合同",
        "completeness": "full",
        "notes": "从主持人上下文组装共享证据包（1 份可用正文附件）",
        "sources": [
            _pack_source(
                source_id="att:contract",
                label="合同.md",
                path="attachments/合同.md",
                excerpt="甲乙双方约定价款与交付期限……",
            )
        ],
        "dispute_candidates": [
            {
                "claim": "价款条款是否完备",
                "why_contested": "双方对违约金计算口径有分歧",
                "related_source_ids": ["att:contract"],
            }
        ],
    }
    external_plan = {
        "mode": "skip",
        "retrieval_budget": 0,
        "sides": [],
        "allow_web_fetch": False,
        "max_tasks_per_side": 0,
        "reason": "evidence_pack_full",
        "allow_external": False,
    }
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("附件已齐，按共享证据包开辩。"),
        run_plan(
            execution_id="exec_ep_full",
            plan_type="debate",
            task_summary="正反辩论：是否续签合同",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        debate_pretrial_started(
            execution_id="exec_ep_full",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
        ),
        debate_pretrial_orders(
            execution_id="exec_ep_full",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
            orders=[],
            evidence_pack=pack_wire,
            path="evidence_pack",
            completeness="full",
            incomplete=False,
            external_evidence=external_plan,
        ),
        debate_pretrial_completed(
            execution_id="exec_ep_full",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
            status="skipped",
            skip_reason="evidence_pack",
            orders=[],
            fallback_self_search=False,
            evidence_ready=True,
            completeness="full",
            incomplete=False,
            evidence_pack=pack_wire,
            external_evidence_mode="skip",
            external_evidence_reason="evidence_pack_full",
            evidence_ledger_count=1,
            evidence_ledger_delta=[
                {
                    "id": "#e1",
                    "url": "",
                    "title": "合同.md",
                    "snippet": "甲乙双方约定价款与交付期限……",
                    "site": "",
                    "date": "",
                    "tier": "primary",
                    "side_key": "evidence_pack",
                }
            ],
        ),
        debate_round_started(
            execution_id="exec_ep_full",
            moderator_run_id=mod,
            round_no=1,
            focus="价款条款",
            cross_exam_enabled=False,
            opening="双方依共享证据包立论。",
            form="debate",
        ),
        run_plan(
            execution_id="exec_ep_full",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(pro_run, pro_run, parent_run_id=mod, stance="pro", round_no=1),
        run_output_delta(pro_run, pro_run, "支持续签【已核实·#e1】。"),
        run_completed(
            pro_run,
            pro_run,
            output_summary="支持方",
            duration_ms=500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(con_run, con_run, parent_run_id=mod, stance="con", round_no=1),
        run_output_delta(con_run, con_run, "反对续签【已核实·#e1】。"),
        run_completed(
            con_run,
            con_run,
            output_summary="反对方",
            duration_ms=500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            mod,
            mod,
            output_summary="1 轮·证据包",
            duration_ms=1500,
            role="主持人",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(
            execution_id="exec_ep_full",
            moderator_run_id=mod,
            payload={
                "form": "debate",
                "motion": "是否续签合同",
                "stop_reason": "converged",
                "opening": "双方依共享证据包立论。",
                "narrative_first": False,
                "sides": [
                    {
                        "key": "pro",
                        "name": "支持方",
                        "stance": "支持",
                        "is_subject": False,
                    },
                    {
                        "key": "con",
                        "name": "反对方",
                        "stance": "反对",
                        "is_subject": False,
                    },
                ],
                "moderator_run_id": mod,
                "moderator_model": "deepseek-v4-flash",
                "rounds": [
                    {
                        "round_no": 1,
                        "focus": "价款条款",
                        "summary": "围绕合同价款",
                        "verdict": {
                            "real_clash": True,
                            "new_arguments": False,
                            "converged": True,
                            "stop_reason": "converged",
                            "rationale": "依共享证据包交锋后收敛",
                        },
                        "sides": [
                            {
                                "key": "pro",
                                "name": "支持方",
                                "run_id": pro_run,
                                "ok": True,
                                "arguments": [],
                            },
                            {
                                "key": "con",
                                "name": "反对方",
                                "run_id": con_run,
                                "ok": True,
                                "arguments": [],
                            },
                        ],
                        "clashes": [],
                        "cross_exam": [],
                        "scores": {},
                    }
                ],
                "closings": [],
                "brief": {
                    "crux": "价款条款",
                    "strongest_points": {"pro": "支持", "con": "反对"},
                    "handoffs": [],
                    "decisive": "",
                    "leaning": "未决",
                    "confidence": "low",
                    "recommendation": "复核违约金口径",
                },
                "evidence_ledger": [
                    {
                        "id": "#e1",
                        "url": "",
                        "title": "合同.md",
                        "snippet": "甲乙双方约定价款与交付期限……",
                        "site": "合同.md",
                        "date": "",
                        "tier": "unknown",
                        "side_key": "evidence_pack",
                        "dossier_path": "attachments/合同.md",
                        "origin_id": "",
                        "dossier_label": "合同.md",
                    }
                ],
            },
        ),
        message_end(FinishReason.END_TURN, input_tokens=600, output_tokens=100, cost=_COST),
    ]


def _multi_agent_debate_pretrial_no_pack() -> list[SSEEvent]:
    """thorough 无 pack：skip_reason=no_pack，无取证员，进入立论（发言期有界预算由 runtime 写入）。"""
    cap, mod = "captain1", "debate_mod_nopack"
    pro_run, con_run = f"{mod}_r1_pro", f"{mod}_r1_con"
    mod_agents, mod_runs = _moderator_agents_runs(mod, cap, "主持正反辩论：是否采用方案 A")
    debater_agents = _pro_con_debater_agents()
    debater_runs = _pro_con_debater_runs(
        mod,
        pro_run,
        con_run,
        pro_task="论证支持采用方案 A",
        con_task="论证反对采用方案 A",
    )
    sides_wire = [
        {"key": "pro", "name": "支持方"},
        {"key": "con", "name": "反对方"},
    ]
    external_plan = {
        "mode": "skip",
        "retrieval_budget": 0,
        "sides": [],
        "allow_web_fetch": False,
        "max_tasks_per_side": 0,
        "reason": "no_pack",
        "allow_external": False,
    }
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("无共享证据包，直接开辩。"),
        run_plan(
            execution_id="exec_nopack",
            plan_type="debate",
            task_summary="正反辩论：是否采用方案 A",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        debate_pretrial_started(
            execution_id="exec_nopack",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
        ),
        debate_pretrial_orders(
            execution_id="exec_nopack",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
            orders=[],
            completeness="empty",
            incomplete=True,
            external_evidence=external_plan,
        ),
        debate_pretrial_completed(
            execution_id="exec_nopack",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
            status="skipped",
            skip_reason="no_pack",
            orders=[],
            fallback_self_search=False,
            evidence_ready=False,
            completeness="empty",
            incomplete=False,
            external_evidence_mode="skip",
            external_evidence_reason="no_pack",
            evidence_ledger_count=0,
            evidence_ledger_delta=[],
        ),
        debate_round_started(
            execution_id="exec_nopack",
            moderator_run_id=mod,
            round_no=1,
            focus="成本与风险",
            cross_exam_enabled=True,
            opening="先从成本与风险切入。",
            form="debate",
        ),
        run_plan(
            execution_id="exec_nopack",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(pro_run, pro_run, parent_run_id=mod, stance="pro", round_no=1),
        run_output_delta(pro_run, pro_run, "### 成本可控\n常识立论。"),
        run_completed(
            pro_run,
            pro_run,
            output_summary="支持方立论",
            duration_ms=1200,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(con_run, con_run, parent_run_id=mod, stance="con", round_no=1),
        run_output_delta(con_run, con_run, "### 风险未兜底\n常识反驳。"),
        run_completed(
            con_run,
            con_run,
            output_summary="反对方立论",
            duration_ms=1100,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            mod,
            mod,
            output_summary="1 轮·收敛",
            duration_ms=5000,
            role="主持人",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(
            execution_id="exec_nopack",
            moderator_run_id=mod,
            payload={
                "form": "debate",
                "motion": "是否采用方案 A",
                "stop_reason": "converged",
                "opening": "先从成本与风险切入。",
                "narrative_first": False,
                "sides": [
                    {
                        "key": "pro",
                        "name": "支持方",
                        "stance": "支持采用方案 A",
                        "is_subject": False,
                    },
                    {
                        "key": "con",
                        "name": "反对方",
                        "stance": "反对采用方案 A",
                        "is_subject": False,
                    },
                ],
                "rounds": [
                    {
                        "round_no": 1,
                        "focus": "成本与风险",
                        "summary": "双方围绕成本与风险交锋。",
                        "verdict": {
                            "real_clash": True,
                            "new_arguments": False,
                            "converged": True,
                            "stop_reason": "converged",
                            "rationale": "核心分歧已暴露",
                        },
                        "sides": [
                            {
                                "key": "pro",
                                "name": "支持方",
                                "run_id": pro_run,
                                "ok": True,
                            },
                            {
                                "key": "con",
                                "name": "反对方",
                                "run_id": con_run,
                                "ok": True,
                            },
                        ],
                        "clashes": [],
                        "cross_exam": [],
                        "scores": {},
                    }
                ],
                "closings": [],
                "brief": {
                    "crux": "成本 vs 风险",
                    "strongest_points": {"pro": "成本可控", "con": "风险未兜底"},
                    "handoffs": [],
                    "decisive": "需用户权衡",
                    "leaning": "未决",
                    "confidence": "medium",
                    "recommendation": "先补风险兜底再定",
                },
                "evidence_ledger": [],
            },
        ),
        content_delta("辩论结束，简报如上。"),
        message_end(FinishReason.END_TURN, input_tokens=2000, output_tokens=400, cost=_COST),
    ]


def _multi_agent_debate_pretrial_evidence_pack_partial() -> list[SSEEvent]:
    """Evidence Pack 截断：skip 外证舰队；completeness=partial（发言期对称有界预算）。"""
    cap, mod = "captain1", "debate_mod_ep_partial"
    pro_run, con_run = f"{mod}_r1_pro", f"{mod}_r1_con"
    mod_agents, mod_runs = _moderator_agents_runs(mod, cap, "主持正反辩论：长约截断")
    debater_agents = _pro_con_debater_agents()
    debater_runs = _pro_con_debater_runs(
        mod,
        pro_run,
        con_run,
        pro_task="依部分证据包论证支持",
        con_task="依部分证据包论证反对",
    )
    sides_wire = [
        {"key": "pro", "name": "支持方"},
        {"key": "con", "name": "反对方"},
    ]
    pack_wire = {
        "motion": "是否采用方案 A",
        "completeness": "partial",
        "notes": "从主持人上下文组装共享证据包（截断附件）",
        "sources": [
            _pack_source(
                source_id="att:long",
                label="长约.md",
                path="attachments/长约.md",
                excerpt="条款正文…" * 20,
                complete=False,
                failure="truncated",
            )
        ],
        "dispute_candidates": [],
    }
    external_plan = {
        "mode": "skip",
        "retrieval_budget": 0,
        "sides": [],
        "allow_web_fetch": False,
        "max_tasks_per_side": 0,
        "reason": "evidence_pack_partial",
        "allow_external": False,
    }
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("附件截断，跳过外证舰队直接开辩。"),
        run_plan(
            execution_id="exec_ep_partial",
            plan_type="debate",
            task_summary="正反辩论：是否采用方案 A",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        debate_pretrial_started(
            execution_id="exec_ep_partial",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
        ),
        debate_pretrial_orders(
            execution_id="exec_ep_partial",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
            orders=[],
            evidence_pack=pack_wire,
            path="evidence_pack",
            completeness="partial",
            incomplete=True,
            external_evidence=external_plan,
        ),
        debate_pretrial_completed(
            execution_id="exec_ep_partial",
            moderator_run_id=mod,
            thorough=True,
            sides=sides_wire,
            status="skipped",
            skip_reason="evidence_pack",
            orders=[],
            fallback_self_search=False,
            evidence_ready=True,
            completeness="partial",
            incomplete=False,
            evidence_pack=pack_wire,
            external_evidence_mode="skip",
            external_evidence_reason="evidence_pack_partial",
            evidence_ledger_count=1,
            evidence_ledger_delta=[
                {
                    "id": "#e1",
                    "url": "",
                    "title": "长约.md",
                    "snippet": "条款正文…",
                    "site": "",
                    "date": "",
                    "tier": "primary",
                    "side_key": "evidence_pack",
                }
            ],
        ),
        debate_round_started(
            execution_id="exec_ep_partial",
            moderator_run_id=mod,
            round_no=1,
            focus="截断条款",
            cross_exam_enabled=False,
            opening="共享包不完整，双方有界补证后立论。",
            form="debate",
        ),
        run_plan(
            execution_id="exec_ep_partial",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(pro_run, pro_run, parent_run_id=mod, stance="pro", round_no=1),
        run_output_delta(pro_run, pro_run, "支持【已核实·#e1】。"),
        run_completed(
            pro_run,
            pro_run,
            output_summary="支持方",
            duration_ms=500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(con_run, con_run, parent_run_id=mod, stance="con", round_no=1),
        run_output_delta(con_run, con_run, "反对【已核实·#e1】。"),
        run_completed(
            con_run,
            con_run,
            output_summary="反对方",
            duration_ms=500,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            mod,
            mod,
            output_summary="1 轮·部分证据包",
            duration_ms=1500,
            role="主持人",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(
            execution_id="exec_ep_partial",
            moderator_run_id=mod,
            payload={
                "form": "debate",
                "motion": "是否采用方案 A",
                "stop_reason": "converged",
                "opening": "共享包不完整，双方有界补证后立论。",
                "narrative_first": False,
                "sides": [
                    {
                        "key": "pro",
                        "name": "支持方",
                        "stance": "支持",
                        "is_subject": False,
                    },
                    {
                        "key": "con",
                        "name": "反对方",
                        "stance": "反对",
                        "is_subject": False,
                    },
                ],
                "rounds": [
                    {
                        "round_no": 1,
                        "focus": "截断条款",
                        "summary": "部分证据包交锋",
                        "verdict": {
                            "real_clash": True,
                            "new_arguments": False,
                            "converged": True,
                            "stop_reason": "converged",
                            "rationale": "承认缺口后收敛",
                        },
                        "sides": [
                            {
                                "key": "pro",
                                "name": "支持方",
                                "run_id": pro_run,
                                "ok": True,
                            },
                            {
                                "key": "con",
                                "name": "反对方",
                                "run_id": con_run,
                                "ok": True,
                            },
                        ],
                        "clashes": [],
                        "cross_exam": [],
                        "scores": {},
                    }
                ],
                "closings": [],
                "brief": {
                    "crux": "截断条款",
                    "strongest_points": {"pro": "支持", "con": "反对"},
                    "handoffs": [],
                    "decisive": "",
                    "leaning": "未决",
                    "confidence": "low",
                    "recommendation": "补齐全文后再辩",
                },
                "evidence_ledger": [
                    {
                        "id": "#e1",
                        "url": "",
                        "title": "长约.md",
                        "snippet": "条款正文…",
                        "site": "长约.md",
                        "date": "",
                        "tier": "unknown",
                        "side_key": "evidence_pack",
                        "dossier_path": "attachments/长约.md",
                        "origin_id": "",
                        "dossier_label": "长约.md",
                    }
                ],
            },
        ),
        message_end(FinishReason.END_TURN, input_tokens=700, output_tokens=120, cost=_COST),
    ]
