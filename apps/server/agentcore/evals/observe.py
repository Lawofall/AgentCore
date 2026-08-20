"""相对基线的观测对比——让真 LLM eval 的结果可比、可见，不当硬门。

真模型输出天然有方差。本模块**不做**「通过率掉过某容差即回归」的假红线：那种线
比没有更有害。能用来区分「方差」和「退化」的，是**共享用例的翻转方向**：

- 双向（有过→挂也有挂→过）→ 今夜更像对称抖动
- 单方向过→挂、挂→过为零 → 不像对称抖动；是否算退化仍须人看那几例
- 没有逐例名单、或共享用例对不上 → **分不出**，报告里直说

不改退出码、不引入合并门禁。CLI 把本段写进报告 JSON 的 ``ratchet`` 键（夜跑
``jq 'del(.ratchet)'`` 仍能剥掉，避免基线文件嵌套昨夜对比）。
"""

from __future__ import annotations

import math
from typing import Any

# 观测签名：给人读的分类，不是门禁判定。
SIG_NO_BASELINE = "no_baseline"
SIG_INCOMPARABLE = "incomparable"
SIG_RATE_ONLY = "rate_only"
SIG_UNCHANGED = "unchanged"
SIG_CHURN = "churn"
SIG_DROP = "directional_drop"
SIG_GAIN = "directional_gain"

_CAVEATS = (
    "真 LLM 多数用例 samples=1：单例翻转随时是一夜抖动，不是提示词/Skill 退化的证明。",
    "夜跑对比最近一次成功快照，另存只升不降的历史峰值；从峰值回落可能是均值回归，单靠今夜翻转不能定罪。",
    "本对比不改变退出码，也不是合并门禁。",
)


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _rate(passed: int, total: int) -> float | None:
    if total <= 0:
        return None
    return passed / total


def _binomial_se(pass_rate: float | None, n: int) -> float | None:
    """Bernoulli 标准误，只描述「这一夜通过率自己有多宽」，不当红线。"""
    if pass_rate is None or n <= 0:
        return None
    p = min(1.0, max(0.0, float(pass_rate)))
    return math.sqrt(p * (1.0 - p) / n)


