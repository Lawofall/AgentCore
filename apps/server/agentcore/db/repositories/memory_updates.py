"""Memory-update data access (记忆更新对话内可见: Agent记忆与知识系统 §1.6 实时提示).

One row per offline consolidation pass that actually changed a memory file, anchored to
the conversation that triggered it. Backs the conversation-tail「记忆已更新」card (read
projection on the latest messages window) and the live firehose push.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import MemoryUpdateRow


class MemoryUpdateRepository:
    """CRUD for ``memory_updates`` — a conversation's offline-consolidation results.

    Append-only in practice (one row per changed pass); never updated in place. Keyed by
    its own uuid, scoped/queried by ``conversation_id``. ``items`` is the applied summary
    the card renders (shape owned by ``memory/maintenance.py``).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        conversation_id: str,
        user_id: str,
        items: list[dict],
        kind: str = "semantic",
        summary: str | None = None,
        anchor_at: datetime | None = None,
    ) -> MemoryUpdateRow:
        """Persist one memory-write notice and return the stored row (with id/created_at).

        ``kind`` is ``episodic`` (session summary tip), ``semantic`` (diff card / explicit
        remember), or ``quota`` (always-pool / billing skip). Records ONLY real writes —
        callers never invent empty notices. Commits
        its own unit of work (offline pass / tool path, not a request transaction).

        ``anchor_at`` is the last consolidated message's ``created_at`` — where the card
        belongs in the thread, which ``created_at`` cannot say for a debounced pass.
        Callers with no message window (semantic sweep, quota) leave it None.
        """
        row = MemoryUpdateRow(
            conversation_id=conversation_id,
            user_id=user_id,
            items=items,
            kind=kind,
            summary=summary,
            anchor_at=anchor_at,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def list_for_conversation(
        self, conversation_id: str, *, limit: int = 20
    ) -> list[MemoryUpdateRow]:
        """A conversation's memory-update records, OLDEST-first (chronological tail order).

        Capped at ``limit`` most-recent rows (a marathon chat may consolidate several
        times), then returned oldest-first so the client appends them after the messages
        in time order. Backs the latest-window read projection (``MessageListResponse``).
        """
        result = await self._session.execute(
            select(MemoryUpdateRow)
            .where(MemoryUpdateRow.conversation_id == conversation_id)
            .order_by(MemoryUpdateRow.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def list_for_user(
        self, user_id: str, *, limit: int = 50
    ) -> list[MemoryUpdateRow]:
        """A user's memory-update records across ALL conversations, NEWEST-first.

        Backs the cross-conversation「记忆动态」feed (记忆编辑器「最近更新」视图,
        Agent记忆与知识系统 §1.6): the memory-write side is per-user long-term data, so a
        single chronological stream of "what the AI recently learned" cuts across every
        conversation — a question the per-conversation tail card cannot answer. Capped at
        ``limit`` most-recent rows; served by the ``(user_id, created_at)`` index.
        """
        result = await self._session.execute(
            select(MemoryUpdateRow)
            .where(MemoryUpdateRow.user_id == user_id)
            .order_by(MemoryUpdateRow.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def load_map(
        self, conversation_ids: Sequence[str]
    ) -> dict[str, list[MemoryUpdateRow]]:
        """Memory-update rows grouped by conversation_id for the ids given (no N+1).

        Reserved for a future multi-conversation surface (e.g. the「记忆动态」feed); the
        single-conversation read uses :meth:`list_for_conversation`.
        """
        if not conversation_ids:
            return {}
        result = await self._session.execute(
            select(MemoryUpdateRow)
            .where(MemoryUpdateRow.conversation_id.in_(conversation_ids))
            .order_by(MemoryUpdateRow.created_at.asc())
        )
        grouped: dict[str, list[MemoryUpdateRow]] = {}
        for row in result.scalars().all():
            grouped.setdefault(row.conversation_id, []).append(row)
        return grouped
