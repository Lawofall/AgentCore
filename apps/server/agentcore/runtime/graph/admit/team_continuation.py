"""补缺口 / 冷开：批次收口后按结构拆堆。

批次一收口，协调会话不再活跃、``replan`` 也不可用（它只在受监督态存在）。
本模块按节点上的 ``replaces_run_id`` 把一批 task 拆成补缺口与冷开，收口闸只对
真正冷开的那堆生效。

- **补缺口**（``replaces_run_id``）：按 ``MAX_GAP_FILL_ADDS`` 限流，与同图
  ``replan`` 补跑闸共用一套判定。
- **冷开**（无 replaces）：收口后大扇出仍是「整团重派」，闸照拒。

不扫 task 自由文、不猜意图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState


def _node_replaces(node: Any) -> str:
    return str(getattr(node, "replaces_run_id", None) or "").strip()


def gap_run_ids(completed: Mapping[str, RunState] | None) -> frozenset[str]:
    """名册里的缺口（FAILED / SKIPPED）；``None`` 名册 → 空集（未知，不代表没有）。"""
    from agentcore.runtime.runs.types import RunPhase

    if not completed:
        return frozenset()
    return frozenset(
        rid
        for rid, st in completed.items()
        if st is not None and st.phase in (RunPhase.FAILED, RunPhase.SKIPPED)
    )


@dataclass(frozen=True, slots=True)
class ContinuationShape:
    """一批 task 按结构拆出的两堆（同一节点只进一堆）。"""

    gap_fill: tuple[Any, ...] = ()
    cold: tuple[Any, ...] = ()

    @property
    def is_pure_continuation(self) -> bool:
        """整批都是补缺口（无冷开节点）。"""
        return not self.cold and bool(self.gap_fill)

    @property
    def cold_has_deps(self) -> bool:
        return any(bool(getattr(n, "depends_on", None)) for n in self.cold)


def classify_batch(
    plan: RunPlan,
    completed: Mapping[str, RunState] | None = None,
) -> ContinuationShape:
    """把 plan 节点拆成 补缺口 / 冷开 两堆。"""
    gap_fill: list[Any] = []
    cold: list[Any] = []
    for node in getattr(plan, "nodes", None) or []:
        if _node_replaces(node):
            gap_fill.append(node)
        else:
            cold.append(node)
    return ContinuationShape(
        gap_fill=tuple(gap_fill),
        cold=tuple(cold),
    )


def _node_as_add(node: Any) -> dict[str, Any]:
    """RunSpec → ``replan.add`` 形状（复用同一套补跑判定，勿另写第二份）。"""
    item: dict[str, Any] = {
        "role": getattr(node, "role", None) or "",
        "task": getattr(node, "task", None) or "",
    }
    replaces = _node_replaces(node)
    if replaces:
        item["replaces_run_id"] = replaces
    return item


def _gap_fill_add_errors(
    adds: list,
    completed: dict[str, RunState],
) -> list[str]:
    """Hard-gate 补跑 adds that carry replaces.

    - 无缺口却带 replaces → 拒（禁止无缺口整团重开）
    - 条数 > min(缺口数, MAX_GAP_FILL_ADDS) → 拒（按缺口限流）
    - 无 replaces 的首派不走本闸
    """
    from agentcore.runtime.runs.constants import MAX_GAP_FILL_ADDS
    from agentcore.runtime.runs.types import RunPhase

    gap_ids = {
        rid
        for rid, st in completed.items()
        if st is not None and st.phase in (RunPhase.FAILED, RunPhase.SKIPPED)
    }

    gap_fill: list[tuple[int, dict[str, Any], str]] = []
    for i, item in enumerate(adds):
        if not isinstance(item, dict):
            continue
        replaces = str(item.get("replaces_run_id") or "").strip()
        if replaces:
            gap_fill.append((i, item, replaces))
    if not gap_fill:
        return []

    if not gap_ids:
        return [
            "补跑拒绝：当前无失败/跳过缺口，禁止无缺口整团重开；"
            "请用 tell 给还没开始的人补一句，或 stop=true 收口，勿用 replaces 重开全队"
        ]

    max_allowed = min(len(gap_ids), MAX_GAP_FILL_ADDS)
    if len(gap_fill) > max_allowed:
        return [
            f"补跑一次最多追加 {max_allowed} 个缺口点名节点"
            f"（缺口 {len(gap_ids)}，上限 {MAX_GAP_FILL_ADDS}，收到 {len(gap_fill)}）；"
            "请只点名最关键失败/跳过节点，分批 replan，勿整团重开"
        ]

    errors: list[str] = []
    for i, _item, target in gap_fill:
        if target not in gap_ids:
            errors.append(
                f"add[{i}]: replaces `{target}` 不是当前失败/跳过缺口；"
                f"请点名缺口 run_id（{', '.join(sorted(gap_ids)[:8])}）"
            )
    return errors


def gap_fill_admission_error(
    shape: ContinuationShape,
    completed: Mapping[str, RunState] | None,
) -> str | None:
    """补缺口那一堆的准入：名册已知走 ``replan`` 同一套判定，未知只兜上限。"""
    from agentcore.runtime.runs.constants import MAX_GAP_FILL_ADDS

    if not shape.gap_fill:
        return None
    if completed is not None:
        errors = _gap_fill_add_errors(
            [_node_as_add(n) for n in shape.gap_fill], dict(completed)
        )
        if errors:
            return errors[0]
        return None
    if len(shape.gap_fill) > MAX_GAP_FILL_ADDS:
        return (
            f"补跑一次最多追加 {MAX_GAP_FILL_ADDS} 个点名节点"
            f"（上限 {MAX_GAP_FILL_ADDS}，收到 {len(shape.gap_fill)}）；"
            "请只点名最关键节点，分批补跑，勿整团重开"
        )
    return None


_MAX_CONTINUATION_CANDIDATES = 12


def _continuation_status_label(phase: Any) -> str:
    from agentcore.runtime.runs.types import RunPhase

    if phase is None:
        return "running"
    value = phase.value if isinstance(phase, RunPhase) else str(phase).strip().lower()
    if value in ("completed", "failed", "cancelled", "skipped"):
        return value
    return "running"


def format_continuation_candidates(
    *,
    plan: RunPlan | None = None,
    completed: Mapping[str, RunState] | None = None,
    max_n: int = _MAX_CONTINUATION_CANDIDATES,
) -> str:
    """Reject-copy roster (run_id / role / status). Empty when nothing to list."""
    seed = dict(completed or {})
    lines: list[str] = []
    seen: set[str] = set()
    nodes = list(getattr(plan, "nodes", None) or [])
    for node in nodes:
        if str(getattr(node, "kind", "") or "") == "captain":
            continue
        run_id = str(getattr(node, "run_id", "") or "").strip()
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        role = (
            str(getattr(node, "role", "") or "").strip()
            or str(getattr(node, "agent_name", "") or "").strip()
            or run_id
        )
        state = seed.get(run_id)
        phase = getattr(state, "phase", None) if state is not None else None
        lines.append(
            f"- run_id={run_id}; role={role}; status={_continuation_status_label(phase)}"
        )
        if len(lines) >= max_n:
            break
    if not lines:
        for run_id, state in seed.items():
            rid = str(run_id or "").strip()
            if not rid:
                continue
            phase = getattr(state, "phase", None) if state is not None else None
            lines.append(f"- run_id={rid}; status={_continuation_status_label(phase)}")
            if len(lines) >= max_n:
                break
    extra = 0
    if nodes:
        countable = [
            n
            for n in nodes
            if str(getattr(n, "kind", "") or "") != "captain"
            and str(getattr(n, "run_id", "") or "").strip()
        ]
        extra = max(0, len(countable) - len(lines))
    elif seed:
        extra = max(0, len(seed) - len(lines))
    if extra > 0:
        lines.append(f"- …另有 {extra} 个未列出")
    return "\n".join(lines)


def cold_open_reject_message(
    shape: ContinuationShape,
    *,
    candidates: str = "",
) -> str:
    """收口后一次开得太多。拒绝理由只说接在已有结论上。"""
    roster = (candidates or "").strip()
    tail = f"\n已有结论：\n{roster}" if roster else ""
    return (
        f"收口后一次开了 {len(shape.cold)} 段，太多。"
        "下一段用 graph 接在这扇窗已经看见的结论上。"
        f"{tail}"
    )
