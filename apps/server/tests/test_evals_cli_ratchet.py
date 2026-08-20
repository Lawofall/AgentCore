"""CLI 相对基线观测（``python -m agentcore.evals --baseline``）的接线单测.

夜跑把 ``--baseline`` 挂在既有真跑上，报告 JSON 的 ``ratchet`` 段喂给作业摘要。
本文件钉：观测写没写、翻转签名对不对、**退出码不跟观测走**（只跟用例自身 pass/fail）。

零 LLM：``run_suite`` 与 ``load_cases`` 都打桩。
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcore.evals.__main__ import main
from agentcore.evals.types import CaseReport, EvalCase, EvalReport, TurnOutcome


def _case(idx: int, *, passed: bool) -> CaseReport:
    outcome = TurnOutcome(
        content="ok", finish_reason="end_turn", rounds=1, error=None if passed else "boom"
    )
    return CaseReport(case_id=f"c{idx}", category="qa", outcome=outcome)


def _stub_suite(monkeypatch, *, passed: int, total: int) -> None:
    cases = [_case(i, passed=i < passed) for i in range(total)]
    report = EvalReport(cases=cases)

    async def _fake_run_suite(*_args, **_kwargs):
        return report

    monkeypatch.setattr(
        "agentcore.evals.__main__.load_cases",
        lambda *_a, **_k: [EvalCase(id="c0", category="qa", user_message="hi", rubric="r")],
    )
    monkeypatch.setattr("agentcore.evals.__main__.run_suite", _fake_run_suite)


def _write_baseline(path: Path, *, passed: int, total: int) -> None:
    cases = [
        {"case_id": f"c{i}", "category": "qa", "passed": i < passed} for i in range(total)
    ]
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "total": total,
                    "passed": passed,
                    "pass_rate": passed / total if total else 0.0,
                },
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )


def test_directional_drop_is_observed_but_exit_follows_cases(
    tmp_path: Path, monkeypatch
):
    """5/10 对 9/10：签名是单方向变差；退出码 1 是因为用例没全过，不是观测门。"""
    baseline = tmp_path / "core-baseline.json"
    _write_baseline(baseline, passed=9, total=10)
    out = tmp_path / "functional.json"
    _stub_suite(monkeypatch, passed=5, total=10)

    code = main(["--suite", "core", "--layer", "1", "--baseline", str(baseline), "--out", str(out)])

    assert code == 1  # 用例未全过
    ratchet = json.loads(out.read_text(encoding="utf-8"))["ratchet"]
    assert ratchet["available"] is True
    assert ratchet["gate"] is False
    assert ratchet["signature"] == "directional_drop"
    assert ratchet["can_separate_variance"] is True
    assert "tolerance" not in ratchet
    assert "regressed" not in ratchet


def test_all_pass_with_baseline_stays_green(tmp_path: Path, monkeypatch):
    """观测不额外弄红：自身全过时退出码仍为 0。"""
    baseline = tmp_path / "core-baseline.json"
    _write_baseline(baseline, passed=10, total=10)
    out = tmp_path / "functional.json"
    _stub_suite(monkeypatch, passed=10, total=10)

    code = main(["--suite", "core", "--layer", "1", "--baseline", str(baseline), "--out", str(out)])

    assert code == 0
    ratchet = json.loads(out.read_text(encoding="utf-8"))["ratchet"]
    assert ratchet["signature"] == "unchanged"
    assert ratchet["gate"] is False


def test_tolerance_flag_is_ignored(tmp_path: Path, monkeypatch, capsys):
    baseline = tmp_path / "core-baseline.json"
    _write_baseline(baseline, passed=10, total=10)
    out = tmp_path / "functional.json"
    _stub_suite(monkeypatch, passed=10, total=10)

    code = main(
        [
            "--suite",
            "core",
            "--layer",
            "1",
            "--baseline",
            str(baseline),
            "--regression-tolerance",
            "0.2",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    err = capsys.readouterr().err
    assert "已忽略" in err
    assert "tolerance" not in json.loads(out.read_text(encoding="utf-8"))["ratchet"]


def test_missing_baseline_is_recorded_as_unavailable(tmp_path: Path, monkeypatch):
    out = tmp_path / "functional.json"
    _stub_suite(monkeypatch, passed=10, total=10)

    code = main(
        [
            "--suite",
            "core",
            "--layer",
            "1",
            "--baseline",
            str(tmp_path / "nope.json"),
            "--out",
            str(out),
        ]
    )

    assert code == 0
    ratchet = json.loads(out.read_text(encoding="utf-8"))["ratchet"]
    assert ratchet["available"] is False
    assert ratchet["signature"] == "no_baseline"
    assert ratchet["pass_rate"] == 1.0


def test_no_baseline_flag_leaves_report_clean(tmp_path: Path, monkeypatch):
    out = tmp_path / "probe.json"
    _stub_suite(monkeypatch, passed=10, total=10)

    assert main(["--suite", "probe", "--layer", "1", "--out", str(out)]) == 0
    assert "ratchet" not in json.loads(out.read_text(encoding="utf-8"))


def test_update_baseline_writes_both_baseline_and_report(tmp_path: Path, monkeypatch):
    baseline = tmp_path / "core-baseline.json"
    out = tmp_path / "functional.json"
    _stub_suite(monkeypatch, passed=8, total=10)

    code = main(
        [
            "--suite",
            "core",
            "--layer",
            "1",
            "--baseline",
            str(baseline),
            "--update-baseline",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    assert json.loads(baseline.read_text(encoding="utf-8"))["summary"]["pass_rate"] == 0.8
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["summary"]["pass_rate"] == 0.8
    assert "ratchet" not in written


def test_diff_reports_is_zero_llm_and_never_reds(tmp_path: Path):
    current = tmp_path / "now.json"
    baseline = tmp_path / "base.json"
    out = tmp_path / "obs.json"
    ids = [f"c{i}" for i in range(4)]
    current.write_text(
        json.dumps(
            {
                "summary": {"total": 4, "passed": 2, "pass_rate": 0.5},
                "cases": [
                    {"case_id": cid, "passed": i < 2, "category": "qa"}
                    for i, cid in enumerate(ids)
                ],
            }
        ),
        encoding="utf-8",
    )
    baseline.write_text(
        json.dumps(
            {
                "summary": {"total": 4, "passed": 4, "pass_rate": 1.0},
                "cases": [{"case_id": cid, "passed": True, "category": "qa"} for cid in ids],
            }
        ),
        encoding="utf-8",
    )

    code = main(["--diff-reports", str(current), str(baseline), "--out", str(out)])

    assert code == 0
    obs = json.loads(out.read_text(encoding="utf-8"))
    assert obs["signature"] == "directional_drop"
    assert obs["gate"] is False


def test_probe_baseline_observes_without_redding(tmp_path: Path, monkeypatch):
    baseline = tmp_path / "probe-latest.json"
    _write_baseline(baseline, passed=10, total=10)
    out = tmp_path / "probe.json"
    _stub_suite(monkeypatch, passed=10, total=10)

    code = main(
        ["--suite", "probe", "--layer", "1", "--baseline", str(baseline), "--out", str(out)]
    )

    assert code == 0
    ratchet = json.loads(out.read_text(encoding="utf-8"))["ratchet"]
    assert ratchet["kind"] == "suite"
    assert ratchet["gate"] is False
    assert ratchet["signature"] == "unchanged"


def test_routing_baseline_observes_without_redding(tmp_path: Path, monkeypatch):
    from agentcore.evals.routing import RoutingMetrics

    _stub_suite(monkeypatch, passed=10, total=10)
    monkeypatch.setattr(
        "agentcore.evals.__main__.routing_metrics",
        lambda *_a, **_k: RoutingMetrics(
            total=4, tp=2, tn=1, fp=0, fn=1, misroutes=[("c3", True, False)]
        ),
    )
    baseline = tmp_path / "routing-latest.json"
    baseline.write_text(
        json.dumps({"routing": {"total": 4, "accuracy": 1.0, "misroutes": []}}),
        encoding="utf-8",
    )
    out = tmp_path / "routing.json"

    code = main(["--routing", "--baseline", str(baseline), "--out", str(out)])

    assert code == 0
    ratchet = json.loads(out.read_text(encoding="utf-8"))["ratchet"]
    assert ratchet["kind"] == "routing"
    assert ratchet["gate"] is False
    assert ratchet["signature"] == "directional_drop"


def test_style_baseline_observes_without_redding(tmp_path: Path, monkeypatch):
    from agentcore.evals.style_lint import StyleMetrics

    _stub_suite(monkeypatch, passed=10, total=10)
    monkeypatch.setattr(
        "agentcore.evals.__main__.style_metrics",
        lambda *_a, **_k: StyleMetrics(total=4, clean=3, offenders=[("c3", ["opening"])]),
    )
    baseline = tmp_path / "style-latest.json"
    baseline.write_text(
        json.dumps({"total": 4, "clean_rate": 1.0, "offenders": []}),
        encoding="utf-8",
    )
    out = tmp_path / "style.json"

    code = main(["--style", "--baseline", str(baseline), "--out", str(out)])

    assert code == 0
    ratchet = json.loads(out.read_text(encoding="utf-8"))["ratchet"]
    assert ratchet["kind"] == "style"
    assert ratchet["gate"] is False
    assert ratchet["signature"] == "directional_drop"


def test_compare_baseline_observes_without_redding(tmp_path: Path, monkeypatch):
    from agentcore.evals.types import ComparisonReport

    async def _fake(*_a, **_k):
        return ComparisonReport(cases=[])

    monkeypatch.setattr(
        "agentcore.evals.__main__.load_comparison_cases", lambda *_a, **_k: []
    )
    monkeypatch.setattr("agentcore.evals.__main__.run_comparison_suite", _fake)
    monkeypatch.setattr(
        "agentcore.evals.__main__.build_default_pairwise_judge", lambda *_a, **_k: object()
    )
    monkeypatch.setattr(
        "agentcore.evals.__main__.comparison_report_to_dict",
        lambda *_a, **_k: {
            "summary": {
                "total_cases": 1,
                "by_archetype": {"simple": {"avg_win_rate": 0.2}},
            },
            "cases": [
                {"case_id": "t1", "comparisons": {"team": {"win_rate": 0.2}}},
            ],
        },
    )
    baseline = tmp_path / "comparison-latest.json"
    baseline.write_text(
        json.dumps(
            {
                "summary": {
                    "total_cases": 1,
                    "by_archetype": {"simple": {"avg_win_rate": 0.8}},
                },
                "cases": [
                    {"case_id": "t1", "comparisons": {"team": {"win_rate": 0.8}}},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "comparison.json"

    code = main(["--compare", "--baseline", str(baseline), "--out", str(out)])

    assert code == 0
    ratchet = json.loads(out.read_text(encoding="utf-8"))["ratchet"]
    assert ratchet["kind"] == "comparison"
    assert ratchet["gate"] is False
    assert ratchet["signature"] == "directional_drop"
    current = tmp_path / "now.json"
    baseline = tmp_path / "base.json"
    out = tmp_path / "obs.json"
    ids = [f"c{i}" for i in range(4)]
    current.write_text(
        json.dumps(
            {
                "summary": {"total": 4, "passed": 2, "pass_rate": 0.5},
                "cases": [
                    {"case_id": cid, "passed": i < 2, "category": "qa"}
                    for i, cid in enumerate(ids)
                ],
            }
        ),
        encoding="utf-8",
    )
    baseline.write_text(
        json.dumps(
            {
                "summary": {"total": 4, "passed": 4, "pass_rate": 1.0},
                "cases": [{"case_id": cid, "passed": True, "category": "qa"} for cid in ids],
            }
        ),
        encoding="utf-8",
    )

    code = main(["--diff-reports", str(current), str(baseline), "--out", str(out)])

    assert code == 0
    obs = json.loads(out.read_text(encoding="utf-8"))
    assert obs["signature"] == "directional_drop"
    assert obs["gate"] is False
