"""Unit tests for the finish_guard delivery-verification light layer (交付前核验·轻层).

Mirrors the check_contract / out_of_range_markers test posture: finish_guard is a
pure function over ``(content, citation_count)`` returning concrete rework items, and
format_guard_steer renders them into one injected ``[系统提示]``. Coverage spans the
two light-layer checks: fabricated citations and structural completeness (unclosed /
empty-bodied code fences).
"""

from agentcore.runtime.closing_posture import closing_honesty_verdict_hit
from agentcore.runtime.verify import finish_guard, format_guard_steer


def test_in_range_citations_pass():
    assert finish_guard("结论见 [1] 与 [2]。", citation_count=2) == []


def test_no_marker_content_passes():
    assert finish_guard("一段没有任何角标的正文。", citation_count=0) == []


def test_out_of_range_marker_flagged():
    reworks = finish_guard("依据 [3] 可知……", citation_count=2)
    assert len(reworks) == 1
    assert "[3]" in reworks[0]
    assert "编造引用" in reworks[0]


def test_no_citations_flags_any_marker():
    # 0 来源时正文出现 [n] = 编造（与客户端「越界角标降级为纯文本」同义）。
    reworks = finish_guard("据来源 [1] 表明……", citation_count=0)
    assert reworks
    assert "[1]" in reworks[0]


def test_multiple_stray_markers_listed_in_one_item():
    # 镜像真实事故：24 源却写了 [25][27] —— 一条修正项里点名所有越界角标。
    reworks = finish_guard("见 [25] 和 [27]。", citation_count=24)
    assert len(reworks) == 1
    assert "[25]" in reworks[0]
    assert "[27]" in reworks[0]


def test_code_fence_markers_ignored():
    # 复用 out_of_range_markers 的抠除：代码块里的数组下标不是引用角标。
    content = "正文 [1]。\n```python\nfoo = arr[9]\n```\n"
    assert finish_guard(content, citation_count=1) == []


def test_empty_content_passes():
    assert finish_guard("", citation_count=0) == []
    assert finish_guard("   ", citation_count=0) == []


def test_closed_nonempty_fence_passes():
    content = "见下例：\n```python\nprint('hi')\n```\n收工。"
    assert finish_guard(content, citation_count=0) == []


def test_unclosed_fence_flagged():
    reworks = finish_guard("步骤如下：\n```python\nprint(1)", citation_count=0)
    assert len(reworks) == 1
    assert "没有闭合" in reworks[0]


def test_empty_fence_with_language_flagged():
    reworks = finish_guard("示例：\n```python\n```\n", citation_count=0)
    assert len(reworks) == 1
    assert "python" in reworks[0]
    assert "空" in reworks[0]


def test_bare_empty_fence_not_flagged():
    # 无语言标注的空围栏可能是有意排版，保守起见不判（守住近零误报）。
    assert finish_guard("```\n```\n", citation_count=0) == []


def test_indented_empty_fence_flagged():
    # 列表内缩进的围栏（lstrip 后仍是 ```）照样检出。
    reworks = finish_guard("- 代码：\n  ```json\n  ```\n", citation_count=0)
    assert len(reworks) == 1
    assert "json" in reworks[0]


def test_citation_and_structure_combine():
    # 造引用 + 空代码块 = 两条独立修正项。
    content = "见 [5]。\n```python\n```\n"
    reworks = finish_guard(content, citation_count=2)
    assert len(reworks) == 2
    assert any("编造引用" in r for r in reworks)
    assert any("空" in r for r in reworks)


def test_format_steer_renders_problems():
    steer = format_guard_steer(["问题甲", "问题乙"])
    assert steer.startswith("[系统提示]")
    assert "问题甲" in steer
    assert "问题乙" in steer
    assert "核验未通过" in steer


def test_format_steer_empty_when_clean():
    assert format_guard_steer([]) == ""


def test_format_steer_marks_automated_and_suppresses_acknowledgement():
    # 这条 steer 以 role=user 进窗口，模型易把它当用户纠错而回「谢谢指正」——那句寒暄会漏进
    # 可见交付（真实事故）。文案须自证是系统自动核验、非用户，并禁止致谢/复述/寒暄。
    steer = format_guard_steer(["问题甲"])
    assert "自动核验" in steer
    assert "非用户" in steer
    assert "道谢" in steer


def test_guard_to_steer_roundtrip():
    # finish_guard 命中 → format_guard_steer 出一条非空提示；干净 → 空串。
    assert format_guard_steer(finish_guard("坏引用 [9]", citation_count=1)).startswith("[系统提示]")
    assert format_guard_steer(finish_guard("好引用 [1]", citation_count=1)) == ""


