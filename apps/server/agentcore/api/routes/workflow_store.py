"""Capability store: open listing + one-click install of workflow snapshots.

Official playbooks stay on ``/workflow-playbook-templates`` + from-playbook.
This route is the cross-user shelf of user-authored graphs.
Install = copy name / description / definition into a new ``user_workflow``
(no trigger, no source). Not live-follow.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db, get_user_workflow_repo
from agentcore.db.models.user_workflows import UserWorkflow
from agentcore.db.models.users import User
from agentcore.db.models.workflow_store import (
    WorkflowStoreInstall,
    WorkflowStoreListing,
    WorkflowStoreVersion,
)
from agentcore.db.repositories import (
    UserRepository,
    UserWorkflowRepository,
    WorkflowStoreRepository,
)
from agentcore.workflows.definition import (
    client_owned_definition,
    validate_workflow_definition,
)

router = APIRouter(prefix="/workflow-store", tags=["workflow-store"])


class WorkflowStoreListingRow(BaseModel):
    id: str
    name: str
    description: str
    author: str
    version_n: int
    installed: bool
    has_update: bool
    status: str
    source_workflow_id: str


class WorkflowStoreListingDetail(WorkflowStoreListingRow):
    definition: dict[str, Any]
    workflow_id: str | None = None


class WorkflowStoreListResponse(BaseModel):
    data: list[WorkflowStoreListingRow]
    total: int
    page: int
    page_size: int


class WorkflowStoreMineResponse(BaseModel):
    data: list[WorkflowStoreListingRow]


class WorkflowStoreInstalledItem(WorkflowStoreListingRow):
    workflow_id: str


class WorkflowStoreInstalledResponse(BaseModel):
    data: list[WorkflowStoreInstalledItem]


class PublishWorkflowRequest(BaseModel):
    workflow_id: str = Field(..., min_length=1)


class ReportWorkflowRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class WorkflowStoreReportView(BaseModel):
    id: str
    listing_id: str
    reason: str


def _store(session: AsyncSession = Depends(get_db)) -> WorkflowStoreRepository:
    return WorkflowStoreRepository(session)


def _author_label(user: User) -> str:
    name = (user.display_name or "").strip()
    return name or user.username


def _row(
    listing: WorkflowStoreListing,
    version: WorkflowStoreVersion,
    author: User,
    install: WorkflowStoreInstall | None,
) -> WorkflowStoreListingRow:
    installed = install is not None
    has_update = bool(
        install is not None
        and listing.current_version_id
        and install.version_id != listing.current_version_id
    )
    return WorkflowStoreListingRow(
        id=listing.id,
        name=version.name,
        description=version.description,
        author=_author_label(author),
        version_n=version.version_n,
        installed=installed,
        has_update=has_update,
        status=listing.status,
        source_workflow_id=listing.source_workflow_id,
    )


async def _resolve_installs(
    user_id: str,
    installs: dict[str, WorkflowStoreInstall],
    workflows: UserWorkflowRepository,
    store: WorkflowStoreRepository,
) -> dict[str, WorkflowStoreInstall]:
    if not installs:
        return {}
    live = await workflows.live_ids(
        user_id, [row.workflow_id for row in installs.values()]
    )
    dead = [row.workflow_id for row in installs.values() if row.workflow_id not in live]
    if dead:
        await store.delete_installs_for_workflows(dead)
    return {lid: row for lid, row in installs.items() if row.workflow_id in live}


async def _resolve_install(
    user_id: str,
    install: WorkflowStoreInstall | None,
    workflows: UserWorkflowRepository,
    store: WorkflowStoreRepository,
) -> WorkflowStoreInstall | None:
    if install is None:
        return None
    resolved = await _resolve_installs(
        user_id, {install.listing_id: install}, workflows, store
    )
    return resolved.get(install.listing_id)


def _snapshot(row: UserWorkflow) -> tuple[str, str, dict[str, Any]]:
    definition = client_owned_definition(row.definition)
    return row.name, (row.description or "").strip(), definition


async def _require_source(
    workflows: UserWorkflowRepository, user_id: str, workflow_id: str
) -> UserWorkflow:
    row = await workflows.get_by_id(workflow_id, user_id=user_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"message": "找不到要上架的工作流"})
    if not (row.description or "").strip():
        raise HTTPException(status_code=400, detail={"message": "上架需要 description"})
    errors = validate_workflow_definition(client_owned_definition(row.definition))
    if errors:
        raise HTTPException(status_code=400, detail={"message": "；".join(errors)})
    return row


@router.get("", response_model=WorkflowStoreListResponse)
async def list_workflow_store(
    user: AuthUser,
    q: str | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreListResponse:
    rows, total = await store.list_shelf(
        q=q, page=page, page_size=page_size, statuses=("published",)
    )
    listing_ids = [listing.id for listing, _, _ in rows]
    installs = await _resolve_installs(
        user.user_id,
        await store.installs_by_listing_ids(user.user_id, listing_ids),
        workflows,
        store,
    )
    return WorkflowStoreListResponse(
        data=[
            _row(listing, version, author, installs.get(listing.id))
            for listing, version, author in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=WorkflowStoreListingDetail)
async def publish_workflow(
    body: PublishWorkflowRequest,
    user: AuthUser,
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreListingDetail:
    source = await _require_source(workflows, user.user_id, body.workflow_id)
    listing = await store.get_listing_by_source(source.id)
    if listing is None:
        listing = await store.create_listing(
            author_user_id=user.user_id, source_workflow_id=source.id, commit=False
        )
    elif listing.author_user_id != user.user_id:
        raise HTTPException(status_code=403, detail={"message": "不能上架别人的工作流"})
    elif listing.status == "taken_down":
        raise HTTPException(status_code=403, detail={"message": "该工作流已被下架，不能再发版本"})
    listing.status = "published"
    name, description, definition = _snapshot(source)
    version = await store.add_version(
        listing=listing, name=name, description=description, definition=definition
    )
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), workflows, store
    )
    return WorkflowStoreListingDetail(
        **_row(listing, version, user, install).model_dump(),
        definition=version.definition,
        workflow_id=install.workflow_id if install else None,
    )


@router.get("/mine", response_model=WorkflowStoreMineResponse)
async def list_mine_listings(
    user: AuthUser,
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreMineResponse:
    rows, _total = await store.list_shelf(
        author_user_id=user.user_id, page=1, page_size=100, statuses=None
    )
    installs = await _resolve_installs(
        user.user_id,
        await store.installs_by_listing_ids(
            user.user_id, [listing.id for listing, _, _ in rows]
        ),
        workflows,
        store,
    )
    return WorkflowStoreMineResponse(
        data=[
            _row(listing, version, author, installs.get(listing.id))
            for listing, version, author in rows
        ]
    )


@router.get("/installed", response_model=WorkflowStoreInstalledResponse)
async def list_installed(
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreInstalledResponse:
    raw = await store.list_installs_for_user(user.user_id)
    if not raw:
        return WorkflowStoreInstalledResponse(data=[])
    live = await _resolve_installs(
        user.user_id, {row.listing_id: row for row in raw}, workflows, store
    )
    users = UserRepository(session)
    items: list[WorkflowStoreInstalledItem] = []
    for install in raw:
        if install.listing_id not in live:
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
        items.append(
            WorkflowStoreInstalledItem(**row.model_dump(), workflow_id=install.workflow_id)
        )
    return WorkflowStoreInstalledResponse(data=items)


@router.get("/{listing_id}", response_model=WorkflowStoreListingDetail)
async def get_listing(
    listing_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreListingDetail:
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    is_author = listing.author_user_id == user.user_id
    if listing.status != "published" and not is_author:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    if author is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), workflows, store
    )
    return WorkflowStoreListingDetail(
        **_row(listing, version, author, install).model_dump(),
        definition=version.definition,
        workflow_id=install.workflow_id if install else None,
    )


@router.post("/{listing_id}/versions", response_model=WorkflowStoreListingDetail)
async def publish_new_version(
    listing_id: str,
    user: AuthUser,
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreListingDetail:
    listing = await store.get_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    if listing.author_user_id != user.user_id:
        raise HTTPException(status_code=403, detail={"message": "只有作者能发新版本"})
    if listing.status == "taken_down":
        raise HTTPException(status_code=403, detail={"message": "该工作流已被下架，不能再发版本"})
    listing.status = "published"
    source = await _require_source(workflows, user.user_id, listing.source_workflow_id)
    name, description, definition = _snapshot(source)
    version = await store.add_version(
        listing=listing, name=name, description=description, definition=definition
    )
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), workflows, store
    )
    return WorkflowStoreListingDetail(
        **_row(listing, version, user, install).model_dump(),
        definition=version.definition,
        workflow_id=install.workflow_id if install else None,
    )


@router.delete("/{listing_id}", response_model=WorkflowStoreListingRow)
async def unpublish_listing(
    listing_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreListingRow:
    listing = await store.get_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    if listing.author_user_id != user.user_id:
        raise HTTPException(status_code=403, detail={"message": "只有作者能下架"})
    if listing.status == "taken_down":
        raise HTTPException(status_code=403, detail={"message": "该工作流已被下架"})
    listing = await store.set_status(listing, "unpublished")
    version = await store.get_current_version(listing)
    if version is None:
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    assert author is not None
    install = await _resolve_install(
        user.user_id, await store.get_install(user.user_id, listing.id), workflows, store
    )
    return _row(listing, version, author, install)


@router.post("/{listing_id}/install", response_model=WorkflowStoreInstalledItem)
async def install_listing(
    listing_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    store: WorkflowStoreRepository = Depends(_store),
    workflows: UserWorkflowRepository = Depends(get_user_workflow_repo),
) -> WorkflowStoreInstalledItem:
    listing = await store.get_listing(listing_id)
    version = await store.get_current_version(listing) if listing is not None else None
    if listing is None or version is None or listing.status != "published":
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    existing = await store.get_install(user.user_id, listing.id)
    if existing is not None and existing.version_id == version.id:
        current = await workflows.get_by_id(existing.workflow_id, user_id=user.user_id)
        if current is not None:
            author = await UserRepository(session).get_by_id(listing.author_user_id)
            assert author is not None
            row = _row(listing, version, author, existing)
            return WorkflowStoreInstalledItem(
                **row.model_dump(), workflow_id=existing.workflow_id
            )

    definition = dict(version.definition or {})
    if existing is not None:
        current = await workflows.get_by_id(existing.workflow_id, user_id=user.user_id)
        if current is not None:
            updated = await workflows.update(
                current.id,
                user_id=user.user_id,
                name=version.name,
                description=version.description,
                definition=definition,
            )
            assert updated is not None
            copy = updated
        else:
            copy = await workflows.create(
                user_id=user.user_id,
                name=version.name,
                description=version.description,
                definition=definition,
            )
    else:
        copy = await workflows.create(
            user_id=user.user_id,
            name=version.name,
            description=version.description,
            definition=definition,
        )
    install = await store.upsert_install(
        user_id=user.user_id,
        listing_id=listing.id,
        version_id=version.id,
        workflow_id=copy.id,
    )
    author = await UserRepository(session).get_by_id(listing.author_user_id)
    assert author is not None
    row = _row(listing, version, author, install)
    return WorkflowStoreInstalledItem(**row.model_dump(), workflow_id=copy.id)


@router.post("/{listing_id}/reports", response_model=WorkflowStoreReportView)
async def report_listing(
    listing_id: str,
    body: ReportWorkflowRequest,
    user: AuthUser,
    store: WorkflowStoreRepository = Depends(_store),
) -> WorkflowStoreReportView:
    listing = await store.get_listing(listing_id)
    if listing is None or listing.status != "published":
        raise HTTPException(status_code=404, detail={"message": "找不到这个工作流"})
    row = await store.add_report(
        user_id=user.user_id, listing_id=listing.id, reason=body.reason.strip()
    )
    return WorkflowStoreReportView(id=row.id, listing_id=row.listing_id, reason=row.reason)
