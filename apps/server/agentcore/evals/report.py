"""报告聚合 + JSON 序列化 + 控制台美化（评估体系 §二 report.py）.

纯函数，吃 :class:`~agentcore.evals.types.EvalReport`，产出 (1) 可落盘/对比 baseline 的
JSON dict 与 (2) 控制台文本。刻意用 ASCII 标记（``[+]``/``[-]``）而非 ✓/✗——Windows 控制台
默认 GBK 编码下 unicode 勾叉会乱码。
"""

from __future__ import annotations

from agentcore.evals.mast import group_of, label_of
from agentcore.evals.types import CaseReport, EvalReport


def category_breakdown(report: EvalReport) -> dict[str, dict[str, float]]:
    """按类别聚合：``{category: {total, passed, pass_rate}}``（samples>1 时同 case 多条计入）。"""
    by_cat: dict[str, dict[str, float]] = {}
    for c in report.cases:
        bucket = by_cat.setdefault(c.category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if c.passed:
            bucket["passed"] += 1
    for bucket in by_cat.values():
        total = bucket["total"]
        bucket["pass_rate"] = round(bucket["passed"] / total, 4) if total else 0.0
    return by_cat


def mast_breakdown(report: EvalReport) -> dict[str, dict[str, dict[str, float]]]:
    """按 MAST 组 + 类聚合通过率（学·度量 §2.5），**仅计带 ``mast`` 标签的用例**。

    返回 ``{"by_group": {FCx: {total,passed,pass_rate}}, "by_mode": {code: {...}}}``——使
    「拆·lead / 合·验证 到底把哪一类失败压下去了」可逐组 / 逐类对照 baseline。无标签用例
    （core/routing 等）不计入，故对非 MAST 套件返回两个空 dict（report 据此跳过该段）。
    """
    by_group: dict[str, dict[str, float]] = {}
    by_mode: dict[str, dict[str, float]] = {}
    for c in report.cases:
        code = c.mast
        if not code:
            continue
        group = group_of(code) or "?"
        for bucket_map, key in ((by_group, group), (by_mode, code)):
            bucket = bucket_map.setdefault(key, {"total": 0, "passed": 0})
            bucket["total"] += 1
            if c.passed:
                bucket["passed"] += 1
    for bucket_map in (by_group, by_mode):
        for bucket in bucket_map.values():
            total = bucket["total"]
            bucket["pass_rate"] = round(bucket["passed"] / total, 4) if total else 0.0
    return {"by_group": by_group, "by_mode": by_mode}


def shape_means_by_case(report: EvalReport) -> dict[str, float]:
    """按 case_id 聚合形状分均值（samples>1 时同 case 多条取平均；无形状分的跳过）。"""
    buckets: dict[str, list[float]] = {}
    for c in report.cases:
        if c.shape_score is None:
            continue
        buckets.setdefault(c.case_id, []).append(c.shape_score)
    return {
        case_id: round(sum(scores) / len(scores), 4)
        for case_id, scores in sorted(buckets.items())
    }


def _case_to_dict(c: CaseReport) -> dict:
    o = c.outcome
    return {
        "case_id": c.case_id,
        "category": c.category,
        "mast": c.mast,
        "passed": c.passed,
        "shape_score": c.shape_score,
        "checks": [
            {"name": ck.name, "passed": ck.passed, "detail": ck.detail, "gating": ck.gating}
            for ck in c.checks
        ],
        "judge": (
            None
            if c.judge is None
            else {
                "score": c.judge.score,
                "passed": c.judge.passed,
                "rationale": c.judge.rationale,
            }
        ),
        "milestone": (
            None
            if c.milestone is None
            else {
                "coverage": c.milestone.coverage,
                "passed": c.milestone.passed,
                "threshold": c.milestone.threshold,
                "items": [
                    {"id": it.id, "covered": it.covered, "weight": it.weight, "desc": it.desc}
                    for it in c.milestone.items
                ],
                "rationale": c.milestone.rationale,
            }
        ),
        "outcome": {
            "finish_reason": o.finish_reason,
            "rounds": o.rounds,
            "delegated": o.delegated,
            "roster": o.roster,
            "plan_runs": o.plan_runs,
            "plan_type": o.plan_type,
            "collab_interactions": o.collab_interactions,
            "tool_calls": [name for name, _ in o.tool_calls],
            "citations": len(o.citations),
            "usage": o.usage,
            "cost_usd": round(o.cost_usd, 6),
            "latency_ms": o.latency_ms,
            "error": o.error,
            "content_preview": (o.content[:200] if o.content else ""),
        },
    }


def report_to_dict(report: EvalReport) -> dict:
    """整套报告 → JSON-able dict（汇总 + 逐例）。落盘为 baseline、供 P2 回归对比。"""
    total_cost = round(sum(c.outcome.cost_usd for c in report.cases), 6)
    shape_scores = [c.shape_score for c in report.cases if c.shape_score is not None]
    shape_by_case = shape_means_by_case(report)
    return {
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "pass_rate": round(report.pass_rate, 4),
            "cost_usd": total_cost,
            "by_category": category_breakdown(report),
            "by_mast": mast_breakdown(report),
            "shape_score_mean": (
                round(sum(shape_scores) / len(shape_scores), 4) if shape_scores else None
            ),
            "shape_score_by_case": shape_by_case or None,
        },
        "cases": [_case_to_dict(c) for c in report.cases],
    }


