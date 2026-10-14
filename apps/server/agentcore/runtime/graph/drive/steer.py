"""Plan steer / downstream scoping and journal snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan


def record_plan_snapshot(plan: RunPlan) -> None:
    """Journal the current plan as a ``plan_snapshot`` fact."""
    from agentcore.runtime.graph.session.journal import record_plan_snapshot as _record

    _record(plan)


def apply_steer(plan: RunPlan, completed: dict, checkpoint_ids: set[str], note: str) -> None:
    """Inject an ADJUST note onto not-yet-run targets.

    With non-empty ``checkpoint_ids``, targets are the transitive dependents of
    those nodes. With empty roots, every not-yet-completed node is steered.
    """
    targets = (
        downstream_of(plan, checkpoint_ids)
        if checkpoint_ids
        else {n.run_id for n in plan.nodes if n.run_id not in completed}
    )
    block = f"- {note}"
    for node in plan.nodes:
        if node.run_id in completed or node.run_id not in targets:
            continue
        node.steer = f"{node.steer}\n{block}" if node.steer else block
    record_plan_snapshot(plan)


def downstream_of(plan: RunPlan, roots: set[str]) -> set[str]:
    """Run ids that (transitively) ``depends_on`` any node in ``roots``."""
    downstream: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in plan.nodes:
            if node.run_id in downstream or node.run_id in roots:
                continue
            if any(dep in roots or dep in downstream for dep in node.depends_on):
                downstream.add(node.run_id)
                changed = True
    return downstream