def test_ledger_ref_gate_dual_track():
    # #rN 轨：合法放行；伪造回炉项；无标记不启用（Q5）。
    assert (
        finish_guard(
            "见 #r1。",
            citation_count=0,
            check_citations=False,
            citable_ids=frozenset({"#r1"}),
        )
        == []
    )
    bad = finish_guard(
        "见 #r9。",
        citation_count=0,
        check_citations=False,
        citable_ids=frozenset({"#r1"}),
    )
    assert bad and "#r9" in bad[0]
    assert "弱源不可引用" not in bad[0]
    assert (
        finish_guard(
            "无标记正文",
            citation_count=0,
            check_citations=False,
            citable_ids=frozenset(),
        )
        == []
    )


def test_bibliography_announcement_rework():
    entries = [
        {
            "id": "#r1",
            "url": "https://example.com/x",
            "title": "研究生开题答辩公告",
            "snippet": "公示安排",
            "deep_read": True,
            "doc_kind": "announcement",
        }
    ]
    reworks = finish_guard(
        "参见张三. 某问题研究[D]. #r1",
        citation_count=0,
        check_citations=False,
        citable_ids=frozenset({"#r1"}),
        ledger_entries=entries,
    )
    assert reworks and any("开题" in r or "公告" in r for r in reworks)


def test_bibliography_requires_deep_read():
    entries = [
        {
            "id": "#r1",
            "url": "https://example.com/paper",
            "title": "正式论文",
            "snippet": "",
            "deep_read": False,
            "doc_kind": "",
        }
    ]
    reworks = finish_guard(
        "李四. 某某研究[J]. #r1",
        citation_count=0,
        check_citations=False,
        citable_ids=frozenset(),  # search-only 也不在 draft
        ledger_entries=entries,
    )
    assert reworks
    assert any("deep_read" in r for r in reworks)


def test_bibliography_unbound_type_marker_rework():
    """GB/T [D] without any #rN must rework (fabricated thesis-style cite)."""
    reworks = finish_guard(
        "郝万鑫. 某问题研究[D]. 长江大学, 2026.",
        citation_count=0,
        check_citations=False,
        citable_ids=frozenset(),
        ledger_entries=[],  # ledger connected (empty ok)
    )
    assert reworks
    assert any("#rN" in r or "编造" in r or "未核验" in r for r in reworks)


def test_bibliography_bound_type_marker_skips_unbound_gate():
    entries = [
        {
            "id": "#r1",
            "url": "https://example.com/paper",
            "title": "正式论文",
            "snippet": "",
            "deep_read": True,
            "doc_kind": "thesis",
        }
    ]
    assert (
        finish_guard(
            "张三. 某问题研究[D]. #r1",
            citation_count=0,
            check_citations=False,
            citable_ids=frozenset({"#r1"}),
            ledger_entries=entries,
        )
        == []
    )


def test_blocked_empty_delivery_rejects_false_completion_claim():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="blocked", delivered_files=(), execution_id="e1"
    )
    reworks = finish_guard(
        "文件已生成，确认结果：`测试演示.pptx` 已存在于工作区根目录。",
        citation_count=0,
        delivery_verdict=verdict,
    )
    assert len(reworks) == 1
    assert "交付验收" in reworks[0]
    assert "不得宣称" in reworks[0]


