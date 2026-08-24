"""收口诚实性（closing_posture）：档位真源 + 薄 A 闭集 + resume 拼接。"""

from agentcore.runtime.closing_posture import (
    claims_full_delivery,
    claims_needs_confirm,
    claims_posture_a,
    claims_posture_c,
    closing_honesty_rework,
    closing_honesty_verdict_hit,
    is_formal_complete_tier,
    mutual_exclusion_rework,
    reconcile_resume_closing,
    resume_continuity_steer,
    tier_forbids_posture_a,
)
from agentcore.runtime.verify import finish_guard


def test_tier_truth_source():
    assert is_formal_complete_tier("delivered")
    assert not is_formal_complete_tier("partial")
    assert not is_formal_complete_tier("notes")
    assert not is_formal_complete_tier("blocked")
    assert tier_forbids_posture_a("partial")
    assert tier_forbids_posture_a("notes")
    assert tier_forbids_posture_a("blocked")
    assert not tier_forbids_posture_a("delivered")


def test_cef27dfa_auc_same_message_not_flagged_without_verdict():
    """cef27dfa：无对账卡时同条 A∪C 不再回炉（团队状态走结构面）。"""
    content = (
        "方向：先问你 / 关键缺口（调研对象未定）调研对象未明确——请确认：\n"
        "三路调研 + 独立审计已全部收卷，以下是决策简报。"
    )
    assert claims_needs_confirm(content)
    assert claims_full_delivery(content)
    assert mutual_exclusion_rework(content) is None
    assert finish_guard(content, citation_count=0) == []


def test_e8fb470c_auc_same_message_not_flagged_without_verdict():
    """e8fb470c：无对账卡时同条「请确认」+「均已落盘」不回炉。"""
    content = (
        "调研可以并行展开，但需要先确认一个关键信息：**调研的对象是什么？**\n"
        "审计已完成，三份调研成稿均已落盘。"
    )
    assert finish_guard(content, citation_count=0) == []


def test_auc_skipped_for_workers():
    content = "请确认调研对象。三路调研已全部收卷。"
    assert (
        finish_guard(content, citation_count=0, check_citations=False) == []
    )


def test_confirm_only_passes():
    assert mutual_exclusion_rework("需要先确认一个关键信息：调研对象是什么？") is None
    assert finish_guard("请确认后继续。", citation_count=0) == []


def test_delivery_only_passes_without_verdict():
    assert (
        mutual_exclusion_rework("三路调研 + 独立审计已全部收卷，以下是决策简报。")
        is None
    )


def test_reconcile_drops_ask_pre_pause_when_resume_dispatched():
    """0cb83288：先问你 pre_pause ∪ 派团队续写 → 只保留续写（禁叠写）。"""
    pre = (
        "方向：先问你 — 日程必须基于你的实际情况，否则只能给空模板。\n\n"
        "要排出真正能用的日程，我需要先了解几个关键点："
    )
    new = (
        "方向：派团队 — 信息已够，按「上班族 + 半天块」的通用模板排，"
        "按确认默认落盘。"
    )
    out = reconcile_resume_closing(pre, new)
    assert "方向：先问你" not in out
    assert "方向：派团队" in out
    assert "按确认默认" in out


def test_reconcile_keeps_structured_pre_pause_over_hollow_template():
    """旧 pause 空壳续写不得冲掉上轮结构化确认正文。"""
    legacy_hollow = "等待确认后再派工；此前尚未真正开工。"

    pre = (
        "交付状态：尚未开工（等待目录恢复）。请选择："
        "重新打开/授权 / 告知新路径 / 改审名册其他项目，然后回复「已恢复」。"
    )
    out = reconcile_resume_closing(pre, legacy_hollow)
    assert "重新打开/授权" in out
    assert "已恢复" in out
    assert out != legacy_hollow
    # Hollow pre_pause must not seed / join over real resume content.
    assert reconcile_resume_closing(legacy_hollow, "日程已落盘。") == "日程已落盘。"


def test_rewrite_stale_ask_after_dispatch_same_message():
    """同条叠写「先问你」+「派团队」→ 剥 ask 残留，标已按默认开工。"""
    from agentcore.runtime.closing_posture import rewrite_stale_ask_after_dispatch

    content = (
        "方向：先问你 — 日程必须基于你的实际情况。\n\n"
        "要了解几个关键点：\n\n"
        "方向：派团队 — 信息已够，按上班族通用节奏排。\n\n"
        "日程文档已落盘。"
    )
    out = rewrite_stale_ask_after_dispatch(content)
    assert "方向：先问你" not in out
    assert "方向：派团队" in out
    assert "已按默认开工" in out or "按确认默认" in out
    assert "日程文档已落盘" in out


