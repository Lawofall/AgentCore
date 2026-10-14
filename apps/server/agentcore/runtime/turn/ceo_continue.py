"""Independent CEO rate-limit continue latch (not a checkpoint card).

Cloud-only. A ``paused_turns`` row with ``kind: ceo_continue`` is a claim-once
lock — ``list_paused_turns`` / ``GET …/recovery.paused`` / ``/resume`` skip it
before ``suspension_from_json``. Consume by marking ``claimed`` (no outcome row);
a delete-on-claim would reopen the no-lock + ``usage.outcome=paused`` hole.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.error_codes import ErrorCode
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import MessageRepository, PausedTurnRepository
from agentcore.runtime.events import FinishReason

logger = get_logger(__name__)

CEO_CONTINUE_KIND = "ceo_continue"
CEO_CONTINUE_CLAIMED_KEY = "claimed"


def is_ceo_continue_frame(frame: object) -> bool:
    """True when the raw ``paused_turns.frame`` is the CEO continue lock."""
    if not isinstance(frame, dict):
        return False
    return str(frame.get("kind") or "") == CEO_CONTINUE_KIND


def is_claimed_ceo_continue_frame(frame: object) -> bool:
    """True when this continue latch has already been consumed."""
    if not isinstance(frame, dict) or not is_ceo_continue_frame(frame):
        return False
    claimed = frame.get(CEO_CONTINUE_CLAIMED_KEY)
    return claimed is True or str(claimed).lower() == "true"


def should_pause_ceo_rate_limit(*, role: str, error_code: str) -> bool:
    """Captain + exhausted ``LLM_RATE_LIMIT`` on cloud → pause, not fail.

    Sidecar stays on the existing degraded/error path (no continue entry there).
    Attested-short Retry-After never reaches this: the provider/executor keeps
    that path and does not bubble ``LLM_RATE_LIMIT``.
    """
    if role != "captain":
        return False
    if error_code != ErrorCode.LLM_RATE_LIMIT:
        return False
    try:
        from agentcore.sidecar.server_pkg.core import is_sidecar_process

        if is_sidecar_process():
            return False
    except Exception:  # noqa: BLE001 — missing sidecar module is cloud
        pass
    return True


def is_ceo_rate_limit_pause(*, sink: object, finish: object) -> bool:
    """Settle-time: this PAUSED close is the CEO rate-limit continue latch."""
    finish_value = getattr(finish, "value", finish)
    if finish_value != FinishReason.PAUSED.value:
        return False
    last_error = getattr(sink, "last_turn_error", None)
    err = last_error() if callable(last_error) else None
    if not isinstance(err, dict):
        return False
    return err.get("code") == ErrorCode.LLM_RATE_LIMIT


def ceo_continue_frame(
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
) -> dict[str, str]:
    return {
        "kind": CEO_CONTINUE_KIND,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
    }


def mark_host_turn_paused() -> None:
    """Process-local harvest skip (set in settle, before pipeline teardown)."""
    from agentcore.runtime.graph.session.session import active_coordination

    session = active_coordination()
    if session is None:
        return
    session.host_turn_paused = True


async def save_ceo_continue_lock(
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
    trace_id: str | None = None,
) -> None:
    """Upsert the claim-once lock. Does not signal attention or push."""
    frame = ceo_continue_frame(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    try:
        async with async_session_factory() as db:
            await PausedTurnRepository(db).upsert(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                frame=frame,
                trace_id=trace_id,
            )
    except Exception as e:  # noqa: BLE001 — best-effort; continue insert-claims if paused
        logger.warning(
            "suspension.ceo_continue_lock_save_failed",
            message_id=message_id,
            conversation_id=conversation_id,
            error=str(e),
        )
        return
    logger.info(
        "suspension.ceo_continue_lock_saved",
        message_id=message_id,
        conversation_id=conversation_id,
    )


async def peek_ceo_continue_lock(
    message_id: str, *, conversation_id: str
) -> dict[str, Any] | None:
    """Read the lock without claiming. ``None`` when absent / wrong conversation / not this kind."""
    try:
        async with async_session_factory() as db:
            row = await PausedTurnRepository(db).get(message_id)
    except Exception as e:  # noqa: BLE001 — peek failure reads as "not continuable"
        logger.warning(
            "suspension.ceo_continue_lock_peek_failed",
            message_id=message_id,
            error=str(e),
        )
        return None
    if row is None or row.conversation_id != conversation_id:
        return None
    frame = row.frame if isinstance(row.frame, dict) else {}
    if not is_ceo_continue_frame(frame) or is_claimed_ceo_continue_frame(frame):
        return None
    return frame


async def claim_ceo_continue_lock(
    message_id: str, *, conversation_id: str, user_id: str
) -> dict[str, Any] | None:
    """Atomically consume the latch. ``None`` if already claimed / not continuable.

    Winning marks the ``paused_turns`` row claimed. No lock + ``usage.outcome=paused``
    inserts the latch here (claim path, not continue-failure restore).
    """
    frame = {
        **ceo_continue_frame(
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
        ),
        CEO_CONTINUE_CLAIMED_KEY: True,
    }
    raw: dict[str, Any] | None = None
    try:
        async with async_session_factory() as db:
            row = await PausedTurnRepository(db).claim_ceo_continue_lock(
                message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                frame=frame,
            )
            if row is not None and isinstance(row.frame, dict):
                raw = dict(row.frame)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "suspension.ceo_continue_lock_claim_failed",
            message_id=message_id,
            error=str(e),
        )
        return None
    if raw is None or not is_ceo_continue_frame(raw):
        return None
    logger.info(
        "suspension.ceo_continue_lock_claimed",
        message_id=message_id,
        conversation_id=conversation_id,
    )
    return raw


async def restore_ceo_continue_lock(
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
    trace_id: str | None = None,
) -> None:
    """Put the lock back after a failed continue so the user can retry."""
    await save_ceo_continue_lock(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        trace_id=trace_id,
    )


async def release_ceo_continue_claim(
    message_id: str, *, conversation_id: str
) -> None:
    """Drop a consumed latch after a successful continue (unclaimed re-pause stays)."""
    try:
        async with async_session_factory() as db:
            await PausedTurnRepository(db).delete_claimed_ceo_continue_lock(
                message_id, conversation_id=conversation_id
            )
    except Exception as e:  # noqa: BLE001 — leftover claimed row is skipped by peek
        logger.warning(
            "suspension.ceo_continue_lock_claim_failed",
            message_id=message_id,
            error=str(e),
        )


async def message_is_ceo_paused(message_id: str, *, conversation_id: str) -> bool:
    """True when the assistant row carries ``usage.outcome=paused`` (turn authority)."""
    try:
        async with async_session_factory() as db:
            row = await MessageRepository(db).get_by_id(
                message_id, conversation_id=conversation_id
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "suspension.ceo_continue_usage_peek_failed",
            message_id=message_id,
            error=str(e),
        )
        return False
    if row is None:
        return False
    raw_usage = getattr(row, "usage", None)
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    return usage.get("outcome") == "paused"


async def host_turn_is_ceo_paused(conversation_id: str, host_turn_id: str) -> bool:
    """Harvest backstop: lock or persisted ``outcome=paused`` on the host turn."""
    mid = (host_turn_id or "").strip()
    if not mid:
        return False
    if await peek_ceo_continue_lock(mid, conversation_id=conversation_id) is not None:
        return True
    return await message_is_ceo_paused(mid, conversation_id=conversation_id)
