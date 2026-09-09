"""End-of-turn persistence facades — delegate to CloudStore (ConversationStore).

Progressive placeholder / finalize / salvage live on ``CloudStore``; this module
keeps the historical import paths and host-side helpers (open-pause detection,
salvage scheduling) stable for turn_runner / turns / tests.

Cancel-path incomplete close funnels through
:func:`agentcore.runtime.turn.interrupt.close_turn_interrupted`.
"""

from __future__ import annotations

import contextlib
from typing import Any

from agentcore.config import settings
from agentcore.conversation.background import spawn_background
from agentcore.conversation.store import (
    MESSAGE_STATUS_COMPLETE,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INCOMPLETE,
    MESSAGE_STATUS_RUNNING,
    get_cloud_store,
)
from agentcore.core.logging import get_logger
from agentcore.llm.resolve import LLMCredentials
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    content_delta,
    error_event,
    message_end,
)
from agentcore.runtime.facts import current_fact_log, pre_pause_from_journal
from agentcore.runtime.turn.interrupt import (
    TurnInterruptReason,
    close_turn_interrupted,
    finish_reason_for,
)
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

# Re-export status constants for callers / tests that imported them here.
__all__ = [
    "MESSAGE_STATUS_COMPLETE",
    "MESSAGE_STATUS_FAILED",
    "MESSAGE_STATUS_INCOMPLETE",
    "MESSAGE_STATUS_RUNNING",
    "close_user_stop_turn",
    "compose_salvage_content",
    "compose_salvage_journal",
    "create_assistant_placeholder",
    "has_open_durable_pause",
    "persist_incomplete_turn",
    "persist_placeholder_abort",
    "persist_turn_result",
    "salvage_incomplete_turn",
]

_PAUSE_REQUIRED_TYPES = ("checkpoint_required", "plan_review_required", "team_preview_required")
_PAUSE_RESOLVED_TYPES = ("checkpoint_resolved", "plan_review_resolved", "team_preview_resolved")


async def create_assistant_placeholder(
    *,
    conversation_id: str,
    message_id: str,
    trace_id: str,
) -> None:
    """Create the running assistant row at turn start (progressive persistence).

    Propagates store errors — callers must not start the pipeline without a row.
    """
    await get_cloud_store().begin_turn(
        conversation_id=conversation_id,
        message_id=message_id,
        trace_id=trace_id,
    )


def has_open_durable_pause(journal: list[dict]) -> bool:
    """True if the journal ends on an UNRESOLVED plan_review / ask_user checkpoint."""
    required: set[str] = set()
    resolved: set[str] = set()
    for event in journal:
        cid = (event.get("payload") or {}).get("checkpoint_id")
        if not cid:
            continue
        if event.get("type") in _PAUSE_REQUIRED_TYPES:
            required.add(cid)
        elif event.get("type") in _PAUSE_RESOLVED_TYPES:
            resolved.add(cid)
    return bool(required - resolved)


async def persist_turn_result(
    *,
    result: dict,
    conversation_id: str,
    user_id: str,
    folder_id: str | None,
    backend: WorkspaceBackend,
    sink: EventSink,
    user_message: str,
    llm_credentials: LLMCredentials | None,
    trace_id: str,
    turn_id: str,
    duration_ms: int,
    kind: str = "turn",
) -> None:
    """Persist a completed turn via ``CloudStore.finalize(mode="cloud")``."""
    await get_cloud_store().finalize(
        mode="cloud",
        result=result,
        conversation_id=conversation_id,
        user_id=user_id,
        folder_id=folder_id,
        backend=backend,
        sink=sink,
        user_message=user_message,
        llm_credentials=llm_credentials,
        trace_id=trace_id,
        turn_id=turn_id,
        duration_ms=duration_ms,
        kind=kind,
    )


