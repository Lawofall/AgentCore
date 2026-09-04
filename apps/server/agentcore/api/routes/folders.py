"""Folder CRUD routes (项目 = 工作区).

``GET /{id}`` is accepted-member (owner / editor / viewer); outsiders 404
(IDOR-safe). Tree mutate (rename / move / delete) remains owner-only (members
403). Soft-deleting a folder archives its conversations in place (keeps
``folder_id``); workspace binding is set at create and is immutable thereafter.

List / create / get-by-id / soft-delete accept either an access session or a
folders narrow ticket (sidecar cloud roster) — the sidecar-hosted CEO owns the
same roster verbs the sidebar does (``delete_folder`` 软删经此路)。
Permanent delete / rename / timeline / 最近删除 remain access-session only: 彻底删
只由用户在桌面弹窗里勾选确认，恢复是用户的补救面，AI 永远够不到（这一轮 AI 只能
删不能恢复）。
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import (
    AuthUser,
    FoldersApiUser,
    get_db,
    get_folder_desk_service,
    get_folder_repo,
)
from agentcore.api.schemas import (
    CollaborationDossierRef,
    CollaborationTimelineAct,
    CollaborationTimelineItem,
    CollaborationTimelineResponse,
    CreateFolderRequest,
    DeletedFolderListResponse,
    DeletedFolderSummary,
    FolderMemberListResponse,
    FolderMemberSummary,
    FolderSummary,
    InviteFolderMemberRequest,
    StatusResponse,
    UpdateFolderMemberRequest,
    UpdateFolderRequest,
)
from agentcore.config import settings
from agentcore.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from agentcore.core.logging import get_logger
from agentcore.db.repositories import FolderRepository
from agentcore.folders.collaboration_timeline import (
    display_act_title,
    list_folder_collaboration_timeline,
)
from agentcore.folders.desk import resolve_desk_access
from agentcore.folders.permanent_delete import permanent_delete_folder
from agentcore.folders.service import FolderDeskService, FolderDeskView, FolderMemberView
from agentcore.folders.tree_ops import (
    FolderTreeError,
    move_folder,
    rename_folder,
    restore_folder_tree,
    soft_delete_folder_tree,
)
from agentcore.fulfill.declare import declare_receipt_root
from agentcore.security.tokens import create_folders_token
from agentcore.workspace.locks import WorkspaceBusyError
from agentcore.workspace.retention import retention_cutoff

logger = get_logger(__name__)

router = APIRouter(prefix="/folders", tags=["folders"])

# Recycle-bin page size. A project list is human-scale; this only bounds a pathological
# account, and「最近删除」has no paging UI to spend a cursor on.
_TRASH_LIST_LIMIT = 200


def _purge_at(folder) -> datetime:
    """When the retention sweeper may hard-purge this soft-deleted project."""
    return folder.deleted_at + timedelta(days=settings.workspace_retention_days)


def _summary_from_desk(
    view: FolderDeskView, *, collaborator_count: int = 0
) -> FolderSummary:
    from agentcore.workspace.cloud_tree import parent_rel_path

    rel_path = view.rel_path or None
    return FolderSummary(
        id=view.id,
        name=view.name,
        mode="local" if view.local_root_id else "cloud",
        local_root_id=view.local_root_id,
        local_subpath=view.local_subpath,
        rel_path=rel_path,
        parent_rel_path=(parent_rel_path(rel_path) or None) if rel_path else None,
        owner_user_id=view.owner_user_id,
        my_role=view.my_role,
        my_state=view.my_state,
        collaborator_count=collaborator_count,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


async def _summaries_from_desk(
    desk: FolderDeskService, views: Sequence[FolderDeskView]
) -> list[FolderSummary]:
    counts = await desk.collaborator_counts([v.id for v in views])
    return [
        _summary_from_desk(v, collaborator_count=counts.get(v.id, 0)) for v in views
    ]


async def _summary_from_owned(desk: FolderDeskService, folder) -> FolderSummary:
    n = (await desk.collaborator_counts([folder.id])).get(folder.id, 0)
    return FolderSummary.from_folder(folder, collaborator_count=n)


def _member_summary(view: FolderMemberView) -> FolderMemberSummary:
    return FolderMemberSummary(
        user_id=view.user_id,
        role=view.role,
        state=view.state,
        invited_by=view.invited_by,
        joined_at=view.joined_at,
        display_name=view.display_name,
        username=view.username,
    )


async def _require_folder_owner(session: AsyncSession, folder_id: str, user_id: str):
    """Owner-only mutate: members get 403; outsiders 404 (no existence leak)."""
    access = await resolve_desk_access(session, folder_id=folder_id, user_id=user_id)
    if access is None:
        raise NotFoundError("文件夹不存在")
    if not access.is_owner:
        raise AuthorizationError("仅所有者可修改此文件夹")
    return access


class FoldersTokenResponse(BaseModel):
    """Freshly minted folders narrow token + lifetime (sidecar roster auth)."""

    token: str
    expires_in_sec: int


@router.post("/token", response_model=FoldersTokenResponse)
async def mint_folders_token(user: AuthUser) -> FoldersTokenResponse:
    """Exchange the caller's cookie/Bearer access session for a folders narrow ticket."""
    return FoldersTokenResponse(
        token=create_folders_token(user.user_id),
        expires_in_sec=settings.folders_token_expire_minutes * 60,
    )


