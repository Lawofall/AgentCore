"""WorkspaceBackend Protocol — the single seam for file + execution access.

Every filesystem / code-execution tool talks to a ``WorkspaceBackend`` instead
of touching ``Path`` directly. This is what lets one agent loop run against two
execution platforms without forking the engine:

- ``ServerWorkspace`` — files and execution live on the server (cloud mode).
- ``LocalWorkspace`` — files and execution live on the user's machine, reached
  over the desktop channel (local mode, added later).

Design constraints (pinned now so the contract never breaks under us):

- **Lean.** The backend owns exactly the pair that must share a platform: file
  I/O (axis 1) and code execution (axis 2). Persistence / snapshotting (axis 3)
  is deliberately NOT here — that is a turn-level storage policy handled by a
  separate ``StorageProvider``.
- **Typed failures.** Methods raise ``WorkspaceError`` subclasses instead of
  returning sentinel strings, so the (thin) tool layer can map each failure to
  its exact user-facing message and a remote ``LocalWorkspace`` can serialize
  the failure kind. The tool layer is responsible for catching these.
- **No absolute paths leak.** All inputs and outputs are workspace-relative
  (POSIX) paths; ``root_label`` is the only human-facing name for the root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult

if TYPE_CHECKING:
    from agentcore.workspace.attachment_parse import ExtractResult


class WorkspaceError(Exception):
    """Base for all workspace-backend failures (caught and mapped by tools)."""


class OutsideWorkspace(WorkspaceError):
    """A supplied path resolved outside the workspace root (traversal guard)."""


class PathNotFound(WorkspaceError):
    """The target file or directory does not exist."""


class NotAFile(WorkspaceError):
    """A file was expected but the path is a directory (or other non-file)."""


class NotADirectory(WorkspaceError):
    """A directory was expected but the path is not one."""


class AlreadyExists(WorkspaceError):
    """The destination of a ``move`` already exists (would clobber)."""


class NotUTF8(WorkspaceError):
    """The file is binary / not decodable as text (UTF-8 or GB18030)."""


class NoMatch(WorkspaceError):
    """``replace``: ``old`` was not found in the target file."""


class AmbiguousMatch(WorkspaceError):
    """``replace``: ``old`` matched multiple times without ``all_=True``."""

    def __init__(self, count: int, message: str = "") -> None:
        self.count = count
        super().__init__(message or f"{count} matches")


class WorkspaceIOError(WorkspaceError):
    """A low-level I/O failure (read/write) that is not one of the above.

    Channel-family failures that tools must branch on are subclasses below.
    They stay ``WorkspaceIOError`` so ``except WorkspaceIOError`` still works.
    They are never ``AgentCoreError`` — mid-turn IO must not enter Class B
    send-as-if-never-happened. Turn-start presence is a separate coded type.
    """


class WorkspaceLivenessTimeout(WorkspaceIOError):
    """Channel settle hang or desktop deadline abort.

    Fails this op only. Does not mean the desktop is disconnected.
    """


class WorkspacePresenceDisconnected(WorkspaceIOError):
    """Fulfiller gone: no fulfiller, origin device offline, or root not held.

    Mid-turn file tools retire the family. Turn-start presence is
    ``AgentCoreError`` (``LOCAL_*``), not this type.
    """


class WorkspaceReconnect(WorkspaceIOError):
    """Fulfill transport dropped while the desktop was already running this op.

    Retry is not futile; not a presence disconnect.
    """


@dataclass(frozen=True)
class DirEntry:
    """One entry from ``list`` — workspace-relative POSIX path + kind.

    ``size_bytes`` / ``mtime_ms`` are optional metadata for UI subtitles (mobile).
    Files fill both when known; directories keep ``size_bytes=None`` and may still
    expose ``mtime_ms``. ``mtime_ms`` matches edit CAS: ``st_mtime_ns // 1_000_000``.
    """

    path: str
    is_dir: bool
    size_bytes: int | None = None
    mtime_ms: int | None = None


@dataclass(frozen=True)
class DirListing:
    """Bounded result of ``list`` — the entries plus whether the cap cut the rest.

    Iterates (and measures) as its ``entries`` so call sites that only browse the
    listing stay unchanged. Anything that shows the listing to a **user or the
    model** must read ``truncated`` and say so: a listing that silently stops at
    the cap reads as "my files are gone", which is the one thing a file view may
    never imply.
    """

    entries: list[DirEntry]
    truncated: bool = False

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True)
class ReadLinesResult:
    """Bounded slice from ``read_lines`` — 1-based inclusive line range."""

    lines: list[str]
    start_line: int
    end_line: int
    total_lines: int


@dataclass(frozen=True)
class ReadHeadResult:
    """First bytes of a file plus total size. Not a whole-file ingest."""

    data: bytes
    size_bytes: int


@dataclass(frozen=True)
class TreeEntry:
    """One node from ``list_tree`` — workspace-relative path + depth."""

    path: str
    is_dir: bool
    depth: int


@dataclass
class TreeResult:
    """Bounded recursive directory listing from ``list_tree``."""

    entries: list[TreeEntry]
    truncated: bool
    elided_count: int
    # Soft skips (e.g. per-dir access denied) — listing still succeeds.
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IndexFileEntry:
    """One file from ``index_files`` — path plus optional local-stat fingerprint.

    ``mtime_ms`` matches edit CAS: ``st_mtime_ns // 1_000_000``.
    """

    path: str
    mtime_ms: int | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class IndexFilesResult:
    """Flat file index from ``index_files`` (paths + optional fingerprints).

    Unpacks as ``(paths, truncated)`` so existing call sites stay valid.
    """

    paths: list[str]
    truncated: bool
    entries: tuple[IndexFileEntry, ...] = ()

    def __iter__(self):
        yield self.paths
        yield self.truncated

    def fingerprints(self) -> dict[str, tuple[int, int]]:
        """``path → (mtime_ms, size_bytes)`` for entries with both fields set."""
        out: dict[str, tuple[int, int]] = {}
        for e in self.entries:
            if e.mtime_ms is not None and e.size_bytes is not None:
                out[e.path] = (int(e.mtime_ms), int(e.size_bytes))
        return out


@dataclass(frozen=True)
class ReplaceOutcome:
    """Result of ``replace``: how many spans changed, and where the first was."""

    count: int
    first_line: int | None = None


@dataclass(frozen=True)
class GrepHit:
    """One content match: workspace-relative POSIX path, 1-based line, text."""

    path: str
    line_no: int
    text: str


@dataclass
class GrepQuery:
    """Inputs for a ``grep`` content search (serializable for remote backends)."""

    pattern: str
    directory: str = "."
    glob: str | None = None
    case_insensitive: bool = False
    files_only: bool = False
    max_results: int = 50


@dataclass
class GrepResult:
    """Bounded result of a ``grep``: line hits, per-file counts, totals, cap.

    ``truncated`` means this batch hit the result cap — not that the alphabetically
    first N matches of a full-corpus scan were chosen. Returned hits are sorted
    for display.
    """

    hits: list[GrepHit] = field(default_factory=list)
    file_counts: list[tuple[str, int]] = field(default_factory=list)
    total_matches: int = 0
    truncated: bool = False
    # Soft skips (e.g. rg IO / access denied on one subtree) — search still succeeds.
    warnings: list[str] = field(default_factory=list)


@dataclass
class GlobFilesQuery:
    """``rg --files`` listing for the glob tool (serializable for the channel)."""

    directory: str = "."
    globs: tuple[str, ...] = ()
    max_depth: int | None = None
    max_entries: int = 50
    reveal_archives: bool = False


@dataclass
class GlobFilesResult:
    """Bounded file paths from ``glob_files`` (files only; sorted for display)."""

    paths: list[str] = field(default_factory=list)
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


class WorkspaceBackend(Protocol):
    """File + execution access for one workspace, on one execution platform."""

    location: Literal["server", "local"]
    root_label: str  # human-facing root name for relative-path rendering
    # Turn-scoped paths exempt from AI-noise list hiding (attachments / materials).
    # Implementations default to empty; prepare/resume stamp before tools run.
    ai_list_materials: frozenset[str]
    # When True, AI list/tree may reveal archive suffixes (zip/rar/…) that are
    # otherwise noise-hidden — set briefly by glob when the pattern targets
    # archives; implementations default False.
    ai_list_reveal_archives: bool

    @property
    def dirty(self) -> bool:
        """True once any mutating op (write/replace/execute) ran this turn.

        Read-only on the Protocol (implementations expose ``@property`` over
        an internal flag). Callers snapshot only workspaces a turn actually
        touched (决策⑥: 改过文件的任务才后台备份). Read-only ops
        (read/list/grep) never set it.
        """
        ...

    async def read(self, path: str) -> str:
        """Return the UTF-8 text content of ``path``.

        Raises ``OutsideWorkspace`` / ``PathNotFound`` / ``NotAFile`` /
        ``WorkspaceIOError``.
        """
        ...

    async def write(self, path: str, content: str) -> int:
        """Create or overwrite ``path`` (with parents); return chars written.

        Raises ``OutsideWorkspace`` / ``WorkspaceIOError``.
        """
        ...

    async def append(self, path: str, content: str) -> int:
        """Append ``content`` to ``path`` (create with parents if missing); return chars appended.

        Raises ``OutsideWorkspace`` / ``PathNotFound`` / ``NotAFile`` / ``WorkspaceIOError``.
        """
        ...

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        """Return the raw bytes of ``path`` (binary-safe; AI / tool whole-file read).

        Default capacity is ``WORKSPACE_READ_MAX_BYTES`` (text). Explicit
        ``max_bytes`` may rise to ``WORKSPACE_REPLACE_MAX_BYTES`` (edit ingest).
        Channel-side Office/PDF extract (``LocalWorkspace``) still passes
        ``OFFICE_EXTRACT_CHANNEL_MAX_BYTES``. On-disk backends must use
        :meth:`extract_office` instead of this method for Office/PDF. HTTP panel
        download must not use this — it goes through the dedicated download path
        (upload-aligned ceiling + ``FileResponse``). Raises ``OutsideWorkspace`` /
        ``PathNotFound`` / ``NotAFile`` / ``WorkspaceIOError``.
        """
        ...

    async def read_head(
        self, path: str, *, max_bytes: int | None = None
    ) -> ReadHeadResult:
        """Return the first ``max_bytes`` (default 1024) plus total file size.

        Does **not** apply the whole-file ``read_bytes`` gate:
        a 20 MiB PDF can still yield its magic. Peek length is clamped to
        ``WORKSPACE_READ_HEAD_MAX_BYTES``. Raises ``OutsideWorkspace`` /
        ``PathNotFound`` / ``NotAFile`` / ``WorkspaceIOError``.
        """
        ...

    async def extract_office(
        self, path: str, *, ext: str, start_page: int = 1
    ) -> ExtractResult:
        """Extract Office/PDF text without the tool layer slurping the file.

        On-disk backends (cloud / sidecar ``ServerWorkspace``) stat first, then
        open the file in the extract child (disk ingest cap). Channel
        ``LocalWorkspace`` still ingests via ``read_bytes`` (JSON/base64 IPC;
        desktop has no Python extract stack; channel ingest cap). ``start_page``
        is extract-internal (1-based PDF window); model-facing ``read`` always
        extracts the first window. Path errors raise like ``read_bytes``;
        conversion failures return an ``ExtractResult``, they are not raised.
        """
        ...

    async def write_bytes(self, path: str, data: bytes) -> int:
        """Create or overwrite ``path`` with raw ``data`` (with parents).

        The byte-level counterpart of ``write`` for binary uploads; returns the
        number of bytes written. Raises ``OutsideWorkspace`` / ``WorkspaceIOError``.
        """
        ...

    async def list(
        self, directory: str, pattern: str, *, cap: int | None = None
    ) -> DirListing:
        """List entries under ``directory`` matching glob ``pattern`` (bounded).

        ``pattern`` containing ``**`` recurses; noise dirs are pruned as the walk
        descends so a ``.git`` / ``node_modules`` subtree cannot spend the budget
        on entries that are about to be filtered out. ``cap`` overrides the
        backend default (the AI-facing ceiling); the file panel passes its own,
        larger browse ceiling. The result reports ``truncated`` — callers that
        render the listing must pass that on instead of cutting silently.

        Raises ``OutsideWorkspace`` / ``NotADirectory`` / ``WorkspaceIOError``.
        """
        ...

    async def exists(self, path: str) -> bool:
        """True iff ``path`` is an existing regular file (not a directory).

        Existence must **not** go through AI-noise browse filters — residency
        checks and similar oracles need truth, not ``file_list`` visibility.
        Missing path / not a file → ``False``. Raises ``OutsideWorkspace`` /
        ``WorkspaceIOError``.
        """
        ...

    async def read_lines(
        self, path: str, *, offset: int = 1, limit: int | None = None
    ) -> ReadLinesResult:
        """Return a 1-based line slice of ``path`` (``limit`` caps rows returned).

        Streams from disk. File size is not an admission ticket (unlike
        :meth:`read` / :meth:`read_bytes`). ``total_lines`` is counted in the
        same pass. Raises ``OutsideWorkspace`` / ``PathNotFound`` / ``NotAFile`` /
        ``WorkspaceIOError``. When ``offset`` is past EOF, returns empty ``lines``
        with the correct ``total_lines``.
        """
        ...

    async def list_tree(
        self,
        directory: str,
        *,
        pattern: str = "*",
        max_depth: int = 3,
        max_entries: int = 200,
    ) -> TreeResult:
        """Recursively list ``directory`` as a depth-bounded tree (ignore-pruned).

        ``pattern="*"`` emits directories plus matching files so the tree stays
        connected. A narrower ``pattern`` emits matching names only (still
        descends unmatched directories). Raises ``OutsideWorkspace`` /
        ``PathNotFound`` (missing) / ``NotADirectory`` (exists but is not a
        directory) / ``WorkspaceIOError``. Local backends may return an empty
        tree for a missing relative dir (lazy workspace) instead of
        ``PathNotFound``.
        """
        ...

    async def index_files(
        self, cap: int | None = None, *, order: str = "path"
    ) -> IndexFilesResult:
        """Flat, ignore-pruned, capped list of workspace-relative file paths.

        Files only (no directories), ``IGNORED_DIRS`` pruned, capped at ``cap``
        (``truncated`` True when the cap was hit; ``cap=None`` uses the backend default).
        ``order`` picks the sort (and thus what survives truncation): ``"path"``
        (default) is POSIX-alphabetical — the @-mention / picker view;
        ``"recent"`` is newest-first by mtime so a worker manifest spends
        its budget on the most-likely-relevant files in a big tree, not whatever sorts
        first. Channel / local backends may fill ``entries`` with ``mtime_ms`` +
        ``size_bytes``. The shared file-discovery primitive behind @ mentions (文件中枢统一 F4) and
        the worker workspace manifest — so both see the same flat view whether the
        workspace is cloud (``ServerWorkspace``) or local (``LocalWorkspace``, indexed on
        the desktop). Read-only (never sets ``dirty``); an empty / not-yet-promoted
        workspace returns an empty ``IndexFilesResult``.
        """
        ...

    async def mkdir(self, path: str) -> None:
        """Create directory ``path`` (with parents).

        Refuses to recreate the root or an existing path. Raises
        ``OutsideWorkspace`` / ``AlreadyExists`` / ``WorkspaceIOError``.
        """
        ...

    async def delete(self, path: str, *, permanent: bool = False) -> None:
        """Delete ``path`` (a file, or a directory and its contents).

        Default is reversible: local Electron channels move to the OS recycle
        bin (system trash — **no** product one-click restore); cloud / sidecar
        backends move into ``AgentCore/trash/`` with restore metadata (list +
        restore API). ``permanent=True`` hard-deletes. Refuses to delete
        the workspace root itself. Raises ``OutsideWorkspace`` /
        ``PathNotFound`` / ``WorkspaceIOError``.
        """
        ...

    async def copy(self, src: str, dst: str) -> None:
        """Copy file or directory tree ``src`` to ``dst`` (creating parents).

        Supports binary files and recursive directory trees. Refuses to copy
        the root, overwrite an existing ``dst``, or copy a directory into
        itself / a descendant. One-way copy from the workspace into an
        ``organize`` external mount is allowed; reverse, readonly dest, and
        cross-mount copy stay denied. Raises ``OutsideWorkspace`` /
        ``PathNotFound`` / ``AlreadyExists`` / ``WorkspaceIOError``.
        """
        ...

    async def move(self, src: str, dst: str) -> None:
        """Move/rename ``src`` to ``dst`` (creating ``dst``'s parents).

        Refuses to move the root or to overwrite an existing ``dst``. Raises
        ``OutsideWorkspace`` / ``PathNotFound`` / ``AlreadyExists`` /
        ``WorkspaceIOError``.
        """
        ...

    async def replace(self, path: str, old: str, new: str, *, all_: bool) -> ReplaceOutcome:
        """Replace exact span(s) ``old`` -> ``new`` in ``path`` atomically.

        Decode matches ``read`` (UTF-8 then GB18030); write-back uses that
        encoding. Unique-match ingest is ``WORKSPACE_REPLACE_MAX_BYTES`` (upload
        / grep cap), not the 5 MiB ``read()`` gate. Raises ``OutsideWorkspace`` /
        ``PathNotFound`` / ``NotAFile`` / ``NotUTF8`` / ``NoMatch`` /
        ``AmbiguousMatch`` / ``WorkspaceIOError``. Argument validation (empty
        ``old``, ``old == new``) is the caller's job.
        """
        ...

    async def grep(self, query: GrepQuery) -> GrepResult:
        """Regex-search file contents under ``query.directory`` (bounded).

        ``query.directory`` may be a directory (recursed, ``glob``-filtered) or a
        single file (scanned alone, ``glob`` ignored — rg PATTERN FILE). Raises
        ``OutsideWorkspace`` / ``PathNotFound``. The regex is assumed already
        validated by the caller. One ripgrep process; stop at ``max_results``.
        """
        ...

    async def glob_files(self, query: GlobFilesQuery) -> GlobFilesResult:
        """List files under ``query.directory`` via ``rg --files`` (bounded).

        Product list-ignore (images and archives visible by name; grep still skips).
        ``query.globs`` are extra include globs (path-aware, not filename-only).
        ``max_depth`` is rg's ``--max-depth`` (1 = cwd files only; rg 0 is empty). Raises
        ``OutsideWorkspace`` / ``PathNotFound`` / ``NotADirectory``.
        """
        ...

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        """Run code on this workspace's platform, in the workspace directory.

        The backend fills ``req.cwd`` so code sees the workspace files; it then
        delegates to its ``SandboxProvider`` (no separate execution path).
        """
        ...
