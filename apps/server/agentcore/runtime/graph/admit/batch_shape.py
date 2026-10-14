"""Delegate batch shape helpers for engine soft gates (audit gate, budgets)."""

from __future__ import annotations

import json
from typing import Any


def batch_shape_from_tasks(tasks: object) -> tuple[int, bool]:
    """Return ``(node_count, has_deps)`` from a raw ``tasks`` array."""
    if not isinstance(tasks, list) or not tasks:
        return 0, False
    has_deps = any(
        isinstance(t, dict) and bool(t.get("depends_on")) for t in tasks
    )
    return len(tasks), has_deps


def batch_shape_from_arguments(arguments: str | dict[str, Any] | None) -> tuple[int, bool]:
    """Parse delegate tool-call arguments into ``(node_count, has_deps)``."""
    if arguments is None:
        return 0, False
    if isinstance(arguments, str):
        if not arguments.strip():
            return 0, False
        try:
            data = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return 0, False
    elif isinstance(arguments, dict):
        data = arguments
    else:
        return 0, False
    return batch_shape_from_tasks(data.get("tasks"))


def is_substantial_batch(node_count: int, has_deps: bool) -> bool:
    """Substantial = ≥3 nodes or any dependency edge (not a trivial 2-node fan-out)."""
    return node_count >= 3 or has_deps


def annotate_batch_meta(
    result: Any,
    *,
    node_count: int,
    has_deps: bool,
    audit_hard: bool = False,
    includes_review: bool = False,
) -> Any:
    """Stamp batch shape onto a ``ToolResult.metadata`` for the loop controller."""
    from dataclasses import replace

    meta = dict(getattr(result, "metadata", None) or {})
    meta["batch_nodes"] = node_count
    meta["batch_has_deps"] = has_deps
    if audit_hard:
        meta["audit_hard"] = True
    if includes_review:
        meta["batch_includes_review"] = True
    return replace(result, metadata=meta)
