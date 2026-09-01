"""Persist a user row when Steer / Queue is accepted (发送即入时间线).

Idle ``stream_chat`` already inserts at turn start. Mid-flight delivery used to
keep the utterance only in process memory / journal interjection — refresh lost
it. This module writes the same ``messages`` row the timeline hydrates from.

Drain reuses the row (``load_or_create_turn_user_message``). Cancel deletes it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from agentcore.conversation.mentions import to_stored_agent_mentions
from agentcore.core.types import is_uuid_id, new_id
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import MessageRepository
from agentcore.workspace.attachments import to_stored_metadata


async def persist_midflight_user_message(
    *,
    conversation_id: str,
    content: str,
    user_message_id: str | None,
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
) -> str:
    """Insert the mid-flight user row. Same conversation + id is success."""
    candidate = (user_message_id or "").strip()
    pinned = candidate if is_uuid_id(candidate) else new_id()
    stored_mentions = to_stored_agent_mentions(agent_mentions)
    stored_atts = to_stored_metadata(attachments) if attachments else None
    try:
        async with async_session_factory() as session:
            await MessageRepository(session).create(
                conversation_id=conversation_id,
                role="user",
                content=content,
                message_id=pinned,
                attachments=stored_atts,
                agent_mentions=stored_mentions or None,
            )
    except IntegrityError:
        async with async_session_factory() as session:
            existing = await MessageRepository(session).get_by_id(
                pinned, conversation_id=conversation_id
            )
        if existing is not None and getattr(existing, "role", None) == "user":
            return pinned
        raise
    return pinned


async def delete_midflight_user_message(
    conversation_id: str, user_message_id: str | None
) -> bool:
    """Drop a queued user row that never started a turn. Missing id → False."""
    mid = (user_message_id or "").strip()
    if not is_uuid_id(mid):
        return False
    async with async_session_factory() as session:
        return await MessageRepository(session).delete_by_id(
            mid, conversation_id=conversation_id
        )


async def load_or_create_turn_user_message(
    session: Any,
    *,
    conversation_id: str,
    user_message: str,
    existing_user_message_id: str | None,
    attachments: list[dict[str, Any]] | None,
    agent_mentions: list[dict[str, Any]] | None,
) -> Any:
    """Reuse a mid-flight row when present; otherwise insert like idle send."""
    repo = MessageRepository(session)
    pinned = (
        existing_user_message_id if is_uuid_id(existing_user_message_id) else None
    )
    user_msg = None
    if pinned:
        user_msg = await repo.get_by_id(pinned, conversation_id=conversation_id)
        if user_msg is not None and getattr(user_msg, "role", None) != "user":
            user_msg = None
    stored_mentions = to_stored_agent_mentions(agent_mentions)
    stored_atts = to_stored_metadata(attachments) if attachments else None
    if user_msg is None:
        return await repo.create(
            conversation_id=conversation_id,
            role="user",
            content=user_message,
            attachments=stored_atts,
            agent_mentions=stored_mentions or None,
            message_id=pinned,
        )
    if stored_atts is not None or stored_mentions:
        await repo.update_content(
            user_msg.id,
            user_message,
            attachments=stored_atts,
            agent_mentions=stored_mentions or None,
        )
    return user_msg
