"""Creation-tool 文档 data access (folder-hung; not memory ``documents``).

Reads join a live folder and the desk-visibility clause so owners and accepted
members see the same list. Writes do not check role — routes call
``resolve_desk_access`` first.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import Doc, Folder, FolderMember
from agentcore.db.repositories._base import commit_or_flush
from agentcore.db.repositories._desk_visibility import folder_accessible_clause
from agentcore.doc.body import empty_body, sanitize_body


class DocRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        user_id: str,
        folder_id: str,
        title: str,
    ) -> Doc:
        doc = Doc(
            user_id=user_id,
            folder_id=folder_id,
            title=title,
            body=empty_body(),
            version=1,
        )
        self._session.add(doc)
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def get_live(self, doc_id: str) -> Doc | None:
        result = await self._session.execute(
            select(Doc).where(Doc.id == doc_id, Doc.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_visible(
        self, user_id: str, *, folder_id: str | None = None
    ) -> Sequence[tuple[Doc, str, bool]]:
        """Live docs on live folders the caller can access, newest first.

        Each row is ``(doc, folder_name, can_write)``.
        """
        writable_member = exists().where(
            FolderMember.folder_id == Folder.id,
            FolderMember.user_id == user_id,
            FolderMember.state == "accepted",
            FolderMember.role == "editor",
        )
        can_write = or_(Folder.user_id == user_id, writable_member)
        stmt = (
            select(Doc, Folder.name, can_write)
            .join(Folder, Folder.id == Doc.folder_id)
            .where(
                Doc.deleted_at.is_(None),
                Folder.deleted_at.is_(None),
                Folder.local_root_id.is_(None),
                folder_accessible_clause(user_id),
            )
        )
        if folder_id is not None:
            stmt = stmt.where(Doc.folder_id == folder_id)
        stmt = stmt.order_by(Doc.updated_at.desc())
        result = await self._session.execute(stmt)
        return [(row[0], row[1], bool(row[2])) for row in result.all()]

    async def update_meta(self, doc: Doc, *, title: str | None = None) -> Doc:
        if title is not None:
            doc.title = title
        await self._session.commit()
        await self._session.refresh(doc)
        return doc

    async def save_body(
        self,
        doc: Doc,
        *,
        body: dict,
        baseline: int | None,
    ) -> tuple[Doc, bool]:
        """CAS-write the block list. ``(doc, True)`` = conflict, live row untouched."""
        if baseline is not None and baseline != doc.version:
            return doc, True
        doc.body = sanitize_body(body)
        doc.version += 1
        await self._session.commit()
        await self._session.refresh(doc)
        return doc, False

    async def soft_delete(self, doc: Doc) -> None:
        doc.deleted_at = datetime.now(UTC)
        await self._session.commit()

    async def soft_delete_on_owned_folders(self, user_id: str) -> None:
        """注销：hide docs sitting on folders this user owns. Others' desks keep theirs."""
        owned = select(Folder.id).where(Folder.user_id == user_id)
        await self._session.execute(
            update(Doc)
            .where(Doc.deleted_at.is_(None), Doc.folder_id.in_(owned))
            .values(deleted_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def hard_delete_for_folders(
        self, folder_ids: Sequence[str], *, commit: bool = True
    ) -> None:
        """Physically remove docs hung on these desks (permanent folder wipe)."""
        ids = [fid for fid in folder_ids if fid]
        if not ids:
            return
        await self._session.execute(delete(Doc).where(Doc.folder_id.in_(ids)))
        await commit_or_flush(self._session, commit=commit)
