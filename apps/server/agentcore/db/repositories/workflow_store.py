"""Workflow-store CRUD: listings, version snapshots, installs, reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models.users import User
from agentcore.db.models.workflow_store import (
    WorkflowStoreInstall,
    WorkflowStoreListing,
    WorkflowStoreReport,
    WorkflowStoreVersion,
)
from agentcore.db.repositories._base import _ilike_pattern, commit_or_flush


class WorkflowStoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_listing(self, listing_id: str) -> WorkflowStoreListing | None:
        result = await self._session.execute(
            select(WorkflowStoreListing).where(WorkflowStoreListing.id == listing_id)
        )
        return result.scalar_one_or_none()

    async def get_listing_by_source(self, source_workflow_id: str) -> WorkflowStoreListing | None:
        result = await self._session.execute(
            select(WorkflowStoreListing).where(
                WorkflowStoreListing.source_workflow_id == source_workflow_id
            )
        )
        return result.scalar_one_or_none()

    async def get_version(self, version_id: str) -> WorkflowStoreVersion | None:
        result = await self._session.execute(
            select(WorkflowStoreVersion).where(WorkflowStoreVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    async def get_current_version(
        self, listing: WorkflowStoreListing
    ) -> WorkflowStoreVersion | None:
        if not listing.current_version_id:
            return None
        return await self.get_version(listing.current_version_id)

    async def max_version_n(self, listing_id: str) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(WorkflowStoreVersion.version_n), 0)).where(
                WorkflowStoreVersion.listing_id == listing_id
            )
        )
        return int(result.scalar_one())

    async def create_listing(
        self,
        *,
        author_user_id: str,
        source_workflow_id: str,
        commit: bool = True,
    ) -> WorkflowStoreListing:
        row = WorkflowStoreListing(
            author_user_id=author_user_id,
            source_workflow_id=source_workflow_id,
            status="published",
        )
        self._session.add(row)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def add_version(
        self,
        *,
        listing: WorkflowStoreListing,
        name: str,
        description: str,
        definition: Mapping[str, Any],
        commit: bool = True,
    ) -> WorkflowStoreVersion:
        next_n = await self.max_version_n(listing.id) + 1
        version = WorkflowStoreVersion(
            listing_id=listing.id,
            version_n=next_n,
            name=name,
            description=description,
            definition=dict(definition),
        )
        self._session.add(version)
        await commit_or_flush(self._session, commit=False)
        listing.current_version_id = version.id
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(version)
        await self._session.refresh(listing)
        return version

    async def set_status(
        self, listing: WorkflowStoreListing, status: str, *, commit: bool = True
    ) -> WorkflowStoreListing:
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
    ) -> tuple[list[tuple[WorkflowStoreListing, WorkflowStoreVersion, User]], int]:
        """Paginated listings joined to current version + author."""
        stmt = (
            select(WorkflowStoreListing, WorkflowStoreVersion, User)
            .join(
                WorkflowStoreVersion,
                WorkflowStoreVersion.id == WorkflowStoreListing.current_version_id,
            )
            .join(User, User.user_id == WorkflowStoreListing.author_user_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(WorkflowStoreListing)
            .join(
                WorkflowStoreVersion,
                WorkflowStoreVersion.id == WorkflowStoreListing.current_version_id,
            )
            .join(User, User.user_id == WorkflowStoreListing.author_user_id)
        )
        if statuses is not None:
            stmt = stmt.where(WorkflowStoreListing.status.in_(list(statuses)))
            count_stmt = count_stmt.where(WorkflowStoreListing.status.in_(list(statuses)))
        if author_user_id is not None:
            stmt = stmt.where(WorkflowStoreListing.author_user_id == author_user_id)
            count_stmt = count_stmt.where(
                WorkflowStoreListing.author_user_id == author_user_id
            )
        needle = (q or "").strip()
        if needle:
            pattern = _ilike_pattern(needle)
            match = or_(
                WorkflowStoreVersion.name.ilike(pattern),
                WorkflowStoreVersion.description.ilike(pattern),
                User.display_name.ilike(pattern),
                User.username.ilike(pattern),
            )
            stmt = stmt.where(match)
            count_stmt = count_stmt.where(match)

        total = int((await self._session.execute(count_stmt)).scalar() or 0)
        offset = (page - 1) * page_size
        result = await self._session.execute(
            stmt.order_by(WorkflowStoreListing.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        rows = [(listing, version, author) for listing, version, author in result.all()]
        return rows, total

    async def get_install(self, user_id: str, listing_id: str) -> WorkflowStoreInstall | None:
        result = await self._session.execute(
            select(WorkflowStoreInstall).where(
                WorkflowStoreInstall.user_id == user_id,
                WorkflowStoreInstall.listing_id == listing_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_installs_for_user(self, user_id: str) -> list[WorkflowStoreInstall]:
        result = await self._session.execute(
            select(WorkflowStoreInstall)
            .where(WorkflowStoreInstall.user_id == user_id)
            .order_by(WorkflowStoreInstall.created_at.desc())
        )
        return list(result.scalars().all())

    async def installs_by_listing_ids(
        self, user_id: str, listing_ids: Sequence[str]
    ) -> dict[str, WorkflowStoreInstall]:
        if not listing_ids:
            return {}
        result = await self._session.execute(
            select(WorkflowStoreInstall).where(
                WorkflowStoreInstall.user_id == user_id,
                WorkflowStoreInstall.listing_id.in_(list(listing_ids)),
            )
        )
        return {row.listing_id: row for row in result.scalars().all()}

    async def upsert_install(
        self,
        *,
        user_id: str,
        listing_id: str,
        version_id: str,
        workflow_id: str,
        commit: bool = True,
    ) -> WorkflowStoreInstall:
        row = await self.get_install(user_id, listing_id)
        if row is None:
            row = WorkflowStoreInstall(
                user_id=user_id,
                listing_id=listing_id,
                version_id=version_id,
                workflow_id=workflow_id,
            )
            self._session.add(row)
        else:
            row.version_id = version_id
            row.workflow_id = workflow_id
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def get_report(self, user_id: str, listing_id: str) -> WorkflowStoreReport | None:
        result = await self._session.execute(
            select(WorkflowStoreReport).where(
                WorkflowStoreReport.user_id == user_id,
                WorkflowStoreReport.listing_id == listing_id,
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
    ) -> WorkflowStoreReport:
        row = await self.get_report(user_id, listing_id)
        if row is None:
            row = WorkflowStoreReport(user_id=user_id, listing_id=listing_id, reason=reason)
            self._session.add(row)
        else:
            row.reason = reason
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def list_reports(
        self, *, page: int = 1, page_size: int = 20
    ) -> tuple[
        list[tuple[WorkflowStoreReport, WorkflowStoreListing, WorkflowStoreVersion | None]],
        int,
    ]:
        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(WorkflowStoreReport)
                )
            ).scalar()
            or 0
        )
        offset = (page - 1) * page_size
        result = await self._session.execute(
            select(WorkflowStoreReport, WorkflowStoreListing, WorkflowStoreVersion)
            .join(WorkflowStoreListing, WorkflowStoreListing.id == WorkflowStoreReport.listing_id)
            .outerjoin(
                WorkflowStoreVersion,
                WorkflowStoreVersion.id == WorkflowStoreListing.current_version_id,
            )
            .order_by(WorkflowStoreReport.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        rows = [
            (report, listing, version) for report, listing, version in result.all()
        ]
        return rows, total

    async def delete_all_for_user(self, user_id: str, *, commit: bool = True) -> None:
        """注销 cascade: this user's listings / installs / reports (copies stay)."""
        authored_ids = select(WorkflowStoreListing.id).where(
            WorkflowStoreListing.author_user_id == user_id
        )
        await self._session.execute(
            delete(WorkflowStoreReport).where(
                or_(
                    WorkflowStoreReport.user_id == user_id,
                    WorkflowStoreReport.listing_id.in_(authored_ids),
                )
            )
        )
        await self._session.execute(
            delete(WorkflowStoreInstall).where(
                or_(
                    WorkflowStoreInstall.user_id == user_id,
                    WorkflowStoreInstall.listing_id.in_(authored_ids),
                )
            )
        )
        await self._session.execute(
            delete(WorkflowStoreVersion).where(
                WorkflowStoreVersion.listing_id.in_(authored_ids)
            )
        )
        await self._session.execute(
            delete(WorkflowStoreListing).where(WorkflowStoreListing.author_user_id == user_id)
        )
        await commit_or_flush(self._session, commit=commit)