def format_report(report: EvalReport) -> str:
    """控制台文本报告（逐例 + 分类通过率 + 总账）。"""
    lines: list[str] = ["=" * 64, "AgentCore 评估报告", "=" * 64]
    for c in report.cases:
        status = "PASS" if c.passed else "FAIL"
        lines.append(f"[{status}] {c.case_id}  ({c.category})")
        for ck in c.checks:
            # 诊断 Check（gating=False）不计入判定：用 [~] 标记区别于门禁 [+]/[-]，避免误读为失败。
            mark = "[~]" if not ck.gating else ("[+]" if ck.passed else "[-]")
            suffix = " (诊断)" if not ck.gating else ""
            lines.append(f"    {mark} {ck.name}{suffix}: {ck.detail}")
        if c.judge is not None:
            mark = "[+]" if c.judge.passed else "[-]"
            lines.append(f"    {mark} Judge {c.judge.score}: {c.judge.rationale[:80]}")
        if c.milestone is not None:
            mk = "[+]" if c.milestone.passed else "[-]"
            cov = c.milestone.coverage * 100
            thr = c.milestone.threshold * 100
            miss = [it.id for it in c.milestone.items if not it.covered]
            lines.append(f"    {mk} Milestone 覆盖 {cov:.0f}% (阈 {thr:.0f}%) 缺={miss}")
        if c.shape_score is not None:
            lines.append(f"    [~] ShapeScore: {c.shape_score:.2f} (诊断)")
        interactions = c.outcome.collab_interactions
        if interactions:
            bits = ", ".join(f"{k}={v}" for k, v in sorted(interactions.items()))
            lines.append(f"    [~] CollabInteractions: {bits} (诊断)")
        if c.outcome.error:
            lines.append(f"    !!! error: {c.outcome.error}")
    lines.append("-" * 64)
    for cat, bucket in sorted(category_breakdown(report).items()):
        passed = int(bucket["passed"])
        total = int(bucket["total"])
        pct = bucket["pass_rate"] * 100
        lines.append(f"  {cat:<14} {passed}/{total}  ({pct:.0f}%)")
    # MAST 失败标签通过率（仅当本套件挂了标签时；学·度量 §2.5）：先按三大组、再逐类，使
    # 「哪一类失败被压下去了」对照 baseline 一目了然。
    mast = mast_breakdown(report)
    if mast["by_group"]:
        lines.append("-" * 64)
        lines.append("  MAST 失败标签通过率")
        for group, bucket in sorted(mast["by_group"].items()):
            passed = int(bucket["passed"])
            total = int(bucket["total"])
            pct = bucket["pass_rate"] * 100
            lines.append(f"  [{group}] {passed}/{total}  ({pct:.0f}%)")
        for code, bucket in sorted(mast["by_mode"].items()):
            passed = int(bucket["passed"])
            total = int(bucket["total"])
            pct = bucket["pass_rate"] * 100
            lines.append(f"    {label_of(code):<18} {passed}/{total}  ({pct:.0f}%)")
    lines.append("-" * 64)
    total_cost = sum(c.outcome.cost_usd for c in report.cases)
    pct = report.pass_rate * 100
    lines.append(f"总计: {report.passed}/{report.total} 通过 ({pct:.0f}%)   成本 ${total_cost:.4f}")
    shape_by_case = shape_means_by_case(report)
    if shape_by_case:
        overall = [
            c.shape_score for c in report.cases if c.shape_score is not None
        ]
        mean = sum(overall) / len(overall) if overall else 0.0
        lines.append(f"形状均分: {mean:.2f}")
        for case_id, score in shape_by_case.items():
            lines.append(f"  shape@{case_id}: {score:.2f}")
    lines.append("=" * 64)
    return "\n".join(lines)
