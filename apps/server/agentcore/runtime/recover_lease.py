"""Crash redrive: expired lease → original turn identity → seed WaveScheduler.

The sweeper claims an expired :class:`~agentcore.db.models.runs.TurnLeaseRow`,
projects the journal into a :class:`~agentcore.runtime.turn.state.TurnState`, and
hands both here. No user is present: this path rebinds the ORIGINAL turn's journal
identity, redrives the unfinished DAG through
:func:`agentcore.runtime.recover.recover_turn`, and degrades to an honest
``interrupted`` terminal when the redrive cannot run.

User-driven resume (ask_user / approval / escalation) lives in
:mod:`agentcore.runtime.recover`.

Backlog (not this iteration):
- Cross-process Redis lease backend (Postgres this iteration).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.runtime.events import EventSink

if TYPE_CHECKING:
    from agentcore.db.models.runs import TurnLeaseRow
    from agentcore.runtime.turn.state import TurnState

logger = get_logger(__name__)


def bind_recovered_turn(execution_id: str, sink: EventSink) -> None:
    """Tell the armed session which turn it continues (崩溃重驱归属原回合).

    Must stay synchronous right after ``resume_plan`` arms the scheduler: the drive
    task cannot interleave before the next await, so the flag is guaranteed to be set
    before ``finish_detached_coordination`` can read it.
    """
    from agentcore.runtime.graph.session.session import active_coordination

    turn_id = (sink.message_id or "").strip()
    if not turn_id:
        return
    session = active_coordination(execution_id)
    if session is None:
        return
    session.recovered_turn_id = turn_id
    logger.info(
        "recover.redrive_bound_turn",
        execution_id=execution_id,
        turn_id=turn_id,
    )


async def _await_crash_redrive_drive(execution_id: str) -> None:
    """When crash redrive arms wall coordination, wait for the background drive.

    ``resume_plan(coordinate=True)`` returns as soon as the scheduler is armed; the
    sweeper must keep the recovering lease (+ heartbeat) until workers settle, else
    the lease is released mid-flight and the next sweep reclaims a still-open DAG.

    Called **outside** ``turn_lease_recover_timeout_seconds`` (that budget only
    covers orphan + factory + arm).
    """
    from agentcore.runtime.graph.session.session import active_coordination

    session = active_coordination(execution_id)
    task = getattr(session, "drive_task", None) if session is not None else None
    if task is None or task.done():
        return
    await task


async def _stamp_turn_recovered(
    *,
    message_id: str,
    conversation_id: str,
    trace_id: str | None,
) -> None:
    """Mark the crashed assistant row 「曾中断恢复」 before the redrive re-opens it.

    Written at interrupt-detection time (not at closing) so the badge is honest no
    matter how the recovery ends — a later ``upsert_assistant`` merge carries the key
    forward. ``content=""`` keeps the monotonic merge from touching the streamed body.
    """
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import MessageRepository

    try:
        async with async_session_factory() as db:
            await MessageRepository(db).upsert_assistant(
                conversation_id=conversation_id,
                message_id=message_id,
                content="",
                trace_id=trace_id,
                metadata={"recovered": True},
                merge=True,
            )
    except Exception as e:  # noqa: BLE001 — badge write must not abort the redrive
        logger.warning(
            "recover.recovered_badge_write_failed",
            message_id=message_id,
            conversation_id=conversation_id,
            error=str(e),
        )
        return
    logger.info(
        "recover.recovered_badge_stamped",
        message_id=message_id,
        conversation_id=conversation_id,
    )


async def recover_expired_lease(lease: TurnLeaseRow, state: TurnState) -> None:
    """Background entry for the sweeper: orphan hot pending, then redrive unfinished DAG.

    When crash redrive is unavailable (unwired factory / hard failure), degrade to an
    honest ``interrupted`` terminal via lease salvage — never leave a fake pause.

    Lease is released only after a successful recover or salvage. Salvage failure
    re-orphans the row so the next sweep can retry (never delete without ``turn_end``).

    ``turn_lease_recover_timeout_seconds`` only bounds orphan + factory +
    ``recover_turn`` (to arm). After arm, heartbeat stays up while awaiting drive;
    ``turn_lease_recover_max_attempts`` still caps ready cycles — no ready-only loop.

    Crash redrive is a turn entry point like ``run`` / ``resume``: it binds the
    ORIGINAL turn's journal identity (sink ids + ``TurnJournalWriter`` +
    ``TurnFactLog``) before arming, so every fact the recovered DAG emits appends to
    that turn's journal — the 唯一事实源 a reconnecting client replays.
    """
    from agentcore.config import settings
    from agentcore.core.types import new_id
    from agentcore.runtime.graph.session.session import cancel_coordination_on_user_stop
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.interaction_orphan import orphan_turn_before_recover
    from agentcore.runtime.journal.writer import (
        TurnJournalWriter,
        current_journal_writer,
    )
    from agentcore.runtime.leases.service import (
        heartbeat_turn_lease,
        lease_heartbeat_loop,
        lease_owner_id,
        orphan_turn_lease,
        release_turn_lease,
    )
    from agentcore.runtime.leases.sweeper import salvage_interrupted_turn
    from agentcore.runtime.turn.interrupt import TurnInterruptReason

    message_id = lease.message_id
    conversation_id = lease.conversation_id
    meta = getattr(lease, "meta", None)
    meta_dict = dict(meta) if isinstance(meta, dict) else {}
    trace_id = getattr(lease, "trace_id", None)
    if trace_id is None:
        trace_id = meta_dict.get("trace_id")
    attempts = int(meta_dict.get("recover_attempts") or 0)
    should_release = False
    lease_stop: asyncio.Event | None = None
    heartbeat_task: asyncio.Task | None = None
    sink: EventSink | None = None
    # Prefer journal execution_id so timeout/cancel can stop leftover coordination.
    eid: str | None = state.execution_id

    # Bound BEFORE ``wait_for`` arms the redrive: the arm task and every worker task
    # it spawns copy this context, so their facts (and the coordination session's
    # host writer) resolve to this turn instead of falling through to a no-op.
    journal_writer = TurnJournalWriter(
        turn_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_id if isinstance(trace_id, str) else None,
        initial_seq=len(state.entries),
    )
    journal_writer_token = current_journal_writer.set(journal_writer)
    fact_log_token = current_fact_log.set(
        TurnFactLog(inherited_entries=list(state.entries))
    )

    def _stop_background() -> None:
        """Hard-stop workers + drive before salvage (not ask_user soft_stop)."""
        stop_eid = (eid or "").strip()
        if not stop_eid:
            return
        with contextlib.suppress(Exception):
            cancel_coordination_on_user_stop(execution_id=stop_eid)

    async def _salvage(*, reason: str, event: str) -> bool:
        logger.warning(
            event,
            message_id=message_id,
            conversation_id=conversation_id,
            attempts=attempts,
        )
        return await salvage_interrupted_turn(
            message_id=message_id,
            conversation_id=conversation_id,
            trace_id=trace_id if isinstance(trace_id, str) else None,
            reason=reason,
        )

    try:
        # Cap ready loops: another claim after a hung/cancelled recover must not spin.
        max_attempts = max(1, int(settings.turn_lease_recover_max_attempts))
        if attempts > max_attempts:
            should_release = await _salvage(
                reason=TurnInterruptReason.REDRIVE_FAILED.value,
                event="recover.lease_stalled",
            )
            return

        # Heartbeat covers rebuild/arm and the post-arm await-drive window.
        owner = lease_owner_id()
        await heartbeat_turn_lease(message_id, owner_id=owner, phase="recovering")
        lease_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            lease_heartbeat_loop(
                message_id,
                owner_id=owner,
                interval_seconds=settings.turn_lease_heartbeat_seconds,
                stop=lease_stop,
                phase="recovering",
            ),
            name=f"recover-lease-hb-{message_id}",
        )

        await _stamp_turn_recovered(
            message_id=message_id,
            conversation_id=conversation_id,
            trace_id=trace_id if isinstance(trace_id, str) else None,
        )

        timeout = float(settings.turn_lease_recover_timeout_seconds)

        async def _arm_redrive() -> str | None:
            """Orphan + factory + recover_turn to arm. Returns eid, or None if salvaged."""
            nonlocal should_release, eid, sink
            # D6：先 orphan 热路 pending，再 recover 重驱
            await orphan_turn_before_recover(
                turn_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
            sink = EventSink(
                conversation_id=conversation_id,
                message_id=message_id,
            )
            from agentcore.runtime.recover_hooks import build_crash_delegate_tool

            delegate_tool = await build_crash_delegate_tool(lease, state, sink=sink)
            if delegate_tool is None:
                logger.warning(
                    "recover.lease_no_delegate",
                    message_id=message_id,
                )
                should_release = await salvage_interrupted_turn(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id if isinstance(trace_id, str) else None,
                    reason=TurnInterruptReason.REDRIVE_FAILED.value,
                )
                return None
            armed_eid = state.execution_id or new_id()
            eid = armed_eid
            # Imported here so this module stays import-independent of the resume
            # side: ``recover`` re-exports us, so a module-level edge would cycle.
            from agentcore.runtime.recover import recover_turn

            await recover_turn(
                state=state,
                sink=sink,
                delegate_tool=delegate_tool,
                execution_id=armed_eid,
            )
            return armed_eid

        try:
            # Timeout only covers rebuild/arm — drive wait is outside this budget.
            armed_eid = await asyncio.wait_for(_arm_redrive(), timeout=timeout)
            if armed_eid is not None:
                await _await_crash_redrive_drive(armed_eid)
                logger.info("recover.lease_done", message_id=message_id)
                should_release = True
        except TimeoutError:
            _stop_background()
            should_release = await _salvage(
                reason=TurnInterruptReason.REDRIVE_FAILED.value,
                event="recover.lease_timeout",
            )
    except asyncio.CancelledError:
        # Strong-ref gap / process teardown used to cancel after ready with no salvage.
        logger.error(
            "recover.lease_cancelled",
            message_id=message_id,
            attempts=attempts,
        )
        _stop_background()
        with contextlib.suppress(Exception):
            should_release = await salvage_interrupted_turn(
                message_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id if isinstance(trace_id, str) else None,
                reason=TurnInterruptReason.REDRIVE_FAILED.value,
            )
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(
            "recover.lease_failed",
            message_id=message_id,
            error=str(e),
            exc_info=True,
        )
        _stop_background()
        with contextlib.suppress(Exception):
            should_release = await salvage_interrupted_turn(
                message_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id if isinstance(trace_id, str) else None,
                reason=TurnInterruptReason.REDRIVE_FAILED.value,
            )
    finally:
        if lease_stop is not None:
            lease_stop.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        if sink is not None:
            sink.close(reason="crash_redrive_settled")
        current_fact_log.reset(fact_log_token)
        current_journal_writer.reset(journal_writer_token)
        # Drain append-on-emit like the run / resume pipelines do at teardown: an
        # abandoned in-flight write leaves a checked-out DB connection behind. Must
        # not swallow the lease release below, cancellation included.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await journal_writer.flush()
        if should_release:
            await release_turn_lease(message_id)
            from agentcore.runtime.turn.durable import note_conversation_slot_free

            note_conversation_slot_free(conversation_id)
        else:
            with contextlib.suppress(Exception):
                await orphan_turn_lease(message_id)
        # Recover was this drive's attached owner; hand ownership back only after the
        # lease is gone, so the closing turn re-acquires it instead of racing our
        # release. ``finish_detached_coordination``'s attach grace wakes on this.
        if eid:
            from agentcore.runtime.graph.session.session import active_coordination

            armed = active_coordination(eid)
            if armed is not None:
                armed.turn_attached = False
