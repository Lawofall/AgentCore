"""Workspace as a first-class, addressable resource (文件中枢统一 Step 1).

文件夹即工作区: a workspace **is** a folder, addressed by its own id
(``folder:<id>``, see ``workspace.locate``) rather than only "through a
conversation". This router is that surface: enumerate a user's workspaces, then
read/CRUD/snapshot any one by id.

Addressing is the only thing new here — the actual file/snapshot/clone logic stays
single-sourced in the ``workspace.*`` service layer that the per-conversation
routes also call (those remain the thin per-conversation alias). Every route is
owner-scoped: the ``ident`` in a ws id is resolved against the user's own folders,
so a non-owner (or a bad id) gets 404 — never another user's data.

Cloud vs local (§五 边界): a **local** workspace's files live on the user's
machine and are reached over desktop IPC, not here; its server-side dir is not the
truth. So file/dir/move/copy/clone and snapshot create/restore reject local ids with
409 — the hub routes those to the desktop. Read-only snapshot list/download stay
open (snapshots are object-store backed, keyed by ws, even for local).
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_folder_repo,
    get_shared_space_service,
)
from agentcore.api.download_headers import download_headers
from agentcore.api.schemas import (
    CloneRepoRequest,
    CloneRepoResponse,
    ConvertMdToDocxRequest,
    ConvertMdToDocxResponse,
    ConvertMdToPdfRequest,
    ConvertMdToPdfResponse,
    CreateDirRequest,
    CreateSnapshotRequest,
    ExportDocxRequest,
    ExportDocxResponse,
    ExportPdfRequest,
    ExportPdfResponse,
    MoveFileRequest,
    SnapshotListResponse,
    SnapshotSummary,
    StatusResponse,
    TrashEntrySummary,
    TrashListResponse,
    UploadFileResponse,
    WorkspaceEditDoc,
    WorkspaceFileEntry,
    WorkspaceFileIndexResponse,
    WorkspaceFileListResponse,
    WorkspaceListResponse,
    WorkspaceSummary,
    WorkspaceWriteRequest,
    WorkspaceWriteResult,
)
from agentcore.config import settings
from agentcore.conversation.scratch import bare_chat_local_subpath
from agentcore.core.errors import ConflictError, NotFoundError, ValidationError
from agentcore.db.repositories import ConversationRepository, FolderRepository
from agentcore.docs_export.md_to_docx import (
    convert_markdown_to_docx,
    docx_path_for_markdown,
)
from agentcore.docs_export.md_to_pdf import (
    convert_markdown_to_pdf,
    pdf_path_for_markdown,
)
from agentcore.docs_export.workspace_export import (
    ExportMarkdownError,
    export_markdown_path,
    export_markdown_to_pdf_path,
)
from agentcore.shared_spaces.service import SharedSpaceService
from agentcore.shared_spaces.types import can_write
from agentcore.storage import SnapshotNotFound
from agentcore.storage._archive import ArchiveLimitError
from agentcore.workspace.files import (
    archive_filename,
    copy_file,
    create_dir,
    delete_file,
    list_file_index,
    list_files,
    move_file,
    raise_http_for_archive_limit,
    raise_http_for_download_io,
    read_file_for_edit,
    resolve_download_file,
    upload_file,
    write_file_text,
    zip_resolved_dir,
    zip_subtree_for_download,
)
from agentcore.workspace.git import CloneError, clone_repo
from agentcore.workspace.limits import WORKSPACE_BROWSE_LIST_MAX
from agentcore.workspace.locate import (
    WorkspaceCoords,
    build_server_workspace,
    build_shared_workspace,
    format_shared_workspace_id,
    parse_workspace_id,
    workspace_has_entries,
    workspace_internal_root,
    workspace_root_path,
    workspace_storage_key,
)
from agentcore.workspace.locks import workspace_lock
from agentcore.workspace.protocol import (
    AlreadyExists,
    NotADirectory,
    NotAFile,
    NotUTF8,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceIOError,
)
from agentcore.workspace.shared_paths import (
    shared_workspace_has_entries,
    shared_workspace_storage_key,
)
from agentcore.workspace.snapshots import (
    create_snapshot,
    list_snapshots,
    read_snapshot,
    restore_snapshot,
)
from agentcore.workspace.trash import (
    TrashExpiredError,
    TrashNotFound,
    list_trash_entries,
    restore_from_trash,
    trash_retention_days,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@dataclass(frozen=True)
class _WsTarget:
    """A resolved workspace — folder/conv (owner-scope) or shared space (membership)."""

    ws_id: str
    folder_id: str | None
    conversation_id: str  # "" for a folder workspace (its path ignores it)
    name: str
    location: Literal["cloud", "local"]
    root_id: str | None
    # Where the folder's directory currently sits (``folders.rel_path``). Carried on
    # the target because resolution already loaded the row: re-querying it later
    # would be a second lookup on a possibly different session, and a placement that
    # comes back empty silently demotes the request to the conversation scratch.
    rel_path: str | None = None
    space_id: str | None = None
    member_role: str | None = None


async def _resolve_owned_workspace(
    ws_id: str,
    user_id: str,
    conv_repo: ConversationRepository,
    folder_repo: FolderRepository,
    shared_svc: SharedSpaceService | None = None,
) -> _WsTarget:
    """Resolve a ws id to an owned project/scratch or a shared space, or 404.

    Owner-scope folder/conv resolution is unchanged. ``shared:`` is an additive
    membership branch (independent of owner-scope repos).
    """
    try:
        parsed = parse_workspace_id(ws_id)
    except ValueError as e:
        raise NotFoundError("工作区不存在") from e

    if parsed.kind == "shared":
        if shared_svc is None:
            raise NotFoundError("工作区不存在")
        space, member = await shared_svc.require_member_for_ws(
            space_id=parsed.ident, user_id=user_id
        )
        return _WsTarget(
            ws_id=ws_id,
            folder_id=None,
            conversation_id="",
            name=space.name,
            location="cloud",
            root_id=None,
            space_id=space.id,
            member_role=member.role,
        )

    if parsed.kind == "conv":
        conv = await conv_repo.get_by_id(parsed.ident, user_id=user_id)
        if not conv:
            raise NotFoundError("工作区不存在")
        # Project chats address via folder:<id>; a bare conv: id that somehow
        # carries folder_id still resolves to the conversation row for alias paths.
        if conv.folder_id:
            folder = await folder_repo.get_by_id(conv.folder_id, user_id=user_id)
            if not folder:
                raise NotFoundError("工作区不存在")
            return _WsTarget(
                ws_id=f"folder:{folder.id}",
                folder_id=folder.id,
                conversation_id=conv.id,
                name=folder.name,
                location="local" if folder.local_root_id else "cloud",
                root_id=folder.local_root_id,
                rel_path=folder.rel_path,
            )
        return _WsTarget(
            ws_id=ws_id,
            folder_id=None,
            conversation_id=conv.id,
            name=conv.title or "未命名对话",
            location="local" if conv.local_root_id else "cloud",
            root_id=conv.local_root_id,
        )

    folder = await folder_repo.get_by_id(parsed.ident, user_id=user_id)
    if not folder:
        raise NotFoundError("工作区不存在")
    return _WsTarget(
        ws_id=ws_id,
        folder_id=folder.id,
        conversation_id="",
        name=folder.name,
        location="local" if folder.local_root_id else "cloud",
        root_id=folder.local_root_id,
        rel_path=folder.rel_path,
    )


def _require_cloud(target: _WsTarget) -> None:
    """Reject ops that only make sense server-side on a local workspace (§五).

    A local workspace's files live on the user's machine; the hub reaches them
    over desktop IPC, so writing/snapshotting the server-side mirror here would
    silently diverge from the truth.
    """
    if target.location == "local":
        raise ConflictError("本地工作区的文件请在桌面端访问")


def _require_shared_write(target: _WsTarget) -> None:
    if target.space_id and not can_write(target.member_role or "viewer"):  # type: ignore[arg-type]
        raise ConflictError("只读成员不能写入共享空间")


def _refuse_shared_extra(target: _WsTarget) -> None:
    """v1: shared spaces support file CRUD only (no clone / snapshot)."""
    if target.space_id:
        raise ConflictError("共享空间暂不支持此操作")


def _workspace_coords(user_id: str, target: _WsTarget) -> WorkspaceCoords:
    """The four coordinates every cloud file / snapshot call needs.

    The services take the placement rather than looking it up, so they stay pure
    functions of ``(user, folder, placement, conversation)`` — and unit-testable
    without a database.
    """
    return {
        "user_id": user_id,
        "folder_id": target.folder_id,
        "folder_rel_path": target.rel_path,
        "conversation_id": target.conversation_id,
    }


def _storage_key(user_id: str, target: _WsTarget) -> str:
    if target.space_id:
        return shared_workspace_storage_key(target.space_id)
    return workspace_storage_key(
        user_id=user_id,
        folder_id=target.folder_id,
        conversation_id=target.conversation_id,
    )


async def _list_shared_entries(space_id: str, *, path: str, recursive: bool):
    backend = build_shared_workspace(space_id)
    pattern = "**/*" if recursive else "*"
    return await backend.list(path or ".", pattern, cap=WORKSPACE_BROWSE_LIST_MAX)


async def _shared_upload(space_id: str, path: str, data: bytes) -> int:
    backend = build_shared_workspace(space_id)
    return await backend.write_bytes(path, data)


async def _shared_resolve_download(space_id: str, path: str, *, max_bytes: int) -> Path:
    backend = build_shared_workspace(space_id)
    return await backend.resolve_for_download(path, max_bytes=max_bytes)


async def _shared_zip_archive(space_id: str, path: str, *, max_bytes: int) -> bytes:
    """Subtree zip for a shared space — same file-download path, not snapshots."""
    backend = build_shared_workspace(space_id)
    target = await backend.resolve_dir_for_download(path)
    return await zip_resolved_dir(target, max_bytes=max_bytes)


async def _shared_read_edit(space_id: str, path: str):
    backend = build_shared_workspace(space_id)
    return await backend.read_for_edit(path)


async def _shared_write_cas(
    space_id: str,
    path: str,
    content: str,
    *,
    baseline_mtime_ms: int,
    eol: Literal["lf", "crlf"],
):
    backend = build_shared_workspace(space_id)
    return await backend.write_text_cas(
        path, content, baseline_mtime_ms=baseline_mtime_ms, eol=eol
    )


async def _shared_mkdir(space_id: str, path: str) -> None:
    backend = build_shared_workspace(space_id)
    await backend.mkdir(path)


async def _shared_delete(space_id: str, path: str) -> None:
    backend = build_shared_workspace(space_id)
    await backend.delete(path)


async def _shared_move(space_id: str, src: str, dst: str) -> None:
    backend = build_shared_workspace(space_id)
    await backend.move(src, dst)


async def _shared_copy(space_id: str, src: str, dst: str) -> None:
    backend = build_shared_workspace(space_id)
    await backend.copy(src, dst)


async def _shared_index(space_id: str) -> tuple[list[str], bool]:
    backend = build_shared_workspace(space_id)
    result = await backend.index_files()
    return result.paths, result.truncated


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Enumerate project workspaces + bare-chat scratches + shared spaces."""
    folders = await folder_repo.list_by_user(user.user_id)
    items: list[WorkspaceSummary] = []
    for folder in folders:
        local = folder.local_root_id is not None
        has_files = (
            True
            if local
            else workspace_has_entries(
                user_id=user.user_id, folder_rel_path=folder.rel_path, conversation_id=""
            )
        )
        # Projects always list (a project is a project), even when empty cloud.
        items.append(
            WorkspaceSummary(
                ws_id=f"folder:{folder.id}",
                name=folder.name,
                location="local" if local else "cloud",
                root_id=folder.local_root_id,
                subpath=folder.local_subpath,
                has_files=has_files if not local else True,
            )
        )

    conversations = await conv_repo.list_all_by_user(user.user_id)
    for conv in conversations:
        if conv.folder_id is not None:
            continue  # covered by project entry above
        root_id = conv.local_root_id or conv.local_container_root_id
        local = root_id is not None
        has_files = (
            True
            if local
            else workspace_has_entries(
                user_id=user.user_id, folder_rel_path=None, conversation_id=conv.id
            )
        )
        if not local and not has_files:
            continue
        items.append(
            WorkspaceSummary(
                ws_id=f"conv:{conv.id}",
                name=conv.title or "未命名对话",
                location="local" if local else "cloud",
                root_id=root_id,
                subpath=(
                    (conv.local_subpath or bare_chat_local_subpath(conv.id)) if local else None
                ),
                has_files=has_files,
            )
        )

    for space_view in await shared_svc.list_spaces(user_id=user.user_id):
        items.append(
            WorkspaceSummary(
                ws_id=format_shared_workspace_id(space_view.id),
                name=space_view.name,
                location="cloud",
                root_id=None,
                subpath=None,
                has_files=shared_workspace_has_entries(space_view.id),
            )
        )
    return WorkspaceListResponse(data=items, total=len(items))


