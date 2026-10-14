"""Callables drive registers so facing can read delivery facts without importing drive."""

from __future__ import annotations

from typing import Any

collect_artifacts: Any = None
acceptance_counts: Any = None
collect_worker_gaps: Any = None
format_worker_gaps_block: Any = None


def require(name: str) -> Any:
    fn = globals().get(name)
    if fn is None:
        raise RuntimeError(f"facing delivery port {name} is not registered")
    return fn
