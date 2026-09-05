"""Skill-slot overlay CRUD: account (folder_id NULL) and folder layers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from agentcore.db.models.skill_slots import SkillSlotMute, SkillSlotReplacement
from agentcore.db.repositories._base import commit_or_flush


def _folder_clause(column: Any, folder_id: str | None) -> ColumnElement[bool]:
    if folder_id is None:
        return column.is_(None)
    return column == folder_id


class SkillSlotRepository:
    """Owner-scoped 换用 rows. One slot name per (user, folder-or-account)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_scope(
        self, user_id: str, folder_id: str | None = None
    ) -> list[SkillSlotReplacement]:
        result = await self._session.execute(
            select(SkillSlotReplacement)
            .where(
                SkillSlotReplacement.user_id == user_id,
                _folder_clause(SkillSlotReplacement.folder_id, folder_id),
            )
            .order_by(SkillSlotReplacement.slot_name.asc())
        )
        return list(result.scalars().all())

    async def list_for_user(self, user_id: str) -> list[SkillSlotReplacement]:
        """Account layer only (``folder_id IS NULL``)."""
        return await self.list_for_scope(user_id, None)

    async def get(
        self, user_id: str, slot_name: str, folder_id: str | None = None
    ) -> SkillSlotReplacement | None:
        result = await self._session.execute(
            select(SkillSlotReplacement).where(
                SkillSlotReplacement.user_id == user_id,
                SkillSlotReplacement.slot_name == slot_name,
                _folder_clause(SkillSlotReplacement.folder_id, folder_id),
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: str,
        slot_name: str,
        document_id: str,
        folder_id: str | None = None,
        commit: bool = True,
    ) -> SkillSlotReplacement:
        row = await self.get(user_id, slot_name, folder_id)
        if row is None:
            row = SkillSlotReplacement(
                user_id=user_id,
                folder_id=folder_id,
                slot_name=slot_name,
                document_id=document_id,
            )
            self._session.add(row)
        else:
            row.document_id = document_id
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def delete(
        self,
        *,
        user_id: str,
        slot_name: str,
        folder_id: str | None = None,
        commit: bool = True,
    ) -> bool:
        row = await self.get(user_id, slot_name, folder_id)
        if row is None:
            return False
        await self._session.delete(row)
        await commit_or_flush(self._session, commit=commit)
        return True

    async def delete_all_for_user(self, user_id: str, *, commit: bool = True) -> None:
        """注销 cascade: account + folder overlay rows for this user."""
        await self._session.execute(
            delete(SkillSlotReplacement).where(SkillSlotReplacement.user_id == user_id)
        )
        await commit_or_flush(self._session, commit=commit)


class SkillMuteRepository:
    """Owner-scoped 藏起 rows. One slot name per (user, folder-or-account)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_scope(
        self, user_id: str, folder_id: str | None = None
    ) -> list[SkillSlotMute]:
        result = await self._session.execute(
            select(SkillSlotMute)
            .where(
                SkillSlotMute.user_id == user_id,
                _folder_clause(SkillSlotMute.folder_id, folder_id),
            )
            .order_by(SkillSlotMute.slot_name.asc())
        )
        return list(result.scalars().all())

    async def list_for_user(self, user_id: str) -> list[SkillSlotMute]:
        """Account layer only (``folder_id IS NULL``)."""
        return await self.list_for_scope(user_id, None)

    async def get(
        self, user_id: str, slot_name: str, folder_id: str | None = None
    ) -> SkillSlotMute | None:
        result = await self._session.execute(
            select(SkillSlotMute).where(
                SkillSlotMute.user_id == user_id,
                SkillSlotMute.slot_name == slot_name,
                _folder_clause(SkillSlotMute.folder_id, folder_id),
            )
        )
        return result.scalar_one_or_none()

    async def add(
        self,
        *,
        user_id: str,
        slot_name: str,
        folder_id: str | None = None,
        commit: bool = True,
    ) -> SkillSlotMute:
        row = await self.get(user_id, slot_name, folder_id)
        if row is None:
            row = SkillSlotMute(
                user_id=user_id, folder_id=folder_id, slot_name=slot_name
            )
            self._session.add(row)
            await commit_or_flush(self._session, commit=commit)
            await self._session.refresh(row)
        return row

    async def delete(
        self,
        *,
        user_id: str,
        slot_name: str,
        folder_id: str | None = None,
        commit: bool = True,
    ) -> bool:
        row = await self.get(user_id, slot_name, folder_id)
        if row is None:
            return False
        await self._session.delete(row)
        await commit_or_flush(self._session, commit=commit)
        return True

    async def delete_all_for_user(self, user_id: str, *, commit: bool = True) -> None:
        """注销 cascade: account + folder mute rows for this user."""
        await self._session.execute(
            delete(SkillSlotMute).where(SkillSlotMute.user_id == user_id)
        )
        await commit_or_flush(self._session, commit=commit)