# --- Workspace files (cloud workspaces; local ones are reached over IPC) ---


@router.get("/{ws_id}/files", response_model=WorkspaceFileListResponse)
async def list_workspace_files(
    ws_id: str,
    user: AuthUser,
    recursive: bool = Query(False),
    path: str = Query(".", description="工作区相对目录（`.` = 根）"),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """List one directory of a cloud workspace (or its whole tree)."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    if target.space_id:
        listing = await _list_shared_entries(
            target.space_id, path=path, recursive=recursive
        )
    else:
        listing = await list_files(
            **_workspace_coords(user.user_id, target),
            path=path,
            recursive=recursive,
        )
    return WorkspaceFileListResponse(
        data=[WorkspaceFileEntry.model_validate(e) for e in listing.entries],
        total=len(listing.entries),
        truncated=listing.truncated,
    )


@router.get("/{ws_id}/file-index", response_model=WorkspaceFileIndexResponse)
async def list_workspace_file_index(
    ws_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Flat file-path list for @ mentions over a cloud workspace (文件中枢统一 F4).

    Files only, ignore-pruned, capped — so cloud workspace files feed the same @
    index local roots already do. Local workspaces are reached over desktop IPC
    (their files aren't here), so they are refused with 409 like other file ops.
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    if target.space_id:
        paths, truncated = await _shared_index(target.space_id)
    else:
        paths, truncated = await list_file_index(
            **_workspace_coords(user.user_id, target),
        )
    return WorkspaceFileIndexResponse(data=paths, total=len(paths), truncated=truncated)


@router.put("/{ws_id}/files/{path:path}", response_model=UploadFileResponse)
async def upload_workspace_file(
    ws_id: str,
    path: str,
    request: Request,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Upload (create/overwrite) a workspace file from the raw request body.

    Body is the file bytes (no multipart); ``path`` is the workspace-relative
    target. Bounded by ``workspace_upload_max_bytes``; a path escaping the
    workspace is rejected (422).
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)

    max_bytes = settings.workspace_upload_max_bytes
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise ValidationError(f"文件超出 {max_bytes} 字节的上传上限")
    data = await request.body()
    if len(data) > max_bytes:
        raise ValidationError(f"文件超出 {max_bytes} 字节的上传上限")

    if target.space_id:
        shared_svc.check_capacity(target.space_id, incoming_bytes=len(data))

    try:
        if target.space_id:
            written = await _shared_upload(target.space_id, path, data)
            await shared_svc.record_file_change(
                space_id=target.space_id,
                actor_user_id=user.user_id,
                actor_via="user",
                action="file_written",
                path=path,
            )
        else:
            written = await upload_file(
                **_workspace_coords(user.user_id, target),
                path=path,
                data=data,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    return UploadFileResponse(path=path, size_bytes=written)


@router.post("/convert/md-to-docx", response_model=ConvertMdToDocxResponse)
async def convert_md_to_docx(
    body: ConvertMdToDocxRequest,
    user: AuthUser,
):
    """Stateless Markdown → Word (shared converter; used by local desktop「导出 Word」).

    Does not touch a workspace. Images are optional base64 payloads keyed by the
    raw Markdown ``src``. Auth required so the surface is not a public converter.
    """
    del user  # auth gate only
    import base64

    images: dict[str, bytes | None] = {}
    for src, b64 in (body.images or {}).items():
        if b64 is None or b64 == "":
            images[src] = None
            continue
        try:
            images[src] = base64.b64decode(b64, validate=False)
        except Exception as e:
            raise ValidationError(f"图片 base64 无效：{src}") from e

    result = convert_markdown_to_docx(body.markdown, images=images)
    suggested = docx_path_for_markdown(body.source_name or "document.md")
    suggested = suggested.rsplit("/", 1)[-1] or "document.docx"
    return ConvertMdToDocxResponse(
        docx_base64=base64.b64encode(result.docx_bytes).decode("ascii"),
        warnings=list(result.warnings),
        suggested_filename=suggested,
    )


@router.post("/{ws_id}/export-docx", response_model=ExportDocxResponse)
async def export_workspace_docx(
    ws_id: str,
    body: ExportDocxRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Export workspace Markdown to a sibling ``.docx`` (shared ``md_to_docx`` converter)."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)

    try:
        if target.space_id:
            backend = build_shared_workspace(target.space_id)
        else:
            backend = build_server_workspace(**_workspace_coords(user.user_id, target))
        result = await export_markdown_path(backend, body.path)
        if target.space_id:
            await shared_svc.record_file_change(
                space_id=target.space_id,
                actor_user_id=user.user_id,
                actor_via="user",
                action="file_written",
                path=result.output_path,
            )
    except ExportMarkdownError as e:
        raise ValidationError(e.message) from e

    return ExportDocxResponse(
        path=result.output_path,
        source_path=result.source_path,
        size_bytes=result.size_bytes,
        warnings=list(result.warnings),
    )


@router.post("/convert/md-to-pdf", response_model=ConvertMdToPdfResponse)
async def convert_md_to_pdf(
    body: ConvertMdToPdfRequest,
    user: AuthUser,
):
    """Stateless Markdown → PDF (shared converter; used by local desktop「导出 PDF」).

    Does not touch a workspace. Auth required so the surface is not a public converter.
    """
    del user  # auth gate only
    import base64

    result = convert_markdown_to_pdf(body.markdown)
    suggested = pdf_path_for_markdown(body.source_name or "document.md")
    suggested = suggested.rsplit("/", 1)[-1] or "document.pdf"
    return ConvertMdToPdfResponse(
        pdf_base64=base64.b64encode(result.pdf_bytes).decode("ascii"),
        warnings=list(result.warnings),
        suggested_filename=suggested,
    )


@router.post("/{ws_id}/export-pdf", response_model=ExportPdfResponse)
async def export_workspace_pdf(
    ws_id: str,
    body: ExportPdfRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Export workspace Markdown to a sibling ``.pdf`` (shared ``md_to_pdf`` converter)."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)

    try:
        if target.space_id:
            backend = build_shared_workspace(target.space_id)
        else:
            backend = build_server_workspace(**_workspace_coords(user.user_id, target))
        result = await export_markdown_to_pdf_path(backend, body.path)
        if target.space_id:
            await shared_svc.record_file_change(
                space_id=target.space_id,
                actor_user_id=user.user_id,
                actor_via="user",
                action="file_written",
                path=result.output_path,
            )
    except ExportMarkdownError as e:
        raise ValidationError(e.message) from e

    return ExportPdfResponse(
        path=result.output_path,
        source_path=result.source_path,
        size_bytes=result.size_bytes,
        warnings=list(result.warnings),
    )


@router.get("/{ws_id}/edit/{path:path}", response_model=WorkspaceEditDoc)
async def read_workspace_file_for_edit(
    ws_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Read a cloud workspace file for in-panel editing (full text + mtime baseline).

    The editable counterpart of the truncated preview download — a save needs the
    whole file. Local ids are reached over desktop IPC, so they 409 like other ops.
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    try:
        if target.space_id:
            text, mtime_ms, eol = await _shared_read_edit(target.space_id, path)
        else:
            text, mtime_ms, eol = await read_file_for_edit(
                **_workspace_coords(user.user_id, target),
                path=path,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except (PathNotFound, NotAFile) as e:
        raise NotFoundError("文件不存在") from e
    except NotUTF8 as e:
        raise ValidationError("文件不是 UTF-8 文本，无法编辑") from e
    return WorkspaceEditDoc(text=text, mtime_ms=mtime_ms, eol=eol)


@router.put("/{ws_id}/edit/{path:path}", response_model=WorkspaceWriteResult)
async def write_workspace_file_text(
    ws_id: str,
    path: str,
    body: WorkspaceWriteRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Conditionally write editor text back to a cloud workspace file (mtime CAS).

    ``baseline_mtime_ms`` makes a save that raced an Agent turn return ``conflict``
    instead of clobbering it (云端硬化 §九). Local ids 409 (desktop owns the bytes).
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)

    max_bytes = settings.workspace_upload_max_bytes
    encoded = body.content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValidationError(f"文件超出 {max_bytes} 字节的上传上限")
    if target.space_id:
        shared_svc.check_capacity(target.space_id, incoming_bytes=len(encoded))

    try:
        if target.space_id:
            ok, mtime_ms = await _shared_write_cas(
                target.space_id,
                path,
                body.content,
                baseline_mtime_ms=body.baseline_mtime_ms,
                eol=body.eol,
            )
            if ok:
                await shared_svc.record_file_change(
                    space_id=target.space_id,
                    actor_user_id=user.user_id,
                    actor_via="user",
                    action="file_written",
                    path=path,
                )
        else:
            ok, mtime_ms = await write_file_text(
                **_workspace_coords(user.user_id, target),
                path=path,
                content=body.content,
                baseline_mtime_ms=body.baseline_mtime_ms,
                eol=body.eol,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except NotAFile as e:
        raise ValidationError("目标是目录，无法作为文件写入") from e
    return WorkspaceWriteResult(ok=ok, mtime_ms=mtime_ms, conflict=not ok)


@router.get("/{ws_id}/files/{path:path}")
async def download_workspace_file(
    ws_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Download a single file from a cloud workspace.

    Uses the panel-download path (upload-aligned ceiling + ``FileResponse``), not
    the AI ``read_bytes`` 5 MiB gate.
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    max_bytes = settings.workspace_upload_max_bytes
    try:
        if target.space_id:
            file_path = await _shared_resolve_download(
                target.space_id, path, max_bytes=max_bytes
            )
        else:
            file_path = await resolve_download_file(
                **_workspace_coords(user.user_id, target),
                path=path,
                max_bytes=max_bytes,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except (PathNotFound, NotAFile) as e:
        raise NotFoundError("文件不存在") from e
    except WorkspaceIOError as e:
        raise_http_for_download_io(e)

    filename = path.rsplit("/", 1)[-1] or "download"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        file_path,
        media_type=media_type,
        headers=download_headers(filename),
    )


@router.get("/{ws_id}/archive/{path:path}")
async def download_workspace_archive(
    ws_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Download a directory subtree as zip (selected dir as archive root).

    Independent of GET ``/{ws_id}/files/{path}`` (preview / single file). Capacity
    is the panel upload ceiling, not snapshot retention. Shared spaces that can
    download a file can download a folder zip — this is not the snapshot 409.
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    max_bytes = settings.workspace_upload_max_bytes
    try:
        if target.space_id:
            data = await _shared_zip_archive(
                target.space_id, path, max_bytes=max_bytes
            )
        else:
            data = await zip_subtree_for_download(
                **_workspace_coords(user.user_id, target),
                path=path,
                max_bytes=max_bytes,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except PathNotFound as e:
        raise NotFoundError("文件夹不存在") from e
    except NotADirectory as e:
        raise ValidationError("目标不是文件夹") from e
    except ArchiveLimitError as e:
        raise_http_for_archive_limit(e)
    except WorkspaceIOError as e:
        raise_http_for_download_io(e)

    return Response(
        content=data,
        media_type="application/zip",
        headers=download_headers(archive_filename(path)),
    )


@router.delete("/{ws_id}/files/{path:path}", response_model=StatusResponse)
async def delete_workspace_file(
    ws_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Delete a file or directory from a cloud workspace."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)
    try:
        if target.space_id:
            await _shared_delete(target.space_id, path)
            await shared_svc.record_file_change(
                space_id=target.space_id,
                actor_user_id=user.user_id,
                actor_via="user",
                action="file_deleted",
                path=path,
            )
        else:
            await delete_file(
                **_workspace_coords(user.user_id, target),
                path=path,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except PathNotFound as e:
        raise NotFoundError("文件不存在") from e
    except WorkspaceIOError as e:
        raise ValidationError(str(e) or "删除失败") from e
    return StatusResponse()


@router.post("/{ws_id}/move", response_model=StatusResponse)
async def move_workspace_file(
    ws_id: str,
    body: MoveFileRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Move/rename a file or directory within a cloud workspace."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)
    try:
        if target.space_id:
            await _shared_move(target.space_id, body.src, body.dst)
            await shared_svc.record_file_change(
                space_id=target.space_id,
                actor_user_id=user.user_id,
                actor_via="user",
                action="file_moved",
                path=body.dst,
                detail={"src": body.src},
            )
        else:
            await move_file(
                **_workspace_coords(user.user_id, target),
                src=body.src,
                dst=body.dst,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except PathNotFound as e:
        raise NotFoundError("文件不存在") from e
    except AlreadyExists as e:
        raise ValidationError("已存在同名文件") from e
    return StatusResponse()


@router.post("/{ws_id}/copy", response_model=StatusResponse)
async def copy_workspace_file(
    ws_id: str,
    body: MoveFileRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Copy a file or directory within a cloud workspace (recursive; no clobber)."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)
    try:
        if target.space_id:
            await _shared_copy(target.space_id, body.src, body.dst)
            await shared_svc.record_file_change(
                space_id=target.space_id,
                actor_user_id=user.user_id,
                actor_via="user",
                action="file_written",
                path=body.dst,
                detail={"src": body.src, "op": "copy"},
            )
        else:
            await copy_file(
                **_workspace_coords(user.user_id, target),
                src=body.src,
                dst=body.dst,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except PathNotFound as e:
        raise NotFoundError("文件不存在") from e
    except AlreadyExists as e:
        raise ValidationError("已存在同名文件") from e
    except WorkspaceIOError as e:
        raise ValidationError(str(e) or "复制失败") from e
    return StatusResponse()


@router.post("/{ws_id}/dirs", response_model=StatusResponse)
async def create_workspace_dir(
    ws_id: str,
    body: CreateDirRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Create a directory in a cloud workspace."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _require_shared_write(target)
    try:
        if target.space_id:
            await _shared_mkdir(target.space_id, body.path)
            await shared_svc.record_file_change(
                space_id=target.space_id,
                actor_user_id=user.user_id,
                actor_via="user",
                action="dir_created",
                path=body.path,
            )
        else:
            await create_dir(
                **_workspace_coords(user.user_id, target),
                path=body.path,
            )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except AlreadyExists as e:
        raise ValidationError("已存在同名文件或文件夹") from e
    return StatusResponse()


@router.post("/{ws_id}/clone", response_model=CloneRepoResponse)
async def clone_repo_into_workspace(
    ws_id: str,
    body: CloneRepoRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Clone a public git repository into a cloud workspace (决策⑤ · G3)."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _refuse_shared_extra(target)
    from agentcore.db.base import async_session_factory
    from agentcore.workspace.git_credentials import load_git_auth

    auth = None
    try:
        async with async_session_factory() as session:
            auth = await load_git_auth(session, user.user_id)
    except Exception:  # noqa: BLE001 — public clone still works without PAT table
        auth = None
    try:
        dest = await clone_repo(
            **_workspace_coords(user.user_id, target),
            repo_url=body.repo_url,
            dest=body.dest,
            auth=auth,
        )
    except ValueError as e:
        raise ValidationError(str(e)) from e
    except CloneError as e:
        raise ValidationError(f"克隆失败：{e}") from e
    return CloneRepoResponse(path=dest)


# --- Workspace snapshots (axis-3: backup / kept versions / download) ---


@router.get("/{ws_id}/snapshots", response_model=SnapshotListResponse)
async def list_workspace_snapshots(
    ws_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """List a workspace's snapshots (newest first). Allowed for local too —
    snapshots are object-store backed and keyed by ws (§五)."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _refuse_shared_extra(target)
    refs = await list_snapshots(
        user_id=user.user_id,
        folder_id=target.folder_id,
        conversation_id=target.conversation_id,
    )
    return SnapshotListResponse(
        data=[SnapshotSummary.model_validate(r) for r in refs],
        total=len(refs),
    )


@router.post("/{ws_id}/snapshots", response_model=SnapshotSummary, status_code=201)
async def create_workspace_snapshot(
    ws_id: str,
    body: CreateSnapshotRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Take a manual snapshot of a cloud workspace (a ``label`` keeps it as a
    version). Local workspaces snapshot via the desktop archive channel, not
    here (§五), so they are rejected with 409."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _refuse_shared_extra(target)
    # create_snapshot holds workspace_lock at the sink (A′).
    ref = await create_snapshot(
        **_workspace_coords(user.user_id, target),
        label=body.label,
    )
    return SnapshotSummary.model_validate(ref)


@router.post("/{ws_id}/snapshots/{snapshot_id}/restore", response_model=StatusResponse)
async def restore_workspace_snapshot(
    ws_id: str,
    snapshot_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Restore a cloud workspace to a snapshot (overwrites current files).

    Refused (409) for local workspaces: it would rewrite the unused server-side
    mirror, not the user's machine."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _refuse_shared_extra(target)
    # restore_snapshot holds workspace_lock at the sink (A′).
    try:
        await restore_snapshot(
            **_workspace_coords(user.user_id, target),
            snapshot_id=snapshot_id,
        )
    except SnapshotNotFound as e:
        raise NotFoundError("快照不存在") from e
    return StatusResponse()


@router.get("/{ws_id}/snapshots/{snapshot_id}/download")
async def download_workspace_snapshot(
    ws_id: str,
    snapshot_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Download a snapshot archive (zip). Allowed for local too (read-only)."""
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _refuse_shared_extra(target)
    try:
        data = await read_snapshot(
            user_id=user.user_id,
            folder_id=target.folder_id,
            conversation_id=target.conversation_id,
            snapshot_id=snapshot_id,
        )
    except SnapshotNotFound as e:
        raise NotFoundError("快照不存在") from e
    filename = f"workspace-{snapshot_id}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers=download_headers(filename),
    )


# --- AgentCore/trash (soft-delete list + one-click restore; not OS recycle bin) ---


@router.get("/{ws_id}/trash", response_model=TrashListResponse)
async def list_workspace_trash(
    ws_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """List reversible soft-deletes under ``AgentCore/trash`` (newest first).

    Cloud / sidecar only. Local OS recycle-bin deletes are **not** listed here —
    restore those via the system trash UI. Expired entries are purged on read
    (retention = ``workspace_retention_days``).
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _refuse_shared_extra(target)
    root = workspace_root_path(
        user_id=user.user_id,
        folder_rel_path=target.rel_path,
        conversation_id=target.conversation_id,
    )
    internal_root = workspace_internal_root(
        user_id=user.user_id,
        folder_id=target.folder_id,
        conversation_id=target.conversation_id,
    )
    entries = list_trash_entries(root=root, internal_root=internal_root)
    days = trash_retention_days()
    return TrashListResponse(
        data=[
            TrashEntrySummary(
                entry_id=e.entry_id,
                original_path=e.original_path,
                name=e.name,
                is_dir=e.is_dir,
                deleted_at=e.deleted_at,
            )
            for e in entries
        ],
        total=len(entries),
        retention_days=days,
    )


@router.post("/{ws_id}/trash/{entry_id}/restore", response_model=StatusResponse)
async def restore_workspace_trash(
    ws_id: str,
    entry_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    folder_repo: FolderRepository = Depends(get_folder_repo),
    shared_svc: SharedSpaceService = Depends(get_shared_space_service),
):
    """Restore one ``AgentCore/trash`` entry to its original relative path.

    Refused for local workspaces (409): files live on the desktop; use the
    desktop AgentCore/trash UI when the soft-delete fallback was used — never
    confuse with OS ``shell.trashItem``.
    """
    target = await _resolve_owned_workspace(
        ws_id, user.user_id, conv_repo, folder_repo, shared_svc
    )
    _require_cloud(target)
    _refuse_shared_extra(target)
    root = workspace_root_path(
        user_id=user.user_id,
        folder_rel_path=target.rel_path,
        conversation_id=target.conversation_id,
    )
    internal_root = workspace_internal_root(
        user_id=user.user_id,
        folder_id=target.folder_id,
        conversation_id=target.conversation_id,
    )
    try:
        async with workspace_lock(_storage_key(user.user_id, target)):
            restore_from_trash(root=root, entry_id=entry_id, internal_root=internal_root)
    except TrashNotFound as e:
        raise NotFoundError("软删条目不存在") from e
    except TrashExpiredError as e:
        raise ConflictError(str(e) or "软删条目已过期") from e
    except AlreadyExists as e:
        raise ConflictError(f"目标路径已存在，无法还原：{e}") from e
    except OutsideWorkspace as e:
        raise ValidationError(f"软删元数据路径非法：{e}") from e
    except WorkspaceIOError as e:
        raise ValidationError(str(e) or "还原失败") from e
    return StatusResponse()