@router.post("", response_model=FolderSummary, status_code=201)
async def create_folder(
    body: CreateFolderRequest,
    user: FoldersApiUser,
    response: Response,
    repo: FolderRepository = Depends(get_folder_repo),
):
    """Create a project, or reuse an existing live local binding (HTTP 200).

    Local mode is unique per ``(user_id, local_root_id, local_subpath)`` — same
    path re-open returns the existing row (VS Code / Cursor workspace reuse).
    Cloud projects become a real directory under ``parent_id``; a name already
    taken among live siblings gets a numeric suffix rather than a 409 — the name
    is now a path segment, and two of them cannot share one directory.

    Registering a local binding also declares that root on the caller's fulfill
    session (``fulfill/declare.py``) — including the reuse branch, where the row
    is old but *this* install may be new to the folder.
    """
    if body.mode == "local":
        assert body.local_root_id is not None  # validated by CreateFolderRequest
        declare_receipt_root(user.user_id, body.local_root_id)
        existing = await repo.find_active_by_local_binding(
            user_id=user.user_id,
            local_root_id=body.local_root_id,
            local_subpath=body.local_subpath,
        )
        if existing is not None:
            response.status_code = 200
            return FolderSummary.from_folder(existing)

    parent_rel_path: str | None = None
    if body.parent_id:
        parent = await repo.get_by_id(body.parent_id, user_id=user.user_id)
        if parent is None:
            raise NotFoundError("上级文件夹不存在")
        parent_rel_path = parent.rel_path

    folder = await repo.create(
        user_id=user.user_id,
        name=body.name,
        local_root_id=body.local_root_id if body.mode == "local" else None,
        local_subpath=body.local_subpath if body.mode == "local" else None,
        parent_rel_path=parent_rel_path,
    )
    return FolderSummary.from_folder(folder)


