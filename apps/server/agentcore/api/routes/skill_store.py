"""Capability store: open listing + one-click install of Skill snapshots.

``GET /v1/capabilities`` stays the platform blueprint. User skills stay base
on-demand documents. This route is the cross-user shelf.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.routes.skill_catalog import _eligible_mine_doc
from agentcore.db.models import Document
from agentcore.db.models.skill_store import SkillStoreInstall, SkillStoreListing, SkillStoreVersion
from agentcore.db.models.users import User
from agentcore.db.repositories import DocumentRepository, SkillStoreRepository, UserRepository
from agentcore.db.skill_store_groups import (
    SkillStoreGroupName,
    empty_group_counts,
)
from agentcore.documents.frontmatter import set_entry_frontmatter, strip_entry_frontmatter
from agentcore.memory.account_prepare_cache import drop_account_rules_memory_cache_for_user
from agentcore.memory.rules_injection import rule_consult_name
from agentcore.runtime.legal_skills import DomainSkillTemplate
from agentcore.runtime.skills.platform_shelf import (
    PLATFORM_AUTHOR,
    get_platform_template,
    platform_listing_id,
    platform_matches_query,
    platform_templates,
    platform_version_id,
)

router = APIRouter(prefix="/skill-store", tags=["skill-store"])


class SkillStoreListingRow(BaseModel):
    id: str
    name: str
    description: str
    author: str
    version_n: int
    installed: bool
    has_update: bool
    status: str
    source_document_id: str
    group: SkillStoreGroupName


class SkillStoreListingDetail(SkillStoreListingRow):
    content: str
    document_id: str | None = None


class SkillStoreListResponse(BaseModel):
    data: list[SkillStoreListingRow]
    total: int
    page: int
    page_size: int
    groups: dict[str, int]


class SkillStoreMineResponse(BaseModel):
    data: list[SkillStoreListingRow]


class SkillStoreInstalledItem(SkillStoreListingRow):
    document_id: str


class SkillStoreInstalledResponse(BaseModel):
    data: list[SkillStoreInstalledItem]


class PublishSkillRequest(BaseModel):
    document_id: str = Field(..., min_length=1)
    group: SkillStoreGroupName


class PublishVersionRequest(BaseModel):
    group: SkillStoreGroupName | None = None


class ReportSkillRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class SkillStoreReportView(BaseModel):
    id: str
    listing_id: str
    reason: str


def _store(session: AsyncSession = Depends(get_db)) -> SkillStoreRepository:
    return SkillStoreRepository(session)


def _docs(session: AsyncSession = Depends(get_db)) -> DocumentRepository:
    return DocumentRepository(session)


def _author_label(user: User) -> str:
    name = (user.display_name or "").strip()
    return name or user.username


def _platform_row(
    skill: DomainSkillTemplate,
    install: SkillStoreInstall | None,
) -> SkillStoreListingRow:
    listing_id = platform_listing_id(skill.name)
    version_id = platform_version_id(skill.name, skill.body)
    installed = install is not None
    has_update = install is not None and install.version_id != version_id
    return SkillStoreListingRow(
        id=listing_id,
        name=skill.title,
        description=skill.summary,
        author=PLATFORM_AUTHOR,
        version_n=1,
        installed=installed,
        has_update=has_update,
        status="published",
        source_document_id="",
        group=skill.group,
    )


def _platform_detail(
    skill: DomainSkillTemplate,
    install: SkillStoreInstall | None,
) -> SkillStoreListingDetail:
    row = _platform_row(skill, install)
    return SkillStoreListingDetail(
        **row.model_dump(),
        content=skill.body,
        document_id=install.document_id if install else None,
    )


def _row(
    listing: SkillStoreListing,
    version: SkillStoreVersion,
    author: User,
    install: SkillStoreInstall | None,
) -> SkillStoreListingRow:
    installed = install is not None
    has_update = bool(
        install is not None
        and listing.current_version_id
        and install.version_id != listing.current_version_id
    )
    return SkillStoreListingRow(
        id=listing.id,
        name=version.name,
        description=version.description,
        author=_author_label(author),
        version_n=version.version_n,
        installed=installed,
        has_update=has_update,
        status=listing.status,
        source_document_id=listing.source_document_id,
        group=listing.shelf_group,  # type: ignore[arg-type]
    )


async def _resolve_installs(
    user_id: str,
    installs: dict[str, SkillStoreInstall],
    docs: DocumentRepository,
    store: SkillStoreRepository,
) -> dict[str, SkillStoreInstall]:
    if not installs:
        return {}
    live = await docs.live_ids(user_id, [row.document_id for row in installs.values()])
    dead = [row.document_id for row in installs.values() if row.document_id not in live]
    if dead:
        await store.delete_installs_for_documents(dead)
    return {lid: row for lid, row in installs.items() if row.document_id in live}


async def _resolve_install(
    user_id: str,
    install: SkillStoreInstall | None,
    docs: DocumentRepository,
    store: SkillStoreRepository,
) -> SkillStoreInstall | None:
    if install is None:
        return None
    resolved = await _resolve_installs(
        user_id, {install.listing_id: install}, docs, store
    )
    return resolved.get(install.listing_id)


async def _require_source_doc(
    docs: DocumentRepository, user_id: str, document_id: str
) -> Document:
    doc = await docs.get(document_id, user_id=user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail={"message": "找不到要上架的技能"})
    if not _eligible_mine_doc(doc):
        raise HTTPException(
            status_code=400,
            detail={"message": "只能上架账号里已启用的按需技能"},
        )
    if not (doc.description or "").strip():
        raise HTTPException(status_code=400, detail={"message": "上架需要 description"})
    stripped = strip_entry_frontmatter(doc.content or "")
    if stripped is None or not stripped.strip():
        raise HTTPException(status_code=400, detail={"message": "这份技能还没有正文"})
    return doc


def _snapshot(doc: Document) -> tuple[str, str, str]:
    name = rule_consult_name(doc.name) or doc.name
    return name, doc.description or "", doc.content or ""


async def _copy_snapshot(
    docs: DocumentRepository,
    *,
    user_id: str,
    name: str,
    description: str,
    content: str,
    existing: Document | None = None,
) -> Document:
    body = set_entry_frontmatter(content, apply="on_demand", description=description)
    filename = name if name.lower().endswith(".md") else f"{name}.md"
    if existing is not None:
        updated = await docs.update_content(existing.id, user_id=user_id, content=body)
        assert updated is not None
        if updated.name != filename:
            renamed = await docs.rename(existing.id, user_id=user_id, name=filename)
            assert renamed is not None
            return renamed
        return updated
    rules_dir = await docs.ensure_rules_dir(user_id, None)
    return await docs.create(
        user_id,
        name=filename,
        parent_id=rules_dir.id,
        folder_id=None,
        kind="document",
        role="rule",
        apply_mode="on_demand",
        content=body,
    )


@router.get("", response_model=SkillStoreListResponse)
async def list_skill_store(
    user: AuthUser,
    q: str | None = Query(default=None),
    group: SkillStoreGroupName | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreListResponse:
    rows, total = await store.list_shelf(
        q=q,
        page=page,
        page_size=page_size,
        statuses=("published",),
        shelf_group=group,
    )
    matched_platform = [
        skill
        for skill in platform_templates()
        if platform_matches_query(skill, q, group)
    ]
    listing_ids = [listing.id for listing, _, _ in rows] + [
        platform_listing_id(skill.name) for skill in matched_platform
    ]
    installs = await _resolve_installs(
        user.user_id,
        await store.installs_by_listing_ids(user.user_id, listing_ids),
        docs,
        store,
    )
    data: list[SkillStoreListingRow] = []
    if page == 1:
        data.extend(
            _platform_row(skill, installs.get(platform_listing_id(skill.name)))
            for skill in matched_platform
        )
    data.extend(
        _row(listing, version, author, installs.get(listing.id))
        for listing, version, author in rows
    )
    counts = empty_group_counts()
    for name, n in (await store.group_counts(q=q, statuses=("published",))).items():
        if name in counts:
            counts[name] = n
    for skill in platform_templates():
        if platform_matches_query(skill, q):
            counts[skill.group] = counts.get(skill.group, 0) + 1
    return SkillStoreListResponse(
        data=data,
        total=total + len(matched_platform),
        page=page,
        page_size=page_size,
        groups=counts,
    )


@router.post("", response_model=SkillStoreListingDetail)
async def publish_skill(
    body: PublishSkillRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreListingDetail:
    del session
    doc = await _require_source_doc(docs, user.user_id, body.document_id)
    listing = await store.get_listing_by_source(doc.id)
    if listing is None:
        listing = await store.create_listing(
            author_user_id=user.user_id,
            source_document_id=doc.id,
            shelf_group=body.group,
            commit=False,
        )
    elif listing.author_user_id != user.user_id:
        raise HTTPException(status_code=403, detail={"message": "不能上架别人的技能"})
    elif listing.status == "taken_down":
        raise HTTPException(status_code=403, detail={"message": "该技能已被下架，不能再发版本"})
    listing.status = "published"
    listing.shelf_group = body.group
    name, description, content = _snapshot(doc)
    version = await store.add_version(
        listing=listing, name=name, description=description, content=content
    )
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), docs, store
    )
    return SkillStoreListingDetail(
        **_row(listing, version, user, install).model_dump(),
        content=version.content,
        document_id=install.document_id if install else None,
    )


@router.get("/mine", response_model=SkillStoreMineResponse)
async def list_mine_listings(
    user: AuthUser,
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreMineResponse:
    rows, _total = await store.list_shelf(
        author_user_id=user.user_id, page=1, page_size=100, statuses=None
    )
    installs = await _resolve_installs(
        user.user_id,
        await store.installs_by_listing_ids(
            user.user_id, [listing.id for listing, _, _ in rows]
        ),
        docs,
        store,
    )
    return SkillStoreMineResponse(
        data=[
            _row(listing, version, author, installs.get(listing.id))
            for listing, version, author in rows
        ]
    )


@router.get("/installed", response_model=SkillStoreInstalledResponse)
async def list_installed(
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreInstalledResponse:
    raw = await store.list_installs_for_user(user.user_id)
    if not raw:
        return SkillStoreInstalledResponse(data=[])
    live = await _resolve_installs(
        user.user_id, {row.listing_id: row for row in raw}, docs, store
    )
    users = UserRepository(session)
    items: list[SkillStoreInstalledItem] = []
    for install in raw:
        if install.listing_id not in live:
            continue
        skill = get_platform_template(install.listing_id)
        if skill is not None:
            row = _platform_row(skill, install)
            items.append(
                SkillStoreInstalledItem(**row.model_dump(), document_id=install.document_id)
            )
            continue
        listing = await store.get_listing(install.listing_id)
        if listing is None:
            continue
        version = await store.get_current_version(listing)
        if version is None:
            continue
        author = await users.get_by_id(listing.author_user_id)
        if author is None:
            continue
        row = _row(listing, version, author, install)
        items.append(SkillStoreInstalledItem(**row.model_dump(), document_id=install.document_id))
    return SkillStoreInstalledResponse(data=items)


@router.get("/{listing_id}", response_model=SkillStoreListingDetail)
async def get_listing(
    listing_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreListingDetail:
    skill = get_platform_template(listing_id)
    if skill is not None:
        install = await _resolve_install(
            user.user_id, await store.get_install(user.user_id, listing_id), docs, store
        )
        return _platform_detail(skill, install)
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    is_author = listing.author_user_id == user.user_id
    if listing.status != "published" and not is_author:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    if author is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), docs, store
    )
    return SkillStoreListingDetail(
        **_row(listing, version, author, install).model_dump(),
        content=version.content,
        document_id=install.document_id if install else None,
    )


@router.post("/{listing_id}/versions", response_model=SkillStoreListingDetail)
async def publish_new_version(
    listing_id: str,
    user: AuthUser,
    body: PublishVersionRequest | None = Body(default=None),
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreListingDetail:
    if get_platform_template(listing_id) is not None:
        raise HTTPException(status_code=403, detail={"message": "官方技能不能发版本"})
    listing = await store.get_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    if listing.author_user_id != user.user_id:
        raise HTTPException(status_code=403, detail={"message": "只有作者能发新版本"})
    if listing.status == "taken_down":
        raise HTTPException(status_code=403, detail={"message": "该技能已被下架，不能再发版本"})
    listing.status = "published"
    if body is not None and body.group is not None:
        listing.shelf_group = body.group
    doc = await _require_source_doc(docs, user.user_id, listing.source_document_id)
    name, description, content = _snapshot(doc)
    version = await store.add_version(
        listing=listing, name=name, description=description, content=content
    )
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), docs, store
    )
    return SkillStoreListingDetail(
        **_row(listing, version, user, install).model_dump(),
        content=version.content,
        document_id=install.document_id if install else None,
    )


@router.delete("/{listing_id}", response_model=SkillStoreListingRow)
async def unpublish_listing(
    listing_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreListingRow:
    if get_platform_template(listing_id) is not None:
        raise HTTPException(status_code=403, detail={"message": "官方技能不能下架"})
    listing = await store.get_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    if listing.author_user_id != user.user_id:
        raise HTTPException(status_code=403, detail={"message": "只有作者能下架"})
    if listing.status == "taken_down":
        raise HTTPException(status_code=403, detail={"message": "该技能已被下架"})
    listing = await store.set_status(listing, "unpublished")
    version = await store.get_current_version(listing)
    if version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    assert author is not None
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), docs, store
    )
    return _row(listing, version, author, install)


@router.post("/{listing_id}/install", response_model=SkillStoreInstalledItem)
async def install_listing(
    listing_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: SkillStoreRepository = Depends(_store),
    docs: DocumentRepository = Depends(_docs),
) -> SkillStoreInstalledItem:
    skill = get_platform_template(listing_id)
    if skill is not None:
        version_id = platform_version_id(skill.name, skill.body)
        existing = await store.get_install(user.user_id, listing_id)
        if existing is not None and existing.version_id == version_id:
            current = await docs.get(existing.document_id, user_id=user.user_id)
            if current is not None:
                row = _platform_row(skill, existing)
                return SkillStoreInstalledItem(
                    **row.model_dump(), document_id=existing.document_id
                )
        snapshot: Document | None = None
        if existing is not None:
            snapshot = await docs.get(existing.document_id, user_id=user.user_id)
        copy = await _copy_snapshot(
            docs,
            user_id=user.user_id,
            name=skill.title,
            description=skill.summary,
            content=skill.body,
            existing=snapshot,
        )
        install = await store.upsert_install(
            user_id=user.user_id,
            listing_id=listing_id,
            version_id=version_id,
            document_id=copy.id,
        )
        drop_account_rules_memory_cache_for_user(user.user_id)
        row = _platform_row(skill, install)
        return SkillStoreInstalledItem(**row.model_dump(), document_id=copy.id)
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None or listing.status != "published":
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    existing = await store.get_install(user.user_id, listing.id)
    if existing is not None and existing.version_id == version.id:
        current = await docs.get(existing.document_id, user_id=user.user_id)
        if current is not None:
            author = await UserRepository(session).get_by_id(listing.author_user_id)
            assert author is not None
            row = _row(listing, version, author, existing)
            return SkillStoreInstalledItem(**row.model_dump(), document_id=existing.document_id)

    snapshot = None
    if existing is not None:
        snapshot = await docs.get(existing.document_id, user_id=user.user_id)
    copy = await _copy_snapshot(
        docs,
        user_id=user.user_id,
        name=version.name,
        description=version.description,
        content=version.content,
        existing=snapshot,
    )
    install = await store.upsert_install(
        user_id=user.user_id,
        listing_id=listing.id,
        version_id=version.id,
        document_id=copy.id,
    )
    drop_account_rules_memory_cache_for_user(user.user_id)
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    assert author is not None
    row = _row(listing, version, author, install)
    return SkillStoreInstalledItem(**row.model_dump(), document_id=copy.id)


@router.post("/{listing_id}/reports", response_model=SkillStoreReportView)
async def report_listing(
    listing_id: str,
    body: ReportSkillRequest,
    user: AuthUser,
    store: SkillStoreRepository = Depends(_store),
) -> SkillStoreReportView:
    if get_platform_template(listing_id) is not None:
        raise HTTPException(status_code=400, detail={"message": "官方技能不能举报"})
    listing = await store.get_listing(listing_id)
    if listing is None or listing.status != "published":
        raise HTTPException(status_code=404, detail={"message": "找不到这个技能"})
    row = await store.add_report(
        user_id=user.user_id, listing_id=listing.id, reason=body.reason.strip()
    )
    return SkillStoreReportView(id=row.id, listing_id=row.listing_id, reason=row.reason)
