"""Public read-only 文档 shares (frozen block-list snapshot)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models import Doc, DocShare, Folder
from agentcore.db.repositories._base import commit_or_flush


class DocShareRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        doc_id: str,
        user_id: str,
        title: str,
        snapshot: dict,
        expires_at: datetime | None = None,
    ) -> DocShare:
        share = DocShare(
            id=new_id(),
            doc_id=doc_id,
            user_id=user_id,
            title=title,
            snapshot=snapshot,
            expires_at=expires_at,
        )
        self._session.add(share)
        await self._session.commit()
        await self._session.refresh(share)
        return share

    async def get_active(self, token: str) -> DocShare | None:
        """Un-revoked, unexpired share by public token, or None (404, no leak)."""
        result = await self._session.execute(
            select(DocShare).where(
                DocShare.id == token,
                DocShare.revoked_at.is_(None),
                or_(
                    DocShare.expires_at.is_(None),
                    DocShare.expires_at > datetime.now(UTC),
                ),
            )
        )
        return result.scalar_one_or_none()

    async def list_active_for_doc(self, doc_id: str) -> Sequence[DocShare]:
        """Live shares for a doc, newest first (desk can_write already gated)."""
        result = await self._session.execute(
            select(DocShare)
            .where(
                DocShare.doc_id == doc_id,
                DocShare.revoked_at.is_(None),
                or_(
                    DocShare.expires_at.is_(None),
                    DocShare.expires_at > datetime.now(UTC),
                ),
            )
            .order_by(DocShare.created_at.desc())
        )
        return result.scalars().all()

    async def revoke(self, share_id: str, *, doc_id: str) -> bool:
        result = await self._session.execute(
            update(DocShare)
            .where(
                DocShare.id == share_id,
                DocShare.doc_id == doc_id,
                DocShare.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()
        return bool(cast("CursorResult[Any]", result).rowcount or 0)

    async def revoke_all_for_doc(self, doc_id: str, *, commit: bool = True) -> int:
        result = await self._session.execute(
            update(DocShare)
            .where(DocShare.doc_id == doc_id, DocShare.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await commit_or_flush(self._session, commit=commit)
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    async def revoke_all_for_folder_ids(
        self, folder_ids: Sequence[str], *, commit: bool = True
    ) -> None:
        ids = [fid for fid in folder_ids if fid]
        if not ids:
            return
        doc_ids = select(Doc.id).where(Doc.folder_id.in_(ids))
        await self._session.execute(
            update(DocShare)
            .where(DocShare.doc_id.in_(doc_ids), DocShare.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await commit_or_flush(self._session, commit=commit)

    async def revoke_all_on_owned_folders(self, user_id: str) -> None:
        """注销：kill public links of docs sitting on folders this user owns."""
        owned = select(Folder.id).where(Folder.user_id == user_id)
        doc_ids = select(Doc.id).where(Doc.folder_id.in_(owned))
        await self._session.execute(
            update(DocShare)
            .where(DocShare.doc_id.in_(doc_ids), DocShare.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke every live 文档 share this user minted (incl. on others' desks)."""
        result = await self._session.execute(
            update(DocShare)
            .where(DocShare.user_id == user_id, DocShare.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()
        return int(cast("CursorResult[Any]", result).rowcount or 0)
