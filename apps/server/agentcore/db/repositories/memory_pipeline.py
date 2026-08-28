"""Data access for consolidation-pipeline tables (episodes + per-scope state)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models.memory_pipeline import MemoryEpisode, MemoryScopeState
from agentcore.db.repositories._base import commit_or_flush


def _folder_match(column, folder_id: str | None):
    return column.is_(None) if folder_id is None else column == folder_id


class MemoryPipelineRepository:
    """CRUD for ``memory_episodes`` / ``memory_scope_states``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_episode(
        self,
        *,
        episode_id: str,
        user_id: str,
        folder_id: str | None,
        conversation_id: str,
        summary: str,
        actions_json: str = "",
        created_at: datetime | None = None,
        digested_at: datetime | None = None,
        commit: bool = True,
    ) -> MemoryEpisode:
        row = MemoryEpisode(
            id=episode_id,
            user_id=user_id,
            folder_id=folder_id,
            conversation_id=conversation_id,
            summary=summary,
            actions_json=actions_json or "",
            created_at=created_at or datetime.now(UTC),
            digested_at=digested_at,
        )
        self._session.add(row)
        await commit_or_flush(self._session, commit=commit)
        if commit:
            await self._session.refresh(row)
        return row

    async def get_episode(self, episode_id: str) -> MemoryEpisode | None:
        result = await self._session.execute(
            select(MemoryEpisode).where(MemoryEpisode.id == episode_id)
        )
        return result.scalars().first()

    async def list_undigested(
        self, user_id: str, folder_id: str | None
    ) -> list[MemoryEpisode]:
        result = await self._session.execute(
            select(MemoryEpisode)
            .where(
                MemoryEpisode.user_id == user_id,
                _folder_match(MemoryEpisode.folder_id, folder_id),
                MemoryEpisode.digested_at.is_(None),
            )
            .order_by(MemoryEpisode.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_undigested_scope_targets(
        self, *, limit: int = 100
    ) -> list[tuple[str, str | None, str]]:
        """Distinct (user_id, folder_id, latest-episode conversation_id) still undigested.

        Newest episode per scope wins so the leak-scan card (if any) lands on the
        conversation the user last settled. First-seen after ``created_at DESC``.
        """
        if limit <= 0:
            return []
        result = await self._session.execute(
            select(
                MemoryEpisode.user_id,
                MemoryEpisode.folder_id,
                MemoryEpisode.conversation_id,
            )
            .where(MemoryEpisode.digested_at.is_(None))
            .order_by(MemoryEpisode.created_at.desc())
            .limit(max(limit * 8, limit))
        )
        seen: set[tuple[str, str | None]] = set()
        out: list[tuple[str, str | None, str]] = []
        for user_id, folder_id, conversation_id in result.all():
            key = (user_id, folder_id)
            if key in seen:
                continue
            seen.add(key)
            out.append((user_id, folder_id, conversation_id))
            if len(out) >= limit:
                break
        return out

    async def mark_digested(
        self,
        user_id: str,
        folder_id: str | None,
        episode_ids: list[str],
        *,
        digested_at: datetime | None = None,
        commit: bool = True,
    ) -> int:
        if not episode_ids:
            return 0
        stamp = digested_at or datetime.now(UTC)
        result = await self._session.execute(
            update(MemoryEpisode)
            .where(
                MemoryEpisode.user_id == user_id,
                _folder_match(MemoryEpisode.folder_id, folder_id),
                MemoryEpisode.id.in_(episode_ids),
                MemoryEpisode.digested_at.is_(None),
            )
            .values(digested_at=stamp)
        )
        await commit_or_flush(self._session, commit=commit)
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    async def purge_digested_older_than(
        self,
        *,
        older_than_days: int = 30,
        user_id: str | None = None,
        commit: bool = True,
    ) -> int:
        """Hard-delete digested episodes older than the retention window."""
        if older_than_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        conditions = [
            MemoryEpisode.digested_at.is_not(None),
            MemoryEpisode.digested_at < cutoff,
        ]
        if user_id is not None:
            conditions.append(MemoryEpisode.user_id == user_id)
        result = await self._session.execute(delete(MemoryEpisode).where(and_(*conditions)))
        await commit_or_flush(self._session, commit=commit)
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    async def get_scope_state(
        self, user_id: str, folder_id: str | None
    ) -> MemoryScopeState | None:
        result = await self._session.execute(
            select(MemoryScopeState).where(
                MemoryScopeState.user_id == user_id,
                _folder_match(MemoryScopeState.folder_id, folder_id),
            )
        )
        return result.scalars().first()

    async def upsert_scope_state(
        self,
        user_id: str,
        folder_id: str | None,
        *,
        last_semantic_at: datetime | None | object = ...,
        explore_workspace_key: str | None | object = ...,
        explore_fingerprint: str | None | object = ...,
        explore_fingerprint_dirty: bool | object = ...,
        commit: bool = True,
    ) -> MemoryScopeState:
        """Create or update one scope state via ``ON CONFLICT`` (no read-then-write race).

        ``...`` means leave the field unchanged on conflict (insert uses column defaults /
        NULL / false). Explicit values overwrite on conflict.
        """
        now = datetime.now(UTC)
        insert_values: dict[str, Any] = {
            "id": str(uuid4()),
            "user_id": user_id,
            "folder_id": folder_id,
            "last_semantic_at": None if last_semantic_at is ... else last_semantic_at,
            "explore_workspace_key": (
                None if explore_workspace_key is ... else explore_workspace_key
            ),
            "explore_fingerprint": (
                None if explore_fingerprint is ... else explore_fingerprint
            ),
            "explore_fingerprint_dirty": (
                False
                if explore_fingerprint_dirty is ...
                else bool(explore_fingerprint_dirty)
            ),
            "updated_at": now,
        }
        update_set: dict[str, Any] = {"updated_at": now}
        if last_semantic_at is not ...:
            update_set["last_semantic_at"] = last_semantic_at
        if explore_workspace_key is not ...:
            update_set["explore_workspace_key"] = explore_workspace_key
        if explore_fingerprint is not ...:
            update_set["explore_fingerprint"] = explore_fingerprint
        if explore_fingerprint_dirty is not ...:
            update_set["explore_fingerprint_dirty"] = bool(explore_fingerprint_dirty)

        stmt = (
            pg_insert(MemoryScopeState)
            .values(**insert_values)
            .on_conflict_do_update(
                index_elements=["user_id", "folder_id"],
                set_=update_set,
            )
            .returning(MemoryScopeState)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        await commit_or_flush(self._session, commit=commit)
        if commit:
            await self._session.refresh(row)
        return row

    async def merge_scope_state_fill_empty(
        self,
        user_id: str,
        folder_id: str | None,
        *,
        last_semantic_at: datetime | None = None,
        explore_workspace_key: str | None = None,
        explore_fingerprint: str | None = None,
        explore_fingerprint_dirty: bool = False,
        commit: bool = True,
    ) -> MemoryScopeState:
        """Insert or coalesce-fill empty fields only (legacy backfill merge)."""
        now = datetime.now(UTC)
        excluded = pg_insert(MemoryScopeState).excluded
        stmt = (
            pg_insert(MemoryScopeState)
            .values(
                id=str(uuid4()),
                user_id=user_id,
                folder_id=folder_id,
                last_semantic_at=last_semantic_at,
                explore_workspace_key=explore_workspace_key,
                explore_fingerprint=explore_fingerprint,
                explore_fingerprint_dirty=bool(explore_fingerprint_dirty),
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "folder_id"],
                set_={
                    "last_semantic_at": func.coalesce(
                        MemoryScopeState.last_semantic_at, excluded.last_semantic_at
                    ),
                    "explore_workspace_key": func.coalesce(
                        MemoryScopeState.explore_workspace_key,
                        excluded.explore_workspace_key,
                    ),
                    "explore_fingerprint": func.coalesce(
                        MemoryScopeState.explore_fingerprint, excluded.explore_fingerprint
                    ),
                    "explore_fingerprint_dirty": (
                        MemoryScopeState.explore_fingerprint_dirty
                        | excluded.explore_fingerprint_dirty
                    ),
                    "updated_at": now,
                },
            )
            .returning(MemoryScopeState)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        await commit_or_flush(self._session, commit=commit)
        if commit:
            await self._session.refresh(row)
        return row

    async def episode_exists(self, episode_id: str) -> bool:
        result = await self._session.execute(
            select(MemoryEpisode.id).where(MemoryEpisode.id == episode_id).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_scope_keys_with_state(self, user_id: str) -> list[str | None]:
        """folder_id values that already have a scope-state row (None = global)."""
        result = await self._session.execute(
            select(MemoryScopeState.folder_id).where(MemoryScopeState.user_id == user_id)
        )
        return list(result.scalars().all())
