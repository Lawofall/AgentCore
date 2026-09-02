"""同队续派 / 补缺口：批次收口后仍能动同一支团队的一等入口。

批次一收口，协调会话不再活跃、``replan`` 也不可用（它只在受监督态存在），
「给同一支团队补跑」于是只剩会被冷开闸拦下的冷派——这才是模型在收口回合反复
被拒的根因，不是它不服从。本模块把这条路补成正式入口：按**结构**（节点上的
``continue_from_run_id`` / ``replaces_run_id``）把一批 task 拆成三堆，收口闸只对
真正冷开的那堆生效。

- **续派**（``continue_from_run_id``）：唤回原作者接着干。不进补跑限流——限流是
  为了拦「整团重开」，而续派的条数天然被名册与作者链 ``recall_count`` 兜住。
  目标现场存不存在由 :mod:`~agentcore.runtime.delegate.continuation` 逐节点如实
  拒绝，本层不重复校验、也不猜。
- **补缺口**（``replaces_run_id``，或 ``continue_from`` 指向失败/跳过节点）：仍按
  ``MAX_GAP_FILL_ADDS`` 限流，与同图 ``replan`` 补跑闸共用一套判定。
- **冷开**（两者皆无）：收口后大扇出仍是「整团重派」，闸照拒。

不扫 task 自由文、不猜意图：三堆的归属只看结构字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState


def _node_continue_from(node: Any) -> str:
    return str(getattr(node, "continue_from_run_id", None) or "").strip()


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
    """一批 task 按结构拆出的三堆（同一节点只进一堆）。"""

    same_person: tuple[Any, ...] = ()
    gap_fill: tuple[Any, ...] = ()
    cold: tuple[Any, ...] = ()

    @property
    def is_pure_continuation(self) -> bool:
        """整批都在动同一支团队（无冷开节点）。"""
        return not self.cold and bool(self.same_person or self.gap_fill)

    @property
    def cold_has_deps(self) -> bool:
        return any(bool(getattr(n, "depends_on", None)) for n in self.cold)


def classify_batch(
    plan: RunPlan,
    completed: Mapping[str, RunState] | None = None,
) -> ContinuationShape:
    """把 plan 节点拆成 续派 / 补缺口 / 冷开 三堆。

    ``completed`` = 已知名册终态；给出时，``continue_from`` 指向缺口的节点算补跑
    （与 ``replan`` 补跑闸同判定），指向已成功节点的算续派。名册未知时一律算续派
    ——续派链的存在性由续派执行层逐节点校验，本层不替它猜。
    """
    gaps = gap_run_ids(completed)
    same_person: list[Any] = []
    gap_fill: list[Any] = []
    cold: list[Any] = []
    for node in getattr(plan, "nodes", None) or []:
        replaces = _node_replaces(node)
        continue_from = _node_continue_from(node)
        if replaces:
            gap_fill.append(node)
        elif continue_from:
            (gap_fill if continue_from in gaps else same_person).append(node)
        else:
            cold.append(node)
    return ContinuationShape(
        same_person=tuple(same_person),
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
    continue_from = _node_continue_from(node)
    if replaces:
        item["replaces_run_id"] = replaces
    if continue_from:
        item["continue_from_run_id"] = continue_from
    return item


def gap_fill_admission_error(
    shape: ContinuationShape,
    completed: Mapping[str, RunState] | None,
) -> str | None:
    """补缺口那一堆的准入：名册已知走 ``replan`` 同一套判定，未知只兜上限。"""
    from agentcore.runtime.runs.constants import MAX_GAP_FILL_ADDS

    if not shape.gap_fill:
        return None
    if completed is not None:
        from agentcore.runtime.delegate.supervised import _gap_fill_add_errors

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
    """收口后冷开整团重派的拒绝正文——指向真实可用的续派入口。"""
    from agentcore.runtime.runs.constants import MAX_GAP_FILL_ADDS

    roster = (candidates or "").strip()
    if roster:
        continue_clause = (
            "`continue_from_run_id`（条数不限；填上轮 delegate 回执名册里的 run_id，"
            "或下列候选）"
        )
        roster_tail = f"\n可续候选：\n{roster}"
    else:
        continue_clause = (
            "`continue_from_run_id`（条数不限；填上轮 delegate 回执「队员终态名册」里的 run_id）"
        )
        roster_tail = ""
    return (
        f"收口后拒绝整团重派：本批有 {len(shape.cold)} 个既不续派、也不补缺口的冷开节点。"
        "要动同一支团队请走续派入口——让原作者接着干用 "
        f"{continue_clause}；"
        f"补失败/跳过缺口用 `replaces_run_id`（单次≤{MAX_GAP_FILL_ADDS}）。"
        "已有产出够交代就直接向老板交代。"
        f"{roster_tail}"
    )