def _frac(passed: int, n: int) -> str:
    return f"{passed}/{n}"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _report_src(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("report")
    return raw if isinstance(raw, dict) else data


def _kind(data: dict[str, Any]) -> str:
    routing = data.get("routing")
    if isinstance(routing, dict) and ("accuracy" in routing or "confusion" in routing):
        return "routing"
    if "clean_rate" in data and isinstance(data.get("offenders"), list):
        return "style"
    summary = _as_dict(data.get("summary"))
    if "by_archetype" in summary:
        return "comparison"
    if "pass_rate" in summary or "total" in summary:
        return "suite"
    if isinstance(data.get("cases"), list):
        return "suite"
    return "unknown"


def _case_scores(cases: list[Any]) -> dict[str, tuple[int, int]]:
    """case_id → (passed_count, n)。samples>1 时同 id 多条累计。"""
    acc: dict[str, list[int]] = {}
    for raw in cases:
        if not isinstance(raw, dict):
            continue
        cid = raw.get("case_id")
        if not isinstance(cid, str) or not cid:
            continue
        acc.setdefault(cid, []).append(1 if raw.get("passed") else 0)
    return {cid: (sum(hits), len(hits)) for cid, hits in acc.items()}


def _flip_signature(n_worse: int, n_better: int) -> str:
    if n_worse == 0 and n_better == 0:
        return SIG_UNCHANGED
    if n_worse > 0 and n_better > 0:
        return SIG_CHURN
    if n_worse > 0:
        return SIG_DROP
    return SIG_GAIN


def _unavailable(
    path: str, *, current_n: int = 0, current_rate: float | None = None
) -> dict[str, Any]:
    return {
        "schema": "observe.v1",
        "available": False,
        "gate": False,
        "kind": "suite",
        "signature": SIG_NO_BASELINE,
        "can_separate_variance": False,
        "baseline_path": path,
        "pass_rate": _round(current_rate),
        "detail": "无基线可比（本次可作首个快照；--update-baseline 落盘）",
        "reading": (
            "没有上一份基线，无法做相对对比。本次报告可当首跑快照；"
            "用 --update-baseline 落盘后，下次才能看出相对变化。"
        ),
        "caveats": list(_CAVEATS),
        "current": {"n": current_n, "pass_rate": _round(current_rate)},
    }


def _incomparable(reason: str, *, kind: str = "unknown") -> dict[str, Any]:
    return {
        "schema": "observe.v1",
        "available": True,
        "gate": False,
        "kind": kind,
        "signature": SIG_INCOMPARABLE,
        "can_separate_variance": False,
        "detail": f"对不上：{reason}",
        "reading": f"基线与本次报告对不上（{reason}），不能做相对对比，更不能据此谈退化。",
        "caveats": list(_CAVEATS),
    }


def _reading_suite(
    *,
    signature: str,
    n_shared: int,
    n_worse: int,
    n_better: int,
    has_cases: bool,
) -> str:
    if not has_cases:
        return (
            "基线或本次缺少逐例名单，只剩通过率之差。"
            "真模型方差下，单靠通过率分不出「这是抖动」还是「这是退化」——"
            "不要把 Δ 读成红线。下次请用带 cases[] 的完整报告作基线。"
        )
    if signature == SIG_UNCHANGED:
        return (
            f"共享 {n_shared} 例无一翻转。只能说明今夜没看见用例级变化，"
            "不能证明没有退化（samples=1 时「全过」也可能是运气）。"
        )
    if signature == SIG_CHURN:
        return (
            f"双向翻转：{n_worse} 例变差、{n_better} 例变好。"
            "双向对冲是方差的典型样子，不是单方向退化；仍建议扫一眼变差的用例。"
        )
    if signature == SIG_DROP:
        return (
            f"{n_worse} 例变差、0 例变好。这不是对称抖动的样子；"
            "是否算提示词/Skill 退化请人看下列用例。"
            "单次采样下即便一边倒，也不能当成硬证据。"
        )
    return (
        f"{n_better} 例变好、0 例变差。单方向改善；同样可能是一夜运气，"
        "请人看变好的是否稳定复现。"
    )


def _detail_line(
    *,
    signature: str,
    cur_rate: float | None,
    base_rate: float | None,
    n_worse: int,
    n_better: int,
    can_separate: bool,
) -> str:
    cur = "n/a" if cur_rate is None else f"{cur_rate:.4f}"
    base = "n/a" if base_rate is None else f"{base_rate:.4f}"
    delta = None
    if cur_rate is not None and base_rate is not None:
        delta = cur_rate - base_rate
    delta_s = "n/a" if delta is None else f"{delta:+.4f}"
    sig_zh = {
        SIG_UNCHANGED: "无翻转",
        SIG_CHURN: f"双向抖动（差{n_worse}/好{n_better}）",
        SIG_DROP: f"单方向变差 {n_worse} 例",
        SIG_GAIN: f"单方向变好 {n_better} 例",
        SIG_RATE_ONLY: "仅有通过率，分不出方差/退化",
        SIG_INCOMPARABLE: "对不上",
        SIG_NO_BASELINE: "无基线",
    }.get(signature, signature)
    sep = "可看翻转方向" if can_separate else "分不出方差/退化"
    if cur_rate is None and base_rate is None:
        return f"相对基线：{sig_zh} · {sep}（非门禁）"
    return f"相对基线：{cur} vs {base}（Δ {delta_s}）· {sig_zh} · {sep}（非门禁）"


def _observe_suite(
    current: dict[str, Any], baseline: dict[str, Any], *, path: str
) -> dict[str, Any]:
    src = _report_src(current)
    base_src = _report_src(baseline)
    cur_summary = _as_dict(src.get("summary"))
    base_summary = _as_dict(base_src.get("summary"))
    cur_cases = _as_list(src.get("cases"))
    base_cases = _as_list(base_src.get("cases"))

    cur_n = int(cur_summary.get("total") or len(cur_cases) or 0)
    base_n = int(base_summary.get("total") or len(base_cases) or 0)
    cur_passed = int(cur_summary.get("passed") or 0)
    base_passed = int(base_summary.get("passed") or 0)
    cur_rate = cur_summary.get("pass_rate")
    base_rate = base_summary.get("pass_rate")
    if cur_rate is None:
        cur_rate = _rate(cur_passed, cur_n)
    if base_rate is None:
        base_rate = _rate(base_passed, base_n)
    try:
        cur_rate_f = float(cur_rate) if cur_rate is not None else None
    except (TypeError, ValueError):
        cur_rate_f = None
    try:
        base_rate_f = float(base_rate) if base_rate is not None else None
    except (TypeError, ValueError):
        base_rate_f = None

    cur_scores = _case_scores(cur_cases)
    base_scores = _case_scores(base_cases)
    shared = sorted(set(cur_scores) & set(base_scores))
    added = sorted(set(cur_scores) - set(base_scores))
    removed = sorted(set(base_scores) - set(cur_scores))

    worse: list[dict[str, Any]] = []
    better: list[dict[str, Any]] = []
    n_same = 0
    for cid in shared:
        c_ok, c_n = cur_scores[cid]
        b_ok, b_n = base_scores[cid]
        c_r, b_r = _rate(c_ok, c_n), _rate(b_ok, b_n)
        item = {
            "case_id": cid,
            "baseline": _frac(b_ok, b_n),
            "current": _frac(c_ok, c_n),
        }
        if c_r is None or b_r is None or c_r == b_r:
            n_same += 1
            continue
        if c_r < b_r:
            worse.append(item)
        else:
            better.append(item)

    has_cases = bool(cur_scores) and bool(base_scores)
    if has_cases:
        signature = _flip_signature(len(worse), len(better))
        can_separate = True
    else:
        signature = SIG_RATE_ONLY
        can_separate = False

    delta = None
    if cur_rate_f is not None and base_rate_f is not None:
        delta = cur_rate_f - base_rate_f

    reading = _reading_suite(
        signature=signature,
        n_shared=len(shared),
        n_worse=len(worse),
        n_better=len(better),
        has_cases=has_cases,
    )
    return {
        "schema": "observe.v1",
        "available": True,
        "gate": False,
        "kind": "suite",
        "signature": signature,
        "can_separate_variance": can_separate,
        "baseline_path": path,
        "pass_rate": _round(cur_rate_f),
        "baseline_pass_rate": _round(base_rate_f),
        "delta_pass_rate": _round(delta),
        "binomial_se_current": _round(_binomial_se(cur_rate_f, cur_n)),
        "detail": _detail_line(
            signature=signature,
            cur_rate=cur_rate_f,
            base_rate=base_rate_f,
            n_worse=len(worse),
            n_better=len(better),
            can_separate=can_separate,
        ),
        "reading": reading,
        "caveats": list(_CAVEATS),
        "current": {"n": cur_n, "passed": cur_passed, "pass_rate": _round(cur_rate_f)},
        "baseline": {"n": base_n, "passed": base_passed, "pass_rate": _round(base_rate_f)},
        "paired": {
            "shared_cases": len(shared),
            "added": added,
            "removed": removed,
            "worse": worse,
            "better": better,
            "same": n_same,
        },
    }


def _id_set(rows: list[Any], key: str = "case_id") -> set[str]:
    out: set[str] = set()
    for raw in rows:
        if isinstance(raw, dict):
            cid = raw.get(key)
            if isinstance(cid, str) and cid:
                out.add(cid)
    return out


def _observe_routing(
    current: dict[str, Any], baseline: dict[str, Any], *, path: str
) -> dict[str, Any]:
    cur = _as_dict(current.get("routing"))
    base = _as_dict(baseline.get("routing"))
    cur_ids = _id_set(cur.get("misroutes") or [])
    base_ids = _id_set(base.get("misroutes") or [])
    worse = sorted(cur_ids - base_ids)
    better = sorted(base_ids - cur_ids)
    has_ids = cur.get("misroutes") is not None and base.get("misroutes") is not None
    if has_ids:
        signature = _flip_signature(len(worse), len(better))
        can_separate = True
    else:
        signature = SIG_RATE_ONLY
        can_separate = False
    try:
        cur_acc = float(cur["accuracy"]) if cur.get("accuracy") is not None else None
    except (TypeError, ValueError, KeyError):
        cur_acc = None
    try:
        base_acc = float(base["accuracy"]) if base.get("accuracy") is not None else None
    except (TypeError, ValueError, KeyError):
        base_acc = None
    delta = None if cur_acc is None or base_acc is None else cur_acc - base_acc
    n_cur = int(cur.get("total") or 0)
    if signature == SIG_CHURN:
        reading = (
            f"路由错判双向进出：新错 {len(worse)}、纠正 {len(better)}。"
            "更像一夜抖动；请扫新错的用例，不要把准确率掉点读成红线。"
        )
    elif signature == SIG_DROP:
        reading = (
            f"新出现 {len(worse)} 条错判、0 条纠正。不像对称抖动；"
            "是否退化请人看这些 case_id。本对比非门禁。"
        )
    elif signature == SIG_GAIN:
        reading = f"纠正 {len(better)} 条错判、无新错。单方向改善，仍可能是运气。"
    elif signature == SIG_UNCHANGED:
        reading = "错判集合与基线相同。只能说明今夜没看见路由错判集合变化。"
    else:
        reading = (
            "没有逐条错判名单，只剩准确率之差。"
            "真模型方差下分不出抖动和退化，不要把 Δ 读成红线。"
        )
    return {
        "schema": "observe.v1",
        "available": True,
        "gate": False,
        "kind": "routing",
        "signature": signature,
        "can_separate_variance": can_separate,
        "baseline_path": path,
        "pass_rate": _round(cur_acc),
        "baseline_pass_rate": _round(base_acc),
        "delta_pass_rate": _round(delta),
        "binomial_se_current": _round(_binomial_se(cur_acc, n_cur)),
        "detail": _detail_line(
            signature=signature,
            cur_rate=cur_acc,
            base_rate=base_acc,
            n_worse=len(worse),
            n_better=len(better),
            can_separate=can_separate,
        ),
        "reading": reading,
        "caveats": list(_CAVEATS),
        "current": {
            "n": n_cur,
            "accuracy": _round(cur_acc),
            "over_delegation_rate": cur.get("over_delegation_rate"),
            "under_delegation_rate": cur.get("under_delegation_rate"),
        },
        "baseline": {
            "n": int(base.get("total") or 0),
            "accuracy": _round(base_acc),
            "over_delegation_rate": base.get("over_delegation_rate"),
            "under_delegation_rate": base.get("under_delegation_rate"),
        },
        "paired": {
            "shared_cases": len(cur_ids | base_ids),
            "worse": [{"case_id": cid} for cid in worse],
            "better": [{"case_id": cid} for cid in better],
            "same": len(cur_ids & base_ids),
            "added": [],
            "removed": [],
        },
    }


def _observe_style(
    current: dict[str, Any], baseline: dict[str, Any], *, path: str
) -> dict[str, Any]:
    cur_ids = _id_set(current.get("offenders") or [])
    base_ids = _id_set(baseline.get("offenders") or [])
    worse = sorted(cur_ids - base_ids)
    better = sorted(base_ids - cur_ids)
    has_lists = isinstance(current.get("offenders"), list) and isinstance(
        baseline.get("offenders"), list
    )
    signature = _flip_signature(len(worse), len(better)) if has_lists else SIG_RATE_ONLY
    can_separate = bool(has_lists)
    try:
        cur_rate = float(current["clean_rate"]) if current.get("clean_rate") is not None else None
    except (TypeError, ValueError, KeyError):
        cur_rate = None
    try:
        base_rate = (
            float(baseline["clean_rate"]) if baseline.get("clean_rate") is not None else None
        )
    except (TypeError, ValueError, KeyError):
        base_rate = None
    delta = None if cur_rate is None or base_rate is None else cur_rate - base_rate
    n_cur = int(current.get("total") or 0)
    if signature == SIG_CHURN:
        reading = (
            f"风格违规双向进出：新违规 {len(worse)}、消失 {len(better)}。更像抖动。"
        )
    elif signature == SIG_DROP:
        reading = (
            f"新出现 {len(worse)} 条违规、0 条消失。不像对称抖动；请人看 offender 名单。"
        )
    elif signature == SIG_GAIN:
        reading = f"{len(better)} 条违规消失、无新增。单方向改善，仍可能是运气。"
    elif signature == SIG_UNCHANGED:
        reading = "违规集合与基线相同。"
    else:
        reading = "没有 offender 名单，只剩干净率之差，分不出方差和退化。"
    return {
        "schema": "observe.v1",
        "available": True,
        "gate": False,
        "kind": "style",
        "signature": signature,
        "can_separate_variance": can_separate,
        "baseline_path": path,
        "pass_rate": _round(cur_rate),
        "baseline_pass_rate": _round(base_rate),
        "delta_pass_rate": _round(delta),
        "binomial_se_current": _round(_binomial_se(cur_rate, n_cur)),
        "detail": _detail_line(
            signature=signature,
            cur_rate=cur_rate,
            base_rate=base_rate,
            n_worse=len(worse),
            n_better=len(better),
            can_separate=can_separate,
        ),
        "reading": reading,
        "caveats": list(_CAVEATS),
        "current": {"n": n_cur, "clean_rate": _round(cur_rate)},
        "baseline": {
            "n": int(baseline.get("total") or 0),
            "clean_rate": _round(base_rate),
        },
        "paired": {
            "shared_cases": len(cur_ids | base_ids),
            "worse": [{"case_id": cid} for cid in worse],
            "better": [{"case_id": cid} for cid in better],
            "same": len(cur_ids & base_ids),
            "added": [],
            "removed": [],
        },
    }


def _arm_win_rates(cases: list[Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw in cases:
        if not isinstance(raw, dict):
            continue
        cid = raw.get("case_id")
        comps = raw.get("comparisons")
        if not isinstance(cid, str) or not isinstance(comps, dict):
            continue
        for arm, metrics in comps.items():
            if not isinstance(metrics, dict):
                continue
            wr = metrics.get("win_rate")
            if wr is None:
                continue
            try:
                out[f"{cid}::{arm}"] = float(wr)
            except (TypeError, ValueError):
                continue
    return out


def _observe_comparison(
    current: dict[str, Any], baseline: dict[str, Any], *, path: str
) -> dict[str, Any]:
    cur_cases = _as_list(current.get("cases"))
    base_cases = _as_list(baseline.get("cases"))
    cur_w = _arm_win_rates(cur_cases)
    base_w = _arm_win_rates(base_cases)
    shared = sorted(set(cur_w) & set(base_w))
    worse: list[dict[str, Any]] = []
    better: list[dict[str, Any]] = []
    n_same = 0
    for key in shared:
        c_r, b_r = cur_w[key], base_w[key]
        cid, _, arm = key.partition("::")
        item = {
            "case_id": cid,
            "arm": arm,
            "baseline": f"{b_r:.4f}",
            "current": f"{c_r:.4f}",
        }
        if c_r == b_r:
            n_same += 1
        elif c_r < b_r:
            worse.append(item)
        else:
            better.append(item)
    has_cases = bool(shared)
    signature = _flip_signature(len(worse), len(better)) if has_cases else SIG_RATE_ONLY
    can_separate = has_cases
    cur_n = int(_as_dict(current.get("summary")).get("total_cases") or len(cur_cases) or 0)
    if signature == SIG_CHURN:
        reading = (
            f"成对胜率双向变化：{len(worse)} 臂变差、{len(better)} 臂变好。更像抖动。"
        )
    elif signature == SIG_DROP:
        reading = (
            f"{len(worse)} 个用例臂胜率下降、0 个上升。不像对称抖动；请人看这些臂。"
        )
    elif signature == SIG_GAIN:
        reading = f"{len(better)} 个用例臂胜率上升、0 个下降。单方向改善，仍可能是运气。"
    elif signature == SIG_UNCHANGED:
        reading = "共享用例臂的胜率与基线相同。"
    else:
        reading = "没有可配对的用例臂胜率，分不出方差和退化。"
    return {
        "schema": "observe.v1",
        "available": True,
        "gate": False,
        "kind": "comparison",
        "signature": signature,
        "can_separate_variance": can_separate,
        "baseline_path": path,
        "detail": _detail_line(
            signature=signature,
            cur_rate=None,
            base_rate=None,
            n_worse=len(worse),
            n_better=len(better),
            can_separate=can_separate,
        ),
        "reading": reading,
        "caveats": list(_CAVEATS),
        "current": {"n": cur_n},
        "baseline": {
            "n": int(_as_dict(baseline.get("summary")).get("total_cases") or len(base_cases) or 0)
        },
        "paired": {
            "shared_cases": len(shared),
            "worse": worse,
            "better": better,
            "same": n_same,
            "added": sorted(set(cur_w) - set(base_w)),
            "removed": sorted(set(base_w) - set(cur_w)),
        },
    }


def observe_report(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    baseline_path: str = "",
) -> dict[str, Any]:
    """当前报告 vs 基线 → 观测 dict（可进报告 JSON / 夜跑摘要）。永不暗示硬门。"""
    src = current if isinstance(current, dict) else {}
    cur_summary = _as_dict(src.get("summary"))
    cur_n = int(cur_summary.get("total") or 0)
    try:
        cur_rate = (
            float(cur_summary["pass_rate"]) if cur_summary.get("pass_rate") is not None else None
        )
    except (TypeError, ValueError, KeyError):
        cur_rate = None
    if baseline is None:
        return _unavailable(baseline_path, current_n=cur_n, current_rate=cur_rate)
    if not isinstance(baseline, dict):
        return _incomparable("基线不是 JSON 对象")
    kind = _kind(src)
    base_kind = _kind(baseline)
    if kind == "unknown" or base_kind == "unknown":
        return _incomparable("无法识别报告形状", kind=kind)
    if kind != base_kind:
        return _incomparable(f"形状不同（本次 {kind}，基线 {base_kind}）", kind=kind)
    if kind == "routing":
        return _observe_routing(src, baseline, path=baseline_path)
    if kind == "style":
        return _observe_style(src, baseline, path=baseline_path)
    if kind == "comparison":
        return _observe_comparison(src, baseline, path=baseline_path)
    return _observe_suite(src, baseline, path=baseline_path)


def format_observe(obs: dict[str, Any]) -> str:
    """控制台文本。ASCII 框线，避免 Windows 控制台乱码。"""
    lines = ["=" * 64, "相对基线观测（非门禁，不改退出码）", "=" * 64]
    if not obs.get("available"):
        lines.append(f"  {obs.get('reading') or obs.get('detail')}")
        lines.append("=" * 64)
        return "\n".join(lines)
    lines.append(f"  签名 {obs.get('signature')}    种类 {obs.get('kind')}")
    cur = _as_dict(obs.get("current"))
    base = _as_dict(obs.get("baseline"))
    if obs.get("pass_rate") is not None or obs.get("baseline_pass_rate") is not None:
        se = obs.get("binomial_se_current")
        se_s = f"{se:.4f}" if isinstance(se, (int, float)) else "n/a"
        delta = obs.get("delta_pass_rate")
        delta_s = f"{delta:+.4f}" if isinstance(delta, (int, float)) else "n/a"
        lines.append(
            f"  本次 {_round(obs.get('pass_rate'))}  vs 基线 "
            f"{_round(obs.get('baseline_pass_rate'))}  Δ {delta_s}"
            f"    n={cur.get('n', '?')}/{base.get('n', '?')}"
        )
        lines.append(f"  二项 SE≈{se_s}（描述今夜通过率宽度，不是红线）")
    lines.append(f"  {obs.get('reading', '')}")
    paired = _as_dict(obs.get("paired"))
    worse = paired.get("worse") or []
    better = paired.get("better") or []
    if worse:
        lines.append(f"  变差 ({len(worse)}):")
        for item in worse[:20]:
            extra = f" {item['arm']}" if item.get("arm") else ""
            b = item.get("baseline", "")
            c = item.get("current", "")
            arrow = f"  {b} -> {c}" if b or c else ""
            lines.append(f"    - {item.get('case_id')}{extra}{arrow}")
        if len(worse) > 20:
            lines.append(f"    ... 另有 {len(worse) - 20} 例")
    if better:
        lines.append(f"  变好 ({len(better)}):")
        for item in better[:10]:
            extra = f" {item['arm']}" if item.get("arm") else ""
            lines.append(f"    - {item.get('case_id')}{extra}")
        if len(better) > 10:
            lines.append(f"    ... 另有 {len(better) - 10} 例")
    added = paired.get("added") or []
    removed = paired.get("removed") or []
    if added:
        lines.append(f"  本次有、基线无: {added[:12]}")
    if removed:
        lines.append(f"  基线有、本次无: {removed[:12]}")
    lines.append("-" * 64)
    for caveat in obs.get("caveats") or _CAVEATS:
        lines.append(f"  ! {caveat}")
    lines.append("=" * 64)
    return "\n".join(lines)
