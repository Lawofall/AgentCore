"""Execution harvester: detached drive → journal terminal → system closing turn (pillar C).

When a background drive reaches a terminal state with no attached chat turn, the
harvester:
1. Emits ``execution_completed`` into the host turn journal (DURABLE).
2. Spawns a system-initiated closing turn that adopts the live coordination session
   so CEO consumes the queued ``ALL_COMPLETED`` and synthesizes a final deliverable.
3. Notifies the user (best-effort push).

If a concurrent user turn has already re-attached (``turn_attached=True``), the
harvester no-ops — that turn's CEO will consume ``ALL_COMPLETED``. Same-turn
``wait`` inject alone does **not** skip harvest (inject ≠ user-visible close);
skip is decided at harvest-arm time when attached inject already streamed a
visible close.

If the conversation slot is busy, the harvester **defers** (keeps the registry)
and retries — never treats deferral as success then ``_close_detached_session``.
"""

from __future__ import annotations

import asyncio
import contextlib

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    _close_detached_session,
)
from agentcore.runtime.turn.runs import turn_runs

logger = get_logger(__name__)

# Slot-busy / transient failure: keep registration and retry (pillar A).
_HARVEST_RETRY_DELAY_S = 1.0
_HARVEST_MAX_ATTEMPTS = 60


async def harvest_detached_execution(session: CoordinationSession) -> None:
    """Complete journal terminal facts and launch the system closing turn."""
    logger.info(
        "coordination.harvest_detached_started",
        execution_id=session.execution_id,
        conversation_id=session.conversation_id or "",
        turn_attached=session.turn_attached,
    )
    if getattr(session, "host_turn_paused", False):
        logger.info(
            "coordination.harvest_skipped_host_paused",
            execution_id=session.execution_id,
            conversation_id=session.conversation_id or "",
        )
        _close_detached_session(session)
        return
    if session.turn_attached:
        # Do not leave a false ``harvest_scheduled`` / ``settled_via=harvest`` that
        # blocks release_prefers_harvest — re-arm must remain possible.
        logger.info(
            "coordination.harvest_skipped_reattached",
            execution_id=session.execution_id,
        )
        session.harvest_scheduled = False
        if session.settled_via == "harvest":
            session.settled_via = None
        return
    if session.user_stopped:
        _close_detached_session(session)
        return

    # ``execution_completed`` already emitted in ``finish_detached_coordination``
    # (while the arming turn sink may still be open). Flush host journal so fold
    # sees terminal facts before the closing turn.
    writer = session.host_journal_writer
    if writer is not None:
        with contextlib.suppress(Exception):
            await writer.flush()

    conversation_id = (session.conversation_id or "").strip()
    if not conversation_id:
        logger.warning(
            "coordination.harvest_missing_conversation",
            execution_id=session.execution_id,
        )
        _close_detached_session(session)
        return

    from agentcore.conversation.execution_harvest import (
        HarvestDeferredError,
        HarvestNotReadyError,
        run_harvest_closing_turn,
    )
    from agentcore.runtime.coordination.session import _sessions

    for attempt in range(_HARVEST_MAX_ATTEMPTS):
        if session.user_stopped:
            return
        if session.turn_attached:
            logger.info(
                "coordination.harvest_skipped_reattached",
                execution_id=session.execution_id,
            )
            session.harvest_scheduled = False
            if session.settled_via == "harvest":
                session.settled_via = None
            return
        if _sessions.get(session.execution_id) is not session:
            return

        try:
            await run_harvest_closing_turn(
                conversation_id=conversation_id,
                execution_id=session.execution_id,
            )
        except HarvestDeferredError:
            logger.info(
                "coordination.harvest_deferred_retry",
                execution_id=session.execution_id,
                conversation_id=conversation_id,
                attempt=attempt,
            )
            await _wait_slot_or_backoff(conversation_id)
            continue
        except HarvestNotReadyError as e:
            logger.error(
                "coordination.harvest_not_ready",
                execution_id=session.execution_id,
                conversation_id=conversation_id,
                reason=e.reason,
            )
            return
        except Exception:  # noqa: BLE001 — retry; do not silently unregister
            logger.exception(
                "coordination.harvest_closing_turn_failed",
                execution_id=session.execution_id,
                conversation_id=conversation_id,
                attempt=attempt,
            )
            if attempt + 1 >= _HARVEST_MAX_ATTEMPTS:
                logger.error(
                    "coordination.harvest_giving_up",
                    execution_id=session.execution_id,
                    conversation_id=conversation_id,
                    attempts=attempt + 1,
                )
                # Keep registry so the session remains observable / re-adoptable.
                return
            await asyncio.sleep(_HARVEST_RETRY_DELAY_S)
            continue

        # Closing turn finished (or no-op skip). Clear only if still unattached.
        if _sessions.get(session.execution_id) is session and not session.turn_attached:
            _close_detached_session(session)
        return

    logger.error(
        "coordination.harvest_deferred_exhausted",
        execution_id=session.execution_id,
        conversation_id=conversation_id,
        attempts=_HARVEST_MAX_ATTEMPTS,
    )
    # Keep registration — never treat deferred exhaustion as a clean close.


