"""Secondary-delegate structured guards for mid-coordination ``delegate``.

1. Isomorphic re-delegation: same roles + similar tasks onto an already-running
   team would silently duplicate the roster (3→6). Reject. Let running workers
   continue via ``continue_from_run_id``; to open a new slice, change the role
   or write a task that is not like the in-flight one.
2. Merge ``run_id`` collisions: ``live.add`` raises :class:`RunPlanError` — report
   merged vs skipped nodes instead of a silent success echo.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

# Task similarity floor for「相似任务」(normalized SequenceMatcher ratio).
_TASK_SIMILARITY = 0.55


def _norm_task(text: str) -> str:
    return "".join((text or "").lower().split())


def tasks_similar(a: str, b: str, *, threshold: float = _TASK_SIMILARITY) -> bool:
    """True when two task strings are the same or close enough to count as isomorphic."""
    na, nb = _norm_task(a), _norm_task(b)
    if not na and not nb:
        return True
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def _node_role(node: Any) -> str:
    return str(getattr(node, "role", None) or getattr(node, "agent_name", None) or "").strip()


def _node_task(node: Any) -> str:
    return str(getattr(node, "task", None) or "")


def _format_skipped_entries(skipped: list[tuple[Any, str]]) -> str:
    parts: list[str] = []
    for node, reason in skipped:
        label = _node_role(node) or str(getattr(node, "run_id", "") or "?")
        rid = str(getattr(node, "run_id", "") or "?")
        parts.append(f"{label}（`{rid}`）— {reason}")
    return "；".join(parts) if parts else "（无）"


def started_run_ids_from_entries(
    entries: list[dict[str, Any]] | None,
) -> set[str]:
    """Run ids that actually dispatched (``run_started``), excluding captain.

    Nodes that only exist on a plan snapshot never enter the isomorphic
    「还在跑」denominator.
    """
    out: set[str] = set()
    if not entries:
        return out
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind != "run_started":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if str(payload.get("kind") or "") == "captain":
            continue
        rid = str(payload.get("run_id") or "").strip()
        if rid:
            out.add(rid)
    return out


def is_isomorphic_redelegation(
    new_plan: RunPlan,
    live_plan: RunPlan | None,
    *,
    completed_run_ids: set[str] | frozenset[str] | None = None,
    started_run_ids: set[str] | frozenset[str] | None = None,
) -> bool:
    """True when every new node matches an incomplete live worker (same role + similar task).

    Partial appends with a net-new role/task are not isomorphic. An empty new plan
    or a live plan with no incomplete workers never rejects.

    When ``started_run_ids`` is set, only those incomplete nodes count as「还在跑」.
    Never-started empty seats (plan-only leftovers) are excluded from the
    denominator so a failed arm cannot isomorphic-lock the next dispatch.
    """
    if live_plan is None or not new_plan.nodes:
        return False
    done = set(completed_run_ids or ())
    active = [n for n in live_plan.nodes if n.run_id not in done]
    if started_run_ids is not None:
        started = set(started_run_ids)
        active = [n for n in active if n.run_id in started]
    if not active:
        return False
    remaining = list(active)
    for nn in new_plan.nodes:
        n_role = _node_role(nn)
        n_task = _node_task(nn)
        hit: int | None = None
        for i, an in enumerate(remaining):
            if _node_role(an) != n_role:
                continue
            if not tasks_similar(n_task, _node_task(an)):
                continue
            hit = i
            break
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def isomorphic_reject_message(
    new_plan: RunPlan,
    *,
    completed: int,
    total: int,
) -> str:
    """Structured rejection body for the delegate tool result."""
    roles = [(_node_role(n) or n.run_id) for n in new_plan.nodes]
    roster = "、".join(roles) if roles else "（空）"
    return (
        "【再委派已拒绝·同构计划】当前协作图仍有未完成队员"
        f"（已完成 {completed}/{total}），本次 tasks（{roster}）与在跑队员角色+任务高度同构，"
        "禁止静默并入以免重复派工。要让在跑的人继续干请填 continue_from_run_id；"
        "确需再开一份请换角色或把任务写得不像在跑的那份。"
    )


def merge_all_skipped_reject_message(
    skipped: list[tuple[RunSpec, str]] | list[tuple[Any, str]],
    *,
    completed: int,
    total: int,
) -> str:
    """Structured rejection when every node in a merge batch failed ``live.add``."""
    detail = _format_skipped_entries(skipped)
    return (
        "【队员追加已拒绝·全部跳过】当前协作图未并入任何新节点"
        f"（已完成 {completed}/{total}）。跳过明细：{detail}。"
        "常见原因是 tasks.id / run_id 与图中已有节点冲突。"
        "请换用未占用的 id 后重试，勿假定本批已入队。"
    )


def merge_partial_skip_message(
    *,
    added_nodes: list[Any],
    skipped: list[tuple[RunSpec, str]] | list[tuple[Any, str]],
    total_workers: int,
    completed: int,
) -> str:
    """Structured success body when some nodes merged and some were skipped."""
    roles = [(_node_role(n) or n.run_id) for n in added_nodes]
    roster = "、".join(roles) if roles else "（无）"
    detail = _format_skipped_entries(skipped)
    return (
        f"【队员已追加·部分跳过】已并入 {len(added_nodes)} 名队员（{roster}）；"
        f"跳过 {len(skipped)} 名：{detail}。"
        f"图共 {total_workers} 名，其中 {completed} 名已完成。"
        "仍属同一协作图 / 同一协调会话。"
        "被跳过节点未入队——请换 id 重派或确认无需该节点。"
    )