def test_blocked_empty_delivery_allows_honest_acknowledgment():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="blocked", delivered_files=(), execution_id="e1"
    )
    assert (
        finish_guard(
            "交付未过关：工作区仍无产物。你可以绑定本地目录后让我继续生成。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_delivery_claim_check_skipped_for_workers():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="blocked", delivered_files=(), execution_id="e1"
    )
    # Workers use check_citations=False — must not inherit CEO delivery claim gate.
    assert (
        finish_guard(
            "文件已生成并已落盘。",
            citation_count=0,
            check_citations=False,
            delivery_verdict=verdict,
        )
        == []
    )


def test_delivered_verdict_allows_completion_claim():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="delivered",
        delivered_files=("测试演示.pptx",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "文件已生成：`测试演示.pptx` 已存在于工作区。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_rejects_all_success_claim():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    claim = "团队已全部完成，所有任务都已就绪，请直接使用。"
    assert closing_honesty_verdict_hit(claim, verdict) == "posture_a"
    assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == []


def test_partial_verdict_rejects_fully_usable_claim():
    """可用性诚实性：blocked/partial +「已完整可用」本轮影子观测，不回炉。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    claim = "质检面板已完整可用，可以开始用了。"
    assert closing_honesty_verdict_hit(claim, verdict) == "posture_a"
    assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == []


def test_blocked_verdict_rejects_fully_usable_claim():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="blocked",
        delivered_files=(),
        execution_id="e1",
    )
    claim = "已经可以使用了。"
    assert closing_honesty_verdict_hit(claim, verdict) == "posture_a"
    assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == []


def test_bare_now_usable_does_not_trigger_fully_usable_gate():
    """收窄：裸「现在可用」不再触发；仍仅在 blocked/partial 生效。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "质检面板现在可用，可以开始试用。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_allows_negated_fully_usable_phrase():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "尚未完整可用：交互层仍有缺口。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_allows_honest_gap_summary():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "已落盘 `src/a.ts`；编译验收未过，尚有缺口，建议下回补跑验证。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_rejects_fixed_claim():
    """乙并入姿势 A：blocked/partial +「已修好」本轮影子观测，不回炉。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    claim = "缺陷已修好，可以收工。"
    assert closing_honesty_verdict_hit(claim, verdict) == "posture_a"
    assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == []


def test_blocked_verdict_rejects_verified_green_claims():
    """乙并入姿势 A：blocked + 验证通过 / 测试已通过 / 已跑通 → 命中但不回炉。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="blocked",
        delivered_files=(),
        execution_id="e1",
    )
    for claim in (
        "验证通过，可以交付。",
        "已验证通过。",
        "测试已通过。",
        "已跑通测试。",
        "验证已绿。",
        "修复已完成。",
        "bug 已修复。",
    ):
        assert closing_honesty_verdict_hit(claim, verdict) == "posture_a", claim
        assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == [], claim


def test_partial_verdict_allows_negated_fixed_phrase():
    """乙：否定前缀「尚未修好」放行（沿用甲 `_GAP_NEGATION_PREFIXES`）。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "尚未修好：约定测试仍失败。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_allows_honest_fix_gap_summary():
    """乙：诚实缺口摘要（未宣称修好/验绿）放行。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("src/fix.py",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "已落盘 `src/fix.py`；约定 pytest 未过，交付卡为部分未满足，下回补验。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_rejects_delivery_done_claims():
    """交付完成闭集并入姿势 A：partial + 文件 → 命中但不回炉。站点「做好了」已退役。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    for claim in (
        "已完成交付，可以收工。",
        "交付已完成。",
        "完成交付。",
        "交付完成。",
        "已经交付完成。",
        "已全部收卷。",
        "三路调研已收齐，汇总如下。",
        "已全部收齐。",
    ):
        assert closing_honesty_verdict_hit(claim, verdict) == "posture_a", claim
        assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == [], claim


def test_site_done_phrase_not_expanded_into_posture_a():
    """禁止案面加词：站点/页面「做好了」不进姿势 A；建站正常收口不误伤。"""
    from agentcore.runtime.closing_posture import claims_posture_a
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    assert not claims_posture_a("站点做好了。")
    assert not claims_posture_a("网站已经做好了。")
    assert not claims_posture_a("页面基本做好了。")
    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "站点做好了，仍有验收缺口。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_allows_negated_delivery_done_phrase():
    """否定前缀「尚未完成交付」放行。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "尚未完成交付：交互层仍有缺口。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_verdict_allows_honest_delivery_gap_with_landed_file():
    """诚实「已落盘 X；缺口」放行（未命中交付完成闭集）。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "已落盘 `site/index.html`；验收缺口仍在，下回补交互层。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_delivery_done_claim_skipped_for_workers():
    """worker check_citations=False：档位姿势闸不继承。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "已完成交付，全部完成。",
            citation_count=0,
            check_citations=False,
            delivery_verdict=verdict,
        )
        == []
    )


