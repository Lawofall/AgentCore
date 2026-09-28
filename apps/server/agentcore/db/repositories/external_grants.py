"""Data access for conversation-scoped external directory grants (W3)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models import Conversation, ConversationExternalGrant


class ExternalGrantRepository:
    """CRUD for ``conversation_external_grants`` (``ON DELETE CASCADE`` from the conversation)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_conversation(
        self, conversation_id: str
    ) -> Sequence[ConversationExternalGrant]:
        result = await self._session.execute(
            select(ConversationExternalGrant)
            .where(ConversationExternalGrant.conversation_id == conversation_id)
            .order_by(ConversationExternalGrant.created_at.asc())
        )
        return result.scalars().all()

    async def list_root_ids_for_device(
        self, *, user_id: str, device_id: str
    ) -> list[str]:
        """Root ids this device registered, across the user's live conversations.

        Read on fulfill (re)connect to rebuild that session's declared roots. The
        join scopes by owner so a device id guessed by another account cannot
        widen its own routing, and skips soft-deleted conversations whose grants
        are on their way out anyway.
        """
        result = await self._session.execute(
            select(ConversationExternalGrant.root_id)
            .join(
                Conversation,
                Conversation.id == ConversationExternalGrant.conversation_id,
            )
            .where(
                ConversationExternalGrant.device_id == device_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .distinct()
        )
        return [row for row in result.scalars().all() if row]

    async def upsert(
        self,
        *,
        conversation_id: str,
        root_id: str,
        alias: str,
        label: str,
        mode: str,
        device_id: str | None = None,
    ) -> ConversationExternalGrant:
        """Insert or refresh by ``root_id`` (alias stable on same root).

        A re-registration from another install moves the binding: the folder is
        on whichever machine just proved it can resolve the path. A call without
        a device (non-desktop caller) leaves the existing binding alone rather
        than erasing the only record of where the folder lives.
        """
        result = await self._session.execute(
            select(ConversationExternalGrant).where(
                ConversationExternalGrant.conversation_id == conversation_id,
                ConversationExternalGrant.root_id == root_id,
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is not None:
            row.label = label or row.label
            row.mode = mode
            if device_id:
                row.device_id = device_id
            row.updated_at = now
            await self._session.commit()
            await self._session.refresh(row)
            return row

        row = ConversationExternalGrant(
            id=new_id(),
            conversation_id=conversation_id,
            alias=alias,
            root_id=root_id,
            label=label or alias,
            mode=mode,
            device_id=device_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete_one(
        self,
        conversation_id: str,
        *,
        alias: str | None = None,
        root_id: str | None = None,
    ) -> int:
        """Delete matching rows; return number removed."""
        stmt = delete(ConversationExternalGrant).where(
            ConversationExternalGrant.conversation_id == conversation_id
        )
        if alias is not None:
            stmt = stmt.where(ConversationExternalGrant.alias == alias)
        if root_id is not None:
            stmt = stmt.where(ConversationExternalGrant.root_id == root_id)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def clear_conversation(self, conversation_id: str) -> None:
        await self._session.execute(
            delete(ConversationExternalGrant).where(
                ConversationExternalGrant.conversation_id == conversation_id
            )
        )
        await self._session.commit()
