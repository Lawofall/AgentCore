"""Model-facing graph nodes, checked before the scheduler builds a run plan.

The model sends ``nodes`` (or replan ``add``) with ``id``, ``goal``,
``depends_on``. Anything else on the item is refused. The scheduler reads
``goal`` as the work of the segment.
"""

from __future__ import annotations

from typing import Any

_ITEM_KEYS = frozenset({"id", "goal", "depends_on"})


def coerce_node_items(
    items: object,
    *,
    where: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Check node objects. The work stays on ``goal``."""
    if not isinstance(items, list) or not items:
        return [], f"{where} 必须是非空数组。"
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return [], f"{where}[{index}] 必须是对象。"
        if set(item) - _ITEM_KEYS:
            return [], f"{where}[{index}] 只收 id、goal、depends_on。"
        node_id = item.get("id")
        goal = item.get("goal")
        if not isinstance(node_id, str) or not node_id.strip():
            return [], f"{where}[{index}] 缺 id。"
        if not isinstance(goal, str) or not goal.strip():
            return [], f"{where}[{index}] 缺 goal。"
        deps = item.get("depends_on") or []
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            return [], f"{where}[{index}] 的 depends_on 必须是字符串数组。"
        out.append(
            {
                "id": node_id.strip(),
                "goal": goal.strip(),
                "depends_on": [dep.strip() for dep in deps if dep.strip()],
            }
        )
    return out, None