def test_partial_with_files_allows_bare_delivered_and_weak_usable():
    """故意不拦：裸「已交付 / 已经交付」与弱「可用」——勿写成失败。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("site/index.html",),
        execution_id="e1",
    )
    for claim in (
        "主页已交付，详见产物卡。",
        "已经交付 `site/index.html`，仍有缺口。",
        "产物可用，但验收未过。",
    ):
        assert (
            finish_guard(
                claim,
                citation_count=0,
                delivery_verdict=verdict,
            )
            == []
        ), claim


def test_partial_verdict_allows_negated_all_success_phrase():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "尚未全部完成：工具层仍缺类型声明。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_blocked_with_files_rejects_all_success_not_file_claim():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    # blocked + some files is unusual but state can be blocked when gaps dominate;
    # posture A still forbidden; bare「已落盘」alone is OK when files exist.
    verdict = DeliveryVerdict(
        state="blocked",
        delivered_files=("notes.md",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "笔记已落盘，但主产物未交付。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )
    claim = "全部完成，可以收工。"
    assert closing_honesty_verdict_hit(claim, verdict) == "posture_a"
    assert finish_guard(claim, citation_count=0, delivery_verdict=verdict) == []


def test_notes_verdict_rejects_posture_a_claim():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    # Soft warnings → notes ≈ 草稿·部分；非正式完成，不得姿势 A。
    verdict = DeliveryVerdict(
        state="notes",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    claim = "全部完成，产物见工作区。"
    assert closing_honesty_verdict_hit(claim, verdict) == "posture_a"
    assert (
        finish_guard(
            claim,
            citation_count=0,
            delivery_verdict=verdict,
            overview_max_chars=1000,
        )
        == []
    )


def test_delivery_verdict_oversized_overview_is_shadow_only(monkeypatch):
    """篇幅超限只打影子日志，不回炉、不改写终稿。"""
    from agentcore.runtime.closing_posture import core as honesty_core
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(honesty_core, "logger", spy)

    verdict = DeliveryVerdict(
        state="delivered",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    body = "结论：已落盘。" + ("细节复述。" * 200)  # >> 1000
    reworks = finish_guard(
        body,
        citation_count=0,
        delivery_verdict=verdict,
        overview_max_chars=1000,
    )
    assert reworks == []
    fields = spy.get("engine.finish_guard_honesty_shadow")
    assert fields["hit"] == "overview_length"
    assert fields["verdict_state"] == "delivered"
    assert fields["has_delivered_files"] is True
    assert "content" not in fields
    assert "preview" not in fields


def test_delivery_verdict_allows_short_overview():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="delivered",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "已落盘 `src/a.ts`，详见产物卡。编译已过。",
            citation_count=0,
            delivery_verdict=verdict,
            overview_max_chars=1000,
        )
        == []
    )


def test_no_delivery_verdict_skips_overview_length_gate():
    # Prose / research turns with no delivery card — long answers OK.
    long = "调研结论。" * 400
    assert (
        finish_guard(
            long,
            citation_count=0,
            delivery_verdict=None,
            overview_max_chars=1000,
        )
        == []
    )


def test_overview_length_gate_disabled_when_max_nonpositive():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="delivered",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    body = "x" * 2000
    assert (
        finish_guard(
            body,
            citation_count=0,
            delivery_verdict=verdict,
            overview_max_chars=0,
        )
        == []
    )


def test_overview_length_skipped_for_workers():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="delivered",
        delivered_files=("src/a.ts",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "x" * 2000,
            citation_count=0,
            check_citations=False,
            delivery_verdict=verdict,
            overview_max_chars=1000,
        )
        == []
    )


def test_delivery_structure_rework_still_fires_when_overview_also_over(monkeypatch):
    """篇幅影子不得吞掉产物结构窄闸：无 .pptx 仍回炉。"""
    from agentcore.runtime.closing_posture import core as honesty_core
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(honesty_core, "logger", spy)

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("build_pptx.py", "讲稿.md"),
        execution_id="e1",
    )
    body = "课件 PPT 已落盘，可直接打开使用。" + ("细节复述。" * 200)
    reworks = finish_guard(
        body,
        citation_count=0,
        delivery_verdict=verdict,
        overview_max_chars=1000,
    )
    assert len(reworks) == 1
    assert ".pptx" in reworks[0]
    assert "不得宣称" in reworks[0]
    fields = spy.get("engine.finish_guard_honesty_shadow")
    assert fields["hit"] == "overview_length"


def test_partial_md_only_rejects_pptx_ready_claim():
    """选了 pptx 却只落 md/脚本：假「PPT 已可打开」必须被 finish_guard 拦回。"""
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("build_pptx.py", "讲稿.md"),
        execution_id="e1",
    )
    reworks = finish_guard(
        "课件 PPT 已落盘，可直接打开使用。",
        citation_count=0,
        delivery_verdict=verdict,
    )
    assert len(reworks) == 1
    assert ".pptx" in reworks[0]
    assert "不得宣称" in reworks[0]


def test_partial_md_only_allows_honest_pptx_gap():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("build_pptx.py", "讲稿.md"),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "讲稿与生成脚本已落盘；pptx 尚未生成，请绑定本地目录后运行脚本。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )


def test_pptx_landed_allows_pptx_ready_claim():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="delivered",
        delivered_files=("course.pptx", "讲稿.md"),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "课件 PPT 已落盘，可直接打开使用。",
            citation_count=0,
            delivery_verdict=verdict,
            overview_max_chars=1000,
        )
        == []
    )


def test_pptx_claim_skipped_for_workers():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("讲稿.md",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "PPT 已落盘，可直接打开。",
            citation_count=0,
            check_citations=False,
            delivery_verdict=verdict,
        )
        == []
    )


def test_negated_pptx_claim_allowed_when_md_only():
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

    verdict = DeliveryVerdict(
        state="partial",
        delivered_files=("讲稿.md",),
        execution_id="e1",
    )
    assert (
        finish_guard(
            "尚未交付 PPT：目前只有讲稿大纲。",
            citation_count=0,
            delivery_verdict=verdict,
        )
        == []
    )
