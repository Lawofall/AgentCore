"""Unit tests for citation-marker bounds validation and exit reconciliation.

Mirrors the desktop renderer's marker semantics (remarkCitations.ts): only
``1..count`` are real chips; anything else is a marker pointing at a source card
that does not exist. ``out_of_range_markers`` observes; ``reconcile_citations``
reports stray markers without stripping so the chat body stays intact.
"""

from agentcore.runtime.citations import (
    extract_ledger_ref_ids,
    invalid_ledger_ref_ids,
    out_of_range_markers,
    reconcile_citations,
    strip_invalid_ledger_refs,
    strip_out_of_range_markers,
)


def test_in_range_markers_are_clean():
    assert out_of_range_markers("See [1] and [2].", 2) == []


def test_marker_past_count_is_flagged():
    assert out_of_range_markers("See [1] and [3].", 2) == [3]


def test_zero_marker_is_flagged():
    assert out_of_range_markers("Bad [0] ref with [1].", 3) == [0]


def test_no_citations_flags_any_marker():
    assert out_of_range_markers("Unsupported claim [1].", 0) == [1]


def test_results_are_deduped_and_sorted():
    assert out_of_range_markers("[5] a [3] b [5] c [3]", 2) == [3, 5]


def test_fenced_code_block_is_ignored():
    content = "Real [1].\n```python\nfoo = arr[9]\nbar[7]\n```\n"
    assert out_of_range_markers(content, 1) == []


def test_inline_code_is_ignored():
    assert out_of_range_markers("Use `arr[9]` then cite [1].", 1) == []


def test_markdown_link_label_is_ignored():
    assert out_of_range_markers("See [9](http://example.com) and [1].", 1) == []


def test_prose_index_shares_client_semantics():
    # A bare "[5]" in prose is indistinguishable from a citation, both here and in
    # the renderer — so it is (correctly) counted as out-of-range.
    assert out_of_range_markers("item [5] only, with [1].", 1) == [5]


def test_negative_like_bracket_is_not_a_marker():
    # "[-1]" is not a \\d+ marker; only real numeric markers count.
    assert out_of_range_markers("range [-1] but cite [2]", 1) == [2]


def test_empty_or_markerless_content():
    assert out_of_range_markers("", 5) == []
    assert out_of_range_markers("No markers at all.", 0) == []


def test_strip_removes_dangling_keeps_valid():
    assert strip_out_of_range_markers("See [1] and [3].", 2) == "See [1] and."
    assert out_of_range_markers(strip_out_of_range_markers("See [1] and [3].", 2), 2) == []


def test_strip_empty_citations_removes_all_prose_markers():
    cleaned = strip_out_of_range_markers("Unsupported claim [1] and [2].", 0)
    assert cleaned == "Unsupported claim and."
    assert out_of_range_markers(cleaned, 0) == []


def test_strip_preserves_code_and_links():
    content = "Real [9].\n```python\nfoo = arr[9]\n```\nUse `arr[9]` and [8](http://x.com)."
    cleaned = strip_out_of_range_markers(content, 0)
    assert "arr[9]" in cleaned
    assert "[8](http://x.com)" in cleaned
    assert "Real." in cleaned
    assert out_of_range_markers(cleaned, 0) == []


def test_reconcile_reports_stray_without_stripping():
    citations = [{"url": "https://a.example"}, {"url": "https://b.example"}]
    cleaned, out_citations, stray_n, stray_r = reconcile_citations(
        "Claim [1] then [5].", citations
    )
    assert stray_n == [5]
    assert stray_r == []
    assert cleaned == "Claim [1] then [5]."
    assert out_citations is citations  # list identity preserved (cards stay)


def test_reconcile_noop_when_clean():
    citations = [{"url": "https://a.example"}]
    cleaned, out_citations, stray_n, stray_r = reconcile_citations("Only [1].", citations)
    assert stray_n == []
    assert stray_r == []
    assert cleaned == "Only [1]."
    assert out_citations is citations


def test_extract_ledger_ref_ids_first_appearance_unique():
    # P2：首次出现序（来源卡投影序）；去重保留先见
    assert extract_ledger_ref_ids("见 #r2 与 #r1，再 #r2。") == ["#r2", "#r1"]


def test_ledger_ref_in_code_ignored():
    content = "正文 #r1。\n```python\nx = '#r9'\n```\n"
    assert extract_ledger_ref_ids(content) == ["#r1"]


def test_invalid_ledger_ref_q5_no_markers():
    # Q5：无约定标记 → 闸不启用
    assert invalid_ledger_ref_ids("无引用的正文。", frozenset()) == []


def test_invalid_ledger_ref_forgery_and_uncitable():
    assert invalid_ledger_ref_ids("见 #r1 与 #r9。", frozenset({"#r1"})) == ["#r9"]
    assert invalid_ledger_ref_ids("弱源 #r2。", frozenset({"#r1"})) == ["#r2"]


def test_strip_invalid_ledger_refs():
    cleaned = strip_invalid_ledger_refs("结论 #r1 与 #r9。", {"#r9"})
    assert cleaned == "结论 #r1 与。"
    assert invalid_ledger_ref_ids(cleaned, frozenset({"#r1"})) == []


def test_reconcile_dual_track_observes_without_stripping():
    citations = [{"url": "https://a.example"}]
    cleaned, _, stray_n, stray_r = reconcile_citations(
        "池序 [1] 与 [3]；台账 #r1 与 #r9。",
        citations,
        citable_ids=frozenset({"#r1"}),
    )
    assert stray_n == [3]
    assert stray_r == ["#r9"]
    assert cleaned == "池序 [1] 与 [3]；台账 #r1 与 #r9。"
