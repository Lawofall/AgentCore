"""输出风格 linter + StyleClean check + 违规率聚合的确定性单测（方向④，零 LLM）.

linter 规则是纯文本启发式，直接喂字符串断言命中/不命中；聚合器是纯算术，用合成 CaseReport
喂。**违规率出数**需真模型回合产生被检文本（已延后的 eval 主线），不在本测覆盖——这里只证
「规则判得准 + 锚定不误报 + 聚合算得对」。
"""

from __future__ import annotations

import pytest

from agentcore.evals.checks import build_check
from agentcore.evals.style_lint import (
    RULE_CLOSING,
    RULE_OPENING,
    StyleMetrics,
    format_style_report,
    style_metrics,
    style_metrics_to_dict,
    style_violations,
)
from agentcore.evals.types import CaseReport, EvalCase, TurnOutcome


def _rules(text: str) -> set[str]:
    return {v.rule for v in style_violations(text)}


# --- linter 规则 ---


def test_clean_prose_has_no_violations() -> None:
    assert style_violations("HTTP 404 表示请求的资源未找到。") == []


def test_empty_or_blank_is_clean() -> None:
    assert style_violations("") == []
    assert style_violations("   \n  ") == []
    assert style_violations(None) == []


def test_opening_boilerplate_detected_at_start() -> None:
    assert RULE_OPENING in _rules("好问题！这个嘛，答案是 42。")
    assert RULE_OPENING in _rules("当然！可以这样做。")
    assert RULE_OPENING in _rules("Great question! Here's the answer.")


def test_opening_anchored_so_midtext_phrase_does_not_trigger() -> None:
    # 「好问题」出现在句中、非开场——不该误报。
    assert RULE_OPENING not in _rules("答案是 42。这是一个好问题但与此无关。")


def test_opening_detected_through_markdown_prefix() -> None:
    assert RULE_OPENING in _rules("## 好问题！\n\n答案如下。")
    assert RULE_OPENING in _rules("**好问题**，我们来看。")


def test_closing_pleasantry_detected_at_end() -> None:
    assert RULE_CLOSING in _rules("步骤如上。希望对你有帮助！")
    assert RULE_CLOSING in _rules("Done. Let me know if you need anything else")


def test_closing_anchored_so_midtext_phrase_does_not_trigger() -> None:
    # 寒暄短语出现在**非末句**——只看最后一句，故不误报。
    assert RULE_CLOSING not in _rules("希望对你有帮助这点我之前确认过。下面进入正题：配置步骤如下。")


def test_multiple_violations_stack() -> None:
    rules = _rules("好问题！步骤如上。希望对你有帮助")
    assert {RULE_OPENING, RULE_CLOSING} <= rules


# --- StyleClean check（经注册表构造，覆盖注册） ---


def _outcome(content: str) -> TurnOutcome:
    return TurnOutcome(content=content, finish_reason="end_turn", rounds=1)


def _case() -> EvalCase:
    return EvalCase(id="c", category="qa", user_message="u")


def test_style_clean_check_fails_on_violation() -> None:
    chk = build_check({"name": "StyleClean"})
    res = chk.run(_case(), _outcome("好问题！答案是 42。"))
    assert res.name == "StyleClean"
    assert res.passed is False
    assert RULE_OPENING in res.detail


def test_style_clean_check_passes_clean() -> None:
    chk = build_check({"name": "StyleClean"})
    assert chk.run(_case(), _outcome("答案是 42。")).passed is True


def test_style_clean_allow_whitelists_a_rule() -> None:
    chk = build_check({"name": "StyleClean", "args": {"allow": [RULE_OPENING]}})
    assert chk.run(_case(), _outcome("好问题！答案是 42。")).passed is True
    assert chk.run(_case(), _outcome("好问题！希望对你有帮助")).passed is False


def test_style_clean_registered_in_check_names() -> None:
    from agentcore.evals.checks import CHECK_NAMES

    assert "StyleClean" in CHECK_NAMES


# --- 聚合器 ---


def _report(cid: str, content: str, *, error: str | None = None) -> CaseReport:
    return CaseReport(
        case_id=cid,
        category="qa",
        outcome=TurnOutcome(content=content, finish_reason="end_turn", rounds=1, error=error),
    )


def test_metrics_counts_clean_and_per_rule() -> None:
    reports = [
        _report("clean", "答案是 42。"),
        _report("open", "好问题！答案是 42。"),
        _report("close", "步骤如上。希望对你有帮助！"),
        _report("boom", "好问题！", error="provider exploded"),  # errored → 跳过
        _report("blank", "   "),  # 空正文 → 跳过
    ]
    m = style_metrics(reports)
    assert m.total == 3
    assert m.clean == 1
    assert m.clean_rate == pytest.approx(1 / 3)
    assert m.per_rule == {RULE_OPENING: 1, RULE_CLOSING: 1}
    assert m.violation_rate(RULE_OPENING) == pytest.approx(1 / 3)
    assert sorted(cid for cid, _ in m.offenders) == ["close", "open"]


def test_metrics_empty_rates_are_none() -> None:
    m = style_metrics([])
    assert m.total == 0
    assert m.clean_rate is None
    assert m.violation_rate(RULE_OPENING) is None


def test_metrics_to_dict_shape() -> None:
    m = style_metrics([_report("open", "好问题！答案是 42。")])
    d = style_metrics_to_dict(m)
    assert d["total"] == 1
    assert d["per_rule"] == {RULE_OPENING: 1}
    assert d["violation_rates"][RULE_OPENING] == pytest.approx(1.0)
    assert d["offenders"] == [{"case_id": "open", "rules": [RULE_OPENING]}]


def test_format_report_smoke() -> None:
    m = style_metrics([_report("open", "好问题！答案是 42。"), _report("ok", "答案是 42。")])
    text = format_style_report(m)
    assert "输出风格违规" in text
    assert "套话开场" in text
    assert "open" in text


def test_style_metrics_defaults() -> None:
    m = StyleMetrics()
    assert (m.total, m.clean) == (0, 0)
    assert m.per_rule == {}
    assert m.offenders == []
