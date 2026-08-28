"""Startup + periodic sweeper for expired RUNNING turn leases.

When a process dies mid-turn, its lease heartbeat stops. This loop claims expired
leases and routes each through :func:`agentcore.runtime.recover.recover_turn` so
unfinished DAG nodes redrive from the journal projection (completed nodes skipped).

Paused turns are owned by ``paused_turns`` (not leases) — a lease that coexists with
a paused frame is released without redrive. Terminal journals (``turn_end``) likewise
drop the stale lease.

No-DAG mid-flight turns (pure chat crash) are salvaged via
:func:`agentcore.runtime.turn.interrupt.close_turn_interrupted` instead of being skipped.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.errors import is_schema_error
from agentcore.db.repositories import PausedTurnRepository, TurnJournalRepository
from agentcore.runtime.journal.entries import KIND_TURN_END
from agentcore.runtime.leases.repo import TurnLeaseRepository
from agentcore.runtime.leases.service import (
    is_local_turn_lease,
    lease_owner_id,
    orphan_turn_lease,
    release_turn_lease,
)
from agentcore.runtime.turn.interrupt import TurnInterruptReason, close_turn_interrupted
from agentcore.runtime.turn.state import TurnState

logger = get_logger(__name__)

# Strong refs for detached recover tasks. ``asyncio.create_task`` alone is only a
# weak ref in the loop — GC can cancel mid-flight after ``crash_delegate_ready``
# and before ``crash_redrive`` (ready-only loop). Also dedupe concurrent recovers.
_recover_tasks: set[asyncio.Task[None]] = set()
_recovering_message_ids: set[str] = set()


def _journal_has_turn_end(entries: list[dict]) -> bool:
    return any((e.get("kind") or "") == KIND_TURN_END for e in entries)


async def _bump_recover_attempts(message_id: str, *, owner_id: str) -> int:
    """Increment ``meta.recover_attempts`` on a just-claimed lease; return new count."""
    async with async_session_factory() as session:
        return await TurnLeaseRepository(session).bump_recover_attempts(
            message_id, owner_id=owner_id
        )


async def salvage_interrupted_turn(
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None = None,
    reason: str = TurnInterruptReason.LEASE_EXPIRED.value,
) -> bool:
    """Mark a crashed turn incomplete + append ``turn_end`` interrupted.

    The default reason says what the sweeper actually knows — a lease stopped
    beating — not「process_kill」, which claims an observation nobody made and
    which case 519270db proved will misdirect the next person who reads the row.

    Works for pure-chat and unfinished-DAG turns: message status is updated from
    stream_state, and ``turn_end`` is appended at the next journal seq (never
    rewritten from seq=0, which would no-op against an existing DAG prefix).

    Returns ``True`` when the close write succeeded (or was already terminal).
    """
    ok = await close_turn_interrupted(
        message_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        reason=reason,
        load_stream_state=True,
    )
    if ok:
        logger.info(
            "turn_lease.sweep_salvage_interrupted",
            message_id=message_id,
            conversation_id=conversation_id,
            reason=reason,
        )
    return ok


async def salvage_no_dag_turn(
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None = None,
) -> bool:
    """Close a crashed no-DAG turn (incomplete + turn_end interrupted)."""
    return await salvage_interrupted_turn(
        message_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        reason=TurnInterruptReason.LEASE_EXPIRED.value,
    )


async def run_turn_lease_sweep() -> int:
    """Claim expired leases and kick recover; return number of recoveries started."""
    if not settings.turn_lease_enabled:
        return 0
    before = datetime.now(UTC) - timedelta(seconds=settings.turn_lease_ttl_seconds)
    limit = settings.turn_lease_sweep_batch_limit
    owner = lease_owner_id()
    started = 0

    async with async_session_factory() as session:
        repo = TurnLeaseRepository(session)
        expired = list(await repo.list_expired(before=before, limit=limit))

    for row in expired:
        if is_local_turn_lease(row):
            # Desktop owns salvage / settle. Cloud redrive would run against the
            # server workspace; interrupting the message races a still-live sidecar.
            async with async_session_factory() as session:
                await TurnLeaseRepository(session).release(row.message_id)
            logger.info(
                "turn_lease.sweep_skip_local",
                message_id=row.message_id,
                conversation_id=row.conversation_id,
            )
            continue
        async with async_session_factory() as session:
            claimed = await TurnLeaseRepository(session).claim_expired(
                row.message_id,
                new_owner_id=owner,
                before=before,
                phase="recovering",
            )
        if claimed is None:
            continue

        # Paused frame owns continuation — drop stale RUNNING lease.
        async with async_session_factory() as session:
            paused = await PausedTurnRepository(session).get(claimed.message_id)
            if paused is not None:
                await TurnLeaseRepository(session).release(claimed.message_id)
                logger.info(
                    "turn_lease.sweep_skip_paused",
                    message_id=claimed.message_id,
                )
                continue

            entries = await TurnJournalRepository(session).load_owned(
                claimed.message_id, claimed.conversation_id
            )

        if entries and _journal_has_turn_end(entries):
            async with async_session_factory() as session:
                await TurnLeaseRepository(session).release(claimed.message_id)
            logger.info(
                "turn_lease.sweep_skip_terminal",
                message_id=claimed.message_id,
                entries=len(entries),
            )
            continue

        state = TurnState.from_journal(entries or [])
        if state.plan is not None and state.unfinished_run_ids:
            mid = claimed.message_id
            if mid in _recovering_message_ids:
                logger.info(
                    "turn_lease.sweep_skip_inflight",
                    message_id=mid,
                )
                continue
            attempts = await _bump_recover_attempts(mid, owner_id=owner)
            meta = dict(claimed.meta) if isinstance(getattr(claimed, "meta", None), dict) else {}
            meta["recover_attempts"] = attempts
            claimed.meta = meta
            logger.info(
                "turn_lease.sweep_recover",
                message_id=mid,
                conversation_id=claimed.conversation_id,
                unfinished=len(state.unfinished_run_ids),
                completed=len(state.completed),
                recover_attempts=attempts,
            )
            # Detached recover — keep a strong ref so GC cannot cancel after ready.
            from agentcore.runtime.recover import recover_expired_lease

            _recovering_message_ids.add(mid)

            async def _run_recover(
                lease=claimed,
                turn_state=state,
                message_id=mid,
            ) -> None:
                try:
                    await recover_expired_lease(lease, turn_state)
                finally:
                    _recovering_message_ids.discard(message_id)

            task = asyncio.create_task(
                _run_recover(),
                name=f"recover-lease-{mid}",
            )
            _recover_tasks.add(task)
            task.add_done_callback(_recover_tasks.discard)
            started += 1
            continue

        # No unfinished DAG (or empty journal pure-chat) — salvage from stream_state.
        # Success → release lease. Failure → re-orphan so the next sweep can retry
        # (never delete the row after a failed salvage — that leaves a fake pause).
        ok = False
        try:
            ok = await salvage_no_dag_turn(
                message_id=claimed.message_id,
                conversation_id=claimed.conversation_id,
            )
        except Exception as e:  # noqa: BLE001 — never stall the sweeper
            logger.warning(
                "turn_lease.sweep_salvage_failed",
                message_id=claimed.message_id,
                error=str(e),
            )
            ok = False
        if ok:
            await release_turn_lease(claimed.message_id)
        else:
            logger.warning(
                "turn_lease.sweep_salvage_failed",
                message_id=claimed.message_id,
                error="salvage_returned_false",
            )
            with contextlib.suppress(Exception):
                await orphan_turn_lease(claimed.message_id)

    if started:
        logger.info("turn_lease.sweep_started", recoveries=started)
    return started


async def turn_lease_sweep_loop() -> None:
    """Run :func:`run_turn_lease_sweep` forever on the configured interval."""
    # Boot pass first so a restart immediately reclaims orphaned RUNNING turns.
    try:
        await run_turn_lease_sweep()
    except Exception as e:  # noqa: BLE001
        log = logger.error if is_schema_error(e) else logger.warning
        log("turn_lease.boot_sweep_failed", error=str(e))

    interval = settings.turn_lease_sweep_interval_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            await run_turn_lease_sweep()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log = logger.error if is_schema_error(e) else logger.warning
            log("turn_lease.sweep_failed", error=str(e))
