"""夜跑 eval 双快照（最近成功 + 只升峰值）——观测，不改红绿。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_nightly_snapshots import main, snapshot_rate, strip_observe


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _suite(pass_rate: float, *, ratchet: bool = False) -> dict:
    data: dict = {
        "summary": {"total": 10, "passed": int(round(10 * pass_rate)), "pass_rate": pass_rate},
        "cases": [{"case_id": f"c{i}", "passed": i < int(round(10 * pass_rate))} for i in range(10)],
    }
    if ratchet:
        data["ratchet"] = {"schema": "observe.v1", "gate": False, "signature": "directional_drop"}
    return data


def test_snapshot_rate_follows_observe_kind_order():
    routing = {
        "report": {"summary": {"pass_rate": 0.1}},
        "routing": {"accuracy": 0.9, "confusion": {"tp": 1, "fp": 0, "fn": 0, "tn": 0}},
    }
    assert snapshot_rate(routing) == 0.9
    assert snapshot_rate({"clean_rate": 0.8, "offenders": []}) == 0.8
    assert snapshot_rate({"summary": {"pass_rate": 0.7}}) == 0.7
    assert snapshot_rate(
        {"summary": {"by_archetype": {"a": {"avg_win_rate": 0.4}, "b": {"avg_win_rate": 0.6}}}}
    ) == 0.5


def test_strip_observe_drops_ratchet_only():
    data = _suite(0.9, ratchet=True)
    stripped = strip_observe(data)
    assert "ratchet" not in stripped
    assert stripped["summary"]["pass_rate"] == 0.9


def test_seed_copies_highwater_when_latest_missing(tmp_path: Path):
    out = tmp_path / "eval-out"
    high = out / "core-baseline.json"
    _write(high, _suite(0.95))
    assert main(["--seed", "--out-dir", str(out), "--suite", "core"]) == 0
    latest = json.loads((out / "core-latest.json").read_text(encoding="utf-8"))
    assert latest["summary"]["pass_rate"] == 0.95


def test_seed_does_not_overwrite_existing_latest(tmp_path: Path):
    out = tmp_path / "eval-out"
    _write(out / "core-baseline.json", _suite(0.99))
    _write(out / "core-latest.json", _suite(0.70))
    assert main(["--seed", "--out-dir", str(out), "--suite", "core"]) == 0
    latest = json.loads((out / "core-latest.json").read_text(encoding="utf-8"))
    assert latest["summary"]["pass_rate"] == 0.70


def test_promote_writes_latest_always_and_raises_highwater_only(tmp_path: Path):
    reports = tmp_path / "eval-reports"
    out = tmp_path / "eval-out"
    _write(reports / "functional.json", _suite(0.80, ratchet=True))
    _write(out / "core-baseline.json", _suite(0.90))
    _write(out / "core-latest.json", _suite(0.90))

    assert main(["--reports-dir", str(reports), "--out-dir", str(out), "--suite", "core"]) == 0

    latest = json.loads((out / "core-latest.json").read_text(encoding="utf-8"))
    high = json.loads((out / "core-baseline.json").read_text(encoding="utf-8"))
    assert latest["summary"]["pass_rate"] == 0.80
    assert "ratchet" not in latest
    assert high["summary"]["pass_rate"] == 0.90  # drop does not lower the peak


def test_promote_raises_highwater_when_better(tmp_path: Path):
    reports = tmp_path / "eval-reports"
    out = tmp_path / "eval-out"
    _write(reports / "functional.json", _suite(0.96, ratchet=True))
    _write(out / "core-baseline.json", _suite(0.90))

    assert main(["--reports-dir", str(reports), "--out-dir", str(out), "--suite", "core"]) == 0

    high = json.loads((out / "core-baseline.json").read_text(encoding="utf-8"))
    assert high["summary"]["pass_rate"] == 0.96
    assert "ratchet" not in high


def test_promote_skips_missing_reports_and_never_reds(tmp_path: Path, capsys):
    reports = tmp_path / "eval-reports"
    reports.mkdir()
    out = tmp_path / "eval-out"
    _write(reports / "routing.json", {"routing": {"accuracy": 0.5, "misroutes": []}})

    code = main(["--reports-dir", str(reports), "--out-dir", str(out), "--suite", "core"])

    assert code == 0
    err = capsys.readouterr()
    assert "跳过 core-latest.json" in err.out
    assert (out / "routing-latest.json").is_file()
    assert (out / "routing-baseline.json").is_file()  # first snapshot becomes the peak
    assert not (out / "style-latest.json").is_file()


def test_promote_four_suites_write_both_snapshots(tmp_path: Path):
    reports = tmp_path / "eval-reports"
    out = tmp_path / "eval-out"
    _write(reports / "routing.json", {"routing": {"accuracy": 0.8, "confusion": {}}})
    _write(reports / "style.json", {"clean_rate": 0.7, "offenders": [], "total": 10})
    _write(
        reports / "comparison.json",
        {"summary": {"by_archetype": {"simple": {"avg_win_rate": 0.6}}, "total_cases": 1}},
    )
    _write(reports / "probe.json", _suite(1.0))

    assert main(["--reports-dir", str(reports), "--out-dir", str(out), "--suite", "core"]) == 0

    for stem in ("routing", "style", "comparison", "probe"):
        assert (out / f"{stem}-latest.json").is_file()
        assert (out / f"{stem}-baseline.json").is_file()
        latest = json.loads((out / f"{stem}-latest.json").read_text(encoding="utf-8"))
        assert "ratchet" not in latest
