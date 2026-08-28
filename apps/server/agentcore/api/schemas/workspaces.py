"""Workspace schemas: local-mode binding, snapshots, and workspace file ops."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# --- Workspace local-mode binding (双模式工作区 §七) ---


class BindLocalWorkspaceRequest(BaseModel):
    """Bind a 裸聊's scratch workspace to a desktop FS root (switch to local mode).

    ``root_id`` is the desktop-minted handle for an authorized local directory
    (from the desktop ``addRoot`` flow). Only ungrouped conversations may rebind —
    project chats inherit an immutable project binding (``PUT`` returns 409).
    """

    root_id: str = Field(..., min_length=1, max_length=200)


class WorkspaceBindingResponse(BaseModel):
    """A conversation's resolved workspace mode + where its binding lives.

    ``mode`` / ``root_id`` follow the turn-routing口径 (``resolve_local_binding``):
    an explicit ``local_root_id`` OR the desktop's ``local_container_root_id`` default
    both count as local. ``source`` distinguishes how the effective bind was chosen
    so the client can present「显式绑定」vs「容器默认」without guessing.
    """

    mode: Literal["local", "cloud"]
    # Which record carries the binding: the shared folder, or the conversation.
    scope: Literal["folder", "conversation"]
    # The bound desktop root id when local; None when cloud.
    root_id: str | None = None
    # How the effective local root was chosen. Absent/None when cloud.
    source: Literal["explicit", "container"] | None = None


# --- W3 session read-only external directory grants ---


class GrantExternalReadonlyRequest(BaseModel):
    """Register a session-scoped desktop root for this conversation.

    Does **not** change workspace binding. ``root_id`` is the desktop-minted handle;
    absolute paths never appear on the wire. ``mode`` is ``readonly`` (W3),
    ``organize`` (move/copy/mkdir/trash-delete), or ``attach_rw`` (本机传统附加可写；
    云协作拒绝该 mode).
    """

    root_id: str = Field(..., min_length=1, max_length=200)
    label: str = Field(..., min_length=1, max_length=200)
    alias_hint: str | None = Field(None, max_length=64)
    mode: Literal["readonly", "organize", "attach_rw"] = "readonly"


class ExternalGrantItem(BaseModel):
    alias: str
    root_id: str
    label: str
    namespace: str  # ``external/<alias>``
    mode: Literal["readonly", "organize", "attach_rw"] = "readonly"


class ExternalGrantListResponse(BaseModel):
    data: list[ExternalGrantItem]


class ExternalGrantResponse(BaseModel):
    grant: ExternalGrantItem


class WorkspaceSummary(BaseModel):
    """One addressable workspace in the file hub (文件中枢统一 Step 1).

    A folder project (``folder:<id>``) or an ungrouped conversation space
    (``conv:<id>``). ``location`` tells the hub how to reach its files: a cloud
    workspace via the ``/v1/workspaces/{ws_id}/files`` REST family; a local one
    over desktop IPC against ``root_id`` (its server-side dir is not the truth).
    """

    ws_id: str
    name: str
    location: Literal["cloud", "local"]
    # The bound desktop root id when local; None when cloud.
    root_id: str | None = None
    # Sub-path within ``root_id`` this workspace lives at (工作区对称化 D1a). Set for a
    # per-conversation local workspace lazily promoted under a shared container root;
    # None for cloud and for explicitly-added local projects bound at their root. The
    # desktop browses ``root_id`` + ``subpath`` so each sub-workspace shows only its
    # own files.
    subpath: str | None = None
    # Whether the space holds files. Folders always list (a project is a project);
    # ungrouped spaces list only when non-empty (F1) — so this is the filter that
    # let them in. Always True for local (the server can't see local files).
    has_files: bool


class WorkspaceListResponse(BaseModel):
    data: list[WorkspaceSummary]
    total: int


# --- Workspace snapshots ---


class CreateSnapshotRequest(BaseModel):
    """Take a manual snapshot of a conversation's workspace.

    A non-empty ``label`` is a user-pinned kept version when it is not a system
    label (turn-baseline / handoff / export·merge). Automatic post-turn backups
    use ``label=null``.
    """

    label: str | None = Field(None, max_length=200)


class SnapshotSummary(BaseModel):
    """One persisted workspace snapshot (kept version or automatic backup)."""

    snapshot_id: str
    label: str | None
    created_at: datetime
    size_bytes: int

    model_config = {"from_attributes": True}


class SnapshotListResponse(BaseModel):
    data: list[SnapshotSummary]
    total: int


# --- AgentCore/trash (soft-delete restore; not OS recycle bin) ---


class TrashEntrySummary(BaseModel):
    """One reversible entry under workspace ``AgentCore/trash/<id>/``."""

    entry_id: str
    original_path: str
    name: str
    is_dir: bool
    deleted_at: datetime


class TrashListResponse(BaseModel):
    """List of AgentCore/trash entries (newest first).

    ``retention_days`` mirrors ``workspace_retention_days`` — product one-click
    restore only covers this zone; Local OS ``shell.trashItem`` is a separate track.
    """

    data: list[TrashEntrySummary]
    total: int
    retention_days: int


# --- Workspace files (bring files in / take results out) ---


class WorkspaceFileEntry(BaseModel):
    """One entry in a workspace listing — relative POSIX path + kind + optional meta.

    ``size_bytes`` / ``mtime_ms`` feed mobile list subtitles. Files fill both when
    known; directories keep ``size_bytes=None`` (``mtime_ms`` when available).
    ``mtime_ms`` matches edit CAS: ``st_mtime_ns // 1_000_000``. Absent → ``None``
    (backward compatible).
    """

    path: str
    is_dir: bool
    size_bytes: int | None = None
    mtime_ms: int | None = None

    model_config = {"from_attributes": True}


class WorkspaceFileListResponse(BaseModel):
    """One directory of a workspace (or its whole tree when ``recursive``).

    ``truncated`` is True when the listing hit the server's entry ceiling and
    entries were left out — clients must show that rather than render the short
    list as the complete contents. Defaults False so older clients keep working.
    """

    data: list[WorkspaceFileEntry]
    total: int
    truncated: bool = False


class WorkspaceFileIndexResponse(BaseModel):
    """Flat file-path list for @ mentions (文件中枢统一 F4).

    Files only (no dirs), ignore-pruned, capped — ``truncated`` is True when the
    cap was hit. Mirrors the desktop ``fsApi.listFiles`` so cloud workspace files
    can feed the same @ index local roots already do.
    """

    data: list[str]
    total: int
    truncated: bool


class UploadFileResponse(BaseModel):
    """Result of a workspace file upload."""

    path: str
    size_bytes: int


class ExportDocxRequest(BaseModel):
    """Export a workspace Markdown file to a sibling ``.docx`` (确定性转换器)."""

    path: str = Field(..., min_length=1, max_length=1000)


class ExportDocxResponse(BaseModel):
    """Result of Markdown → Word export into the workspace."""

    path: str
    source_path: str
    size_bytes: int
    warnings: list[str] = Field(default_factory=list)


class ConvertMdToDocxRequest(BaseModel):
    """Stateless Markdown → Word conversion (local desktop UI; images as base64)."""

    markdown: str
    # Raw Markdown image ``src`` → base64 payload (omit / null = missing → warning).
    images: dict[str, str | None] = Field(default_factory=dict)
    source_name: str = Field("document.md", max_length=500)


class ConvertMdToDocxResponse(BaseModel):
    """Base64-encoded .docx plus non-fatal warnings."""

    docx_base64: str
    warnings: list[str] = Field(default_factory=list)
    suggested_filename: str


class ExportPdfRequest(BaseModel):
    """Export a workspace Markdown file to a sibling ``.pdf`` (确定性转换器)."""

    path: str = Field(..., min_length=1, max_length=1000)


class ExportPdfResponse(BaseModel):
    """Result of Markdown → PDF export into the workspace."""

    path: str
    source_path: str
    size_bytes: int
    warnings: list[str] = Field(default_factory=list)


class ConvertMdToPdfRequest(BaseModel):
    """Stateless Markdown → PDF conversion (local desktop UI)."""

    markdown: str
    source_name: str = Field("document.md", max_length=500)


class ConvertMdToPdfResponse(BaseModel):
    """Base64-encoded .pdf plus non-fatal warnings."""

    pdf_base64: str
    warnings: list[str] = Field(default_factory=list)
    suggested_filename: str


class WorkspaceEditDoc(BaseModel):
    """Full text of a cloud workspace file for in-panel editing, plus CAS baseline.

    Unlike the preview download (truncated at the transfer cap), this returns the
    **whole** file so a save never drops the tail. ``mtime_ms`` is the write-time CAS
    baseline (compared on write); ``eol`` lets the editor restore the original line
    ending. Cloud files are server-stored UTF-8, so there is no encoding field.
    """

    text: str
    mtime_ms: int
    eol: Literal["lf", "crlf"]


class WorkspaceWriteRequest(BaseModel):
    """Conditional write of editor text to a cloud workspace file (mtime CAS).

    ``baseline_mtime_ms`` is the version the edit started from (``0`` = new file); a
    mismatch with the current disk mtime returns a conflict instead of clobbering an
    Agent's concurrent write. ``content`` uses ``\\n`` newlines; the server restores
    ``eol`` on write. Byte size is bounded in the route (same cap as upload).
    """

    content: str
    eol: Literal["lf", "crlf"] = "lf"
    baseline_mtime_ms: int = Field(0, ge=0)


class WorkspaceWriteResult(BaseModel):
    """Outcome of a conditional write.

    ``ok`` → ``mtime_ms`` is the new version (next baseline). On ``conflict`` →
    ``ok`` is False and ``mtime_ms`` is the **current disk** version, so the client
    can offer "overwrite anyway" by re-writing with it as the baseline.
    """

    ok: bool
    mtime_ms: int
    conflict: bool = False


class MoveFileRequest(BaseModel):
    """Move/rename a workspace file or directory (both workspace-relative)."""

    src: str = Field(..., min_length=1, max_length=1000)
    dst: str = Field(..., min_length=1, max_length=1000)


class CreateDirRequest(BaseModel):
    """Create a workspace directory (workspace-relative, parents created)."""

    path: str = Field(..., min_length=1, max_length=1000)


class CloneRepoRequest(BaseModel):
    """Clone an http(s) git repository into the conversation's workspace (G3)."""

    repo_url: str = Field(..., min_length=1, max_length=2000)
    # Optional workspace-relative target dir; defaults to the repo name.
    dest: str | None = Field(None, max_length=500)


class CloneRepoResponse(BaseModel):
    """Result of a workspace clone — the relative dir the repo landed in."""

    path: str
