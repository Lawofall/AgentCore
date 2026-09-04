"""Folder collaboration-desk membership (folder_members). Independent of IM."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import Folder, FolderMember


class FolderMemberRepository:
    """Membership rows only — authorization lives in FolderDeskService."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_member(self, folder_id: str, user_id: str) -> FolderMember | None:
        result = await self._session.execute(
            select(FolderMember).where(
                FolderMember.folder_id == folder_id,
                FolderMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_members(self, folder_id: str) -> Sequence[FolderMember]:
        result = await self._session.execute(
            select(FolderMember)
            .where(FolderMember.folder_id == folder_id)
            .order_by(FolderMember.joined_at.asc())
        )
        return result.scalars().all()

    async def count_members(self, folder_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(FolderMember)
            .where(FolderMember.folder_id == folder_id)
        )
        return int(result.scalar_one())

    async def add_member(
        self,
        *,
        folder_id: str,
        user_id: str,
        role: str,
        state: str,
        invited_by: str | None,
    ) -> FolderMember:
        member = FolderMember(
            folder_id=folder_id,
            user_id=user_id,
            role=role,
            state=state,
            invited_by=invited_by,
        )
        self._session.add(member)
        await self._session.commit()
        await self._session.refresh(member)
        return member

    async def set_member_state(self, folder_id: str, user_id: str, *, state: str) -> None:
        await self._session.execute(
            update(FolderMember)
            .where(
                FolderMember.folder_id == folder_id,
                FolderMember.user_id == user_id,
            )
            .values(state=state)
        )
        await self._session.commit()

    async def set_member_role(self, folder_id: str, user_id: str, *, role: str) -> None:
        await self._session.execute(
            update(FolderMember)
            .where(
                FolderMember.folder_id == folder_id,
                FolderMember.user_id == user_id,
            )
            .values(role=role)
        )
        await self._session.commit()

    async def remove_member(self, folder_id: str, user_id: str) -> None:
        await self._session.execute(
            delete(FolderMember).where(
                FolderMember.folder_id == folder_id,
                FolderMember.user_id == user_id,
            )
        )
        await self._session.commit()

    async def list_for_user(
        self, user_id: str, *, state: str = "accepted"
    ) -> Sequence[tuple[Folder, FolderMember]]:
        result = await self._session.execute(
            select(Folder, FolderMember)
            .join(FolderMember, FolderMember.folder_id == Folder.id)
            .where(
                FolderMember.user_id == user_id,
                FolderMember.state == state,
                Folder.deleted_at.is_(None),
            )
            .order_by(Folder.updated_at.desc())
        )
        return [(folder, member) for folder, member in result.all()]

    async def list_pending_for_user(
        self, user_id: str
    ) -> Sequence[tuple[Folder, FolderMember]]:
        return await self.list_for_user(user_id, state="pending")

    async def delete_pending_between(self, user_a: str, user_b: str) -> int:
        """Auto-reject pending invites between a blocked pair; do not kick members."""
        result = await self._session.execute(
            select(FolderMember).where(
                FolderMember.state == "pending",
                (
                    (
                        (FolderMember.user_id == user_a)
                        & (FolderMember.invited_by == user_b)
                    )
                    | (
                        (FolderMember.user_id == user_b)
                        & (FolderMember.invited_by == user_a)
                    )
                ),
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            await self._session.delete(row)
        if rows:
            await self._session.commit()
        return len(rows)

    async def delete_all_memberships_for_user(self, user_id: str) -> Sequence[str]:
        result = await self._session.execute(
            select(FolderMember.folder_id).where(FolderMember.user_id == user_id)
        )
        folder_ids = list(result.scalars().all())
        await self._session.execute(
            delete(FolderMember).where(FolderMember.user_id == user_id)
        )
        await self._session.commit()
        return folder_ids

    async def delete_memberships_for_folders(self, folder_ids: Sequence[str]) -> int:
        if not folder_ids:
            return 0
        result = await self._session.execute(
            delete(FolderMember).where(FolderMember.folder_id.in_(list(folder_ids)))
        )
        await self._session.commit()
        return int(cast("CursorResult[Any]", result).rowcount or 0)
