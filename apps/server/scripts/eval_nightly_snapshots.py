"""夜跑 eval 快照：最近一次成功报告 + 只升的历史峰值。

夜跑 ``--baseline`` 对着 ``*-latest.json``（昨夜成功快照），才能看见缓慢漂移。
``*-baseline.json`` 仍按只升更新，给人留「历史最好」；``--baseline`` 对比本身不看它。
作业摘要（``eval_nightly_summary.py``）会读峰值，并排展示本次 vs 历史峰值。

**不改红绿**：恒 0 退出。通过率只用来决定峰值文件动不动，不是退化阈值——
退化判据仍是 ``evals/observe.py`` 的翻转方向。

纯 stdlib、不 import ``agentcore``：跟 ``eval_nightly_summary.py`` 一样，
装依赖挂了也要能跑（虽然本步通常在真跑之后）。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def snapshot_pairs(suite: str) -> tuple[tuple[str, str, str], ...]:
    """(报告文件, 最近成功快照, 只升峰值)。功能套件文件名跟 workflow 的 SUITE 走。"""
    return (
        ("functional.json", f"{suite}-latest.json", f"{suite}-baseline.json"),
        ("mast.json", "mast-latest.json", "mast-baseline.json"),
        ("routing.json", "routing-latest.json", "routing-baseline.json"),
        ("style.json", "style-latest.json", "style-baseline.json"),
        ("comparison.json", "comparison-latest.json", "comparison-baseline.json"),
        ("probe.json", "probe-latest.json", "probe-baseline.json"),
    )


def strip_observe(data: dict[str, Any]) -> dict[str, Any]:
    """基线只存结果，不存对上一版的对比（对应夜跑曾经的 ``jq 'del(.ratchet)'``）。"""
    out = dict(data)
    out.pop("ratchet", None)
    return out


def snapshot_rate(data: dict[str, Any]) -> float | None:
    """越高越好的标量，只决定峰值文件抬不抬。不是退化阈值。

    识别顺序与 ``evals/observe.py`` 的 ``_kind`` 对齐，避免 routing 报告误吃
    内层 ``report.summary.pass_rate``。
    """
    routing = data.get("routing")
    if isinstance(routing, dict) and ("accuracy" in routing or "confusion" in routing):
        acc = routing.get("accuracy")
        try:
            return float(acc) if acc is not None else None
        except (TypeError, ValueError):
            return None
    if "clean_rate" in data and isinstance(data.get("offenders"), list):
        try:
            return float(data["clean_rate"]) if data.get("clean_rate") is not None else None
        except (TypeError, ValueError):
            return None
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    if "by_archetype" in summary:
        rates: list[float] = []
        by = summary.get("by_archetype") or {}
        if isinstance(by, dict):
            for block in by.values():
                if not isinstance(block, dict):
                    continue
                wr = block.get("avg_win_rate")
                if wr is None:
                    continue
                try:
                    rates.append(float(wr))
                except (TypeError, ValueError):
                    continue
        return sum(rates) / len(rates) if rates else None
    src = data.get("report") if isinstance(data.get("report"), dict) else data
    src_summary = src.get("summary") if isinstance(src.get("summary"), dict) else {}
    pr = src_summary.get("pass_rate")
    try:
        return float(pr) if pr is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def seed_latest(out_dir: Path, suite: str) -> None:
    """缓存里若只有旧的峰值、还没有 latest，用峰值播种，免得改策略后首夜对比空白。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for _report, latest_name, high_name in snapshot_pairs(suite):
        latest = out_dir / latest_name
        high = out_dir / high_name
        if latest.is_file() or not high.is_file():
            continue
        latest.write_bytes(high.read_bytes())
        print(f"[snapshot] 用历史峰值播种最近快照 {latest}")


def promote(reports_dir: Path, out_dir: Path, suite: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for report_name, latest_name, high_name in snapshot_pairs(suite):
        report_path = reports_dir / report_name
        latest = out_dir / latest_name
        high = out_dir / high_name
        if not report_path.is_file():
            print(f"[snapshot] 跳过 {latest_name}：本次无报告")
            continue
        try:
            raw = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[snapshot] 跳过 {latest_name}：报告无法解析（{type(e).__name__}）")
            continue
        if not isinstance(raw, dict):
            print(f"[snapshot] 跳过 {latest_name}：报告顶层不是对象")
            continue
        payload = strip_observe(raw)
        _write_json(latest, payload)
        cur = snapshot_rate(payload)
        print(f"[snapshot] 已写入最近成功快照 {latest}（本次 {_fmt(cur)}）")
        if not high.is_file():
            _write_json(high, payload)
            print(f"[snapshot] 首个历史峰值 {high}: {_fmt(cur)}")
            continue
        try:
            base_raw = json.loads(high.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _write_json(high, payload)
            print(f"[snapshot] 历史峰值损坏，已用本次覆盖 {high}")
            continue
        base = snapshot_rate(base_raw if isinstance(base_raw, dict) else {})
        if cur is not None and base is not None and cur > base:
            _write_json(high, payload)
            print(f"[snapshot] 抬历史峰值 {high}: {_fmt(base)} -> {_fmt(cur)}")
        else:
            print(f"[snapshot] 保持历史峰值 {high}: {_fmt(base)}（本次 {_fmt(cur)}）")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python scripts/eval_nightly_snapshots.py",
        description="写出最近成功快照并按只升更新历史峰值（观测，不改红绿）。",
    )
    p.add_argument(
        "--seed",
        action="store_true",
        help="若 latest 缺失而峰值存在，用峰值播种 latest（真跑前调用）",
    )
    p.add_argument("--reports-dir", default="eval-reports", help="本次报告目录")
    p.add_argument("--out-dir", default="eval-out", help="快照目录（默认 eval-out）")
    p.add_argument("--suite", default=None, help="功能套件名（缺省读环境变量 SUITE）")
    args = p.parse_args(argv)

    suite = args.suite or os.environ.get("SUITE") or "core"
    out_dir = Path(args.out_dir)
    if args.seed:
        seed_latest(out_dir, suite)
    else:
        promote(Path(args.reports_dir), out_dir, suite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
