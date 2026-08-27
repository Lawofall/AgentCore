"""Detached-drive settlement: journal terminal + notify. No second CEO turn.

When a background drive reaches a terminal state, this module:
1. Emits ``execution_completed`` into the host turn journal (DURABLE).
2. Pushes a best-effort notification if the user is not in a live occupant turn
   and the captain bubble did not already stream a visible close.
3. Unregisters the coordination session.

Same-turn CEO already writes the ending in the current reply after ``wait``
injects ``ALL_COMPLETED``. Background teams that finish after the captain
``end_turn`` only notify — they do not mint a synthetic user row or spawn a
closing LLM turn.

If a concurrent user turn has already re-attached (``turn_attached=True``),
settlement waits for detach (attach grace) rather than emitting a false
complete under a still-writing captain.
"""

from __future__ import annotations

import contextlib
from typing import Literal

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    _close_detached_session,
)
from agentcore.runtime.coordination.session_types import CoordinationEventKind

logger = get_logger(__name__)

ExecutionTerminalKind = Literal["success", "failure", "cancelled"]

_SETTLE_PUSH: dict[ExecutionTerminalKind, tuple[str, str]] = {
    "success": ("团队好了", "队员已收工，打开对话看协作图。"),
    "failure": ("团队有失败", "有队员未完成，打开对话看协作图。"),
    "cancelled": ("团队已停止", "打开对话看协作图。"),
}


def execution_terminal_kind(session: CoordinationSession) -> ExecutionTerminalKind:
    """Classify team terminal for ``execution_completed.status`` and push copy."""
    if session.soft_stop or session.user_stopped:
        return "cancelled"
    if getattr(session, "drive_cancelled", False):
        return "cancelled"
    pending = list(getattr(session, "_pending", []) or [])
    if any(ev.kind is CoordinationEventKind.DRIVE_CANCELLED for ev in pending):
        return "cancelled"
    if session.failed_run_ids:
        return "failure"
    cancelled = (session.cancel_ids & session.completed_run_ids) - session.failed_run_ids
    if cancelled:
        return "cancelled"
    return "success"


async def settle_detached_execution(session: CoordinationSession) -> None:
    """Emit already happened in ``_arm_settle_now``; notify (if away) and close."""
    logger.info(
        "coordination.settle_detached_started",
        execution_id=session.execution_id,
        conversation_id=session.conversation_id or "",
        turn_attached=session.turn_attached,
    )
    if getattr(session, "host_turn_paused", False):
        logger.info(
            "coordination.settle_skipped_host_paused",
            execution_id=session.execution_id,
            conversation_id=session.conversation_id or "",
        )
        _close_detached_session(session)
        return
    if session.turn_attached:
        logger.info(
            "coordination.settle_skipped_reattached",
            execution_id=session.execution_id,
        )
        session.harvest_scheduled = False
        if session.settled_via == "detached":
            session.settled_via = None
        return
    if session.user_stopped:
        _close_detached_session(session)
        return
    from agentcore.runtime.interaction_orphan import holds_for_hot_user

    if holds_for_hot_user(session):
        logger.info(
            "coordination.settle_held_hot_pending",
            execution_id=session.execution_id,
            conversation_id=session.conversation_id or "",
            stage="detached_execution",
        )
        session.harvest_scheduled = False
        if session.settled_via == "detached":
            session.settled_via = None
        return

    writer = session.host_journal_writer
    if writer is not None:
        with contextlib.suppress(Exception):
            await writer.flush()

    conversation_id = (session.conversation_id or "").strip()
    if not conversation_id:
        logger.warning(
            "coordination.settle_missing_conversation",
            execution_id=session.execution_id,
        )
        _close_detached_session(session)
        return

    from agentcore.runtime.coordination.session import (
        _conversation_slot_has_live_occupant,
        _sessions,
        attached_inject_closed_visibly,
    )

    if _sessions.get(session.execution_id) is not session:
        return

    visible = attached_inject_closed_visibly(session)
    occupant = _conversation_slot_has_live_occupant(conversation_id)
    if not visible and not occupant:
        await _notify_team_settled(session)

    if _sessions.get(session.execution_id) is session and not session.turn_attached:
        _close_detached_session(session)


async def _notify_team_settled(session: CoordinationSession) -> None:
    conversation_id = (session.conversation_id or "").strip()
    if not conversation_id:
        return
    kind = execution_terminal_kind(session)
    title, body = _SETTLE_PUSH[kind]
    user_id = await _conversation_user_id(conversation_id)
    if not user_id:
        logger.warning(
            "coordination.settle_notify_skipped",
            execution_id=session.execution_id,
            conversation_id=conversation_id,
            reason="user_missing",
        )
        return
    from agentcore.push import PushNotification, notify_user

    with contextlib.suppress(Exception):
        await notify_user(
            user_id,
            PushNotification(
                title=title,
                body=body,
                data={
                    "conversation_id": conversation_id,
                    "execution_id": session.execution_id,
                    "origin": "team_settled",
                    "kind": kind,
                },
            ),
        )
    logger.info(
        "coordination.settle_notified",
        execution_id=session.execution_id,
        conversation_id=conversation_id,
        kind=kind,
    )


async def _conversation_user_id(conversation_id: str) -> str:
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import ConversationRepository

    try:
        async with async_session_factory() as db:
            conv = await ConversationRepository(db).get_by_id_unscoped(conversation_id)
    except Exception:  # noqa: BLE001 — notify is best-effort
        logger.warning(
            "coordination.settle_notify_skipped",
            conversation_id=conversation_id,
            reason="db_error",
        )
        return ""
    if conv is None:
        return ""
    return str(conv.user_id or "").strip()


def emit_execution_completed(session: CoordinationSession) -> None:
    """Push ``execution_completed`` (live sink if open, else host journal).

    Called from :func:`finish_detached_coordination` *before* the async settle
    task so ``await_live_detached_drive`` owners can still deliver the frame
    before closing the turn sink / sealing outbox READY.

    Invariant: execution terminal ⇒ every non-skipped plan run is terminal.
    Unsettled workers get ``run_cancelled`` first; ``status`` mirrors
    ``execution_terminal_kind`` (success→completed / failure→failed /
    cancelled→cancelled) so cancel never folds as 「团队完成」.
    """
    from agentcore.runtime.events import execution_completed
    from agentcore.runtime.interaction_orphan import holds_for_hot_user

    if holds_for_hot_user(session):
        logger.info(
            "coordination.settle_held_hot_pending",
            execution_id=session.execution_id,
            conversation_id=session.conversation_id or "",
            stage="emit_execution_completed",
        )
        return

    _cancel_unsettled_plan_workers(session)

    kind = execution_terminal_kind(session)
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
    from agentcore.runtime.interaction_orphan import holds_for_hot_user

    if holds_for_hot_user(session):
        return []

    candidates: list[tuple[str, str]] = []
    live = session.live_plan
    nodes = getattr(live, "nodes", None) if live is not None else None
    if nodes:
        for node in nodes:
            rid = (getattr(node, "run_id", None) or "").strip()
            if not rid or rid in session.completed_run_ids:
                continue
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
