"""Owner-scoped chat-window assembly — cloud send and sidecar share this loader.

Sidecar has no conversation message DB. The cloud builds the same window
``load_chat_context`` would (summary + recency cap + harvest notes).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.conversation.compaction import compact_before_turn
from agentcore.conversation.history import load_chat_context
from agentcore.core.errors import NotFoundError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import ConversationRepository
from agentcore.llm.resolve import resolve_conversation_model_selection

logger = get_logger(__name__)


async def assemble_owned_chat_context(
    session: AsyncSession,
    conversation_id: str,
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    """Near-ceiling compact then ``load_chat_context`` for the owner.

    Same pre-turn order as cloud send / regenerate / resume. Raises
    :class:`NotFoundError` when the conversation is missing, not owned, or handoff.
    Raises :class:`~agentcore.core.errors.ContextOverflowError` when the fit-check
    is near the model window and the fold did not write.
    """
    conv = await ConversationRepository(session).get_by_id(
        conversation_id, user_id=user_id
    )
    if conv is None or getattr(conv, "mode", None) == "handoff":
        raise NotFoundError("对话不存在")
    model_id: str | None = None
    try:
        selection = await resolve_conversation_model_selection(session, conv, user_id)
        model_id = selection.model
    except Exception:
        logger.warning(
            "chat_context.model_resolve_failed",
            conversation_id=conversation_id,
        )
    await compact_before_turn(conversation_id, model_id=model_id)
    async with async_session_factory() as fresh:
        return await load_chat_context(fresh, conversation_id)
