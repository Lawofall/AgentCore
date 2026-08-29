"""Workspace files (bring files in / take results out: 文件进出·先上传)."""

import mimetypes

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_db,
)
from agentcore.api.download_headers import download_headers
from agentcore.api.schemas import (
    CloneRepoRequest,
    CloneRepoResponse,
    CreateDirRequest,
    ExportDocxRequest,
    ExportDocxResponse,
    MoveFileRequest,
    StatusResponse,
    UploadFileResponse,
    WorkspaceEditDoc,
    WorkspaceFileEntry,
    WorkspaceFileListResponse,
    WorkspaceWriteRequest,
    WorkspaceWriteResult,
)
from agentcore.config import settings
from agentcore.conversation.common import resolve_turn_file_workspace
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.db.models import Conversation
from agentcore.db.repositories import ConversationRepository
from agentcore.docs_export.workspace_export import ExportMarkdownError, export_markdown_path
from agentcore.folders.placement import resolve_folder_placement
from agentcore.storage._archive import ArchiveLimitError
from agentcore.workspace.files import (
    archive_filename,
    copy_file,
    create_dir,
    delete_file,
    list_files,
    move_file,
    raise_http_for_archive_limit,
    raise_http_for_download_io,
    read_file_for_edit,
    resolve_download_file,
    upload_file,
    write_file_text,
    zip_subtree_for_download,
)
from agentcore.workspace.git import CloneError, clone_repo
from agentcore.workspace.locate import WorkspaceCoords, build_server_workspace
from agentcore.workspace.protocol import (
    AlreadyExists,
    NotADirectory,
    NotAFile,
    NotUTF8,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceIOError,
)

from ._helpers import _get_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _file_workspace_folder_id(conv: Conversation) -> str | None:
    """Same physical root as AI turn tools (birth folder, else auto desk, else scratch).

    Reuses :func:`resolve_turn_file_workspace` — never a second judgment. Does not
    change affiliation / sidebar / memory scope (``conv.folder_id`` stays birth).
    """
    ws_folder_id, _ = resolve_turn_file_workspace(
        birth_folder_id=conv.folder_id,
        auto_desk_folder_id=getattr(conv, "auto_desk_folder_id", None),
    )
    return ws_folder_id


async def _workspace_coords(
    user_id: str, conv: Conversation, session: AsyncSession
) -> WorkspaceCoords:
    """The four coordinates every ``workspace.files`` call needs.

    Resolved once per request here rather than inside the file service: the
    service stays a pure function of ``(user, folder, placement, conversation)``,
    and the placement lookup rides this request's own session.
    """
    folder_id = _file_workspace_folder_id(conv)
    placement = await resolve_folder_placement(folder_id, session=session)
    return {
        "user_id": user_id,
        "folder_id": folder_id,
        "folder_rel_path": placement.rel_path,
        "conversation_id": conv.id,
    }


