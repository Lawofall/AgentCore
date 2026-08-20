"""Conformance vector builders — legal vertical end-to-end scenarios.

See ``vectors/__init__.py`` for the aggregated ``VECTORS`` registry.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcore.runtime.events import (
    FinishReason,
    SSEEvent,
    checkpoint_required,
    citations_event,
    content_delta,
    debate_result,
    message_end,
    message_start,
    run_completed,
    run_output_delta,
    run_plan,
    run_started,
    run_tool_progress,
    tool_use_end,
    tool_use_start,
)

from ._common import _CONV, _COST, _USAGE


def _multi_agent_legal_war_room() -> list[SSEEvent]:
    """多 Agent：法律「答辩状作战室」端到端（hero · 远期规划.md §4.5 法律垂直，M3 玻璃箱 fixture）。
    复用现有事件类型**组合**出法律 hero 的玻璃箱全流程——给它一个常驻离线预览场景 + CI 渲染冒烟门，
    **不新增事件类型 → fold 不碰**（守协议边界）。流程与 M2 实测形态一致：① CEO
    `consult_skill(legal_answer_brief)` 翻作战室打法；② `delegate` 起草律师出 `答辩状初稿.md`
    （worker 内 `file_write`）；③ `debate(form=red_team)` 让**原告红队**单向压测我方答辩
    （`defense` is_subject vs 程序 / 实体红队），收场 `debate_result` 承「风险看板 + 加固建议 +
    我方回应」双产物，挖出 3 个攻击点（送达举证 / 质量异议具体性 / 沉默推定）；④ `delegate` 核验
    律师 `web_search` + 落 `法条核验报告.md`（带**出处** + `[待核验]`）；⑤ 终稿前 `checkpoint`
    人审闸门**暂停**（status=paused、pendingInteraction=checkpoint），把攻防 / 核验结论摊给律师
    拍板再收口。本向量取**人审暂停态**：停在人审闸门（status=paused、pendingInteraction=checkpoint），
    如实**不发** citations_event、亦不 message_end。**收口终稿态**（人审通过 → CEO 收口正文出终稿、
    已核验法条带 `[n]` 角标连到法条来源卡，§十一 方案①）由姊妹向量 `multi_agent_legal_war_room_settled`
    覆盖。"""
    cap = "captain1"
    mod = "warroom_mod1"
    r_draft, w_draft = "r_draft", "w_draft"
    r_verify, w_verify = "r_verify", "w_verify"
    subj_run = f"{mod}_r1_defense"
    redp_run = f"{mod}_r1_redproc"
    reds_run = f"{mod}_r1_redsubst"

    skill_guidance = (
        "## 答辩状作战室\n"
        "1. 解析对方起诉状 + 我方事实 → 逐项答辩（程序抗辩 + 实体抗辩 + 质证 + 法律依据）。\n"
        "2. delegate 起草，debate(red_team, is_subject=我方答辩) 让原告红队单向压测，再逐点加固。\n"
        "3. delegate 核验逐条法条 / 时效，未核验不得引用、标 [待核验]；终稿标法域 + 免责 + 人审闸门。\n"
    )
    draft_md = (
        "# 民事答辩状（初稿）\n"
        "## 程序抗辩\n- 对原告送达与举证提出异议。\n"
        "## 实体抗辩\n- 质量异议函已发，违约责任不成立。\n"
        "## 法律依据\n- 《民法典》合同编相关条款（待核验）。\n"
    )
    verify_md = (
        "# 法条核验报告\n"
        "| 引用 | 现行有效性 | 出处 | 结论 |\n"
        "|---|---|---|---|\n"
        "| 违约金超损失 30% 可调减 | 现行有效 | 《民法典合同编通则若干问题的解释》第 65 条 | ✅ 已核验 |\n"
        "| 质量异议合理期限 | 现行有效 | 《民法典》第 621 条 | ✅ 已核验 |\n"
        "| 送达推定到达规则 | 待确认条款 | web 摘要不足 | ⚠️ [待核验]（转人工 / 库核） |\n"
    )

    draft_agents = [
        {
            "id": w_draft,
            "role": "起草律师",
            "thinking": True,
        },
    ]
    draft_runs = [{"id": r_draft, "agent_id": w_draft, "task": "起草答辩状初稿", "depends_on": []}]
    verify_agents = [
        {
            "id": w_verify,
            "role": "核验律师",
            "thinking": True,
        },
    ]
    verify_runs = [
        {"id": r_verify, "agent_id": w_verify, "task": "逐条核验法条与时效", "depends_on": []}
    ]
    mod_agents = [
        {
            "id": mod,
            "role": "主持人",
            "thinking": False,
        },
    ]
    mod_runs = [
        {
            "id": mod,
            "agent_id": mod,
            "task": "主持红队审查：原告视角压测我方答辩",
            "depends_on": [],
            "parent_run_id": cap,
        },
    ]
    debater_agents = [
        {
            "id": "d_defense",
            "role": "我方答辩",
            "thinking": True,
        },
        {
            "id": "d_red_proc",
            "role": "程序红队",
            "thinking": True,
        },
        {
            "id": "d_red_subst",
            "role": "实体红队",
            "thinking": True,
        },
    ]
    debater_runs = [
        {
            "id": subj_run,
            "agent_id": "d_defense",
            "task": "为我方答辩抗辩并加固",
            "depends_on": [],
            "parent_run_id": mod,
            "group": "debate:red_team",
            "round": 1,
        },
        {
            "id": redp_run,
            "agent_id": "d_red_proc",
            "task": "以原告视角挖程序 / 送达漏洞",
            "depends_on": [],
            "parent_run_id": mod,
            "group": "debate:red_team",
            "round": 1,
        },
        {
            "id": reds_run,
            "agent_id": "d_red_subst",
            "task": "以原告视角挖实体抗辩漏洞",
            "depends_on": [],
            "parent_run_id": mod,
            "group": "debate:red_team",
            "round": 1,
        },
    ]
    debate_payload = {
        "form": "red_team",
        "motion": "以原告视角压测我方《民事答辩状》的稳健性",
        "stop_reason": "red_team_exhausted",
        "narrative_first": False,
        "sides": [
            {
                "key": "defense",
                "name": "我方答辩",
                "stance": "逐项抗辩成立、无需担责",
                "is_subject": True,
                "model": "",
            },
            {
                "key": "red_proc",
                "name": "程序红队（原告）",
                "stance": "程序与送达举证存在硬伤",
                "is_subject": False,
                "model": "",
            },
            {
                "key": "red_subst",
                "name": "实体红队（原告）",
                "stance": "实体抗辩不成立",
                "is_subject": False,
                "model": "",
            },
        ],
        "rounds": [
            {
                "round_no": 1,
                "focus": "送达举证 / 质量异议具体性 / 沉默推定 三点攻防",
                "summary": "原告红队挖出送达签收链缺失、异议函笼统、对部分诉请沉默三点；我方补证据并逐点否认加固。",
                "verdict": {
                    "real_clash": True,
                    "new_arguments": True,
                    "converged": True,
                    "stop_reason": "red_team_exhausted",
                    "rationale": "三点攻击已被逐点回应 / 加固，无新增有效攻击。",
                },
                "sides": [
                    {"key": "defense", "name": "我方答辩", "run_id": subj_run, "ok": True},
                    {"key": "red_proc", "name": "程序红队（原告）", "run_id": redp_run, "ok": True},
                    {"key": "red_subst", "name": "实体红队（原告）", "run_id": reds_run, "ok": True},
                ],
                "clashes": [
                    {
                        "from_key": "red_proc",
                        "to_key": "defense",
                        "point": "到达举证缺签收链，送达争议规则对我方不利。",
                    },
                    {
                        "from_key": "red_subst",
                        "to_key": "defense",
                        "point": "质量异议函笼统、未指向批次 / 标准；对部分诉请沉默易被推定认可。",
                    },
                ],
            },
        ],
        "brief": {
            "crux": "我方答辩能否扛住原告对『送达举证 + 质量异议具体性 + 沉默推定』三点的攻击",
            "strongest_points": {
                "red_proc": "到达举证缺签收链，按送达争议规则我方不利，是最尖锐风险。",
                "red_subst": "质量异议函表述笼统、未指向具体批次 / 标准，难达异议成立要件；对原告主张沉默处易被推定认可。",
                "defense": "已补送达回执 + 异议函逐条对应批次，并就沉默项明确否认。",
            },
            "risk_severities": {
                "red_proc": "high",
                "red_subst": "medium",
            },
            "handoffs": [
                {"kind": "value", "text": "以程序抗辩拖延 vs 实体一次性了结的策略取舍"},
                {"kind": "fact", "text": "质量异议函是否在合理期限内送达原告口径不一"},
                {"kind": "question", "text": "违约金调减的请求权基础按哪条主张？"},
            ],
            "leaning": "有条件成立：补强送达证据 + 异议函具体化 + 逐项否认后可扛住",
            "confidence": "medium",
            "recommendation": "终稿前必须：① 补送达签收链证据 ② 异议函按批次 / 标准逐条具体化 ③ 对原告每项主张明确否认，杜绝沉默推定。",
        },
    }
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我先翻一下「答辩状作战室」的打法。"),
        tool_use_start("cs1", "consult_skill", {"name": "legal_answer_brief"}),
        tool_use_end(
            "cs1",
            "consult_skill",
            success=True,
            output=skill_guidance,
            display={
                "skill_name": "legal_answer_brief",
                "summary": "答辩状要素结构 + 对方律师作战室编排 + 反幻觉硬约束",
            },
        ),
        content_delta("按作战室打法组队：先起草，再让原告红队压一遍，核验法条，最后请你拍板。"),
        # ① delegate 起草律师 → 答辩状初稿.md（worker 内 file_write）
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "起草律师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="起草答辩状初稿",
            agents=draft_agents,
            runs=draft_runs,
        ),
        run_started(r_draft, w_draft),
        run_tool_progress(r_draft, w_draft, "file_write", len(draft_md)),
        tool_use_start(
            "fw1", "file_write", {"path": "答辩状初稿.md", "content": draft_md}, run_id=r_draft
        ),
        tool_use_end("fw1", "file_write", success=True, output="已写入", run_id=r_draft),
        run_output_delta(r_draft, w_draft, "答辩状初稿就绪：程序抗辩 + 实体抗辩 + 质证 + 法律依据。"),
        run_completed(
            r_draft,
            w_draft,
            output_summary="起草完成：答辩状初稿.md",
            duration_ms=1600,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="起草完成：答辩状初稿.md"),
        # ② debate(red_team)：原告红队单向压测我方答辩（hero）
        content_delta("现在让原告红队来压测我方答辩。"),
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="红队审查：原告视角压测我方《民事答辩状》",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(subj_run, "d_defense", parent_run_id=mod),
        run_output_delta(
            subj_run, "d_defense", "我方：已补送达回执、异议函逐条对应批次，并就沉默项明确否认。"
        ),
        run_completed(
            subj_run,
            "d_defense",
            output_summary="我方抗辩 + 加固完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(redp_run, "d_red_proc", parent_run_id=mod),
        run_output_delta(redp_run, "d_red_proc", "程序红队：到达举证缺签收链，送达争议对我方不利。"),
        run_completed(
            redp_run,
            "d_red_proc",
            output_summary="程序红队挖掘完成",
            duration_ms=840,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(reds_run, "d_red_subst", parent_run_id=mod),
        run_output_delta(
            reds_run, "d_red_subst", "实体红队：质量异议函笼统、对部分诉请沉默易被推定认可。"
        ),
        run_completed(
            reds_run,
            "d_red_subst",
            output_summary="实体红队挖掘完成",
            duration_ms=860,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            mod,
            mod,
            output_summary="原告红队三点攻击已被逐点回应",
            duration_ms=2200,
            role="主持人",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(execution_id="exec1", moderator_run_id=mod, payload=debate_payload),
        # ③ delegate 核验律师 → web_search + 法条核验报告.md（出处 + [待核验]）
        content_delta("再逐条核验法条与时效。"),
        tool_use_start("dc2", "delegate", {"tasks": [{"role": "核验律师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="逐条核验法条 / 时效",
            agents=verify_agents,
            runs=verify_runs,
        ),
        run_started(r_verify, w_verify),
        run_tool_progress(r_verify, w_verify, "web_search", 3),
        tool_use_start(
            "ws1",
            "web_search",
            {"query": "民法典合同编通则解释 违约金 调减"},
            run_id=r_verify,
        ),
        tool_use_end(
            "ws1",
            "web_search",
            success=True,
            output="命中：合同编通则解释第 65 条；送达规则未取到权威原文。",
            run_id=r_verify,
        ),
        run_tool_progress(r_verify, w_verify, "file_write", len(verify_md)),
        tool_use_start(
            "fw2", "file_write", {"path": "法条核验报告.md", "content": verify_md}, run_id=r_verify
        ),
        tool_use_end("fw2", "file_write", success=True, output="已写入", run_id=r_verify),
        run_output_delta(
            r_verify,
            w_verify,
            "核验完成：违约金调减引《合同编通则解释》第 65 条；送达规则标 [待核验]。",
        ),
        run_completed(
            r_verify,
            w_verify,
            output_summary="核验完成：法条核验报告.md",
            duration_ms=2000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end(
            "dc2", "delegate", success=True, output="核验完成：法条核验报告.md（1 处 [待核验]）"
        ),
        # ④ 终稿前人审闸门 → 暂停（status=paused，结论摊给律师拍板再收口）
        content_delta(
            "终稿前请你过一下：红队 3 个攻击点已逐点加固，法条已核验，有 1 处待人工确认。"
        ),
        checkpoint_required(
            checkpoint_id="cp1",
            conversation_id=_CONV,
            question=(
                "是否采纳终稿并提交？（含 1 处 [待核验] 法条）\n"
                "原告红队已挖尽收敛，核验报告与答辩状初稿见工作区；终稿将标注法域 + 免责。"
            ),
            intent="decision",
        ),
    ]


def _multi_agent_legal_war_room_settled() -> list[SSEEvent]:
    """多 Agent：法律「答辩状作战室」端到端【收口终稿态】(hero · §十一 方案① 来源卡接入)。
    姊妹向量 `multi_agent_legal_war_room` 取人审**暂停**态；本向量取**人审通过后的收口**态，专覆盖
    §十一 方案①：consult → delegate 起草 → debate(red_team) 原告红队压测 → delegate 核验
    （`web_search` 命中权威法条源 → 这些源经 delegate 结果汇入回合「来源卡」）→ **CEO 收口正文
    完整输出答辩状终稿**，每条【已核验】法条带 `[n]` 角标连到对应来源卡（玻璃箱可审计 / 可溯源），
    仍 `[待核验]` 的送达规则**不编角标**（反幻觉：宁缺勿造，编造角标会被 finish_guard 拦回）；收口前
    发 `citations_event`（2 张法条来源卡），`message_end(status=success)`。**复用现有事件类型组合、
    不新增类型 → fold 不碰**（守协议边界）。前缀流程与暂停态一致（如实镜像 M2 实测形态）。"""
    cap = "captain1"
    mod = "warroom_mod1"
    r_draft, w_draft = "r_draft", "w_draft"
    r_verify, w_verify = "r_verify", "w_verify"
    subj_run = f"{mod}_r1_defense"
    redp_run = f"{mod}_r1_redproc"
    reds_run = f"{mod}_r1_redsubst"

    skill_guidance = (
        "## 答辩状作战室\n"
        "1. 解析对方起诉状 + 我方事实 → 逐项答辩（程序抗辩 + 实体抗辩 + 质证 + 法律依据）。\n"
        "2. delegate 起草，debate(red_team, is_subject=我方答辩) 让原告红队单向压测，再逐点加固。\n"
        "3. delegate 核验逐条法条 / 时效，未核验不得引用、标 [待核验]；终稿标法域 + 免责 + 人审闸门。\n"
    )
    draft_md = (
        "# 民事答辩状（初稿）\n"
        "## 程序抗辩\n- 对原告送达与举证提出异议。\n"
        "## 实体抗辩\n- 质量异议函已发，违约责任不成立。\n"
        "## 法律依据\n- 《民法典》合同编相关条款（待核验）。\n"
    )
    verify_md = (
        "# 法条核验报告\n"
        "| 引用 | 现行有效性 | 出处 | 结论 |\n"
        "|---|---|---|---|\n"
        "| 违约金超损失 30% 可调减 | 现行有效 | 《民法典合同编通则若干问题的解释》第 65 条 | ✅ 已核验 |\n"
        "| 质量异议合理期限 | 现行有效 | 《民法典》第 621 条 | ✅ 已核验 |\n"
        "| 送达推定到达规则 | 待确认条款 | web 摘要不足 | ⚠️ [待核验]（转人工 / 库核） |\n"
    )
    final_brief = (
        "# 民事答辩状（终稿）\n"
        "## 程序抗辩\n"
        "- 原告送达签收链缺失，对送达是否到达有异议（送达推定到达规则待权威核验，暂标 [待核验]）。\n"
        "## 实体抗辩\n"
        "- 买方已在合理期限内就质量提出异议，依《民法典》第六百二十一条 [1]，异议成立、可主张相应减款。\n"
        "- 即便认定违约，约定违约金过分高于实际损失，依《合同编通则若干问题的解释》第六十五条 [2]，"
        "请求酌减至不超过损失的 130%。\n"
        "## 质证意见\n- 对送货单真实性无异议；对原告『货物合格』主张的关联性有异议。\n"
        "## 答辩意见\n- 请求依法驳回原告的全部或部分诉讼请求。\n\n"
        "【法域】中国大陆法。【免责】本文为 AI 辅助起草，须执业律师复核后使用，不构成法律意见。\n"
    )

    draft_agents = [
        {
            "id": w_draft,
            "role": "起草律师",
            "thinking": True,
        },
    ]
    draft_runs = [{"id": r_draft, "agent_id": w_draft, "task": "起草答辩状初稿", "depends_on": []}]
    verify_agents = [
        {
            "id": w_verify,
            "role": "核验律师",
            "thinking": True,
        },
    ]
    verify_runs = [
        {"id": r_verify, "agent_id": w_verify, "task": "逐条核验法条与时效", "depends_on": []}
    ]
    mod_agents = [
        {
            "id": mod,
            "role": "主持人",
            "thinking": False,
        },
    ]
    mod_runs = [
        {
            "id": mod,
            "agent_id": mod,
            "task": "主持红队审查：原告视角压测我方答辩",
            "depends_on": [],
            "parent_run_id": cap,
        },
    ]
    debater_agents = [
        {
            "id": "d_defense",
            "role": "我方答辩",
            "thinking": True,
        },
        {
            "id": "d_red_proc",
            "role": "程序红队",
            "thinking": True,
        },
        {
            "id": "d_red_subst",
            "role": "实体红队",
            "thinking": True,
        },
    ]
    debater_runs = [
        {
            "id": subj_run,
            "agent_id": "d_defense",
            "task": "为我方答辩抗辩并加固",
            "depends_on": [],
            "parent_run_id": mod,
            "group": "debate:red_team",
            "round": 1,
        },
        {
            "id": redp_run,
            "agent_id": "d_red_proc",
            "task": "以原告视角挖程序 / 送达漏洞",
            "depends_on": [],
            "parent_run_id": mod,
            "group": "debate:red_team",
            "round": 1,
        },
        {
            "id": reds_run,
            "agent_id": "d_red_subst",
            "task": "以原告视角挖实体抗辩漏洞",
            "depends_on": [],
            "parent_run_id": mod,
            "group": "debate:red_team",
            "round": 1,
        },
    ]
    debate_payload = {
        "form": "red_team",
        "motion": "以原告视角压测我方《民事答辩状》的稳健性",
        "stop_reason": "red_team_exhausted",
        "narrative_first": False,
        "sides": [
            {
                "key": "defense",
                "name": "我方答辩",
                "stance": "逐项抗辩成立、无需担责",
                "is_subject": True,
                "model": "",
            },
            {
                "key": "red_proc",
                "name": "程序红队（原告）",
                "stance": "程序与送达举证存在硬伤",
                "is_subject": False,
                "model": "",
            },
            {
                "key": "red_subst",
                "name": "实体红队（原告）",
                "stance": "实体抗辩不成立",
                "is_subject": False,
                "model": "",
            },
        ],
        "rounds": [
            {
                "round_no": 1,
                "focus": "送达举证 / 质量异议具体性 / 沉默推定 三点攻防",
                "summary": "原告红队挖出送达签收链缺失、异议函笼统、对部分诉请沉默三点；我方补证据并逐点否认加固。",
                "verdict": {
                    "real_clash": True,
                    "new_arguments": True,
                    "converged": True,
                    "stop_reason": "red_team_exhausted",
                    "rationale": "三点攻击已被逐点回应 / 加固，无新增有效攻击。",
                },
                "sides": [
                    {"key": "defense", "name": "我方答辩", "run_id": subj_run, "ok": True},
                    {"key": "red_proc", "name": "程序红队（原告）", "run_id": redp_run, "ok": True},
                    {"key": "red_subst", "name": "实体红队（原告）", "run_id": reds_run, "ok": True},
                ],
                "clashes": [
                    {
                        "from_key": "red_proc",
                        "to_key": "defense",
                        "point": "到达举证缺签收链，送达争议规则对我方不利。",
                    },
                    {
                        "from_key": "red_subst",
                        "to_key": "defense",
                        "point": "质量异议函笼统、未指向批次 / 标准；对部分诉请沉默易被推定认可。",
                    },
                ],
            },
        ],
        "brief": {
            "crux": "我方答辩能否扛住原告对『送达举证 + 质量异议具体性 + 沉默推定』三点的攻击",
            "strongest_points": {
                "red_proc": "到达举证缺签收链，按送达争议规则我方不利，是最尖锐风险。",
                "red_subst": "质量异议函表述笼统、未指向具体批次 / 标准，难达异议成立要件；对原告主张沉默处易被推定认可。",
                "defense": "已补送达回执 + 异议函逐条对应批次，并就沉默项明确否认。",
            },
            "risk_severities": {
                "red_proc": "high",
                "red_subst": "medium",
            },
            "handoffs": [
                {"kind": "value", "text": "以程序抗辩拖延 vs 实体一次性了结的策略取舍"},
                {"kind": "fact", "text": "质量异议函是否在合理期限内送达原告口径不一"},
                {"kind": "question", "text": "违约金调减的请求权基础按哪条主张？"},
            ],
            "leaning": "有条件成立：补强送达证据 + 异议函具体化 + 逐项否认后可扛住",
            "confidence": "medium",
            "recommendation": "终稿前必须：① 补送达签收链证据 ② 异议函按批次 / 标准逐条具体化 ③ 对原告每项主张明确否认，杜绝沉默推定。",
        },
    }
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我先翻一下「答辩状作战室」的打法。"),
        tool_use_start("cs1", "consult_skill", {"name": "legal_answer_brief"}),
        tool_use_end(
            "cs1",
            "consult_skill",
            success=True,
            output=skill_guidance,
            display={
                "skill_name": "legal_answer_brief",
                "summary": "答辩状要素结构 + 对方律师作战室编排 + 反幻觉硬约束",
            },
        ),
        content_delta("按作战室打法组队：先起草，再让原告红队压一遍，核验法条，最后请你拍板。"),
        # ① delegate 起草律师 → 答辩状初稿.md（worker 内 file_write）
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "起草律师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="起草答辩状初稿",
            agents=draft_agents,
            runs=draft_runs,
        ),
        run_started(r_draft, w_draft),
        run_tool_progress(r_draft, w_draft, "file_write", len(draft_md)),
        tool_use_start(
            "fw1", "file_write", {"path": "答辩状初稿.md", "content": draft_md}, run_id=r_draft
        ),
        tool_use_end("fw1", "file_write", success=True, output="已写入", run_id=r_draft),
        run_output_delta(r_draft, w_draft, "答辩状初稿就绪：程序抗辩 + 实体抗辩 + 质证 + 法律依据。"),
        run_completed(
            r_draft,
            w_draft,
            output_summary="起草完成：答辩状初稿.md",
            duration_ms=1600,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="起草完成：答辩状初稿.md"),
        # ② debate(red_team)：原告红队单向压测我方答辩（hero）
        content_delta("现在让原告红队来压测我方答辩。"),
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="红队审查：原告视角压测我方《民事答辩状》",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(subj_run, "d_defense", parent_run_id=mod),
        run_output_delta(
            subj_run, "d_defense", "我方：已补送达回执、异议函逐条对应批次，并就沉默项明确否认。"
        ),
        run_completed(
            subj_run,
            "d_defense",
            output_summary="我方抗辩 + 加固完成",
            duration_ms=900,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(redp_run, "d_red_proc", parent_run_id=mod),
        run_output_delta(redp_run, "d_red_proc", "程序红队：到达举证缺签收链，送达争议对我方不利。"),
        run_completed(
            redp_run,
            "d_red_proc",
            output_summary="程序红队挖掘完成",
            duration_ms=840,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(reds_run, "d_red_subst", parent_run_id=mod),
        run_output_delta(
            reds_run, "d_red_subst", "实体红队：质量异议函笼统、对部分诉请沉默易被推定认可。"
        ),
        run_completed(
            reds_run,
            "d_red_subst",
            output_summary="实体红队挖掘完成",
            duration_ms=860,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            mod,
            mod,
            output_summary="原告红队三点攻击已被逐点回应",
            duration_ms=2200,
            role="主持人",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(execution_id="exec1", moderator_run_id=mod, payload=debate_payload),
        # ③ delegate 核验律师 → web_search（命中权威法条源 → 汇入来源卡）+ 法条核验报告.md
        content_delta("再逐条核验法条与时效。"),
        tool_use_start("dc2", "delegate", {"tasks": [{"role": "核验律师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="逐条核验法条 / 时效",
            agents=verify_agents,
            runs=verify_runs,
        ),
        run_started(r_verify, w_verify),
        run_tool_progress(r_verify, w_verify, "web_search", 3),
        tool_use_start(
            "ws1",
            "web_search",
            {"query": "民法典合同编通则解释 违约金 调减"},
            run_id=r_verify,
        ),
        tool_use_end(
            "ws1",
            "web_search",
            success=True,
            output="命中：合同编通则解释第 65 条、民法典第 621 条；送达规则未取到权威原文。",
            run_id=r_verify,
        ),
        run_tool_progress(r_verify, w_verify, "file_write", len(verify_md)),
        tool_use_start(
            "fw2", "file_write", {"path": "法条核验报告.md", "content": verify_md}, run_id=r_verify
        ),
        tool_use_end("fw2", "file_write", success=True, output="已写入", run_id=r_verify),
        run_output_delta(
            r_verify,
            w_verify,
            "核验完成：违约金调减引《合同编通则解释》第 65 条、质量异议引第 621 条；送达规则标 [待核验]。",
        ),
        run_completed(
            r_verify,
            w_verify,
            output_summary="核验完成：法条核验报告.md",
            duration_ms=2000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end(
            "dc2", "delegate", success=True, output="核验完成：法条核验报告.md（1 处 [待核验]）"
        ),
        # ④ 人审通过 → CEO 收口正文出终稿：已核验法条带 [n] 角标连到来源卡，[待核验] 不编角标
        content_delta(
            "人审已通过，现出具答辩状终稿（完整正文如下，《答辩状初稿.md》留作可下载附件）：\n\n"
        ),
        content_delta(final_brief),
        citations_event(
            [
                {
                    "url": "https://flk.npc.gov.cn/minfadian-621",
                    "title": "中华人民共和国民法典 第六百二十一条（买受人质量异议合理期限）",
                    "snippet": "当事人约定检验期限的，买受人应当在检验期限内将标的物数量或质量不符合约定的情形通知出卖人……",
                    "site": "flk.npc.gov.cn",
                },
                {
                    "url": "https://www.court.gov.cn/hetongbian-tongze-jieshi-65",
                    "title": "最高法关于适用民法典合同编通则若干问题的解释 第六十五条（违约金调减）",
                    "snippet": "约定的违约金超过造成损失百分之三十的，一般可以认定为民法典第五百八十五条规定的『过分高于造成的损失』……",
                    "site": "court.gov.cn",
                },
            ]
        ),
        message_end(FinishReason.END_TURN, input_tokens=6200, output_tokens=1100, cost=_COST),
    ]


def _multi_agent_legal_case_analysis() -> list[SSEEvent]:
    """多 Agent：法律「三方视角案情研判」端到端【收场】(远期规划.md §4.5 法律垂直，第二支 skill)。
    与作战室同纪律——复用现有事件类型**组合**出三方研判玻璃箱全流程，**不新增事件类型 → fold
    不碰**。流程按 skill 编排（接案评估场景，载体=买卖合同货款纠纷）：① CEO
    `consult_skill(legal_case_analysis)` 翻三方研判打法；② `debate(form="debate")` 让**原告 / 被告
    两方独立对称对抗**（非红队、无 is_subject）**两轮**收敛（第 2 轮辩手为首轮 `revision=2` 续写、
    `converged` 收场），收场 `debate_result` 承「决策简报 + 交锋叙事线」双产物，交锋收敛到「质量异议
    的举证」；③ `delegate` **中立法官研判** worker 读交锋 → 按举证责任出实体研判、落 `接案研判.md`
    （worker 内 `file_write`）；④ `delegate` 核验 worker `web_search` + 落 `法条核验报告.md`（带
    **出处** + `[待核验]`）；⑤ CEO **收口** `message_end` 出结论提要 + 指向产出文件（标**倾向研判·
    非判决结果预测** + 中国大陆**法域** + **免责** + 人审复核提示）。人审闸门**暂停**态由
    `single_agent_checkpoint` 单独覆盖，本向量取**收场**态（status=success）。"""
    cap = "captain1"
    mod = "analysis_mod1"
    r_judge, w_judge = "r_judge", "w_judge"
    r_verify, w_verify = "r_verify", "w_verify"
    pro_run, con_run = f"{mod}_r1_pro", f"{mod}_r1_con"
    pro_r2, con_r2 = f"{mod}_r2_pro", f"{mod}_r2_con"

    skill_guidance = (
        "## 三方视角案情研判\n"
        "1. 先分流：接案评估 / 诉讼策略。\n"
        "2. debate(form=debate) 原告 vs 被告独立对抗 → delegate 中立法官按举证责任研判 → delegate 核验法条。\n"
        "3. 胜负定性为倾向研判（非判决结果预测）；终稿标法域 + 免责 + 人审闸门。\n"
    )
    judge_md = (
        "# 接案研判（买卖合同货款纠纷）\n"
        "## 一句话研判\n胜算：中偏低（取决于买方能否证明质量异议及时且成立）。\n"
        "## 原告视角（卖方）\n合同 + 送货单 + 对账单可证交付与欠款 80 万，主张买方应付款。\n"
        "## 被告视角（买方）\n主张货物质量不合格、已提质量异议，可拒付并主张减款 / 反诉。\n"
        "## 法官视角（按举证责任）\n"
        "- 交付与货款：举证责任在卖方，现有凭证较充分。\n"
        "- 质量抗辩：举证责任在买方——关键看异议是否在合理期限内书面提出、有无质量鉴定。\n"
        "- 倾向：若买方无及时书面异议 + 鉴定，质量抗辩难成立。【倾向研判，非判决结果预测】\n"
        "## 证据短板清单（要接 / 要赢需补）\n- 质量异议的书面记录及送达凭证\n- 质量鉴定报告\n- 因质量问题造成的损失证据\n"
        "## 风险与告知\n质量抗辩举证不能则可能全额担责；建议据补证情况评估和解。\n"
        "## 下一步\n补齐上述证据后再评估接案 / 主攻方向 / 是否和解。\n"
    )
    verify_md = (
        "# 法条核验报告\n"
        "| 引用 | 现行有效性 | 出处 | 结论 |\n"
        "|---|---|---|---|\n"
        "| 买受人质量异议合理期限 | 现行有效 | 《民法典》第 621 条 | ✅ 已核验 |\n"
        "| 检验期限 / 异议失权 | 现行有效 | 《民法典》第 622 条 | ✅ 已核验 |\n"
        "| 买卖合同司法解释·质量异议举证 | 待确认条款 | web 摘要不足 | ⚠️ [待核验]（转人工 / 库核） |\n"
    )

    mod_agents = [
        {
            "id": mod,
            "role": "主持人",
            "thinking": False,
        },
    ]
    mod_runs = [
        {
            "id": mod,
            "agent_id": mod,
            "task": "主持原被告对抗：买卖合同货款纠纷的攻防",
            "depends_on": [],
            "parent_run_id": cap,
        },
    ]
    debater_agents = [
        {
            "id": "d_pro",
            "role": "原告视角",
            "thinking": True,
        },
        {
            "id": "d_con",
            "role": "被告视角",
            "thinking": True,
        },
    ]
    debater_runs = [
        {
            "id": pro_run,
            "agent_id": "d_pro",
            "task": "以原告（卖方）立场主张交付与欠款",
            "depends_on": [],
            "parent_run_id": mod,
            "stance": "pro",
            "group": "debate:debate",
            "round": 1,
        },
        {
            "id": con_run,
            "agent_id": "d_con",
            "task": "以被告（买方）立场主张质量抗辩",
            "depends_on": [],
            "parent_run_id": mod,
            "stance": "con",
            "group": "debate:debate",
            "round": 1,
        },
    ]
    judge_agents = [
        {
            "id": w_judge,
            "role": "法官研判",
            "thinking": True,
        },
    ]
    judge_runs = [
        {"id": r_judge, "agent_id": w_judge, "task": "中立按举证责任研判并出具接案研判", "depends_on": []}
    ]
    verify_agents = [
        {
            "id": w_verify,
            "role": "核验律师",
            "thinking": True,
        },
    ]
    verify_runs = [
        {"id": r_verify, "agent_id": w_verify, "task": "逐条核验法条与异议期限", "depends_on": []}
    ]
    debate_payload = {
        "form": "debate",
        "motion": "买卖合同货款纠纷：原被告各自最强主张与抗辩、争议焦点何在",
        "stop_reason": "converged",
        "narrative_first": False,
        "sides": [
            {
                "key": "pro",
                "name": "原告视角（卖方）",
                "stance": "已交付货物、买方应付欠款 80 万",
                "is_subject": False,
                "model": "",
            },
            {
                "key": "con",
                "name": "被告视角（买方）",
                "stance": "货物质量不合格、已提异议，可拒付 / 减款",
                "is_subject": False,
                "model": "",
            },
        ],
        "rounds": [
            {
                "round_no": 1,
                "focus": "货款请求权 vs 质量抗辩",
                "summary": "原告以合同 + 送货单 + 对账单主张交付与欠款；被告以质量不合格主张拒付，焦点指向质量异议是否成立。",
                "verdict": {
                    "real_clash": True,
                    "new_arguments": True,
                    "converged": False,
                    "stop_reason": "",
                    "rationale": "双方立场清晰，质量异议的举证尚未交锋，需二轮收口。",
                },
                "sides": [
                    {"key": "pro", "name": "原告视角（卖方）", "run_id": pro_run, "ok": True},
                    {"key": "con", "name": "被告视角（买方）", "run_id": con_run, "ok": True},
                ],
                "clashes": [
                    {
                        "from_key": "con",
                        "to_key": "pro",
                        "point": "货物质量不合格，付款义务应相应减免。",
                    },
                ],
            },
            {
                "round_no": 2,
                "focus": "质量异议的举证：是否及时书面 + 有无鉴定",
                "summary": "原告指出买方未在合理期限内书面异议、无质量鉴定应视为认可；被告承认书面凭证与鉴定确有缺口，焦点收敛到买方举证能否补足。",
                "verdict": {
                    "real_clash": True,
                    "new_arguments": False,
                    "converged": True,
                    "stop_reason": "converged",
                    "rationale": "核心分歧收敛到买方质量异议的举证缺口，无新论据。",
                },
                "sides": [
                    {"key": "pro", "name": "原告视角（卖方）", "run_id": pro_r2, "ok": True},
                    {"key": "con", "name": "被告视角（买方）", "run_id": con_r2, "ok": True},
                ],
                "clashes": [
                    {
                        "from_key": "pro",
                        "to_key": "con",
                        "point": "买方未在合理期限内书面异议、无鉴定，质量抗辩难成立。",
                    },
                ],
            },
        ],
        "brief": {
            "crux": "买方的质量异议能否在举证上成立（是否及时书面 + 有无鉴定）",
            "strongest_points": {
                "pro": "合同 + 送货单 + 对账单可证交付与欠款，凭证较充分。",
                "con": "若能补强及时书面异议 + 质量鉴定，则可主张减款 / 拒付。",
            },
            "handoffs": [
                {"kind": "value", "text": "先和解止损 vs 补证后再诉的策略取舍"},
                {"kind": "fact", "text": "买方是否在合理期限内书面提出质量异议"},
                {"kind": "fact", "text": "是否存在质量鉴定结论"},
                {"kind": "question", "text": "买方主张的减款金额与依据为何？"},
            ],
            "leaning": "在买方补足质量异议举证前，付款义务大概率成立（倾向研判，非判决结果预测）",
            "confidence": "medium",
            "recommendation": "接案前请买方补：① 质量异议的书面记录及送达凭证 ② 质量鉴定报告 ③ 损失证据；据补证情况再定接案 / 和解。",
        },
    }
    return [
        message_start("m1", conversation_id=_CONV),
        content_delta("我先翻一下「三方视角案情研判」的打法。"),
        tool_use_start("cs1", "consult_skill", {"name": "legal_case_analysis"}),
        tool_use_end(
            "cs1",
            "consult_skill",
            success=True,
            output=skill_guidance,
            display={
                "skill_name": "legal_case_analysis",
                "summary": "三方视角案情研判：原被告对抗 + 中立法官 + 反幻觉硬约束",
            },
        ),
        content_delta("这是接案评估场景。先让原告、被告两方独立对抗，再请中立法官按举证责任研判，最后核验法条、请你拍板。"),
        # ① debate(form=debate)：原告 vs 被告 独立对称对抗（两轮收敛）
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="原被告对抗：买卖合同货款纠纷的攻防",
            agents=mod_agents,
            runs=mod_runs,
        ),
        run_started(mod, mod, parent_run_id=cap),
        run_plan(
            execution_id="exec1",
            plan_type="debate",
            task_summary="",
            agents=debater_agents,
            runs=debater_runs,
        ),
        run_started(pro_run, "d_pro", parent_run_id=mod),
        run_output_delta(pro_run, "d_pro", "原告（卖方）：合同 + 送货单 + 对账单证明交付与欠款 80 万。"),
        run_completed(
            pro_run,
            "d_pro",
            output_summary="原告视角陈述完成",
            duration_ms=860,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(con_run, "d_con", parent_run_id=mod),
        run_output_delta(con_run, "d_con", "被告（买方）：货物质量不合格、已提质量异议，主张拒付 / 减款。"),
        run_completed(
            con_run,
            "d_con",
            output_summary="被告视角陈述完成",
            duration_ms=880,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        # 第 2 轮：辩手为首轮 continue_run 续写——交锋收敛到质量异议的举证。
        run_started(pro_r2, "d_pro2", parent_run_id=mod, continues_run_id=pro_run),
        run_output_delta(
            pro_r2, "d_pro2", "原告（续）：买方未在合理期限内书面异议、无质量鉴定，应视为认可。"
        ),
        run_completed(
            pro_r2,
            "d_pro2",
            output_summary="原告二轮完成",
            duration_ms=720,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_started(con_r2, "d_con2", parent_run_id=mod, continues_run_id=con_run),
        run_output_delta(con_r2, "d_con2", "被告（续）：曾口头异议，但书面凭证与鉴定确有缺口。"),
        run_completed(
            con_r2,
            "d_con2",
            output_summary="被告二轮完成",
            duration_ms=700,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        run_completed(
            mod,
            mod,
            output_summary="原被告交锋收敛到质量异议的举证",
            duration_ms=2400,
            role="主持人",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        debate_result(execution_id="exec1", moderator_run_id=mod, payload=debate_payload),
        # ② delegate 中立法官研判 → 接案研判.md（worker 内 file_write）
        content_delta("现在请中立法官按举证责任研判，并出具《接案研判》。"),
        tool_use_start("dc1", "delegate", {"tasks": [{"role": "法官研判"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="中立法官按举证责任研判",
            agents=judge_agents,
            runs=judge_runs,
        ),
        run_started(r_judge, w_judge),
        run_tool_progress(r_judge, w_judge, "file_write", len(judge_md)),
        tool_use_start(
            "fw1", "file_write", {"path": "接案研判.md", "content": judge_md}, run_id=r_judge
        ),
        tool_use_end("fw1", "file_write", success=True, output="已写入", run_id=r_judge),
        run_output_delta(r_judge, w_judge, "研判完成：胜算中偏低，关键在买方质量异议的举证。"),
        run_completed(
            r_judge,
            w_judge,
            output_summary="研判完成：接案研判.md",
            duration_ms=1800,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end("dc1", "delegate", success=True, output="研判完成：接案研判.md"),
        # ③ delegate 核验法官引用的法条 → 法条核验报告.md（出处 + [待核验]）
        content_delta("再逐条核验法官引用的法条与异议期限。"),
        tool_use_start("dc2", "delegate", {"tasks": [{"role": "核验律师"}]}),
        run_plan(
            execution_id="exec1",
            plan_type="multi_agent",
            task_summary="逐条核验法条 / 异议期限",
            agents=verify_agents,
            runs=verify_runs,
        ),
        run_started(r_verify, w_verify),
        run_tool_progress(r_verify, w_verify, "web_search", 3),
        tool_use_start(
            "ws1",
            "web_search",
            {"query": "民法典 买受人 质量异议 合理期限 622条"},
            run_id=r_verify,
        ),
        tool_use_end(
            "ws1",
            "web_search",
            success=True,
            output="命中：民法典第 621 / 622 条；买卖合同司法解释举证细则未取到权威原文。",
            run_id=r_verify,
        ),
        run_tool_progress(r_verify, w_verify, "file_write", len(verify_md)),
        tool_use_start(
            "fw2", "file_write", {"path": "法条核验报告.md", "content": verify_md}, run_id=r_verify
        ),
        tool_use_end("fw2", "file_write", success=True, output="已写入", run_id=r_verify),
        run_output_delta(
            r_verify,
            w_verify,
            "核验完成：异议期限引《民法典》第 621 / 622 条；举证细则标 [待核验]。",
        ),
        run_completed(
            r_verify,
            w_verify,
            output_summary="核验完成：法条核验报告.md",
            duration_ms=2000,
            role="member",
            model="deepseek-v4-flash",
            usage=_USAGE,
            cost=_COST,
        ),
        tool_use_end(
            "dc2", "delegate", success=True, output="核验完成：法条核验报告.md（1 处 [待核验]）"
        ),
        # ⑤ CEO 收口：结论提要 + 指向产出文件（倾向研判·非预测 + 法域 + 免责 + 人审）。
        content_delta(
            "综合原被告对抗与法官研判，出具《接案研判》：胜算中偏低，关键在买方质量异议的举证；已列证据短板与下一步。"
        ),
        content_delta(
            "【倾向研判，非判决结果预测】【法域】中国大陆法。【免责】本文为 AI 辅助研判、非正式法律意见，"
            "亦不构成对诉讼结果的承诺，须执业律师独立判断并人工复核。"
        ),
        message_end(FinishReason.END_TURN, input_tokens=5400, output_tokens=950, cost=_COST),
    ]



VECTORS: dict[str, tuple[str, Callable[[], list[SSEEvent]]]] = {
    "multi_agent_legal_war_room": (
        "法律「答辩状作战室」端到端 hero：consult_skill→delegate 起草→debate(red_team) 原告红队压测→delegate 核验(法条核验报告 + [待核验])→人审闸门 checkpoint 暂停",
        _multi_agent_legal_war_room,
    ),
    "multi_agent_legal_war_room_settled": (
        "法律「答辩状作战室」收口终稿态 (§十一 方案①)：consult_skill→delegate 起草→debate(red_team) "
        "原告红队压测→delegate 核验(web_search 命中法条源)→CEO 收口正文出终稿(已核验法条带 [n] 角标连到"
        "来源卡、[待核验] 不编角标)→citations_event(2 张法条来源卡)→message_end(success)",
        _multi_agent_legal_war_room_settled,
    ),
    "multi_agent_legal_case_analysis": (
        "法律「三方视角案情研判」端到端收场：consult_skill(legal_case_analysis)→debate(form=debate) "
        "原被告两轮对抗→delegate 中立法官研判(接案研判.md)→delegate 核验(法条核验报告 + [待核验])"
        "→CEO 收口(倾向研判·非预测 + 法域 + 免责)",
        _multi_agent_legal_case_analysis,
    ),
}
