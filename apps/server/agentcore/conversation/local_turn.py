"""Sidecar local-turn write-back — finalize snapshot + progressive cloud projection."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy.exc import IntegrityError

from agentcore.conversation.mentions import to_stored_agent_mentions
from agentcore.conversation.store import MESSAGE_STATUS_RUNNING, get_cloud_store
from agentcore.conversation.zero_output_rollback import (
    delete_assistant_and_paired_user,
    error_code_from_turn_result,
    result_from_local_turn_writeback,
    should_delete_zero_output_send_result,
)
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import MessageRepository
from agentcore.llm.resolve import LLMCredentials

logger = get_logger(__name__)


async def record_local_turn(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    assistant_content: str,
    assistant_reasoning: str | None = None,
    citations: list[dict] | None = None,
    evidence_ledger: list[dict] | None = None,
    runs: dict | None = None,
    journal: list[dict] | None = None,
    tool_failures: Sequence[dict[str, Any]] | None = None,
    user_message_id: str,
    message_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    rounds: int = 0,
    trace_id: str,
    finish_reason: str | None = None,
    llm_credentials: LLMCredentials | None = None,
    origin: str | None = None,
    execution_id: str | None = None,
    harvest_kind: str | None = None,
    agent_mentions: list[dict] | None = None,
) -> dict:
    """Persist a turn that ran on the user's machine via the sidecar.

    Routes through ``CloudStore.finalize(mode="local")`` so D7 merge rules apply
    (content monotonic, status gate, journal seq upsert, no early-return).
    Spend is NOT recorded here — metered at ``/v1/inference``.
    ``tool_failures`` is observability-only (logged, not persisted to message tables).
    """
    with log_context(trace_id=trace_id, conversation_id=conversation_id, user_id=user_id):
        from agentcore.runtime.engine.tool_channel_redirect import is_channel_redirect_code

        failures = [
            f
            for f in (tool_failures or ())
            if isinstance(f, dict)
            and not is_channel_redirect_code(str(f.get("code") or ""))
        ]
        if failures:
            codes = [str(f.get("code") or "other") for f in failures]
            tools = [str(f.get("tool") or "") for f in failures]
            # Wire already carries ``message`` (≤200); log it so the ``other`` bucket
            # can be split (timeout / sandbox / fence / HTTP) without a client change.
            messages = [str(f.get("message") or "")[:200] for f in failures]
            logger.info(
                "chat.local_turn_tool_failures",
                conversation_id=conversation_id,
                message_id=message_id,
                count=len(failures),
                codes=codes,
                tools=tools,
                messages=messages,
            )
        # Harvest / origin write-backs are not this-send creates (cloud continue
        # / workflow pass False). Ordinary startTurn write-back is this-send.
        user_created_this_send = not (
            (origin or "").strip() or (harvest_kind or "").strip()
        )
        result_like = result_from_local_turn_writeback(
            message_id=message_id,
            content=assistant_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            journal=journal,
            runs=runs,
        )
        if should_delete_zero_output_send_result(
            result_like, user_created_this_send=user_created_this_send
        ):
            logger.info(
                "chat.zero_output_send_deleted",
                conversation_id=conversation_id,
                message_id=str(message_id or ""),
                user_message_id=user_message_id,
                error_code=error_code_from_turn_result(result_like),
            )
            return {
                "user_message_id": user_message_id,
                "assistant_message_id": None,
                "title": None,
                "followups": None,
                "noop": True,
            }
        result = await get_cloud_store().finalize(
            mode="local",
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=user_message,
            assistant_content=assistant_content,
            assistant_reasoning=assistant_reasoning,
            citations=citations,
            evidence_ledger=evidence_ledger,
            runs=runs,
            journal=journal,
            user_message_id=user_message_id,
            message_id=message_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            rounds=rounds,
            trace_id=trace_id,
            finish_reason=finish_reason,
            llm_credentials=llm_credentials,
            origin=origin,
            execution_id=execution_id,
            harvest_kind=harvest_kind,
            agent_mentions=agent_mentions,
        )
        assert result is not None
        return result


async def _insert_user_idempotent(
    *,
    conversation_id: str,
    user_message: str,
    user_message_id: str,
    agent_mentions: list[dict] | None,
) -> None:
    """Pin a user row to ``user_message_id``; same conversation + id is success."""
    stored_mentions = to_stored_agent_mentions(agent_mentions)
    try:
        async with async_session_factory() as session:
            await MessageRepository(session).create(
                conversation_id=conversation_id,
                role="user",
                content=user_message,
                message_id=user_message_id,
                agent_mentions=stored_mentions or None,
            )
    except IntegrityError:
        async with async_session_factory() as session:
            existing = await MessageRepository(session).get_by_id(
                user_message_id, conversation_id=conversation_id
            )
        if existing is not None and getattr(existing, "role", None) == "user":
            logger.info(
                "chat.local_turn_user_idempotent",
                conversation_id=conversation_id,
                user_message_id=user_message_id,
            )
            return
        raise


async def begin_local_turn(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    user_message_id: str,
    message_id: str,
    trace_id: str,
    agent_mentions: list[dict] | None = None,
) -> dict[str, str]:
    """Idempotent user row + running assistant placeholder. No cloud turn / title."""
    with log_context(trace_id=trace_id, conversation_id=conversation_id, user_id=user_id):
        await _insert_user_idempotent(
            conversation_id=conversation_id,
            user_message=user_message,
            user_message_id=user_message_id,
            agent_mentions=agent_mentions,
        )
        await get_cloud_store().begin_turn(
            conversation_id=conversation_id,
            message_id=message_id,
            trace_id=trace_id,
        )
        logger.info(
            "chat.local_turn_begin",
            conversation_id=conversation_id,
            message_id=message_id,
            user_message_id=user_message_id,
        )
        return {
            "user_message_id": user_message_id,
            "assistant_message_id": message_id,
        }


async def append_local_turn_journal(
    *,
    conversation_id: str,
    user_id: str,
    message_id: str,
    entries: Sequence[tuple[int, dict[str, Any]]],
    trace_id: str | None = None,
) -> None:
    """Append-on-emit journal facts (``seq`` required). Failures do not settle."""
    store = get_cloud_store()
    with log_context(trace_id=trace_id, conversation_id=conversation_id, user_id=user_id):
        for seq, entry in entries:
            await store.append_journal(
                turn_id=message_id,
                seq=seq,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entry=entry,
            )


async def upsert_local_turn_stream_segments(
    *,
    conversation_id: str,
    user_id: str,
    message_id: str,
    segments: Sequence[tuple[str, str, int]],
) -> None:
    """UPSERT in-flight channel snapshots. Does not touch ``messages.content``."""
    with log_context(conversation_id=conversation_id, user_id=user_id):
        await get_cloud_store().upsert_stream_segments(
            turn_id=message_id,
            segments=segments,
        )


def _assistant_is_in_flight(usage: Any) -> bool:
    if not isinstance(usage, dict):
        return False
    if usage.get("paused"):
        return False
    return usage.get("status") == MESSAGE_STATUS_RUNNING


async def abort_local_turn(
    *,
    conversation_id: str,
    user_id: str,
    user_message_id: str,
    message_id: str,
) -> dict[str, bool]:
    """Delete a still-running assistant + paired user. Settled rows are a no-op."""
    with log_context(conversation_id=conversation_id, user_id=user_id):
        async with async_session_factory() as session:
            assistant = await MessageRepository(session).get_by_id(
                message_id, conversation_id=conversation_id
            )
        if assistant is None or getattr(assistant, "role", None) != "assistant":
            logger.info(
                "chat.local_turn_abort_noop",
                conversation_id=conversation_id,
                message_id=message_id,
                reason="missing",
            )
            return {"aborted": False}
        if not _assistant_is_in_flight(getattr(assistant, "usage", None)):
            logger.info(
                "chat.local_turn_abort_noop",
                conversation_id=conversation_id,
                message_id=message_id,
                reason="settled",
            )
            return {"aborted": False}
        await delete_assistant_and_paired_user(
            conversation_id=conversation_id,
            assistant_message_id=message_id,
            user_message_id=user_message_id,
        )
        logger.info(
            "chat.local_turn_aborted",
            conversation_id=conversation_id,
            message_id=message_id,
            user_message_id=user_message_id,
        )
        return {"aborted": True}
