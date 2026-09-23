"""Turn-end cost ledger reconcile (cloud finalize / handoff / interrupt / pause).

``cost_calls`` is the billing authority for metered LLM calls; ``cost_events`` is
the product / quota view. Cloud in-process metering already writes call details;
this module drains the outbox, upserts per-run aggregates from those calls, then
folds any ``cost_runs`` orphans (e.g. ``role=vision`` 读图 rows that are
priced only onto the turn sink — not via ``log_llm_call`` metering).

Interrupt closers (``/stop`` / sweeper / process_kill) call the same reconcile with
empty ``cost_runs`` so already-metered calls still emit ``cost.recorded``.

Idempotent: call ``call_id`` / run ``run_id`` unique keys + materialize DO UPDATE
mean retries never double-bill. Captain + worker + debate spend land as separate
rows with ``role`` / ``persona`` / ``run_id`` so payroll and ``log_stats`` can
split by role without changing credential-source pricing semantics.

Pool discipline: ``drain_cost_ledger_before_reconcile`` must run **before** opening
any main-pool session. ``reconcile_turn_cost_ledger`` only materializes on the
caller's session and requires a ``CostLedgerDrained`` proof so drain cannot be
forgotten (and cannot run while a main-pool connection is held).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.logging import get_logger
from agentcore.db.repositories.billing import CostEventRepository

logger = get_logger(__name__)

_DRAIN_PROOF = object()


class CostLedgerDrained:
    """Proof that outbox drain already ran outside any main-pool session.

    Construct only via ``drain_cost_ledger_before_reconcile`` — required by
    ``reconcile_turn_cost_ledger`` so call sites cannot silently skip drain.
    """

    __slots__ = ()

    def __init__(self, _proof: object = None) -> None:
        if _proof is not _DRAIN_PROOF:
            raise TypeError(
                "Call drain_cost_ledger_before_reconcile() before opening a "
                "main-pool session; do not construct CostLedgerDrained directly."
            )


async def drain_cost_ledger_before_reconcile(
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> CostLedgerDrained:
    """Drain shared ``cost_ledger_outbox`` before materialize (main-pool free).

    Always drains pending rows — not gated on ``queue.running`` — so finalize
    sees cross-worker outbox rows even if this process has not started its
    background loop (tests / edge). Best-effort: failures are logged and the
    proof is still returned so materialize can proceed.
    """
    try:
        from agentcore.billing.cost_ledger_queue import get_cost_ledger_queue

        await get_cost_ledger_queue().drain_once()
    except Exception:  # noqa: BLE001 — drain best-effort; materialize still runs
        logger.warning(
            "cost.ledger_drain_before_reconcile_failed",
            conversation_id=conversation_id,
            message_id=message_id,
            exc_info=True,
        )
    return CostLedgerDrained(_DRAIN_PROOF)


async def reconcile_turn_cost_ledger(
    session: AsyncSession,
    *,
    drained: CostLedgerDrained,
    user_id: str,
    conversation_id: str,
    message_id: str | None,
    cost_runs: list[dict[str, Any]],
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Persist the turn's full ledger and return the authoritative per-run rows.

    ``drained`` must come from ``drain_cost_ledger_before_reconcile`` called
    **before** this session was opened (drain uses the telemetry pool).

    Steps (after drain):
    1. Upsert ``cost_events`` from all ``cost_calls`` for ``message_id``.
    2. ``record_runs`` any ``cost_runs`` whose ``run_id`` has no call details yet
       (vision / drain race) — DO NOTHING so metered runs stay call-authoritative.
    3. Re-read ``cost_events`` for the message as the return value (payroll shape).
    """
    if not isinstance(drained, CostLedgerDrained):
        raise TypeError(
            "drained must be CostLedgerDrained from drain_cost_ledger_before_reconcile()"
        )
    if not cost_runs and not message_id:
        return []

    repo = CostEventRepository(session)
    call_run_ids: set[str] = set()
    if message_id:
        call_run_ids = await repo.materialize_message_runs(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
        )

    orphans = [
        row
        for row in cost_runs
        if isinstance(row, dict) and row.get("run_id") and str(row["run_id"]) not in call_run_ids
    ]
    if orphans:
        await repo.record_runs(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            runs=orphans,
            trace_id=trace_id,
        )

    if not message_id:
        return list(cost_runs)

    events = await repo.list_for_message(message_id, user_id=user_id)
    return [_event_as_run_dict(e) for e in events]


def _event_as_run_dict(event: Any) -> dict[str, Any]:
    """Map a ``CostEvent`` ORM row to the ``asdict(RunCost)``-like shape for logs."""
    return {
        "run_id": event.run_id,
        "parent_run_id": event.parent_run_id,
        "agent_id": event.agent_id,
        "role": event.role,
        "persona": event.persona,
        "model": event.model,
        "tokens": dict(event.tokens or {}),
        "cost": dict(event.cost or {}),
        "cost_total_nano": int(event.cost_total_nano or 0),
        "cost_estimated_nano": int(getattr(event, "cost_estimated_nano", 0) or 0),
        "currency": event.currency or "CNY",
        "rounds": int(event.rounds or 0),
        "duration_ms": int(event.duration_ms or 0),
    }
