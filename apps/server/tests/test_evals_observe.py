"""相对基线观测：翻转方向区分方差 vs 单方向变差；无逐例则明说分不出。零 LLM。"""

from __future__ import annotations

from agentcore.evals.observe import (
    SIG_CHURN,
    SIG_DROP,
    SIG_GAIN,
    SIG_NO_BASELINE,
    SIG_RATE_ONLY,
    SIG_UNCHANGED,
    format_observe,
    observe_report,
)


def _suite(passed_ids: set[str], all_ids: list[str]) -> dict:
    cases = [
        {"case_id": cid, "category": "qa", "passed": cid in passed_ids} for cid in all_ids
    ]
    passed = len(passed_ids)
    total = len(all_ids)
    return {
        "summary": {
            "total": total,
            "passed": passed,
            "pass_rate": passed / total if total else 0.0,
        },
        "cases": cases,
    }


def test_no_baseline_is_explicit():
    obs = observe_report(_suite({"a"}, ["a"]), None, baseline_path="x.json")
    assert obs["available"] is False
    assert obs["gate"] is False
    assert obs["signature"] == SIG_NO_BASELINE
    assert obs["can_separate_variance"] is False
    assert "没有上一份基线" in obs["reading"]


def test_rate_only_cannot_separate_variance():
    current = {"summary": {"total": 10, "passed": 7, "pass_rate": 0.7}}
    baseline = {"summary": {"total": 10, "passed": 9, "pass_rate": 0.9}}
    obs = observe_report(current, baseline, baseline_path="b.json")
    assert obs["signature"] == SIG_RATE_ONLY
    assert obs["can_separate_variance"] is False
    assert obs["gate"] is False
    assert obs["delta_pass_rate"] == -0.2
    assert "分不出" in obs["reading"]
    assert "不要把 Δ 读成红线" in obs["reading"]


def test_unidirectional_drop_is_not_symmetric_jitter():
    ids = [f"c{i}" for i in range(10)]
    current = _suite(set(ids[:5]), ids)  # c0-c4 pass
    baseline = _suite(set(ids[:9]), ids)  # c0-c8 pass
    obs = observe_report(current, baseline)
    assert obs["signature"] == SIG_DROP
    assert obs["can_separate_variance"] is True
    assert obs["gate"] is False
    worse_ids = {x["case_id"] for x in obs["paired"]["worse"]}
    assert worse_ids == {"c5", "c6", "c7", "c8"}
    assert obs["paired"]["better"] == []
    assert "不是对称抖动" in obs["reading"]
    assert "非门禁" in format_observe(obs) or "不改退出码" in format_observe(obs)


def test_bidirectional_flips_read_as_churn():
    ids = ["a", "b", "c", "d"]
    # baseline: a,b pass; current: a,c pass → b worse, c better
    current = _suite({"a", "c"}, ids)
    baseline = _suite({"a", "b"}, ids)
    obs = observe_report(current, baseline)
    assert obs["signature"] == SIG_CHURN
    assert {x["case_id"] for x in obs["paired"]["worse"]} == {"b"}
    assert {x["case_id"] for x in obs["paired"]["better"]} == {"c"}
    assert "双向" in obs["reading"]


def test_all_same_is_unchanged_not_a_pass_verdict():
    ids = ["a", "b"]
    report = _suite({"a", "b"}, ids)
    obs = observe_report(report, report)
    assert obs["signature"] == SIG_UNCHANGED
    assert "不能证明没有退化" in obs["reading"]


def test_unidirectional_gain():
    ids = ["a", "b"]
    current = _suite({"a", "b"}, ids)
    baseline = _suite({"a"}, ids)
    obs = observe_report(current, baseline)
    assert obs["signature"] == SIG_GAIN
    assert obs["paired"]["worse"] == []


def test_multi_sample_uses_per_case_rate():
    current = {
        "summary": {"total": 4, "passed": 2, "pass_rate": 0.5},
        "cases": [
            {"case_id": "x", "passed": True},
            {"case_id": "x", "passed": False},
            {"case_id": "y", "passed": True},
            {"case_id": "y", "passed": False},
        ],
    }
    baseline = {
        "summary": {"total": 4, "passed": 4, "pass_rate": 1.0},
        "cases": [
            {"case_id": "x", "passed": True},
            {"case_id": "x", "passed": True},
            {"case_id": "y", "passed": True},
            {"case_id": "y", "passed": True},
        ],
    }
    obs = observe_report(current, baseline)
    assert obs["signature"] == SIG_DROP
    by_id = {x["case_id"]: x for x in obs["paired"]["worse"]}
    assert by_id["x"]["baseline"] == "2/2"
    assert by_id["x"]["current"] == "1/2"


def test_shape_mismatch_is_incomparable():
    current = {"clean_rate": 0.9, "offenders": [], "total": 10}
    baseline = _suite({"a"}, ["a"])
    obs = observe_report(current, baseline)
    assert obs["signature"] == "incomparable"
    assert obs["can_separate_variance"] is False


def test_routing_new_misroutes_are_directional_drop():
    current = {
        "routing": {
            "total": 8,
            "accuracy": 0.5,
            "misroutes": [{"case_id": "a"}, {"case_id": "b"}],
        }
    }
    baseline = {
        "routing": {
            "total": 8,
            "accuracy": 0.9,
            "misroutes": [{"case_id": "a"}],
        }
    }
    obs = observe_report(current, baseline)
    assert obs["kind"] == "routing"
    assert obs["signature"] == SIG_DROP
    assert obs["paired"]["worse"] == [{"case_id": "b"}]


def test_style_offender_churn():
    current = {
        "total": 4,
        "clean_rate": 0.5,
        "offenders": [{"case_id": "new", "rules": ["opening"]}],
    }
    baseline = {
        "total": 4,
        "clean_rate": 0.5,
        "offenders": [{"case_id": "old", "rules": ["closing"]}],
    }
    obs = observe_report(current, baseline)
    assert obs["kind"] == "style"
    assert obs["signature"] == SIG_CHURN


def test_comparison_win_rate_drop():
    current = {
        "summary": {"total_cases": 1, "by_archetype": {}},
        "cases": [
            {
                "case_id": "t1",
                "comparisons": {"team": {"win_rate": 0.2, "wins": 1, "losses": 4, "ties": 0}},
            }
        ],
    }
    baseline = {
        "summary": {"total_cases": 1, "by_archetype": {}},
        "cases": [
            {
                "case_id": "t1",
                "comparisons": {"team": {"win_rate": 0.8, "wins": 4, "losses": 1, "ties": 0}},
            }
        ],
    }
    obs = observe_report(current, baseline)
    assert obs["kind"] == "comparison"
    assert obs["signature"] == SIG_DROP
    assert obs["paired"]["worse"][0]["arm"] == "team"


def test_small_n_binomial_se_is_descriptive_not_a_gate():
    ids = [f"c{i}" for i in range(12)]
    obs = observe_report(_suite(set(ids[:10]), ids), _suite(set(ids[:11]), ids))
    se = obs["binomial_se_current"]
    assert se is not None
    # n=12、p≈0.83 时 SE 约 0.11，远大于旧 0.05 容差——这正是假红线有害的原因。
    assert se > 0.05
    assert obs["gate"] is False
    text = format_observe(obs)
    assert "不是红线" in text
    assert "只升不降" in text