@router.get("", response_model=list[FolderSummary])
async def list_folders(
    user: FoldersApiUser,
    repo: FolderRepository = Depends(get_folder_repo),
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    folders = await repo.list_by_user(user.user_id)
    counts = await desk.collaborator_counts([f.id for f in folders])
    return [
        FolderSummary.from_folder(f, collaborator_count=counts.get(f.id, 0))
        for f in folders
    ]


# --- 最近删除 (project recycle bin) ---
# Declared ahead of the ``/{folder_id}`` matchers: FastAPI resolves in registration
# order, so a literal ``/trash`` segment must be registered first to win.


@router.get("/trash", response_model=DeletedFolderListResponse)
async def list_deleted_folders(
    user: AuthUser,
    repo: FolderRepository = Depends(get_folder_repo),
):
    """列出可恢复的已删项目（最近删除）。

    Access-session only — the folders narrow ticket is for the sidecar CEO's roster
    chores, and recovery is the user's own remedy surface.
    """
    folders = await repo.list_deleted_by_user(
        user.user_id, not_before=retention_cutoff(), limit=_TRASH_LIST_LIMIT
    )
    return DeletedFolderListResponse(
        data=[DeletedFolderSummary.from_folder(f, purge_at=_purge_at(f)) for f in folders],
        total=len(folders),
        retention_days=settings.workspace_retention_days,
    )


@router.post("/trash/{folder_id}/restore", response_model=FolderSummary)
async def restore_deleted_folder(
    folder_id: str,
    user: AuthUser,
    repo: FolderRepository = Depends(get_folder_repo),
    session: AsyncSession = Depends(get_db),
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    """恢复一个已删项目：项目行复活，删除时连带归档的对话解档。

    Past retention the answer is 409, never a silent success — the workspace files
    may already be gone. Losing the race with the 6-hour purge sweep between the
    lookup and the conditional UPDATE surfaces the same way (「该项目已被清理」);
    that window is reported honestly rather than reconciled.

    Boards unbound at delete time and the bare-chat auto cloud desk pointer stay
    cleared — see :meth:`FolderRepository.restore`.
    """
    folder = await repo.get_deleted_by_id(folder_id, user_id=user.user_id)
    if not folder:
        raise NotFoundError("项目不存在或不在最近删除中")
    if folder.deleted_at <= retention_cutoff():
        raise ConflictError(
            f"该项目已超过 {settings.workspace_retention_days} 天保留期，无法恢复"
        )

    try:
        restored = await restore_folder_tree(
            session,
            user_id=user.user_id,
            folder_id=folder_id,
            not_before=retention_cutoff(),
        )
    except WorkspaceBusyError as e:
        raise ConflictError("工作区正忙（有回合在跑），请稍后再恢复") from e
    if restored is None:
        raise ConflictError("该项目已被清理，无法恢复")
    logger.info(
        "folders.restored",
        folder_id=folder_id,
        user_id=user.user_id,
    )
    return await _summary_from_owned(desk, restored)


@router.get("/shared-with-me", response_model=list[FolderSummary])
async def list_shared_with_me(
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    """Cloud folders the caller joined as editor/viewer (not owned)."""
    views = await desk.list_shared_with_me(user_id=user.user_id)
    return await _summaries_from_desk(desk, views)


@router.get("/invites/pending", response_model=list[FolderSummary])
async def list_pending_folder_invites(
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    views = await desk.list_pending_invites(user_id=user.user_id)
    return await _summaries_from_desk(desk, views)


@router.get("/{folder_id}", response_model=FolderSummary)
async def get_folder(
    folder_id: str,
    user: FoldersApiUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    """Accepted desk member fetch (owner or editor/viewer). Outsiders 404."""
    view = await desk.get_desk(folder_id=folder_id, user_id=user.user_id)
    summaries = await _summaries_from_desk(desk, [view])
    return summaries[0]


@router.post("/{folder_id}/invites", response_model=FolderMemberSummary, status_code=201)
async def invite_folder_member(
    folder_id: str,
    body: InviteFolderMemberRequest,
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    view = await desk.invite(
        folder_id=folder_id,
        actor_id=user.user_id,
        target_user_id=body.user_id,
        role=body.role,
    )
    return _member_summary(view)


@router.post("/{folder_id}/invites/accept", response_model=FolderSummary)
async def accept_folder_invite(
    folder_id: str,
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    view = await desk.accept_invite(folder_id=folder_id, user_id=user.user_id)
    summaries = await _summaries_from_desk(desk, [view])
    return summaries[0]


@router.post("/{folder_id}/invites/reject", response_model=StatusResponse)
async def reject_folder_invite(
    folder_id: str,
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    await desk.reject_invite(folder_id=folder_id, user_id=user.user_id)
    return StatusResponse()


@router.get("/{folder_id}/members", response_model=FolderMemberListResponse)
async def list_folder_members(
    folder_id: str,
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    views = await desk.list_members(folder_id=folder_id, user_id=user.user_id)
    return FolderMemberListResponse(
        data=[_member_summary(v) for v in views], total=len(views)
    )


@router.patch("/{folder_id}/members/{member_user_id}", response_model=FolderMemberSummary)
async def change_folder_member_role(
    folder_id: str,
    member_user_id: str,
    body: UpdateFolderMemberRequest,
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    view = await desk.change_role(
        folder_id=folder_id,
        actor_id=user.user_id,
        target_user_id=member_user_id,
        role=body.role,
    )
    return _member_summary(view)


@router.delete("/{folder_id}/members/{member_user_id}", response_model=StatusResponse)
async def remove_or_leave_folder_member(
    folder_id: str,
    member_user_id: str,
    user: AuthUser,
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    if member_user_id == user.user_id:
        await desk.leave(folder_id=folder_id, user_id=user.user_id)
    else:
        await desk.remove_member(
            folder_id=folder_id, actor_id=user.user_id, target_user_id=member_user_id
        )
    return StatusResponse()


@router.patch("/{folder_id}", response_model=FolderSummary)
async def update_folder(
    folder_id: str,
    body: UpdateFolderRequest,
    user: AuthUser,
    repo: FolderRepository = Depends(get_folder_repo),
    session: AsyncSession = Depends(get_db),
    desk: FolderDeskService = Depends(get_folder_desk_service),
):
    """Rename and/or re-parent a folder — DB rows and the directory move together.

    Refuses with 409 while a turn holds the workspace lock: moving the directory
    out from under a running turn is not something to do silently, and neither is
    queueing the user's rename behind it (不得静默改名).
    """
    fields = body.model_fields_set
    await _require_folder_owner(session, folder_id, user.user_id)
    folder = None
    try:
        if "name" in fields and body.name is not None:
            folder = await rename_folder(
                session, user_id=user.user_id, folder_id=folder_id, new_name=body.name
            )
            if folder is None:
                raise NotFoundError("文件夹不存在")
        if "parent_id" in fields:
            folder = await move_folder(
                session,
                user_id=user.user_id,
                folder_id=folder_id,
                new_parent_id=body.parent_id,
            )
    except FolderTreeError as e:
        raise ValidationError(str(e)) from e
    except WorkspaceBusyError as e:
        raise ConflictError("工作区正忙（有回合在跑），请稍后再改名或移动") from e

    if folder is None:
        folder = await repo.get_by_id(folder_id, user_id=user.user_id)
    if not folder:
        raise NotFoundError("文件夹不存在")
    return await _summary_from_owned(desk, folder)


@router.delete("/{folder_id}", response_model=StatusResponse)
async def delete_folder(
    folder_id: str,
    user: FoldersApiUser,
    session: AsyncSession = Depends(get_db),
):
    """软删项目（对话就地归档；工作区走保留期自动清理）。

    Nested folders go with it, and the directory is parked in the tombstone area so
    the name is free again immediately (双模式工作区 §5.4).

    Reachable with a folders narrow ticket so the sidecar CEO's ``delete_folder``
    lands on the same path as the sidebar. The irreversible twin below stays
    access-session only.
    """
    try:
        await _require_folder_owner(session, folder_id, user.user_id)
        repo = FolderRepository(session)
        subtree_ids = await repo.list_live_subtree_ids(
            folder_id, user_id=user.user_id
        )
        deleted = await soft_delete_folder_tree(
            session, user_id=user.user_id, folder_id=folder_id
        )
    except WorkspaceBusyError as e:
        raise ConflictError("工作区正忙（有回合在跑），请稍后再删除") from e
    if not deleted:
        raise NotFoundError("文件夹不存在")
    from agentcore.memory.account_prepare_cache import hibernate_folder_injection_cache

    await hibernate_folder_injection_cache(user.user_id, subtree_ids)
    return StatusResponse()


@router.delete("/{folder_id}/permanent", response_model=StatusResponse)
async def delete_folder_permanent(
    folder_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
):
    """彻底删除文件夹：清盘成员对话 + 云端共享工作区/快照，再移除文件夹行.

    Distinct from ``DELETE /{folder_id}`` (soft-delete + archive members).
    Local-mode OS directories are never touched — only DB + server-side data.
    """
    await _require_folder_owner(session, folder_id, user.user_id)
    deleted = await permanent_delete_folder(folder_id=folder_id, user_id=user.user_id)
    if not deleted:
        raise NotFoundError("文件夹不存在")
    return StatusResponse()


@router.get(
    "/{folder_id}/collaboration-timeline",
    response_model=CollaborationTimelineResponse,
)
async def get_collaboration_timeline(
    folder_id: str,
    user: AuthUser,
    repo: FolderRepository = Depends(get_folder_repo),
    session: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    """项目协作时间线（读时聚合）：有 execution 的会话 + 幕序列摘要 + 约定文档引用条.

    零写路径。约定文档快照（AgentCore/文档/research/ / debate/ 文件列表）复用工作区
    文件 API，不在此返回。
    """
    folder = await repo.get_accessible(folder_id, user_id=user.user_id)
    if not folder:
        raise NotFoundError("文件夹不存在")
    result = await list_folder_collaboration_timeline(
        session,
        folder_id=folder_id,
        user_id=user.user_id,
        limit=limit,
        offset=offset,
    )
    items = [
        CollaborationTimelineItem(
            conversation_id=it.conversation_id,
            title=it.title,
            updated_at=it.updated_at,
            execution_id=it.execution_id,
            host_turn_id=it.host_turn_id,
            acts=[
                CollaborationTimelineAct(
                    act_id=a.act_id,
                    kind=a.kind if a.kind in ("multi_agent", "debate") else "multi_agent",
                    title=display_act_title(kind=a.kind, title=a.title),
                    started_at=a.started_at,
                )
                for a in it.acts
            ],
            dossier_refs=[
                CollaborationDossierRef(path=r.path, sources=list(r.sources))
                for r in it.dossier_refs
            ],
        )
        for it in result.items
    ]
    return CollaborationTimelineResponse(
        folder_id=result.folder_id,
        items=items,
        total=result.total,
        limit=result.limit,
        offset=result.offset,
        dossier_refs_note=result.dossier_refs_note,
    )