async def _wait_slot_or_backoff(conversation_id: str) -> None:
    """Await the occupying turn when present; otherwise brief backoff."""
    existing = turn_runs.get(conversation_id)
    if existing is not None and not existing.task.done():
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(existing.task), timeout=30.0)
        return
    from agentcore.sidecar.server_pkg.core import get_active_sidecar

    sidecar = get_active_sidecar()
    if sidecar is not None:
        live = sidecar.live_turn_task(conversation_id)
        if live is not None and not live.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(live), timeout=30.0)
            return
    await asyncio.sleep(_HARVEST_RETRY_DELAY_S)


def emit_execution_completed(session: CoordinationSession) -> None:
    """Push ``execution_completed`` (live sink if open, else host journal).

    Called from :func:`finish_detached_coordination` *before* the async harvest
    task so ``await_live_detached_drive`` owners can still deliver the frame
    before closing the turn sink / sealing outbox READY.

    Invariant: execution terminal ⇒ every non-skipped plan run is terminal.
    Unsettled workers get ``run_cancelled`` first; ``status`` mirrors
    ``harvest_closing_kind`` (success→completed / failure→failed /
    cancelled→cancelled) so cancel/harvest never folds as 「团队完成」.
    """
    from agentcore.conversation.execution_harvest import harvest_closing_kind
    from agentcore.runtime.events import execution_completed

    _cancel_unsettled_plan_workers(session)

    kind = harvest_closing_kind(session)
    status = {"success": "completed", "failure": "failed", "cancelled": "cancelled"}[
        kind
    ]
    event = execution_completed(
        execution_id=session.execution_id,
        conversation_id=session.conversation_id or "",
        completed=len(session.completed_run_ids),
        total=session.total_workers,
        status=status,
        host_turn_id=session.host_turn_id or None,
    )
    _emit_coordination_durable(session, event)
    logger.info(
        "coordination.execution_completed_emitted",
        execution_id=session.execution_id,
        completed=len(session.completed_run_ids),
        total=session.total_workers,
        status=status,
    )


def _cancel_unsettled_plan_workers(session: CoordinationSession) -> list[str]:
    """Emit ``run_cancelled`` for plan nodes that are not yet terminal.

    Skipped nodes already sit in ``completed_run_ids`` (via wave materialisation).
    Remaining pending/running nodes are cancelled so fold never freezes them
    under a completed execution. Returns cancelled run_ids (stable plan order).
    """
    from agentcore.runtime.events import run_cancelled

    candidates: list[tuple[str, str]] = []
    live = session.live_plan
    nodes = getattr(live, "nodes", None) if live is not None else None
    if nodes:
        for node in nodes:
            rid = (getattr(node, "run_id", None) or "").strip()
            if not rid or rid in session.completed_run_ids:
                continue
            # Phase-1: agent_id == run_id on wire.
            candidates.append((rid, rid))
    else:
        for rid, _role in session.running_workers():
            if rid in session.completed_run_ids:
                continue
            candidates.append((rid, rid))

    cancelled: list[str] = []
    for run_id, agent_id in candidates:
        session.request_cancel(run_id)
        session.mark_worker_completed(run_id)
        event = run_cancelled(
            run_id,
            agent_id,
            reason="stop",
            execution_id=session.execution_id,
        )
        _emit_coordination_durable(session, event)
        cancelled.append(run_id)
    if cancelled:
        logger.info(
            "coordination.unsettled_runs_cancelled",
            execution_id=session.execution_id,
            run_ids=cancelled,
        )
    return cancelled


def _emit_coordination_durable(session: CoordinationSession, event: object) -> None:
    """Emit via live sink (open or closed DURABLE path) else host journal."""
    from agentcore.runtime.events.types import SSEEvent

    if not isinstance(event, SSEEvent):
        return
    sink = session.event_sink
    if sink is not None:
        with contextlib.suppress(Exception):
            sink.emit(event)
            return
    writer = session.host_journal_writer
    if writer is not None:
        writable = getattr(writer, "writable", None)
        if callable(writable):
            writer = writable()
        log = getattr(session, "host_fact_log", None)
        if log is not None:
            from agentcore.runtime.facts import Fact

            with contextlib.suppress(Exception):
                log.record_fact(
                    Fact(
                        kind=event.type.value,
                        payload=event.payload,
                        ts=event.timestamp,
                    )
                )
        with contextlib.suppress(Exception):
            writer.schedule_append(
                {
                    "kind": event.type.value,
                    "payload": event.payload,
                    "ts": event.timestamp,
                }
            )


# Back-compat alias (tests / older call sites).
_emit_execution_completed = emit_execution_completed