@router.get("/{conversation_id}/workspace/files", response_model=WorkspaceFileListResponse)
async def list_workspace_files(
    conversation_id: str,
    user: AuthUser,
    recursive: bool = Query(False),
    path: str = Query(".", description="工作区相对目录（`.` = 根）"),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """List one directory of the conversation's scratch workspace (or its tree)."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    listing = await list_files(
        **await _workspace_coords(user.user_id, conv, session),
        path=path,
        recursive=recursive,
    )
    return WorkspaceFileListResponse(
        data=[WorkspaceFileEntry.model_validate(e) for e in listing.entries],
        total=len(listing.entries),
        truncated=listing.truncated,
    )


@router.put("/{conversation_id}/workspace/files/{path:path}", response_model=UploadFileResponse)
async def upload_workspace_file(
    conversation_id: str,
    path: str,
    request: Request,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Upload (create/overwrite) a workspace file from the raw request body.

    The body is the file bytes (no multipart); ``path`` is the workspace-relative
    target. Bounded by ``workspace_upload_max_bytes`` so one request can't exhaust
    memory. A path that escapes the workspace is rejected (422).
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)

    max_bytes = settings.workspace_upload_max_bytes
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise ValidationError(f"文件超出 {max_bytes} 字节的上传上限")
    data = await request.body()
    if len(data) > max_bytes:
        raise ValidationError(f"文件超出 {max_bytes} 字节的上传上限")

    try:
        written = await upload_file(
            **await _workspace_coords(user.user_id, conv, session),
            path=path,
            data=data,
        )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    return UploadFileResponse(path=path, size_bytes=written)


@router.post(
    "/{conversation_id}/workspace/export-docx",
    response_model=ExportDocxResponse,
)
async def export_conversation_workspace_docx(
    conversation_id: str,
    body: ExportDocxRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Export a conversation-workspace Markdown file to a sibling ``.docx``."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        backend = build_server_workspace(**await _workspace_coords(user.user_id, conv, session))
        result = await export_markdown_path(backend, body.path)
    except ExportMarkdownError as e:
        raise ValidationError(e.message) from e
    return ExportDocxResponse(
        path=result.output_path,
        source_path=result.source_path,
        size_bytes=result.size_bytes,
        warnings=list(result.warnings),
    )


@router.get(
    "/{conversation_id}/workspace/edit/{path:path}",
    response_model=WorkspaceEditDoc,
)
async def read_workspace_file_for_edit(
    conversation_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Read a workspace file for in-panel editing (full text + mtime CAS baseline).

    Distinct from the raw-bytes download (preview, truncated): editing needs the whole
    file or a save would drop the tail.
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        text, mtime_ms, eol = await read_file_for_edit(
            **await _workspace_coords(user.user_id, conv, session),
            path=path,
        )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except (PathNotFound, NotAFile) as e:
        raise NotFoundError("文件不存在") from e
    except NotUTF8 as e:
        raise ValidationError("文件不是 UTF-8 文本，无法编辑") from e
    return WorkspaceEditDoc(text=text, mtime_ms=mtime_ms, eol=eol)


@router.put(
    "/{conversation_id}/workspace/edit/{path:path}",
    response_model=WorkspaceWriteResult,
)
async def write_workspace_file(
    conversation_id: str,
    path: str,
    body: WorkspaceWriteRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Conditionally write editor text back to a workspace file (mtime CAS).

    The write-time CAS (``baseline_mtime_ms``) makes a save that raced an Agent turn
    return ``conflict`` instead of clobbering it.
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)

    max_bytes = settings.workspace_upload_max_bytes
    if len(body.content.encode("utf-8")) > max_bytes:
        raise ValidationError(f"文件超出 {max_bytes} 字节的上传上限")

    try:
        ok, mtime_ms = await write_file_text(
            **await _workspace_coords(user.user_id, conv, session),
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


@router.get("/{conversation_id}/workspace/files/{path:path}")
async def download_workspace_file(
    conversation_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Download a single file from the conversation's scratch workspace.

    Panel download uses upload-aligned capacity + ``FileResponse`` — not the AI
    ``read_bytes`` 5 MiB gate.
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        file_path = await resolve_download_file(
            **await _workspace_coords(user.user_id, conv, session),
            path=path,
            max_bytes=settings.workspace_upload_max_bytes,
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


@router.get("/{conversation_id}/workspace/archive/{path:path}")
async def download_workspace_archive(
    conversation_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Download a directory subtree as zip (selected dir as archive root).

    Independent of GET ``.../workspace/files/{path}`` (preview / single file).
    Capacity is the panel upload ceiling, not snapshot retention.
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        data = await zip_subtree_for_download(
            **await _workspace_coords(user.user_id, conv, session),
            path=path,
            max_bytes=settings.workspace_upload_max_bytes,
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


@router.delete("/{conversation_id}/workspace/files/{path:path}", response_model=StatusResponse)
async def delete_workspace_file(
    conversation_id: str,
    path: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Delete a file or directory from the conversation's scratch workspace."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        await delete_file(
            **await _workspace_coords(user.user_id, conv, session),
            path=path,
        )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except PathNotFound as e:
        raise NotFoundError("文件不存在") from e
    return StatusResponse()


@router.post("/{conversation_id}/workspace/move", response_model=StatusResponse)
async def move_workspace_file(
    conversation_id: str,
    body: MoveFileRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Move/rename a file or directory within the conversation's scratch workspace."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        await move_file(
            **await _workspace_coords(user.user_id, conv, session),
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


@router.post("/{conversation_id}/workspace/copy", response_model=StatusResponse)
async def copy_workspace_file(
    conversation_id: str,
    body: MoveFileRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Copy a file or directory within the conversation's scratch workspace."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        await copy_file(
            **await _workspace_coords(user.user_id, conv, session),
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


@router.post("/{conversation_id}/workspace/dirs", response_model=StatusResponse)
async def create_workspace_dir(
    conversation_id: str,
    body: CreateDirRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Create a directory in the conversation's scratch workspace."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    try:
        await create_dir(
            **await _workspace_coords(user.user_id, conv, session),
            path=body.path,
        )
    except OutsideWorkspace as e:
        raise ValidationError("路径非法：超出工作区范围") from e
    except AlreadyExists as e:
        raise ValidationError("已存在同名文件或文件夹") from e
    return StatusResponse()


@router.post("/{conversation_id}/workspace/clone", response_model=CloneRepoResponse)
async def clone_repo_into_workspace(
    conversation_id: str,
    body: CloneRepoRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
):
    """Clone a public git repository into the conversation's scratch workspace (决策⑤ · G3)."""
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
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
            **await _workspace_coords(user.user_id, conv, session),
            repo_url=body.repo_url,
            dest=body.dest,
            auth=auth,
        )
    except ValueError as e:
        raise ValidationError(str(e)) from e
    except CloneError as e:
        raise ValidationError(f"克隆失败：{e}") from e
    return CloneRepoResponse(path=dest)