async def persist_placeholder_abort(
    *,
    exc: BaseException,
    conversation_id: str,
    user_id: str,
    folder_id: str | None,
    backend: WorkspaceBackend,
    sink: EventSink,
    user_message: str,
    llm_credentials: LLMCredentials | None,
    trace_id: str,
    turn_id: str,
    message_id: str,
    duration_ms: int,
    kind: str = "turn",
) -> dict:
    """Stamp a running placeholder failed when the turn dies before the pipeline.

    Presence-gate / prepare abort used to emit SSE error and leave the row
    ``running`` — a later open / follow then kept the composer spinning. Product
    face via ``error_fields_for`` (dedicated local-workspace codes, not
    STREAM_ERROR).
    """
    from agentcore.core.error_codes import ErrorCode
    from agentcore.core.errors import error_fields_for

    code, message, err_ctx = error_fields_for(
        exc,
        fallback_code=ErrorCode.STREAM_ERROR,
        fallback_message="服务出错了，请稍后重试。",
    )
    if not sink._closed:
        sink.emit(error_event(code, message, context=err_ctx))
        sink.emit(message_end(FinishReason.ERROR, outcome="error"))
    result = {
        "message_id": message_id,
        "content": "",
        "error": message,
        "error_code": code,
        "finish_reason": FinishReason.ERROR,
        "outcome": "error",
    }
    await persist_turn_result(
        result=result,
        conversation_id=conversation_id,
        user_id=user_id,
        folder_id=folder_id,
        backend=backend,
        sink=sink,
        user_message=user_message,
        llm_credentials=llm_credentials,
        trace_id=trace_id,
        turn_id=turn_id,
        duration_ms=duration_ms,
        kind=kind,
    )
    return result


async def persist_incomplete_turn(
    *,
    journal: list[dict],
    content: str,
    conversation_id: str,
    trace_id: str,
    message_id: str | None,
) -> None:
    """Persist a cancelled turn's already-streamed reply + finished work."""
    if not message_id:
        logger.warning(
            "chat.incomplete_persist_skipped",
            conversation_id=conversation_id,
            reason="no_message_id",
        )
        return
    await close_turn_interrupted(
        message_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        reason=TurnInterruptReason.USER_STOP,
        content=content,
        journal=list(journal) if journal else [],
    )


async def close_user_stop_turn(
    *,
    sink: EventSink,
    conversation_id: str,
    trace_id: str,
    message_id: str | None = None,
    journal_entries: list[dict[str, Any]] | None = None,
) -> bool:
    """Synchronously close a user-stopped turn (terminal incomplete + release path).

    Awaited so ``/stop`` finishes the durable write before releasing the lease.
    Callers must only ``release_turn_lease`` when this returns ``True``; on
    ``False`` they must ``orphan_turn_lease`` so a RUNNING row never loses its lease.

    Always emits live ``message_end`` first so an attached SSE client can
    leave ``stopping``. Empty journal / empty captain body must still durable-close
    (tool-only / pre-stream cancel); open durable pause keeps the pause frame and
    returns ``True`` (safe to release — pause owns continuation).

    After ``content_reset`` with no new delta, reinjects stashed prose via
    ``content_delta`` before ``message_end`` so the live bubble is not left empty.
    """
    from agentcore.runtime.turn.runs import turn_runs

    journal = sink.execution_journal()
    salvage = sink.interrupt_salvage_content()
    content = compose_salvage_content(salvage, journal_entries)
    reason = TurnInterruptReason.USER_STOP
    if turn_runs.is_superseded(conversation_id) and not turn_runs.is_user_stop(
        conversation_id
    ):
        reason = TurnInterruptReason.OVERLAP
    finish = finish_reason_for(reason)
    # Live confirmation before durable close — FE confirms stop on this frame.
    # If finish_guard cleared the bubble and no rewrite started, push salvage
    # text so attached clients see what already streamed before cancelled.
    if not sink._closed:
        live = sink.streamed_content()
        if not (live or "").strip() and (salvage or "").strip():
            with contextlib.suppress(Exception):
                sink.emit(content_delta(salvage))
        with contextlib.suppress(Exception):
            sink.emit(message_end(finish))
    if not settings.incomplete_turn_persist_enabled:
        return False
    if not message_id:
        return False
    suspend_frames = settings.structured_suspension_persist_enabled
    if journal and suspend_frames and has_open_durable_pause(journal):
        # Pause frame is the durable record — do not interrupt-close; lease may release.
        return True
    return await close_turn_interrupted(
        message_id=message_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        reason=reason,
        content=content,
        journal=list(journal) if journal else [],
        load_stream_state=True,
    )


