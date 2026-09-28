"""Public read-only conversation shares data access (公开只读分享链接)."""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models import ConversationShare


class ConversationShareRepository:
    """Public read-only conversation shares (公开只读分享链接, 对标 ChatGPT 分享).

    A share freezes a content-only transcript snapshot at create time; the public
    page renders that copy. Revocation is soft (``revoked_at``) so a killed link
    404s at once while the row survives. Hard-deleting the conversation nulls
    ``conversation_id`` (``fk_conversation_shares_conversation_id``). The same
    revoke runs when the account is 注销.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        conversation_id: str,
        user_id: str,
        title: str,
        snapshot: list[dict],
        expires_at: datetime | None = None,
    ) -> ConversationShare:
        share = ConversationShare(
            id=new_id(),
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            snapshot=snapshot,
            expires_at=expires_at,
        )
        self._session.add(share)
        await self._session.commit()
        await self._session.refresh(share)
        return share

    async def get_active(self, token: str) -> ConversationShare | None:
        """An un-revoked share by its public token (the row id), or None.

        Backs the public ``/shared/<token>`` render: a revoked / unknown token
        resolves to None so the page 404s without leaking whether the id ever
        existed.
        """
        result = await self._session.execute(
            select(ConversationShare).where(
                ConversationShare.id == token,
                ConversationShare.revoked_at.is_(None),
                or_(
                    ConversationShare.expires_at.is_(None),
                    ConversationShare.expires_at > datetime.now(UTC),
                ),
            )
        )
        return result.scalar_one_or_none()

    async def list_active_for_conversation(
        self, conversation_id: str, *, user_id: str
    ) -> Sequence[ConversationShare]:
        """A conversation's live shares (owner-scoped), newest first — the "manage
        links" list. Owner scoping means a guessed conversation id can't enumerate
        another user's shares."""
        result = await self._session.execute(
            select(ConversationShare)
            .where(
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.user_id == user_id,
                ConversationShare.revoked_at.is_(None),
                or_(
                    ConversationShare.expires_at.is_(None),
                    ConversationShare.expires_at > datetime.now(UTC),
                ),
            )
            .order_by(ConversationShare.created_at.desc())
        )
        return result.scalars().all()

    async def revoke(self, share_id: str, *, conversation_id: str, user_id: str) -> bool:
        """Revoke one share (owner + conversation scoped). Returns False when the id
        is unknown / already revoked / not the user's, so a stale revoke 404s."""
        result = await self._session.execute(
            update(ConversationShare)
            .where(
                ConversationShare.id == share_id,
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.user_id == user_id,
                ConversationShare.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()
        return bool(result.rowcount or 0)

    async def revoke_all_for_conversation(self, conversation_id: str) -> int:
        """Revoke every live share of a conversation (删除对话 cascade)."""
        result = await self._session.execute(
            update(ConversationShare)
            .where(
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()
        return int(result.rowcount or 0)

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke every live share a user created (账户注销 cascade)."""
        result = await self._session.execute(
            update(ConversationShare)
            .where(
                ConversationShare.user_id == user_id,
                ConversationShare.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()
        return int(result.rowcount or 0)