def test_resume_continuity_steer_for_confirm_pre_pause():
    steer = resume_continuity_steer(
        prior_deliverable="需要先确认一个关键信息：调研对象是什么？"
    )
    assert "禁止" in steer
    assert "请确认" in steer
    assert "档位" in steer
    assert "按确认默认" in steer
    assert "先问你" in steer
    assert "复述" in steer or "沿用" in steer
    assert "路径" in steer
    assert "空转确认" in steer or "不承接选项" in steer


def test_reconcile_drops_confirm_pre_pause_when_new_delivers():
    """resume 拼接真源：C pre_pause ∪ A 续写 → 只保留续写。"""
    pre = "方向：先问你 / 关键缺口。调研对象未明确——请确认："
    new = "三路调研 + 独立审计已全部收卷，以下是决策简报。"
    assert reconcile_resume_closing(pre, new) == new


def test_reconcile_keeps_neutral_join():
    pre = "阶段成果如下：已整理竞品表。"
    new = "接下来补渠道策略一节。"
    out = reconcile_resume_closing(pre, new)
    assert "竞品表" in out
    assert "渠道策略" in out


def test_resume_continuity_steer_falls_back_for_deliverable():
    steer = resume_continuity_steer(prior_deliverable="已交付的前半段分析如下。")
    assert "自然衔接续写" in steer


def test_reconcile_drops_dispatch_kickoff_pre_pause():
    """plan_review 后续写：派工 kickoff 不拼进交付终稿（ce1ecfc2 流水账）。"""
    from agentcore.runtime.closing_posture import is_process_dispatch_preamble

    pre = (
        "方向：派团队 — 用户明示 research_report 成文落盘，"
        "主体（医学报告生成近三年文献）已点名，直接开委派。"
    )
    new = (
        "综述终稿已落盘 `AgentCore/文档/research/报告.md`；"
        "审校指出的事实错误已修订。建议下一步核验两处出处。"
    )
    assert is_process_dispatch_preamble(pre)
    assert reconcile_resume_closing(pre, new) == new
    assert "派团队" not in reconcile_resume_closing(pre, new)


def test_resume_continuity_steer_for_dispatch_kickoff():
    steer = resume_continuity_steer(
        prior_deliverable="方向：派团队 — 直接开委派，组建团队并行调研。"
    )
    assert "交付说明" in steer
    assert "自然衔接续写" not in steer
    assert "已交付前文如下" not in steer


def test_partial_verdict_rejects_posture_a():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("research/a.md",),
        execution_id="e1",
    )
    assert closing_honesty_verdict_hit("三路调研已全部收卷。", verdict) == "posture_a"
    assert closing_honesty_rework("三路调研已全部收卷。", verdict) is None
    reworks = finish_guard(
        "三路调研已全部收卷。",
        citation_count=0,
        delivery_verdict=verdict,
    )
    assert not any("姿势 A" in r or "档位" in r for r in reworks)


def test_partial_verdict_rejects_gathered_claim():
    """案面「已收齐」：partial/blocked/notes 时与收卷同属姿势 A。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("research/a.md",),
        execution_id="e1",
    )
    for claim in (
        "三路调研已收齐，汇总如下。",
        "已全部收齐。",
        "全部收齐，以下是决策简报。",
        "已收齐。",
    ):
        assert claims_posture_a(claim), claim
        assert closing_honesty_verdict_hit(claim, verdict) == "posture_a", claim
        reworks = finish_guard(
            claim,
            citation_count=0,
            delivery_verdict=verdict,
        )
        assert not any("姿势 A" in r or "档位" in r for r in reworks), claim


def test_notes_verdict_rejects_posture_a():
    """notes ≈ 草稿·部分：非正式完成，不得姿势 A。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="notes",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    assert closing_honesty_verdict_hit("全部完成，产物见工作区。", verdict) == "posture_a"
    reworks = finish_guard(
        "全部完成，产物见工作区。",
        citation_count=0,
        delivery_verdict=verdict,
        overview_max_chars=1000,
    )
    assert not any("姿势 A" in r or "档位" in r for r in reworks)


def test_gathered_auc_same_message_not_flagged_without_verdict():
    """无对账卡：同条「请确认」+「已收齐」不回炉。"""
    content = (
        "方向：先问你 / 关键缺口（调研对象未定）调研对象未明确——请确认：\n"
        "三路调研已收齐，汇总如下。"
    )
    assert claims_posture_c(content)
    assert claims_posture_a(content)
    assert mutual_exclusion_rework(content) is None
    assert finish_guard(content, citation_count=0) == []


