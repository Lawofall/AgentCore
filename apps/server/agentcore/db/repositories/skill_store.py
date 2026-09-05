"""Capability-store CRUD: listings, version snapshots, installs, reports."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models.skill_store import (
    SkillStoreInstall,
    SkillStoreListing,
    SkillStoreReport,
    SkillStoreVersion,
)
from agentcore.db.models.users import User
from agentcore.db.repositories._base import _ilike_pattern, commit_or_flush


class SkillStoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_listing(self, listing_id: str) -> SkillStoreListing | None:
        result = await self._session.execute(
            select(SkillStoreListing).where(SkillStoreListing.id == listing_id)
        )
        return result.scalar_one_or_none()

    async def get_listing_by_source(self, source_document_id: str) -> SkillStoreListing | None:
        result = await self._session.execute(
            select(SkillStoreListing).where(
                SkillStoreListing.source_document_id == source_document_id
            )
        )
        return result.scalar_one_or_none()

    async def get_version(self, version_id: str) -> SkillStoreVersion | None:
        result = await self._session.execute(
            select(SkillStoreVersion).where(SkillStoreVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    async def get_current_version(self, listing: SkillStoreListing) -> SkillStoreVersion | None:
        if not listing.current_version_id:
            return None
        return await self.get_version(listing.current_version_id)

    async def max_version_n(self, listing_id: str) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(SkillStoreVersion.version_n), 0)).where(
                SkillStoreVersion.listing_id == listing_id
            )
        )
        return int(result.scalar_one())

    async def create_listing(
        self,
        *,
        author_user_id: str,
        source_document_id: str,
        commit: bool = True,
    ) -> SkillStoreListing:
        row = SkillStoreListing(
            author_user_id=author_user_id,
            source_document_id=source_document_id,
            status="published",
        )
        self._session.add(row)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def add_version(
        self,
        *,
        listing: SkillStoreListing,
        name: str,
        description: str,
        content: str,
        commit: bool = True,
    ) -> SkillStoreVersion:
        next_n = await self.max_version_n(listing.id) + 1
        version = SkillStoreVersion(
            listing_id=listing.id,
            version_n=next_n,
            name=name,
            description=description,
            content=content,
        )
        self._session.add(version)
        await commit_or_flush(self._session, commit=False)
        listing.current_version_id = version.id
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(version)
        await self._session.refresh(listing)
        return version

    async def set_status(
        self, listing: SkillStoreListing, status: str, *, commit: bool = True
    ) -> SkillStoreListing:
        listing.status = status
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(listing)
        return listing

    async def list_shelf(
        self,
        *,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
        author_user_id: str | None = None,
        statuses: Sequence[str] | None = None,
    ) -> tuple[list[tuple[SkillStoreListing, SkillStoreVersion, User]], int]:
        """Paginated listings joined to current version + author."""
        stmt = (
            select(SkillStoreListing, SkillStoreVersion, User)
            .join(
                SkillStoreVersion,
                SkillStoreVersion.id == SkillStoreListing.current_version_id,
            )
            .join(User, User.user_id == SkillStoreListing.author_user_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(SkillStoreListing)
            .join(
                SkillStoreVersion,
                SkillStoreVersion.id == SkillStoreListing.current_version_id,
            )
            .join(User, User.user_id == SkillStoreListing.author_user_id)
        )
        if statuses is not None:
            stmt = stmt.where(SkillStoreListing.status.in_(list(statuses)))
            count_stmt = count_stmt.where(SkillStoreListing.status.in_(list(statuses)))
        if author_user_id is not None:
            stmt = stmt.where(SkillStoreListing.author_user_id == author_user_id)
            count_stmt = count_stmt.where(SkillStoreListing.author_user_id == author_user_id)
        needle = (q or "").strip()
        if needle:
            pattern = _ilike_pattern(needle)
            match = or_(
                SkillStoreVersion.name.ilike(pattern),
                SkillStoreVersion.description.ilike(pattern),
                User.display_name.ilike(pattern),
                User.username.ilike(pattern),
            )
            stmt = stmt.where(match)
            count_stmt = count_stmt.where(match)

        total = int((await self._session.execute(count_stmt)).scalar() or 0)
        offset = (page - 1) * page_size
        result = await self._session.execute(
            stmt.order_by(SkillStoreListing.created_at.desc()).limit(page_size).offset(offset)
        )
        rows = [(listing, version, author) for listing, version, author in result.all()]
        return rows, total

    async def get_install(self, user_id: str, listing_id: str) -> SkillStoreInstall | None:
        result = await self._session.execute(
            select(SkillStoreInstall).where(
                SkillStoreInstall.user_id == user_id,
                SkillStoreInstall.listing_id == listing_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_installs_for_user(self, user_id: str) -> list[SkillStoreInstall]:
        result = await self._session.execute(
            select(SkillStoreInstall)
            .where(SkillStoreInstall.user_id == user_id)
            .order_by(SkillStoreInstall.created_at.desc())
        )
        return list(result.scalars().all())

    async def installs_by_listing_ids(
        self, user_id: str, listing_ids: Sequence[str]
    ) -> dict[str, SkillStoreInstall]:
        if not listing_ids:
            return {}
        result = await self._session.execute(
            select(SkillStoreInstall).where(
                SkillStoreInstall.user_id == user_id,
                SkillStoreInstall.listing_id.in_(list(listing_ids)),
            )
        )
        return {row.listing_id: row for row in result.scalars().all()}

    async def upsert_install(
        self,
        *,
        user_id: str,
        listing_id: str,
        version_id: str,
        document_id: str,
        commit: bool = True,
    ) -> SkillStoreInstall:
        row = await self.get_install(user_id, listing_id)
        if row is None:
            row = SkillStoreInstall(
                user_id=user_id,
                listing_id=listing_id,
                version_id=version_id,
                document_id=document_id,
            )
            self._session.add(row)
        else:
            row.version_id = version_id
            row.document_id = document_id
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def get_report(self, user_id: str, listing_id: str) -> SkillStoreReport | None:
        result = await self._session.execute(
            select(SkillStoreReport).where(
                SkillStoreReport.user_id == user_id,
                SkillStoreReport.listing_id == listing_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_report(
        self,
        *,
        user_id: str,
        listing_id: str,
        reason: str,
        commit: bool = True,
    ) -> SkillStoreReport:
        row = await self.get_report(user_id, listing_id)
        if row is None:
            row = SkillStoreReport(user_id=user_id, listing_id=listing_id, reason=reason)
            self._session.add(row)
        else:
            row.reason = reason
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def list_reports(
        self, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[tuple[SkillStoreReport, SkillStoreListing, SkillStoreVersion | None]], int]:
        total = int(
            (
                await self._session.execute(select(func.count()).select_from(SkillStoreReport))
            ).scalar()
            or 0
        )
        offset = (page - 1) * page_size
        result = await self._session.execute(
            select(SkillStoreReport, SkillStoreListing, SkillStoreVersion)
            .join(SkillStoreListing, SkillStoreListing.id == SkillStoreReport.listing_id)
            .outerjoin(
                SkillStoreVersion,
                SkillStoreVersion.id == SkillStoreListing.current_version_id,
            )
            .order_by(SkillStoreReport.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        rows = [
            (report, listing, version) for report, listing, version in result.all()
        ]
        return rows, total

    async def delete_all_for_user(self, user_id: str, *, commit: bool = True) -> None:
        """注销 cascade: this user's listings / installs / reports (copies stay)."""
        authored_ids = select(SkillStoreListing.id).where(
            SkillStoreListing.author_user_id == user_id
        )
        await self._session.execute(
            delete(SkillStoreReport).where(
                or_(
                    SkillStoreReport.user_id == user_id,
                    SkillStoreReport.listing_id.in_(authored_ids),
                )
            )
        )
        await self._session.execute(
            delete(SkillStoreInstall).where(
                or_(
                    SkillStoreInstall.user_id == user_id,
                    SkillStoreInstall.listing_id.in_(authored_ids),
                )
            )
        )
        await self._session.execute(
            delete(SkillStoreVersion).where(SkillStoreVersion.listing_id.in_(authored_ids))
        )
        await self._session.execute(
            delete(SkillStoreListing).where(SkillStoreListing.author_user_id == user_id)
        )
        await commit_or_flush(self._session, commit=commit)
