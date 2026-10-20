"""Run-tree concurrency budget (ContextVar).

Root CEO fan-out still **分而不乘**: each :class:`WaveScheduler` divides
``current_budget`` by its dispatch width (:func:`child_budget`) and installs the
share on child tasks, so a wide root wave does not pretend every child still
holds the full knobs.

Product nested ``delegate`` (depth≥1) **reseeds full** via
:func:`reseed_nested_delegation_budget` before the nested scheduler runs — each
sub-team schedules against ``engine_max_parallel_delegations`` so a 4-wide nest
is not starved to ``12//N`` by sibling leads. Safe because nesting is hard-capped
at ``MAX_DELEGATION_DEPTH`` (2) and per-lead spawn at ``MAX_WORKER_SUBDELEGATIONS``.
Raw WaveScheduler-in-WaveScheduler without a reseed still divides (unit tests).
"""

from __future__ import annotations

import contextvars

from agentcore.runtime.runs.constants import MAX_PARALLEL_DELEGATIONS

# No ``default=`` on the ContextVar: the root budget is resolved LAZILY (see
# :func:`current_budget`) the first time it is read without an explicit :func:`set_budget`,
# so the configured value governs the tree root. A module-level ``default=`` would freeze at
# import time — before settings load — and re-reading settings at module import is forbidden
# for the ``runs`` package (依赖纪律). Once a scheduler seeds / divides the budget via
# :func:`set_budget`, that explicit value wins in-context, exactly as before.
_budget: contextvars.ContextVar[int] = contextvars.ContextVar("run_parallel_budget")


def resolve_max_parallel() -> int:
    """The configured tree-wide + single-scheduler parallel budget.

    Reads ``settings.engine_max_parallel_delegations`` via a lazy import so the ``runs``
    package imports nothing package-external at module load (parity with
    :func:`~agentcore.runtime.runs.worker_budget._settings_default_token_ceiling`); falls back
    to :data:`MAX_PARALLEL_DELEGATIONS` when settings are unavailable (unit stubs) or the
    configured value is non-positive.
    """
    try:
        from agentcore.config import settings

        value = int(settings.engine_max_parallel_delegations)
        if value > 0:
            return value
    except Exception:  # noqa: BLE001 — settings optional in unit stubs
        pass
    return MAX_PARALLEL_DELEGATIONS


def current_budget() -> int:
    """Remaining parallel slots available to the current subtree (always >= 1).

    With no budget explicitly installed on this context (tree root not yet seeded by a
    scheduler), the configured budget is resolved lazily via :func:`resolve_max_parallel`.
    """
    try:
        value = _budget.get()
    except LookupError:
        value = resolve_max_parallel()
    return max(1, value)


def set_budget(value: int) -> contextvars.Token[int]:
    """Set the parallel budget for the current context; returns a reset token.

    Seeds the root budget at a tree entry point (and by tests), and is called inside
    each child run's task to install its reduced share — no reset needed there, the
    task's context copy is discarded when it ends.
    """
    return _budget.set(max(1, value))


def reset_budget(token: contextvars.Token[int]) -> None:
    """Restore a budget previously set via :func:`set_budget`."""
    _budget.reset(token)


def child_budget(width: int) -> int:
    """The per-child budget when this subtree runs ``width`` nodes concurrently.

    Integer-dividing the current budget by the concurrency width keeps the sum of
    the concurrent children's budgets ≤ this subtree's budget, so the product across
    depth can't explode. Always ≥ 1 (a single slot still makes progress).
    """
    return max(1, current_budget() // max(1, width))


def reseed_nested_delegation_budget(depth: int) -> contextvars.Token[int] | None:
    """Re-install the full parallel knob for a nested ``delegate`` drive.

    ``depth >= 1`` (worker-captain sub-team) returns a :func:`set_budget` token for
    the caller to :func:`reset_budget` after the nested wave; ``depth == 0`` is a
    no-op (CEO root keeps whatever share the outer context already holds).
    """
    if int(depth or 0) < 1:
        return None
    return set_budget(resolve_max_parallel())