def compose_salvage_content(
    live: str,
    journal_entries: list[dict[str, Any]] | None = None,
) -> str:
    """Join turn_paused pre_pause with live streamed content for cancel salvage (G8).

    ``journal_entries`` is preferred (hang-frame facts or an explicit snapshot). When
    omitted, the ambient :data:`current_fact_log` is used. Journals without
    ``turn_paused`` yield an empty base — same as legacy live-only salvage.

    Truncates at the first DSML open tag (salvage B) before returning — unfinished
    tool XML must not join the incomplete body.
    """
    entries = journal_entries
    if entries is None:
        log = current_fact_log.get()
        entries = log.entries() if log is not None else None
    snap = pre_pause_from_journal(entries)
    pre = (snap.content if snap is not None else "") or ""
    from agentcore.core.assistant_content import prepare_assistant_content
    from agentcore.runtime.closing_posture import reconcile_resume_closing

    joined = reconcile_resume_closing(pre, live or "")
    return prepare_assistant_content(joined, salvage=True)


def compose_salvage_journal(
    live: list[dict[str, Any]] | None,
    hang_frame: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge hang-frame journal with live post-resume facts for cancel salvage.

    Symmetric to :func:`compose_salvage_content`: live ``execution_journal`` alone
    drops pre-pause ``process_*``. Hang-frame first (original order), then live
    entries not already covered by a settlement ``(kind, checkpoint_id)`` key.
    Explicit ``seq`` is stamped so outbox salvage keys continue past the seed.
    """
    base = [e for e in (hang_frame or []) if isinstance(e, dict)]
    live_list = [e for e in (live or []) if isinstance(e, dict)]
    if not base:
        return list(live_list)
    if not live_list:
        return list(base)

    settled: set[tuple[str, str]] = set()
    for entry in base:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if not kind.endswith("_resolved"):
            continue
        payload = dict(entry.get("payload") or {})
        cid = str(payload.get("checkpoint_id") or "")
        if cid:
            settled.add((kind, cid))

    merged: list[dict[str, Any]] = list(base)
    for entry in live_list:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind.endswith("_resolved"):
            payload = dict(entry.get("payload") or {})
            cid = str(payload.get("checkpoint_id") or "")
            if cid and (kind, cid) in settled:
                continue
        merged.append(entry)

    out: list[dict[str, Any]] = []
    for i, entry in enumerate(merged):
        stamped = dict(entry)
        stamped["seq"] = i
        out.append(stamped)
    return out


def salvage_incomplete_turn(
    *,
    sink: EventSink,
    conversation_id: str,
    trace_id: str,
    message_id: str | None = None,
    journal_entries: list[dict[str, Any]] | None = None,
) -> None:
    """On a turn cancel, schedule saving its streamed reply + finished work as one message.

    Salvages when there is EITHER finished team work (a replayable journal) OR the CEO had
    streamed some reply text — so a cancelled pure-text answer (no team/tool journal surface)
    is no longer silently dropped. Skips a turn parked at an unresolved durable checkpoint
    (its paused frame is the record).

    ``journal_entries`` (optional): hang-frame §8.3 facts so G8 can prepend
    ``turn_paused.content`` when the ambient fact log is already unbound (resume
    pipeline ``finally``). Omit on fresh turns — legacy live-only behaviour.
    """
    if not settings.incomplete_turn_persist_enabled:
        return
    if not message_id:
        return
    journal = sink.execution_journal()
    content = compose_salvage_content(sink.interrupt_salvage_content(), journal_entries)
    if not journal and not content.strip():
        return
    suspend_frames = settings.structured_suspension_persist_enabled
    if journal and suspend_frames and has_open_durable_pause(journal):
        return
    spawn_background(
        persist_incomplete_turn(
            journal=list(journal) if journal else [],
            content=content,
            conversation_id=conversation_id,
            trace_id=trace_id,
            message_id=message_id,
        )
    )
