"""Shared capture skeleton for durable suspension frames.

The two suspending faces (ask_user / plan_review) share one capture
shape: read the CEO transcript, fold the about-to-emit ``*_required`` into the fact-log
snapshot (the §8.3 唯一权威载体), assemble a ``turn_paused`` trailing snapshot of the
resumable turn state, then hand the pieces to a kind-specific frame builder + saver.
The display ``journal`` (resume seed) is NOT captured here — it is a DERIVED property
of ``journal_entries`` (P0-B Phase 3), so there is no second, drift-prone copy.
Kind-specific fields and the frame subclass stay at the call site — this module only
owns the common skeleton so the three faces cannot drift.

D11 failure kinds (callers must treat differently):
- **Config unavailable** (no transcript) → returns ``False``; nested / non-prod may proceed.
- **Runtime failure** (saver present but raises) → raises :class:`SuspensionPersistError`;
  all three kinds must terminate the turn explicitly (no silent continue).
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.suspension import (
    TurnSuspension,
    release_claimed_pause_tool_calls_if_complete,
)

logger = get_logger(__name__)

# Same-turn parallel SUSPEND faces (two ``ask_user`` in one gather) share one
# lock per message_id so each snapshot keeps prior sibling ``*_required`` cards.
# Weak values: the entry lives exactly as long as a caller holds the lock, so a
# long-running process does not retain one lock per paused message forever.
_PAUSE_LOCKS_GUARD = threading.Lock()
_PAUSE_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _pause_persist_lock(message_id: str) -> asyncio.Lock:
    with _PAUSE_LOCKS_GUARD:
        lock = _PAUSE_LOCKS.get(message_id)
        if lock is None:
            lock = asyncio.Lock()
            _PAUSE_LOCKS[message_id] = lock
        return lock


class SuspensionPersistError(Exception):
    """Saver was wired but failed at runtime (DB / disk) — D11 honest termination."""

    def __init__(self, checkpoint_id: str, cause: BaseException) -> None:
        self.checkpoint_id = checkpoint_id
        self.cause = cause
        super().__init__(f"suspension persist failed for {checkpoint_id}: {cause}")


@dataclass(frozen=True, slots=True)
class SuspensionCapture:
    """Shared capture payload handed to a kind-specific frame builder."""

    transcript: list[Any]
    history: list[dict[str, Any]]
    journal_entries: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    trace_id: str | None
    # Deliverable content snapshotted into ``turn_paused`` — same source the paused
    # message row must persist (G4 暂停收口同源). Empty when assembly was skipped.
    paused_content: str = ""


async def persist_suspension_capture(
    *,
    checkpoint_id: str,
    required_event: Any,
    build_frame: Callable[[SuspensionCapture], TurnSuspension],
    saver: Callable[[TurnSuspension], Awaitable[None]],
    sink: Any | None = None,
    suspension_kind: str,
    turn_paused_extras: dict[str, Any] | None = None,
    message_id: str = "",
) -> bool:
    """Capture transcript + the fact-log snapshot (+ ``turn_paused``), build, and save.

    Returns ``True`` iff a durable frame was actually saved. Returns ``False`` when the
    CEO transcript is absent (config / non-prod — a faithful resume is impossible).
    Raises :class:`SuspensionPersistError` when the saver raises (runtime failure).

    ``turn_paused`` assembly is best-effort: mirror / sink gaps yield empty fields;
    assembly exceptions are logged and the frame still saves without the trailing fact
    so the durable pause path is never blocked by capture-side gaps.
    ``turn_paused_extras`` is optional adjunct data stored on the same fact (e.g. a
    demo-tape frame cursor) — live faces leave it unset.
    ``message_id`` serializes same-turn sibling persists so later snapshots keep
    earlier ``*_required`` cards (``turn_journal.record`` replaces wholesale).
    """
    if message_id:
        async with _pause_persist_lock(message_id):
            return await _persist_suspension_capture_unlocked(
                checkpoint_id=checkpoint_id,
                required_event=required_event,
                build_frame=build_frame,
                saver=saver,
                sink=sink,
                suspension_kind=suspension_kind,
                turn_paused_extras=turn_paused_extras,
                message_id=message_id,
            )
    return await _persist_suspension_capture_unlocked(
        checkpoint_id=checkpoint_id,
        required_event=required_event,
        build_frame=build_frame,
        saver=saver,
        sink=sink,
        suspension_kind=suspension_kind,
        turn_paused_extras=turn_paused_extras,
        message_id="",
    )


async def _persist_suspension_capture_unlocked(
    *,
    checkpoint_id: str,
    required_event: Any,
    build_frame: Callable[[SuspensionCapture], TurnSuspension],
    saver: Callable[[TurnSuspension], Awaitable[None]],
    sink: Any | None,
    suspension_kind: str,
    turn_paused_extras: dict[str, Any] | None,
    message_id: str,
) -> bool:
    from agentcore.core.log_context import get_log_value
    from agentcore.runtime.facts import Fact, record_turn_fact, snapshot_fact_log
    from agentcore.runtime.suspension import captain_transcript, turn_citations, turn_history

    transcript = captain_transcript.get()
    if not transcript:
        logger.info("suspension.no_transcript", checkpoint_id=checkpoint_id)
        return False

    required_entry = {
        "kind": required_event.type.value,
        "payload": required_event.payload,
        "ts": required_event.timestamp,
    }
    # Snapshot WITHOUT this pause's trailing entries — multi-cycle inheritance reads
    # the last prior ``turn_paused`` from here (not from a contextvar).
    base_entries = snapshot_fact_log()
    trailing: list[dict[str, Any]] = [required_entry]
    paused_content = ""
    try:
        from agentcore.runtime.turn.paused_capture import build_turn_paused_fact

        paused_fact = build_turn_paused_fact(
            checkpoint_id=checkpoint_id,
            suspension_kind=suspension_kind,
            required_event=required_event,
            journal_entries_before_trailing=base_entries,
            sink=sink,
            extras=turn_paused_extras,
        )
        trailing.append(paused_fact.to_fact().entry())
        paused_content = paused_fact.content
    except Exception:
        logger.warning(
            "suspension.turn_paused_capture_failed",
            checkpoint_id=checkpoint_id,
            suspension_kind=suspension_kind,
            exc_info=True,
        )

    journal_entries = snapshot_fact_log(trailing=trailing)
    from agentcore.runtime.memory_consult_cache import get_consult_cache

    capture = SuspensionCapture(
        transcript=list(transcript),
        history=list(turn_history.get() or []),
        journal_entries=journal_entries,
        # In-memory carrier for legacy / settle paths; durable copy lives on ``turn_paused``.
        citations=list(turn_citations.get() or []),
        trace_id=get_log_value("trace_id"),
        paused_content=paused_content,
    )
    frame = build_frame(capture)
    # Kickoff 已查阅记忆随帧走，resume 同 key 复用（consult_memory.reuse）。
    frame.consulted_memory = dict(get_consult_cache())
    try:
        await saver(frame)
    except Exception as e:
        logger.warning(
            "suspension.saver_failed",
            checkpoint_id=checkpoint_id,
            error=str(e),
        )
        raise SuspensionPersistError(checkpoint_id, e) from e
    if suspension_kind == "ask_user" and message_id:
        release_claimed_pause_tool_calls_if_complete(
            message_id, capture.transcript, "ask_user"
        )
    # After save, land this card on the ambient log so a later sibling persist
    # (same message_id lock) keeps it — ``save_paused_turn`` replaces the journal.
    raw_payload = required_entry.get("payload")
    record_turn_fact(
        Fact(
            kind=str(required_entry["kind"]),
            payload=dict(raw_payload) if isinstance(raw_payload, dict) else {},
            ts=required_entry.get("ts") if isinstance(required_entry.get("ts"), str) else None,
        )
    )
    return True
