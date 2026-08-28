"""Turn-lease repository (durable RUNNING ownership)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models.conversations import Conversation
from agentcore.db.models.runs import TurnLeaseRow

# Process-level cancel / shutdown: keep the row so the sweeper can reclaim.
PHASE_ORPHANED = "orphaned"


class TurnLeaseRepository:
    """Postgres store for in-flight turn leases (swappable for Redis later)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        owner_id: str,
        phase: str = "running",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Acquire / refresh a lease for ``message_id`` under ``owner_id``."""
        now = datetime.now(UTC)
        payload = meta or {}
        stmt = (
            pg_insert(TurnLeaseRow)
            .values(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                owner_id=owner_id,
                phase=phase,
                meta=payload,
                heartbeat_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["message_id"],
                set_={
                    "owner_id": owner_id,
                    "phase": phase,
                    "meta": payload,
                    "heartbeat_at": now,
                    "updated_at": now,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def heartbeat(
        self,
        message_id: str,
        *,
        owner_id: str,
        phase: str | None = None,
        conversation_id: str | None = None,
    ) -> bool:
        """Bump heartbeat when ``owner_id`` still owns the row. Returns False if lost."""
        now = datetime.now(UTC)
        values: dict[str, Any] = {"heartbeat_at": now, "updated_at": now}
        if phase is not None:
            values["phase"] = phase
        cond = [
            TurnLeaseRow.message_id == message_id,
            TurnLeaseRow.owner_id == owner_id,
        ]
        if conversation_id is not None:
            cond.append(TurnLeaseRow.conversation_id == conversation_id)
        result = await self._session.execute(
            update(TurnLeaseRow).where(*cond).values(**values)
        )
        await self._session.commit()
        return (result.rowcount or 0) > 0

    async def release(self, message_id: str, *, owner_id: str | None = None) -> None:
        """Drop the lease (terminal finish / pause / stop). Owner-scoped when given."""
        stmt = delete(TurnLeaseRow).where(TurnLeaseRow.message_id == message_id)
        if owner_id is not None:
            stmt = stmt.where(TurnLeaseRow.owner_id == owner_id)
        await self._session.execute(stmt)
        await self._session.commit()

    async def mark_orphaned(self, message_id: str, *, owner_id: str | None = None) -> bool:
        """Keep the lease row for sweeper reclaim after process cancel / shutdown.

        Sets ``phase=orphaned`` and bumps ``updated_at`` only — does **not** refresh
        ``heartbeat_at`` (TTL expiry still applies as a second signal).
        """
        now = datetime.now(UTC)
        stmt = (
            update(TurnLeaseRow)
            .where(TurnLeaseRow.message_id == message_id)
            .values(phase=PHASE_ORPHANED, updated_at=now)
        )
        if owner_id is not None:
            stmt = stmt.where(TurnLeaseRow.owner_id == owner_id)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return (result.rowcount or 0) > 0

    async def get(self, message_id: str) -> TurnLeaseRow | None:
        result = await self._session.execute(
            select(TurnLeaseRow).where(TurnLeaseRow.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def bump_recover_attempts(self, message_id: str, *, owner_id: str) -> int:
        """Increment ``meta.recover_attempts`` when this owner still holds the row.

        Returns the new attempt count, or ``0`` when ownership was lost.
        """
        row = await self.get(message_id)
        if row is None or row.owner_id != owner_id:
            return 0
        meta = dict(row.meta) if isinstance(row.meta, dict) else {}
        attempts = int(meta.get("recover_attempts") or 0) + 1
        meta["recover_attempts"] = attempts
        result = await self._session.execute(
            update(TurnLeaseRow)
            .where(
                TurnLeaseRow.message_id == message_id,
                TurnLeaseRow.owner_id == owner_id,
            )
            .values(meta=meta)
        )
        await self._session.commit()
        if (result.rowcount or 0) <= 0:
            return 0
        return attempts

    async def exists_fresh_for_conversation(
        self, conversation_id: str, *, after: datetime
    ) -> bool:
        """Whether the conversation has a live turn (lease heartbeat newer than ``after``).

        Stale leases (owner presumed dead, heartbeat past the TTL) do not count — a
        crashed turn must not keep read-side probes reporting "running" forever.
        """
        result = await self._session.execute(
            select(TurnLeaseRow.message_id)
            .where(
                TurnLeaseRow.conversation_id == conversation_id,
                TurnLeaseRow.heartbeat_at >= after,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_fresh_for_user(
        self, user_id: str, *, after: datetime
    ) -> Sequence[TurnLeaseRow]:
        """This user's fresh leases on live conversations (owner presumed live).

        Account-level「哪些云对话还在跑」seeds from this, not the process-local
        registry — a restart empties memory while the rows remain. Soft-deleted
        (``deleted_at`` set) and already-gone conversations are excluded so a
        reconnect snapshot cannot relight a chat the user deleted.
        """
        result = await self._session.execute(
            select(TurnLeaseRow)
            .join(Conversation, Conversation.id == TurnLeaseRow.conversation_id)
            .where(
                TurnLeaseRow.user_id == user_id,
                TurnLeaseRow.heartbeat_at >= after,
                Conversation.deleted_at.is_(None),
            )
            .order_by(TurnLeaseRow.heartbeat_at.desc())
        )
        return result.scalars().all()

    async def list_expired(self, *, before: datetime, limit: int) -> Sequence[TurnLeaseRow]:
        """Leases whose owner is presumed dead (stale heartbeat or cancel orphan mark)."""
        result = await self._session.execute(
            select(TurnLeaseRow)
            .where(
                or_(
                    TurnLeaseRow.heartbeat_at < before,
                    TurnLeaseRow.phase == PHASE_ORPHANED,
                )
            )
            .order_by(TurnLeaseRow.heartbeat_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def claim_expired(
        self,
        message_id: str,
        *,
        new_owner_id: str,
        before: datetime,
        phase: str = "recovering",
    ) -> TurnLeaseRow | None:
        """Atomically take over an expired / orphaned lease (only one sweeper wins).

        UPDATE … WHERE (heartbeat stale OR phase=orphaned) RETURNING — a second
        concurrent claim sees 0 rows. Returns the row after claim, or ``None``.
        """
        now = datetime.now(UTC)
        result = await self._session.execute(
            update(TurnLeaseRow)
            .where(
                TurnLeaseRow.message_id == message_id,
                or_(
                    TurnLeaseRow.heartbeat_at < before,
                    TurnLeaseRow.phase == PHASE_ORPHANED,
                ),
            )
            .values(
                owner_id=new_owner_id,
                phase=phase,
                heartbeat_at=now,
                updated_at=now,
            )
            .returning(TurnLeaseRow)
        )
        row = result.scalar_one_or_none()
        await self._session.commit()
        return row
