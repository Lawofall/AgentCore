"""Admin capability-store queue: listings + reports + takedown."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.admin.audit import record_admin_audit
from agentcore.api.dependencies import AdminUser, get_db
from agentcore.db.repositories.skill_store import SkillStoreRepository
from agentcore.db.repositories.users import UserRepository

router = APIRouter()


class AdminSkillStoreListingRow(BaseModel):
    id: str
    name: str
    description: str
    author: str
    author_user_id: str
    version_n: int
    status: str
    updated_at: datetime


class AdminSkillStoreListingDetail(AdminSkillStoreListingRow):
    content: str


class AdminSkillStoreListingList(BaseModel):
    data: list[AdminSkillStoreListingRow]
    total: int
    page: int
    page_size: int


class AdminSkillStoreReportRow(BaseModel):
    id: str
    listing_id: str
    listing_name: str
    listing_status: str
    user_id: str
    reporter: str
    reason: str
    created_at: datetime


class AdminSkillStoreReportList(BaseModel):
    data: list[AdminSkillStoreReportRow]
    total: int
    page: int
    page_size: int


def _store(session: AsyncSession = Depends(get_db)) -> SkillStoreRepository:
    return SkillStoreRepository(session)


def _author_label(display_name: str, username: str) -> str:
    name = (display_name or "").strip()
    return name or username


@router.get("/skill-store/listings", response_model=AdminSkillStoreListingList)
async def admin_list_listings(
    _admin: AdminUser,
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store: SkillStoreRepository = Depends(_store),
) -> AdminSkillStoreListingList:
    statuses = (status,) if status in ("published", "unpublished", "taken_down") else None
    rows, total = await store.list_shelf(
        q=q, page=page, page_size=page_size, statuses=statuses
    )
    return AdminSkillStoreListingList(
        data=[
            AdminSkillStoreListingRow(
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
    "/skill-store/listings/{listing_id}",
    response_model=AdminSkillStoreListingDetail,
)
async def admin_get_listing(
    listing_id: str,
    _admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
) -> AdminSkillStoreListingDetail:
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    author_name = _author_label(author.display_name, author.username) if author else ""
    return AdminSkillStoreListingDetail(
        id=listing.id,
        name=version.name,
        description=version.description,
        author=author_name,
        author_user_id=listing.author_user_id,
        version_n=version.version_n,
        status=listing.status,
        updated_at=listing.updated_at,
        content=version.content,
    )


@router.get("/skill-store/reports", response_model=AdminSkillStoreReportList)
async def admin_list_reports(
    _admin: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
) -> AdminSkillStoreReportList:
    rows, total = await store.list_reports(page=page, page_size=page_size)
    users = UserRepository(session)
    data: list[AdminSkillStoreReportRow] = []
    for report, listing, version in rows:
        reporter = await users.get_by_id(report.user_id)
        data.append(
            AdminSkillStoreReportRow(
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
    return AdminSkillStoreReportList(
        data=data,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/skill-store/listings/{listing_id}/takedown",
    response_model=AdminSkillStoreListingRow,
)
async def admin_takedown_listing(
    listing_id: str,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
) -> AdminSkillStoreListingRow:
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    listing = await store.set_status(listing, "taken_down")
    await record_admin_audit(
        session,
        actor_id=admin.user_id,
        action="skill_store.takedown",
        target_type="skill_store_listing",
        target_id=listing.id,
    )
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    author_name = _author_label(author.display_name, author.username) if author else ""
    return AdminSkillStoreListingRow(
        id=listing.id,
        name=version.name,
        description=version.description,
        author=author_name,
        author_user_id=listing.author_user_id,
        version_n=version.version_n,
        status=listing.status,
        updated_at=listing.updated_at,
    )