def test_bare_completed_not_posture_a():
    """修码/建站正常「已完成」不得误伤——裸「已完成」不进姿势 A 闭集。"""
    assert not claims_posture_a("修码已完成，详见 diff。")
    assert not claims_posture_a("站点已完成基础搭建。")
    assert not claims_posture_a("页面做好了，仍有缺口。")


def test_partial_requires_draft_acknowledgment_without_adding_completion_words():
    """evidence 降档（requires_draft_ack）时「综述已完成」不进姿势 A，缺承认 → 回炉。"""
    from agentcore.runtime.closing_posture import claims_draft_acknowledgment
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("AgentCore/文档/research/报告.md",),
        execution_id="e1",
        requires_draft_ack=True,
    )
    hollow = (
        "综述已完成。团队产出了一份 499 行、约 15,000 字的全面综述，"
        "结构如下。要我做下一步处理吗？"
    )
    assert not claims_posture_a(hollow)
    assert not claims_draft_acknowledgment(hollow)
    assert closing_honesty_verdict_hit(hollow, verdict) == "draft_ack"
    assert closing_honesty_rework(hollow, verdict) is None

    honest = (
        "先交一版草稿（证据不足）：缺参考文献列表，关键数据待核实。"
        "成稿见工作区；要我按审校意见补引用吗？"
    )
    assert claims_draft_acknowledgment(honest)
    assert closing_honesty_verdict_hit(honest, verdict) is None
    assert closing_honesty_rework(honest, verdict) is None


def test_notes_without_posture_a_does_not_require_draft_ack():
    """notes 仅软提醒：无姿势 A 时不强制草稿承认句。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="notes",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    assert (
        closing_honesty_rework("修码已完成，详见 diff；另有一处软提醒。", verdict)
        is None
    )


def test_ordinary_partial_without_draft_flag_allows_bare_delivered():
    """普通 partial（无 requires_draft_ack）不强制草稿承认——勿误伤建站裸「已交付」。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
        requires_draft_ack=False,
    )
    assert closing_honesty_rework("主页已交付，详见产物卡。", verdict) is None


def test_node_failed_draft_ack_blocks_hollow_verified_landing_opening():
    """能力4：partial+requires_draft_ack 时「全部核实落盘」类开场不能裸过（靠 latch，不加词）。"""
    from agentcore.runtime.closing_posture import claims_draft_acknowledgment
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    # 实测漏拦：该句不进姿势 A 闭集——禁加词，靠 draft_ack 闩。
    hollow = "审计已收口，全部结果已核实落盘。各模块如下。"
    assert not claims_posture_a(hollow)
    assert not claims_draft_acknowledgment(hollow)

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("AgentCore/文档/reviews/a.md",),
        execution_id="e-cap4",
        requires_draft_ack=True,
    )
    assert closing_honesty_verdict_hit(hollow, verdict) == "draft_ack"
    assert closing_honesty_rework(hollow, verdict) is None

    honest = (
        "部分完成：模块B契约未过，已验收见产物卡，仍有缺口。"
        "要我续派补跑吗？"
    )
    assert claims_draft_acknowledgment(honest)
    assert closing_honesty_rework(honest, verdict) is None


def test_capability4_does_not_expand_posture_a_for_verified_landing():
    """否决：不得把「核实落盘」加进姿势 A；漏拦走 draft_ack。"""
    assert not claims_posture_a("全部结果已核实落盘")
    assert not claims_posture_a("全部核实落盘")


