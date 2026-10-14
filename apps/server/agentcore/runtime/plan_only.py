"""Plan-only eval switch: record the plan, skip worker / debate execution.

Default **off**. Eval harness enables it via :func:`use_plan_only`; production
paths never set the flag, so behavior is unchanged.

Injection points (callers check :func:`is_plan_only`):

- ``GraphTool.execute`` — after ``build_run_plan`` + ``run_plan`` emit, return
  ``HANDOFF`` instead of ``drive`` (no workers, no coordination).
- ``debate.rounds`` first-round — after debater ``run_plan`` emit, raise
  :class:`PlanOnlyAbortError` so the debate tool returns ``HANDOFF`` without running
  debaters.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# CEO tool-round budget when plan-only (solo / search-heavy bailout).
PLAN_ONLY_CEO_MAX_ROUNDS = 4

_plan_only: ContextVar[bool] = ContextVar("agentcore_plan_only", default=False)


class PlanOnlyAbortError(Exception):
    """Raised after the first meaningful ``run_plan`` when plan-only is on.

    Caught inside the debate tool (before the engine's per-tool exception firewall)
    and converted into a terminal ``HANDOFF`` ToolResult.
    """


def is_plan_only() -> bool:
    return _plan_only.get()


@contextmanager
def use_plan_only(enabled: bool = True) -> Iterator[None]:
    """Enable/disable plan-only for the current task; always resets on exit."""
    token = _plan_only.set(bool(enabled))
    try:
        yield
    finally:
        _plan_only.reset(token)
