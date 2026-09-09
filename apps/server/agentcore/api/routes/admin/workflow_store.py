"""Admin workflow-store queue: listings + reports + takedown."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.admin.audit import record_admin_audit
from agentcore.api.dependencies import AdminUser, get_db
from agentcore.db.repositories.users import UserRepository
from agentcore.db.repositories.workflow_store import WorkflowStoreRepository

router = APIRouter()


class AdminWorkflowStoreListingRow(BaseModel):
    id: str
    name: str
    description: str
    author: str
    author_user_id: str
    version_n: int
    status: str
    updated_at: datetime


class AdminWorkflowStoreListingDetail(AdminWorkflowStoreListingRow):
    definition: dict[str, Any]


class AdminWorkflowStoreListingList(BaseModel):
    data: list[AdminWorkflowStoreListingRow]
    total: int
    page: int
    page_size: int


class AdminWorkflowStoreReportRow(BaseModel):
    id: str
    listing_id: str
    listing_name: str
    listing_status: str
    user_id: str
    reporter: str
    reason: str
    created_at: datetime


class AdminWorkflowStoreReportList(BaseModel):
    data: list[AdminWorkflowStoreReportRow]
    total: int
    page: int
    page_size: int


def _store(session: AsyncSession = Depends(get_db)) -> WorkflowStoreRepository:
    return WorkflowStoreRepository(session)


def _author_label(display_name: str, username: str) -> str:
    name = (display_name or "").strip()
    return name or username


@router.get("/workflow-store/listings", response_model=AdminWorkflowStoreListingList)
async def admin_list_listings(
    _admin: AdminUser,
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store: WorkflowStoreRepository = Depends(_store),
) -> AdminWorkflowStoreListingList:
    statuses = (status,) if status in ("published", "unpublished", "taken_down") else None
    rows, total = await store.list_shelf(
        q=q, page=page, page_size=page_size, statuses=statuses
    )
    return AdminWorkflowStoreListingList(
        data=[
            AdminWorkflowStoreListingRow(
                id=listing.id,
                name=version.name,
                description=version.description,
                author=_author_label(author.display_name, author.username),
                author_user_id=listing.author_user_id,
                version_n=version.version_n,
                status=listing.status,
                updated_at=listing.updated_at,
            )
            for listing, version, author in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/workflow-store/listings/{listing_id}",
    response_model=AdminWorkflowStoreListingDetail,
)
async def admin_get_listing(
    listing_id: str,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    store: WorkflowStoreRepository = Depends(_store),
) -> AdminWorkflowStoreListingDetail:
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    author_name = _author_label(author.display_name, author.username) if author else ""
    return AdminWorkflowStoreListingDetail(
        id=listing.id,
        name=version.name,
        description=version.description,
        author=author_name,
        author_user_id=listing.author_user_id,
        version_n=version.version_n,
        status=listing.status,
        updated_at=listing.updated_at,
        definition=version.definition,
    )


@router.get("/workflow-store/reports", response_model=AdminWorkflowStoreReportList)
async def admin_list_reports(
    _admin: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    store: WorkflowStoreRepository = Depends(_store),
) -> AdminWorkflowStoreReportList:
    rows, total = await store.list_reports(page=page, page_size=page_size)
    users = UserRepository(session)
    data: list[AdminWorkflowStoreReportRow] = []
    for report, listing, version in rows:
        reporter = await users.get_by_id(report.user_id)
        data.append(
            AdminWorkflowStoreReportRow(
                id=report.id,
                listing_id=listing.id,
                listing_name=version.name if version is not None else "",
                listing_status=listing.status,
                user_id=report.user_id,
                reporter=_author_label(reporter.display_name, reporter.username)
                if reporter is not None
                else "",
                reason=report.reason,
                created_at=report.created_at,
            )
        )
    return AdminWorkflowStoreReportList(
        data=data,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/workflow-store/listings/{listing_id}/takedown",
    response_model=AdminWorkflowStoreListingRow,
)
async def admin_takedown_listing(
    listing_id: str,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    store: WorkflowStoreRepository = Depends(_store),
) -> AdminWorkflowStoreListingRow:
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    listing = await store.set_status(listing, "taken_down")
    await record_admin_audit(
        session,
        actor_id=admin.user_id,
        action="workflow_store.takedown",
        target_type="workflow_store_listing",
        target_id=listing.id,
    )
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    author_name = _author_label(author.display_name, author.username) if author else ""
    return AdminWorkflowStoreListingRow(
        id=listing.id,
        name=version.name,
        description=version.description,
        author=author_name,
        author_user_id=listing.author_user_id,
        version_n=version.version_n,
        status=listing.status,
        updated_at=listing.updated_at,
    )