def test_thin_review_expansion_withdrawn_from_posture_a():
    """✅ 收窄姿势 A：撤回 20260803「复核/已修复/可玩」扩面；乙修好/验绿仍在。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    for claim in (
        "独立复核通过，可以收工。",
        "复核通过。",
        "审查通过。",
        "验收通过。",
        "炮塔购买已修复。",
        "现在可玩了。",
        "已经可以玩。",
        "本轮修复通过。",
    ):
        assert not claims_posture_a(claim), claim
        assert (
            finish_guard(
                claim,
                citation_count=0,
                delivery_verdict=verdict,
            )
            == []
        ), claim
    # 乙骨架保留：修好 / 验绿仍属姿势 A。
    assert claims_posture_a("缺陷已修好，可以收工。")
    assert claims_posture_a("验证通过，可以交付。")
    assert claims_posture_a("bug 已修复。")


def test_bare_pass_without_review_prefix_not_posture_a():
    """裸「通过」不得进姿势 A——避免误伤「测试摘要：通过 3」。"""
    assert not claims_posture_a("摘要：通过 3 / 失败 0。")
    assert not claims_posture_a("请通过左侧栏购买炮塔。")


def test_ceo_mutation_honesty_banner_withdrawn():
    """2026-08-04：只删【落盘说明】横幅；enforce 恒等；检测器仍可用。"""
    from agentcore.runtime.closing_posture import (
        asks_whole_file_user_paste,
        claims_ceo_mutation_done,
        enforce_ceo_mutation_honesty,
    )

    claim = "标题已修改，计时逻辑已修正，请自行替换整文件。"
    assert claims_ceo_mutation_done(claim)
    assert asks_whole_file_user_paste(claim)
    assert enforce_ceo_mutation_honesty(claim, landing_succeeded=False) == claim
    assert "【落盘说明】" not in enforce_ceo_mutation_honesty(claim)

    check = "我对了一下工作区，文件里已经是新版本，不是我本轮又改了。"
    assert not claims_ceo_mutation_done(check)
    assert enforce_ceo_mutation_honesty(check, landing_succeeded=False) == check
    assert not claims_ceo_mutation_done("已处理你的疑问，下面解释原因。")
    assert enforce_ceo_mutation_honesty(claim, landing_succeeded=True) == claim


def test_cloud_web_verify_honesty_banner_soft_only():
    """案 cloud-web-install-deny：装包拒/验证缺口 + 称跑绿 → 横幅；无闩锁或无口感 → 不拦。"""
    from agentcore.runtime.closing_posture import (
        claims_cloud_web_verify_green,
        clear_cloud_web_verify_gap,
        enforce_cloud_web_verify_honesty,
        note_cloud_web_verify_gap,
        note_cloud_web_verify_gap_from_delivery,
        turn_has_cloud_web_verify_gap,
    )

    clear_cloud_web_verify_gap()
    claim = "沙箱内能自检的链路全部通过，后端单测已跑绿；请你本机 npm install。"
    assert claims_cloud_web_verify_green(claim)
    assert enforce_cloud_web_verify_honesty(claim) == claim  # 无闩锁

    note_cloud_web_verify_gap()
    assert turn_has_cloud_web_verify_gap()
    out = enforce_cloud_web_verify_honesty(claim)
    assert out.startswith("【验证说明】")
    assert "已跑绿" in out
    # 幂等：已有横幅不再叠
    assert enforce_cloud_web_verify_honesty(out) == out

    # 诚实收口不拦
    honest = "源码已落盘；云端无法装包，请 export_to_local 后本机 npm install → build。"
    assert not claims_cloud_web_verify_green(honest)
    assert enforce_cloud_web_verify_honesty(honest) == honest

    clear_cloud_web_verify_gap()
    note_cloud_web_verify_gap_from_delivery(
        [{"reason": "verify_failed", "description": "测试未通过（test_run 未全部通过）"}]
    )
    assert turn_has_cloud_web_verify_gap()
    clear_cloud_web_verify_gap()
    note_cloud_web_verify_gap_from_delivery(
        criteria_gaps=["提醒（不阻断验收）：已落盘 .ts/.tsx，建议补一次验证（test_run）"]
    )
    assert turn_has_cloud_web_verify_gap()
    clear_cloud_web_verify_gap()
    note_cloud_web_verify_gap_from_delivery(
        [{"description": "无法装包：无出网·无 chokepoint"}]
    )
    assert turn_has_cloud_web_verify_gap()

    # 案 88625：记分板「13/13 OK」在 verify_failed 闩锁下走软横幅（不进姿势 A）
    clear_cloud_web_verify_gap()
    scoreboard = (
        "测试｜`python -m unittest …` → **13/13 OK，exit code 0**；"
        "`pytest -q` → **13 passed**"
    )
    assert claims_cloud_web_verify_green(scoreboard)
    assert enforce_cloud_web_verify_honesty(scoreboard) == scoreboard  # 无闩
    note_cloud_web_verify_gap_from_delivery(
        [{"reason": "verify_failed", "description": "测试未通过（test_run 未全部通过）"}]
    )
    scored = enforce_cloud_web_verify_honesty(scoreboard)
    assert scored.startswith("【验证说明】")
    assert "13/13 OK" in scored
    clear_cloud_web_verify_gap()
    clear_cloud_web_verify_gap()


def test_max_rounds_ceiling_honesty_steer_and_banner():
    """max_rounds：steer 禁止无条件通过；仍宣称姿势 A → 加收口说明横幅。"""
    from agentcore.runtime.closing_posture import (
        ceiling_honesty_steer,
        downgrade_verdict_for_max_rounds,
        enforce_ceiling_closing_honesty,
    )
    from agentcore.runtime.delegate.delivery_status import (
        DeliveryVerdict,
        current_delivery_verdict,
    )

    steer = ceiling_honesty_steer(reason="max_rounds")
    assert steer is not None
    assert "部分落地" in steer
    assert "max_rounds" in steer
    assert "continue_from_run_id" in steer
    assert "replaces_run_id" in steer

    dishonest = "修复已全部完成，已完整可用。"
    out = enforce_ceiling_closing_honesty(dishonest, reason="max_rounds")
    assert out.startswith("【收口说明】")
    assert "已全部完成" in out
    assert "continue_from_run_id" in out
    # 扩面词族已撤回：仅「复核通过/可玩」不再触发横幅。
    thin = "独立复核通过，现在可玩了。"
    assert not claims_posture_a(thin)
    assert enforce_ceiling_closing_honesty(thin, reason="max_rounds") == thin
    assert enforce_ceiling_closing_honesty("部分落地，炮塔栏仍缺一行。", reason="max_rounds") == (
        "部分落地，炮塔栏仍缺一行。"
    )

    token = current_delivery_verdict.set(
        DeliveryVerdict(state="delivered", delivered_files=("a.ts",), execution_id="e1")
    )
    try:
        downgrade_verdict_for_max_rounds()
        v = current_delivery_verdict.get()
        assert v is not None
        assert v.state == "partial"
    finally:
        current_delivery_verdict.reset(token)


def test_token_budget_ceiling_honesty_steer_and_banner_symmetric_with_max_rounds():
    """token_budget ↔ max_rounds：诚实 steer / 姿势 A 横幅 / verdict 降档对称。"""
    from agentcore.runtime.closing_posture import (
        ceiling_honesty_steer,
        downgrade_verdict_for_ceiling,
        enforce_ceiling_closing_honesty,
    )
    from agentcore.runtime.delegate.delivery_status import (
        DeliveryVerdict,
        current_delivery_verdict,
    )

    steer = ceiling_honesty_steer(reason="token_budget")
    assert steer is not None
    assert "token_budget" in steer
    assert "部分落地" in steer
    assert "continue_from_run_id" in steer
    assert "replaces_run_id" in steer
    assert "禁止并行" in steer
    assert ceiling_honesty_steer(reason="other") is None

    dishonest = "修复已全部完成，已完整可用。"
    out = enforce_ceiling_closing_honesty(dishonest, reason="token_budget")
    assert out.startswith("【收口说明】")
    assert "token" in out.lower() or "预算" in out
    assert "已全部完成" in out
    assert "continue_from_run_id" in out
    assert "replaces_run_id" in out
    # 非姿势 A 不因 ceiling banner  alone 改写（「完整落盘」不进姿势 A）。
    complete_landing = "文档已完整落盘（六章全部）。"
    assert not claims_posture_a(complete_landing)
    assert (
        enforce_ceiling_closing_honesty(complete_landing, reason="token_budget")
        == complete_landing
    )
    assert enforce_ceiling_closing_honesty("部分落地，缺收口。", reason="token_budget") == (
        "部分落地，缺收口。"
    )

    token = current_delivery_verdict.set(
        DeliveryVerdict(state="delivered", delivered_files=("a.ts",), execution_id="e-tb")
    )
    try:
        downgrade_verdict_for_ceiling(reason="token_budget")
        v = current_delivery_verdict.get()
        assert v is not None
        assert v.state == "partial"
    finally:
        current_delivery_verdict.reset(token)


def test_cutoff_delivery_gap_ceo_soft_banner_not_posture_a_expansion():
    """B′：token_budget gap latch → CEO 综收软横幅；「完整落盘」不靠扩姿势 A。"""
    from agentcore.runtime.closing_posture import (
        clear_cutoff_delivery_gap,
        enforce_cutoff_closing_honesty,
        note_cutoff_delivery_gap_from_delivery,
        turn_has_cutoff_delivery_gap,
    )

    clear_cutoff_delivery_gap()
    assert not turn_has_cutoff_delivery_gap()
    note_cutoff_delivery_gap_from_delivery(
        [{"description": "成篇未写完", "reason": "token_budget"}]
    )
    assert turn_has_cutoff_delivery_gap()

    # 本案用户可见句：不进姿势 A，靠结构化 latch 软横幅 + continue_from 续作教法。
    dishonest = "文档已完整落盘（六章全部），可直接使用。"
    assert not claims_posture_a(dishonest)
    bannered = enforce_cutoff_closing_honesty(dishonest)
    assert bannered.startswith("【收口说明】")
    assert "部分交付" in bannered
    assert "完整落盘" in bannered
    assert "continue_from_run_id" in bannered
    assert "replaces_run_id" in bannered
    assert "禁止并行" in bannered
    assert "continue_writing" not in bannered

    # 已诚实部分交付 → 不叠横幅。
    honest = "部分交付：前五章已落盘，第六章未闭合，建议续派补齐。"
    assert enforce_cutoff_closing_honesty(honest) == honest

    clear_cutoff_delivery_gap()
    assert enforce_cutoff_closing_honesty(dishonest) == dishonest


def test_unresolved_write_ownership_downgrades_verdict_no_dinggao_hard_reject():
    """案 P0-B：未解写权冲突 → 内部降档；「定稿」正文不硬拒。"""
    from agentcore.runtime.closing_posture import (
        clear_unresolved_write_ownership,
        collect_unresolved_write_ownership_paths,
        downgrade_verdict_for_unresolved_write_ownership,
        enforce_write_ownership_honesty,
        note_unresolved_write_ownership,
        turn_has_unresolved_write_ownership,
    )
    from agentcore.runtime.delegate.delivery_status import (
        DeliveryVerdict,
        current_delivery_verdict,
    )
    from agentcore.runtime.verify import finish_guard
    from agentcore.workspace.write_claims import WriteCoordinator

    clear_unresolved_write_ownership()
    coord = WriteCoordinator({"plan.md": "del_owner"})
    # Merger refused on claim — still held by owner → unresolved.
    assert coord.claim("plan.md", "del_merger", frozenset()) == "del_owner"
    paths = collect_unresolved_write_ownership_paths(
        run_ids={"del_merger", "del_owner"},
        coordinator=coord,
    )
    assert paths == ("plan.md",)
    note_unresolved_write_ownership(run_id="del_merger")
    assert turn_has_unresolved_write_ownership()

    token = current_delivery_verdict.set(
        DeliveryVerdict(
            state="delivered",
            delivered_files=("plan.md",),
            execution_id="e-own",
        )
    )
    try:
        downgrade_verdict_for_unresolved_write_ownership(
            execution_id="e-own",
            run_ids={"del_merger", "del_owner"},
            coordinator=coord,
        )
        v = current_delivery_verdict.get()
        assert v is not None
        assert v.state == "partial"
        assert turn_has_unresolved_write_ownership()
    finally:
        current_delivery_verdict.reset(token)

    # Soft banner only for posture A — 「定稿」 alone is not posture A / not hard-rejected.
    dinggao = "主文件已定稿，两轮审校闭环，可进 W1。"
    assert enforce_write_ownership_honesty(dinggao) == dinggao
    assert finish_guard(
        dinggao,
        citation_count=0,
        delivery_verdict=DeliveryVerdict(
            state="partial",
            delivered_files=("plan.md",),
            execution_id="e-own",
        ),
    ) == []  # no「定稿」硬拒

    # Posture A + latch → soft banner only (no discard).
    posture_a = "三路产出已全部收卷，已完整可用。"
    bannered = enforce_write_ownership_honesty(posture_a)
    assert bannered.startswith("【写权说明】")
    assert "已全部收卷" in bannered

    # Structured transfer resolves → latch clears, no downgrade.
    clear_unresolved_write_ownership()
    note_unresolved_write_ownership(run_id="del_merger")
    coord.transfer("plan.md", "del_merger")
    token2 = current_delivery_verdict.set(
        DeliveryVerdict(
            state="delivered",
            delivered_files=("plan.md",),
            execution_id="e-own",
        )
    )
    try:
        downgrade_verdict_for_unresolved_write_ownership(
            execution_id="e-own",
            run_ids={"del_merger", "del_owner"},
            coordinator=coord,
        )
        assert not turn_has_unresolved_write_ownership()
        assert current_delivery_verdict.get().state == "delivered"
    finally:
        current_delivery_verdict.reset(token2)
        clear_unresolved_write_ownership()


# --- B1 收口对账 ---


def test_b1_browser_success_latch_accepts_new_and_legacy_names():
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
    from agentcore.runtime.closing_posture import (
        clear_b1_closing_latches,
        note_browser_tool_success_from_messages,
        turn_has_browser_tool_success,
    )

    clear_b1_closing_latches()
    legacy = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="n1",
                    function=ToolCallFunction(name="browser_navigate", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content="opened", tool_call_id="n1"),
    ]
    note_browser_tool_success_from_messages(legacy)
    assert turn_has_browser_tool_success()

    clear_b1_closing_latches()
    unified = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="n2",
                    function=ToolCallFunction(
                        name="browser", arguments='{"action":"navigate"}'
                    ),
                )
            ],
        ),
        LLMMessage(role="tool", content="opened", tool_call_id="n2"),
    ]
    note_browser_tool_success_from_messages(unified)
    assert turn_has_browser_tool_success()
    clear_b1_closing_latches()


def test_b1_browser_claim_requires_tool_success():
    """17cafc76：未装配/无 browser 成功时禁称已打开右坞."""
    from agentcore.runtime.closing_posture import (
        claims_browser_open_or_login,
        clear_b1_closing_latches,
        closing_honesty_rework,
        note_browser_assembled,
        note_browser_tool_success,
    )

    clear_b1_closing_latches()
    note_browser_assembled(False)
    claim = "登录页已在右坞浏览器打开并渲染成功，请填写用户名。"
    assert claims_browser_open_or_login(claim)
    rework = closing_honesty_rework(claim)
    assert rework is not None
    assert "未装配" in rework or "browser" in rework.lower()

    note_browser_assembled(True)
    rework2 = closing_honesty_rework(claim)
    assert rework2 is not None
    assert "browser_*" in rework2 or "工具成功" in rework2

    note_browser_tool_success()
    assert closing_honesty_rework(claim) is None
    clear_b1_closing_latches()


def test_b1_zero_write_landing_hard_rework_withdrawn():
    """2026-08-09 定案 B：零写落盘声称扫词硬回炉已撤；检测器仍可用；不恢复横幅."""
    from agentcore.runtime.closing_posture import (
        claims_disk_landing,
        clear_b1_closing_latches,
        closing_honesty_rework,
        enforce_ceo_mutation_honesty,
    )
    from agentcore.runtime.closing_posture.ceo_mutation import _zero_write_landing_rework

    clear_b1_closing_latches()
    claim = "评审报告已落盘 `AgentCore/文档/reviews/v3.md`，验证通过。"
    assert claims_disk_landing(claim)
    assert _zero_write_landing_rework(claim) is None
    # 无对账卡：不再因落盘词硬回炉（亦非 A∪C）。
    assert closing_honesty_rework(claim) is None
    assert "【落盘说明】" not in enforce_ceo_mutation_honesty(claim)
    # 解释规则时的禁语举例不得再清气泡。
    meta = "时序诚实：没落盘成功之前，不宣称「已改好」。"
    assert closing_honesty_rework(meta) is None
    clear_b1_closing_latches()


def test_b1_over_seat_forces_partial_gap_checklist():
    """e94dcd6b：超席闩锁 → 禁仍在进行 / 须缺口承认."""
    from agentcore.runtime.closing_posture import (
        clear_b1_closing_latches,
        closing_honesty_rework,
        note_over_seat_reject,
    )

    clear_b1_closing_latches()
    note_over_seat_reject(task_count=31, max_tasks=20)
    hanging = "目前仍在进行第三层审查，尚未形成最终审查结论。"
    rework = closing_honesty_rework(hanging)
    assert rework is not None
    assert "PARTIAL" in rework or "缺口" in rework
    honest = "部分完成：前 12 席有摘要；其余因超席未跑，缺口清单如下，建议分批续派。"
    assert closing_honesty_rework(honest) is None
    clear_b1_closing_latches()


def test_b1_ceiling_bans_hollow_teach_invite():
    """1eb5eb99 C：ceiling 后禁空心请开讲."""
    from agentcore.runtime.closing_posture import (
        ceiling_honesty_steer,
        claims_hollow_teach_invite,
        clear_cutoff_delivery_gap,
        enforce_ceiling_closing_honesty,
        enforce_cutoff_closing_honesty,
        note_cutoff_delivery_gap,
    )

    hollow = "好，我在听——请讲，第一部分怎么写。"
    assert claims_hollow_teach_invite(hollow)
    steer = ceiling_honesty_steer(reason="token_budget")
    assert steer is not None
    assert "请开讲" in steer or "请讲" in steer
    assert "continue_from_run_id" in steer
    out = enforce_ceiling_closing_honesty(hollow, reason="token_budget")
    assert out.startswith("【收口说明】")
    assert "开讲" in out or "请讲" in out or "硬顶" in out

    clear_cutoff_delivery_gap()
    note_cutoff_delivery_gap()
    cut = enforce_cutoff_closing_honesty(hollow)
    assert cut.startswith("【收口说明】")
    clear_cutoff_delivery_gap()


def test_b1_cancel_zero_does_not_force_gap_ack():
    """取消零落盘不再闩 B1：仅 note 不得要求缺口承认."""
    from agentcore.runtime.closing_posture import (
        clear_b1_closing_latches,
        closing_honesty_rework,
        note_cancel_zero_output,
    )

    clear_b1_closing_latches()
    note_cancel_zero_output()
    thin = "好的，我重新建图派工继续完成。"
    assert closing_honesty_rework(thin) is None
    clear_b1_closing_latches()


def test_delivery_verdict_set_in_child_task_readable_via_shared_ledger():
    """后台 Task 的 ContextVar set 到不了父任务；共享 ledger 槽必须能读到。"""
    import asyncio

    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.delegate.delivery_status import (
        DeliveryVerdict,
        bind_delivery_verdict,
        current_delivery_verdict,
        read_delivery_verdict,
    )
    from agentcore.runtime.engine.directive import Return
    from agentcore.runtime.engine.outcome import RoundOutcome
    from agentcore.runtime.engine.round import decide_no_tool_round
    from agentcore.runtime.loop_controller import LoopController
    from agentcore.tools.protocol import TurnPromotionLedger

    ledger = TurnPromotionLedger()
    stamped = DeliveryVerdict(
        state="partial",
        delivered_files=("research/a.md",),
        execution_id="e-bg",
        requires_draft_ack=True,
        gap_reasons=("evidence_deficit", "thin_review"),
    )

    async def child() -> None:
        bind_delivery_verdict(stamped, promotion_ledger=ledger)

    async def parent() -> None:
        await asyncio.create_task(child())
        assert current_delivery_verdict.get() is None
        got = read_delivery_verdict(promotion_ledger=ledger)
        assert got is not None
        assert got.state == "partial"
        assert got.execution_id == "e-bg"
        assert got.gap_reasons == ("evidence_deficit", "thin_review")

    asyncio.run(parent())

    claim = "三路调研已全部收卷。"
    assert closing_honesty_verdict_hit(claim, ledger.delivery_verdict) == "posture_a"
    controller = LoopController(
        empty_threshold=2,
        tool_failure_warn=3,
        tool_failure_disable=5,
        unproductive_threshold=3,
        convergence_finalize_rounds=3,
        investigation_tools=frozenset(),
    )
    directive = decide_no_tool_round(
        RoundOutcome(content=claim, reasoning="", usage=TokenUsage()),
        final_content=claim,
        controller=controller,
        annotate_citations=True,
        citation_sink=None,
        finish_guard_reworks=0,
        promotion_ledger=ledger,
    )
    assert isinstance(directive, Return)


def test_honesty_shadow_does_not_rework_or_reset(monkeypatch):
    """本该回炉的档位命中只打影子日志，不回炉、不 content_reset。"""
    from agentcore.llm.provider.protocol import TokenUsage
    from agentcore.runtime.closing_posture import core as honesty_core
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict
    from agentcore.runtime.engine.directive import Return
    from agentcore.runtime.engine.outcome import RoundOutcome
    from agentcore.runtime.engine.round import decide_no_tool_round
    from agentcore.runtime.loop_controller import LoopController
    from agentcore.tools.protocol import TurnPromotionLedger
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(honesty_core, "logger", spy)

    ledger = TurnPromotionLedger()
    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("AgentCore/文档/research/报告.md",),
        execution_id="e-shadow",
        requires_draft_ack=True,
        gap_reasons=("evidence_deficit",),
    )
    ledger.delivery_verdict = verdict
    claim = "三路调研已全部收卷。"
    assert closing_honesty_verdict_hit(claim, verdict) == "posture_a"
    assert closing_honesty_rework(claim, verdict) is None
    fields = spy.get("engine.finish_guard_honesty_shadow")
    assert fields["verdict_state"] == "partial"
    assert fields["hit"] == "posture_a"
    assert fields["has_delivered_files"] is True
    assert fields["gap_reasons"] == ["evidence_deficit"]
    assert "content" not in fields
    assert "preview" not in fields
    assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == []

    hollow = "综述已完成。团队产出了一份全面综述。"
    assert closing_honesty_verdict_hit(hollow, verdict) == "draft_ack"
    assert closing_honesty_rework(hollow, verdict) is None
    draft_fields = [
        kw for name, kw in spy.events if name == "engine.finish_guard_honesty_shadow"
    ][-1]
    assert draft_fields["hit"] == "draft_ack"
    assert draft_fields["gap_reasons"] == ["evidence_deficit"]

    controller = LoopController(
        empty_threshold=2,
        tool_failure_warn=3,
        tool_failure_disable=5,
        unproductive_threshold=3,
        convergence_finalize_rounds=3,
        investigation_tools=frozenset(),
    )
    directive = decide_no_tool_round(
        RoundOutcome(content=claim, reasoning="", usage=TokenUsage()),
        final_content=claim,
        controller=controller,
        annotate_citations=True,
        citation_sink=None,
        finish_guard_reworks=0,
        promotion_ledger=ledger,
    )
    assert isinstance(directive, Return)
