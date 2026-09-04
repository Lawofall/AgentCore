"""ServerWorkspace — files and code execution on the server (cloud mode).

The first ``WorkspaceBackend`` implementation. It owns a root directory on the
server and a ``SandboxProvider`` for code execution. All filesystem operations
resolve through ``resolve_safe_path`` (the traversal guard, now internal to the
backend), and ``execute`` delegates to the sandbox with ``cwd`` set to the root
so executed code sees the workspace files — fixing the long-standing bug where
``code_execute`` ran in a throwaway temp dir disconnected from file tools.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

from agentcore.tools.sandbox.exec_env import ExecEnvProbeMemo, ExecEnvProbeVerdict
from agentcore.tools.sandbox.protocol import (
    ExecutionRequest,
    ExecutionResult,
    SandboxProvider,
)
from agentcore.workspace._paths import (
    is_access_denied_oserror,
    is_ignored_dir_entry,
    is_ignored_file_name,
    is_system_ignored_file_name,
    normalize_glob,
    normalize_workspace_path,
    path_has_non_internal_entries,
    resolve_safe_path,
)
from agentcore.workspace.channel import WorkspaceChannel
from agentcore.workspace.declared_dirs import is_declared_latent_dir
from agentcore.workspace.external_mounts import (
    ExternalMount,
    build_external_env,
    cross_root_copy_error,
    cross_root_move_error,
    external_mutation_allowed,
    external_ns,
    is_external_namespace,
    parse_external_path,
    route_external,
)
from agentcore.workspace.indexing.maintainer import IndexMaintainer
from agentcore.workspace.indexing.manager import IndexManager
from agentcore.workspace.indexing.registry import (
    drop_index_registry,
    shared_index_maintainer_for_dir,
    shared_index_manager_for_dir,
)
from agentcore.workspace.limits import (
    FILE_TOO_LARGE_DETAIL,
    OFFICE_EXTRACT_DISK_MAX_BYTES,
    effective_read_bytes_cap,
    effective_read_head_cap,
)
from agentcore.workspace.local import LocalWorkspace, request_diagnostics_via_channel
from agentcore.workspace.locks import workspace_lock
from agentcore.workspace.protocol import (
    AlreadyExists,
    AmbiguousMatch,
    CodeSearchResult,
    DirEntry,
    DirListing,
    GrepQuery,
    GrepResult,
    IndexFileEntry,
    IndexFilesResult,
    NoMatch,
    NotADirectory,
    NotAFile,
    NotUTF8,
    OutsideWorkspace,
    PathNotFound,
    ReadHeadResult,
    ReadLinesResult,
    ReplaceOutcome,
    TreeEntry,
    TreeResult,
    WorkspaceIOError,
)
from agentcore.workspace.rg_grep import run_grep_rg
from agentcore.workspace.sparse_listing import is_ai_list_hidden_file
from agentcore.workspace.stage_dirs import INDEX_ZONE_NAME, internal_zone_path
from agentcore.workspace.text_replace import (
    TextReplaceAmbiguous,
    TextReplaceNoMatch,
    apply_text_replace,
)
from agentcore.workspace.trash import (
    is_internal_zone_path,
    soft_delete_expanding_trash_ancestor,
    soft_delete_to_trash,
    trash_dest_under_target,
)

# AI-facing ``list`` default — a directory dump this size already crowds the
# model's context, and ``file_list`` says so when it bites (mirrors desktop
# ``WORKSPACE_LIST_MAX``). The file panel passes ``WORKSPACE_BROWSE_LIST_MAX``.
_MAX_LIST_ENTRIES = 100
_MAX_INDEX_FILES = 5000  # @ mention flat index cap (mirrors desktop LIST_FILES_CAP)


def _posix(rel: str) -> str:
    return rel.replace(os.sep, "/")


def _collect_index_files_sync(
    root: Path, *, cap: int, recent: bool
) -> IndexFilesResult:
    """Synchronous ``os.walk`` scan — must run off the asyncio event-loop thread."""
    # (posix_path, sort_mtime, mtime_ms, size_bytes)
    collected: list[tuple[str, float, int, int]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune noise dirs in place so os.walk never descends into them.
        rel_dir = os.path.relpath(dirpath, root)
        parent_rel = "" if rel_dir == "." else rel_dir.replace("\\", "/")
        dirnames[:] = sorted(
            d for d in dirnames if not is_ignored_dir_entry(parent_rel=parent_rel, name=d)
        )
        for fname in sorted(filenames):
            if is_ignored_file_name(fname):
                continue
            full = Path(dirpath) / fname
            if full.is_symlink() or not full.is_file():
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            mtime_ms = st.st_mtime_ns // 1_000_000
            size_bytes = int(st.st_size)
            sort_mtime = float(st.st_mtime) if recent else 0.0
            collected.append(
                (_posix(os.path.relpath(full, root)), sort_mtime, mtime_ms, size_bytes)
            )
            if len(collected) >= cap:
                truncated = True
                break
        if truncated:
            break
    if recent:
        collected.sort(key=lambda row: row[1], reverse=True)  # newest first
    else:
        collected.sort(key=lambda row: row[0])  # alphabetical
    entries = tuple(
        IndexFileEntry(path=p, mtime_ms=ms, size_bytes=sz)
        for p, _, ms, sz in collected
    )
    return IndexFilesResult(
        paths=[e.path for e in entries],
        truncated=truncated,
        entries=entries,
    )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file in the same dir + rename).

    Windows: ``os.replace`` retries transient WinError 5/32 then falls back to
    in-place write (same durability as :meth:`ServerWorkspace.write`).
    """
    from agentcore.workspace.fs_replace import atomic_write_bytes

    atomic_write_bytes(path, data)


def _write_bytes_sync(path: Path, data: bytes) -> None:
    """Parent ``mkdir`` + atomic write — must run off the asyncio event-loop thread.

    Panel uploads / editor saves run up to ``workspace_upload_max_bytes`` (50 MiB);
    writing that inline would stall every other request on this worker (other
    users' SSE streams included) for the whole flush.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(path, data)


def _write_text_sync(path: Path, content: str) -> None:
    """Parent ``mkdir`` + truncating text write — must run off the event-loop thread.

    Deliberately *not* atomic: :meth:`ServerWorkspace.write` never was, and turning
    it into a temp+rename here would change what a concurrent reader observes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_text_sync(path: Path, content: str) -> None:
    """Parent ``mkdir`` + append (creating the file) — must run off the event loop."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(content)


def _read_bytes_with_mtime_sync(path: Path) -> tuple[bytes, int]:
    """Whole-file read + its mtime — must run off the asyncio event-loop thread."""
    return path.read_bytes(), path.stat().st_mtime_ns // 1_000_000


def _list_entries_sync(
    base: Path,
    *,
    base_rel: str,
    name_pattern: str,
    recursive: bool,
    cap: int,
) -> tuple[list[DirEntry], bool]:
    """Bounded directory listing — must run off the asyncio event-loop thread.

    Replaces ``sorted(base.glob(pattern))[:cap]``, which sorted and stat'd the
    whole tree and then spent the budget on ``.git`` / ``node_modules`` entries it
    was about to filter out — which is how a cloned repo listed as empty. Here the
    ignore rules prune *as the walk descends*, and the walk is breadth-first so a
    deep subtree can never push a top-level sibling out of the budget.

    Walks one entry past ``cap`` so the caller can say「还有更多」rather than cut
    silently; ``stat`` runs here too (it used to run per entry on the event loop).
    """
    prefix = "" if base_rel in ("", ".") else base_rel.replace("\\", "/").strip("/")
    collected: list[DirEntry] = []
    truncated = False
    queue: deque[tuple[Path, str]] = deque([(base, prefix)])

    while queue and not truncated:
        dir_path, parent_rel = queue.popleft()
        try:
            with os.scandir(dir_path) as scan:
                children = sorted(scan, key=lambda c: c.name)
        except OSError as e:
            if is_access_denied_oserror(e):
                continue  # one unreadable subtree must not fail the whole listing
            raise
        for child in children:
            name = child.name
            try:
                is_dir = child.is_dir()
                # Never descend a symlinked dir: the tree may loop back on itself.
                descend = is_dir and not child.is_symlink()
            except OSError as e:
                if is_access_denied_oserror(e):
                    continue
                raise
            if is_dir:
                if is_ignored_dir_entry(parent_rel=parent_rel, name=name):
                    continue
            elif is_system_ignored_file_name(name):
                continue
            rel = f"{parent_rel}/{name}" if parent_rel else name
            if recursive and descend:
                queue.append((Path(child.path), rel))
            if not fnmatch.fnmatch(name, name_pattern):
                continue
            if len(collected) >= cap:
                truncated = True
                break
            # Soft meta for UI subtitles — never fail the whole listing on stat.
            size_bytes: int | None = None
            mtime_ms: int | None = None
            try:
                st = child.stat()
                mtime_ms = st.st_mtime_ns // 1_000_000
                if not is_dir:
                    size_bytes = int(st.st_size)
            except OSError:
                pass
            collected.append(
                DirEntry(
                    path=rel,
                    is_dir=is_dir,
                    size_bytes=size_bytes,
                    mtime_ms=mtime_ms,
                )
            )

    collected.sort(key=lambda e: e.path)
    return collected, truncated


def _copy_sync(source: Path, dest: Path) -> None:
    """Whole-file / whole-tree copy — must run off the asyncio event-loop thread."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, dest)
    else:
        shutil.copy2(source, dest)


def _delete_target_sync(
    target: Path,
    *,
    hard: bool,
    mount_root: Path,
    zone_root: Path | None,
    trash_rel: str,
) -> None:
    """Hard delete / soft-delete move — must run off the asyncio event-loop thread.

    All the policy (which mount, hard vs trash, index handle release) is decided by
    the caller; this is only the unbounded tree work.
    """
    if hard:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    elif trash_dest_under_target(root=mount_root, target=target, internal_root=zone_root):
        soft_delete_expanding_trash_ancestor(
            root=mount_root, target=target, internal_root=zone_root
        )
    else:
        soft_delete_to_trash(
            root=mount_root,
            target=target,
            original_rel=trash_rel,
            internal_root=zone_root,
        )


def _merge_diagnostics_payloads(parts: list[dict]) -> dict:
    """Combine primary-root + external-bridge diagnostics envelopes."""
    if not parts:
        return {"status": "ok", "diagnostics": []}
    if len(parts) == 1:
        return parts[0]
    merged: list[dict] = []
    any_ok = False
    unavailable_reason: str | None = None
    for payload in parts:
        status = str(payload.get("status") or "")
        if status == "ok":
            any_ok = True
        elif status == "unavailable":
            reason = payload.get("reason")
            if isinstance(reason, str) and reason.strip():
                unavailable_reason = reason.strip()
            else:
                unavailable_reason = unavailable_reason or "diagnostics unavailable"
        for item in payload.get("diagnostics") or []:
            if isinstance(item, dict):
                merged.append(item)
    if any_ok:
        return {"status": "ok", "diagnostics": merged}
    return {
        "status": "unavailable",
        "reason": unavailable_reason or "diagnostics unavailable",
        "diagnostics": merged,
    }


class ServerWorkspace:
    """``WorkspaceBackend`` backed by a directory + sandbox on the host machine.

    ``location`` defaults to ``"server"`` (an isolated cloud sandbox — a worker's
    server-sandbox calls skip the per-call approval card). The sidecar reuses this exact
    backend but passes ``location="local"``: there the engine runs ON the user's machine
    and ``root`` IS their real disk, so a delegated worker's ``file_write`` /
    ``code_execute`` needs the same consent the cloud's local mode demands — that
    per-call decision keys off ``backend.location == "local"`` inside
    ``runtime/sandbox_approval`` (the turn's ``ApprovalGate`` is always handed down;
    MCP / Host / 恒确认 / ``file_write=ask`` still prompt on cloud). Primary-root ops
    use direct ``Path`` I/O either way. Cloud sessions with W3/organize grants (no
    ``abs_path``) additionally attach a ``WorkspaceChannel`` and route **only**
    ``external/<alias>/…`` via per-op ``root_id`` (same transport as
    ``LocalWorkspace``) — ``location`` stays ``"server"`` so those calls keep the
    cloud card exemption.
    """

    def __init__(
        self,
        root: Path,
        sandbox: SandboxProvider,
        *,
        root_label: str = "workspace",
        location: Literal["server", "local"] = "server",
        lock_key: str | None = None,
        internal_root: Path | None = None,
    ) -> None:
        self._root = root
        self._sandbox = sandbox
        # Where ``{index,trash,baselines}`` live for this root. ``None`` = in-tree
        # under ``root/AgentCore/`` — correct for sidecar / local (root IS the
        # user's own directory). Cloud
        # conversation workspaces pass an out-of-tree, id-keyed path: cloud folders
        # nest for real, so an ancestor folder must not see a child's deleted
        # files / baseline zips / index DB as ordinary content, and the zones must
        # survive the child being renamed (双模式工作区 §5.4).
        self._internal_root = internal_root
        self.root_label = root_label
        self.location: Literal["server", "local"] = location
        # A′ sink: primary-tree mutations take this key (cloud builds). None =
        # sidecar / hermetic tests — unlocked (sidecar never held folder lock).
        self._lock_key = lock_key
        # Optional UX hook when mutation lock must wait (不得静默等锁 → SSE).
        self._on_lock_waiting: Callable[[bool], None] | None = None
        # Flips True on the first mutating op so the service snapshots only
        # workspaces a turn actually changed (see WorkspaceBackend.dirty).
        self._dirty = False
        self._index_manager: IndexManager | None = None
        self._index_maintainer: IndexMaintainer | None = None
        # W3 session mounts (``external/<alias>/…``). Sidecar sets ``abs_path``;
        # cloud grants carry ``root_id`` only and need ``_external_bridge``.
        self._mounts: dict[str, ExternalMount] = {}
        # Desktop channel bridge for cloud external-only ops (per-op root_id).
        self._external_bridge: LocalWorkspace | None = None
        # Sidecar location=local: desktop-held ops (diagnostics). File I/O stays Path.
        self._desktop_channel: WorkspaceChannel | None = None
        # Turn material paths for AI ``list_tree`` AI-noise reveal (∪ attachments/).
        # Set by prepare/wire from ``collect_turn_material_paths``; default empty.
        self.ai_list_materials: frozenset[str] = frozenset()
        # When True, AI list_tree / channel list keep archive suffixes visible
        # (glob pattern targets zip/rar/…). Default False.
        self.ai_list_reveal_archives: bool = False
        # gVisor: once-per-backend runsc smoke. Sidecar: sticky hard-evidence
        # deaths from a real run (missing interpreter / refused spawn), keyed
        # by language. A timeout never lands here.
        self._exec_env_probe = ExecEnvProbeMemo()

    def set_lock_waiting_hook(self, hook: Callable[[bool], None] | None) -> None:
        """Register UX callback for contended mutation-lock waits (不得静默等锁)."""
        self._on_lock_waiting = hook

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _mark_mutated(self) -> None:
        """Snapshot dirty + invalidate code index (schedule background refresh).

        Always routes through :meth:`start_code_index_maintenance` so the first
        write on a previously empty workspace still starts indexing (empty /
        internal-only trees are a no-op until non-internal content exists).
        """
        self._dirty = True
        if self._index_manager is not None:
            self._index_manager.mark_content_dirty()
        self.start_code_index_maintenance()

    def start_code_index_maintenance(self) -> None:
        """Kick coalesced background ensure (write / ``code_search`` / warm).

        Uses the process-wide maintainer for this **index dir** so sidecar turn
        backends and ``warmCodeIndex`` coalesce. Keying on the index dir rather
        than the root also means a folder rename keeps one SQLite handle instead
        of opening a second one under the new path. Lazy B1: only when the
        workspace has non-internal content — empty chats must not materialize an
        index dir (which would leak into hub has_files while in-tree).
        """
        if not path_has_non_internal_entries(self._root):
            return
        self._get_index_manager()
        self._index_maintainer = shared_index_maintainer_for_dir(self.index_dir, self)
        self._index_maintainer.schedule()

    async def _release_code_index_for_tree_delete(self) -> None:
        """Abort maintenance + drop SQLite handles before removing the index dir.

        Windows cannot ``rmtree`` a SQLite file held open by a background ensure
        (WinError 32).
        """
        await drop_index_registry(self.index_dir)
        self._index_manager = None
        self._index_maintainer = None

    def attach_external_mounts(self, mounts: dict[str, ExternalMount]) -> None:
        """Attach session-scoped external mounts for this turn (W3 / organize)."""
        self._mounts = dict(mounts)
        if self._external_bridge is not None:
            self._external_bridge.attach_external_mounts(self._mounts)

    def attach_external_channel(self, channel: WorkspaceChannel) -> None:
        """Attach a desktop channel for cloud ``external/`` ops (root_id only grants).

        Does not flip ``location`` — cloud workers stay ungated; desktop pathGuard +
        organize whitelist + plan card remain the authorization surface.
        """
        bridge = LocalWorkspace(channel, root_label=self.root_label)
        bridge.attach_external_mounts(self._mounts)
        self._external_bridge = bridge

    def attach_desktop_channel(self, channel: WorkspaceChannel) -> None:
        """Attach the desktop channel for sidecar-held language-service ops.

        File CRUD stays on ``Path``. Only ``diagnostics`` (and the same channel
        ``terminal`` already uses) leave for Electron main. Does not flip
        ``location``.
        """
        self._desktop_channel = channel

    def _external_needs_channel(self, *paths: str) -> bool:
        """True when any path is ``external/`` without ``abs_path`` (desktop channel).

        Unknown aliases raise ``PathNotFound`` immediately. Paths with ``abs_path``
        (sidecar) stay on direct Path I/O.
        """
        needs = False
        for path in paths:
            if not is_external_namespace(path):
                continue
            routed = route_external(path, self._mounts)
            if routed is None:
                raise PathNotFound(path)
            if not routed.mount.abs_path:
                needs = True
        return needs

    def _require_external_bridge(self) -> LocalWorkspace:
        if self._external_bridge is None:
            raise WorkspaceIOError("会话授权目录在本机引擎外不可直读")
        return self._external_bridge

    @property
    def root(self) -> Path:
        """The server-side workspace directory (used by the snapshot path)."""
        return self._root

    @property
    def index_dir(self) -> Path:
        """Where this workspace's BM25 code index lives (may be outside the tree)."""
        return internal_zone_path(
            INDEX_ZONE_NAME, root=self._root, internal_root=self._internal_root
        )

    def _internal_root_for(self, mount_root: Path) -> Path | None:
        """Zone container for whichever root an op resolved against.

        Only the primary root has an out-of-tree container; session external
        mounts keep their zones in-tree.
        """
        try:
            if mount_root.resolve() == self._root.resolve():
                return self._internal_root
        except OSError:
            pass
        return None

    @asynccontextmanager
    async def _mutation_lock(self, _path: str):
        """Single-layer lock for one mutating op (A′).

        Primary tree uses ``lock_key`` when set. Never nest with an outer
        same-key ``workspace_lock``. Contended waits notify ``_on_lock_waiting``
        (honest SSE; 不得静默等锁).
        """
        if self._lock_key:
            async with workspace_lock(self._lock_key, on_waiting=self._on_lock_waiting):
                yield
            return
        yield

    def _safe(
        self,
        rel: str,
        *,
        write: bool = False,
        op: str | None = None,
        permanent: bool = False,
    ) -> Path:
        if is_external_namespace(rel):
            routed = route_external(rel, self._mounts)
            if routed is None:
                raise PathNotFound(rel)
            if write:
                err = external_mutation_allowed(
                    routed.mount,
                    op or "write",
                    path=rel,
                    permanent=permanent,
                )
                if err:
                    raise OutsideWorkspace(err)
            if not routed.mount.abs_path:
                raise WorkspaceIOError("会话授权目录在本机引擎外不可直读")
            mount_root = Path(routed.mount.abs_path)
            mount_rel = routed.rel if routed.rel not in ("", ".") else "."
            # Same guard algorithm — separate root, not a weakened boundary.
            resolved = resolve_safe_path(mount_root, mount_rel if mount_rel != "." else ".")
            if resolved is None:
                # ``"."`` against root: resolve_safe_path(workspace, ".") → workspace
                if mount_rel in ("", "."):
                    return mount_root.resolve()
                raise OutsideWorkspace(rel)
            return resolved
        # Normalize model-supplied absolute root-label paths (``/workspace/x.md`` →
        # ``x.md``) at this single seam before the traversal guard runs — only inputs
        # the guard would already reject can be rescued; ``..`` / other-root paths
        # still fail (see strip_root_label_prefix).
        resolved = resolve_safe_path(self._root, rel, root_label=self.root_label)
        if resolved is None:
            raise OutsideWorkspace(rel)
        return resolved

    def _model_path(self, abs_path: Path, *, logical: str | None = None) -> str:
        """Map an absolute path back to a model-facing relative path.

        Prefer the caller's logical ``external/<alias>/…`` namespace; if that
        fails (or is absent), reverse-lookup mounts by abs
        containment so a mount file never falls through to
        ``relpath(…, primary_root)`` which would leak ``../``-shaped paths into
        model-visible list/grep output.
        """
        resolved = abs_path.resolve()
        if logical and parse_external_path(logical) is not None:
            routed = route_external(logical, self._mounts)
            if routed and routed.mount.abs_path:
                mount_root = Path(routed.mount.abs_path).resolve()
                try:
                    rel = resolved.relative_to(mount_root)
                    return external_ns(routed.mount.alias, _posix(str(rel)))
                except ValueError:
                    pass
        for mount in self._mounts.values():
            if not mount.abs_path:
                continue
            mount_root = Path(mount.abs_path).resolve()
            try:
                rel = resolved.relative_to(mount_root)
                return external_ns(mount.alias, _posix(str(rel)))
            except ValueError:
                continue
        return _posix(os.path.relpath(resolved, self._root.resolve()))

    def _get_index_manager(self) -> IndexManager:
        if self._index_manager is None:
            self._index_manager = shared_index_manager_for_dir(self.index_dir)
        return self._index_manager

    def _reject_oversized_file(
        self,
        target: Path,
        *,
        max_bytes: int | None = None,
        ingest_cap: int | None = None,
    ) -> None:
        """Capacity contract: refuse whole-file loads above the requested cap.

        Same detail prefix as desktop Local so the tool layer can mark
        ``too_large`` / observation envelopes. Size suffix is optional metadata.
        ``ingest_cap`` bypasses the ``read_bytes`` channel clamp (disk extract).
        """
        cap = (
            ingest_cap
            if ingest_cap is not None
            else effective_read_bytes_cap(max_bytes)
        )
        try:
            size = target.stat().st_size
        except OSError as e:
            raise WorkspaceIOError(str(e)) from e
        if size > cap:
            raise WorkspaceIOError(f"{FILE_TOO_LARGE_DETAIL}（{size}字节）")

    async def read(self, path: str) -> str:
        # Reads stay inline, unlike the write path: the background index maintainer
        # reads workspace files through here, and a read handle held across an await
        # makes a concurrent atomic write's ``os.replace`` fail with WinError 5 on
        # Windows (sidecar). ``WORKSPACE_READ_MAX_BYTES`` caps the loop stall.
        if self._external_needs_channel(path):
            return await self._require_external_bridge().read(path)
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_file():
            raise NotAFile(path)
        self._reject_oversized_file(target)
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise WorkspaceIOError(str(e)) from e

    async def write(self, path: str, content: str) -> int:
        if self._external_needs_channel(path):
            n = await self._require_external_bridge().write(path, content)
            self._mark_mutated()
            return n
        async with self._mutation_lock(path):
            target = self._safe(path, write=True, op="write")
            try:
                await asyncio.to_thread(_write_text_sync, target, content)
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()
            return len(content)

    async def append(self, path: str, content: str) -> int:
        if self._external_needs_channel(path):
            n = await self._require_external_bridge().append(path, content)
            self._mark_mutated()
            return n
        async with self._mutation_lock(path):
            target = self._safe(path, write=True, op="append")
            try:
                if target.exists() and not target.is_file():
                    raise NotAFile(path)
                await asyncio.to_thread(_append_text_sync, target, content)
            except NotAFile:
                raise
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()
            return len(content)

    async def resolve_for_download(self, path: str, *, max_bytes: int) -> Path:
        """Resolve a on-disk path for HTTP panel download (``FileResponse``).

        Capacity is ``max_bytes`` (aligned with upload), **not** the AI-tool
        ``WORKSPACE_READ_MAX_BYTES`` gate used by :meth:`read` / :meth:`read_bytes`.
        Does not load file contents into memory.
        """
        if self._external_needs_channel(path):
            raise WorkspaceIOError("会话授权目录在本机引擎外不可直读")
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_file():
            raise NotAFile(path)
        try:
            size = target.stat().st_size
        except OSError as e:
            raise WorkspaceIOError(str(e)) from e
        if size > max_bytes:
            raise WorkspaceIOError(FILE_TOO_LARGE_DETAIL)
        return target

    async def resolve_dir_for_download(self, path: str) -> Path:
        """Resolve a directory for HTTP subtree-zip download (selected dir as zip root).

        Same traversal gates as :meth:`resolve_for_download`. Does
        not apply a byte ceiling here — ``zip_dir`` gates uncompressed source
        bytes at the panel upload cap. GET ``.../files/{path}`` stays file-only.
        """
        if self._external_needs_channel(path):
            raise WorkspaceIOError("会话授权目录在本机引擎外不可直读")
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_dir():
            raise NotADirectory(path)
        return target

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        if self._external_needs_channel(path):
            return await self._require_external_bridge().read_bytes(
                path, max_bytes=max_bytes
            )
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_file():
            raise NotAFile(path)
        self._reject_oversized_file(target, max_bytes=max_bytes)
        try:
            return target.read_bytes()
        except OSError as e:
            raise WorkspaceIOError(str(e)) from e

    async def read_head(
        self, path: str, *, max_bytes: int | None = None
    ) -> ReadHeadResult:
        if self._external_needs_channel(path):
            return await self._require_external_bridge().read_head(
                path, max_bytes=max_bytes
            )
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_file():
            raise NotAFile(path)
        cap = effective_read_head_cap(max_bytes)
        try:
            size = target.stat().st_size
            with target.open("rb") as fh:
                data = fh.read(cap)
        except OSError as e:
            raise WorkspaceIOError(str(e)) from e
        return ReadHeadResult(data=data, size_bytes=size)

    async def extract_office(
        self, path: str, *, ext: str, start_page: int = 1
    ):
        """Stat then extract in a child that opens ``path``; do not slurp here."""
        from agentcore.workspace.attachment_parse import extract_office_file

        if self._external_needs_channel(path):
            return await self._require_external_bridge().extract_office(
                path, ext=ext, start_page=start_page
            )
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_file():
            raise NotAFile(path)
        self._reject_oversized_file(target, ingest_cap=OFFICE_EXTRACT_DISK_MAX_BYTES)
        return await extract_office_file(
            target, ext=ext, start_page=start_page
        )

    async def write_bytes(self, path: str, data: bytes) -> int:
        if self._external_needs_channel(path):
            n = await self._require_external_bridge().write_bytes(path, data)
            self._mark_mutated()
            return n
        async with self._mutation_lock(path):
            target = self._safe(path, write=True, op="write_bytes")
            try:
                await asyncio.to_thread(_write_bytes_sync, target, data)
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()
            return len(data)

    async def read_for_edit(self, path: str) -> tuple[str, int, Literal["lf", "crlf"]]:
        """Read a text file for in-panel editing: ``(text, mtime_ms, eol)``.

        Unlike the preview download (truncated), this returns the **whole** file so
        a later save never drops the tail. Content is newline-normalized to ``\\n``;
        the original EOL is reported so the editor can restore it on write.
        ``mtime_ms`` is the write-time CAS baseline (see :meth:`write_text_cas`).
        Raises ``OutsideWorkspace`` / ``PathNotFound`` / ``NotAFile`` / ``NotUTF8`` /
        ``WorkspaceIOError``.
        """
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_file():
            raise NotAFile(path)
        self._reject_oversized_file(target)
        try:
            raw, mtime_ms = await asyncio.to_thread(_read_bytes_with_mtime_sync, target)
        except OSError as e:
            raise WorkspaceIOError(str(e)) from e
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise NotUTF8(path) from e
        eol: Literal["lf", "crlf"] = "crlf" if "\r\n" in text else "lf"
        return text.replace("\r\n", "\n"), mtime_ms, eol

    async def write_text_cas(
        self,
        path: str,
        content: str,
        *,
        baseline_mtime_ms: int,
        eol: Literal["lf", "crlf"],
    ) -> tuple[bool, int]:
        """Conditionally write ``content`` with a write-time CAS on mtime.

        Returns ``(ok, mtime_ms)``: on success ``mtime_ms`` is the new mtime; on a
        **conflict** (``ok`` is False) it is the current disk mtime, so the caller can
        offer "overwrite anyway" using it as the next baseline — we never blind-clobber
        a file changed under us (e.g. by an Agent turn). ``baseline_mtime_ms == 0``
        means "new file": a conflict if something already exists at ``path``. ``\\n``
        is restored to ``eol`` before an atomic (temp + rename) write. Raises
        ``OutsideWorkspace`` / ``NotAFile`` / ``WorkspaceIOError``.

        Best-effort against external writers; this method holds ``workspace_lock``
        (via ``lock_key``) for the CAS so an Agent write can't
        interleave mid-check — callers must not nest another same-key hold.
        """
        async with self._mutation_lock(path):
            target = self._safe(path, write=True, op="write")
            exists = target.exists()
            if exists and not target.is_file():
                raise NotAFile(path)
            try:
                if baseline_mtime_ms == 0:
                    if exists:
                        return False, target.stat().st_mtime_ns // 1_000_000
                elif not exists:
                    return False, 0  # the baseline file was deleted under us
                else:
                    disk_ms = target.stat().st_mtime_ns // 1_000_000
                    if disk_ms != baseline_mtime_ms:
                        return False, disk_ms
                body = content.replace("\n", "\r\n") if eol == "crlf" else content
                await asyncio.to_thread(_write_bytes_sync, target, body.encode("utf-8"))
                new_ms = target.stat().st_mtime_ns // 1_000_000
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()
            return True, new_ms

    async def list(
        self, directory: str, pattern: str, *, cap: int | None = None
    ) -> DirListing:
        if self._external_needs_channel(directory):
            bridge = self._require_external_bridge()
            bridge.ai_list_reveal_archives = self.ai_list_reveal_archives
            return await bridge.list(directory, pattern, cap=cap)
        base = self._safe(directory)
        if not base.is_dir():
            # Declared stage / attachments trees: writes mkdir parents — missing
            # means latent empty, not a guess failure. File-at-path still errors.
            if not base.exists() and is_declared_latent_dir(directory):
                return DirListing(entries=[], truncated=False)
            raise NotADirectory(directory)
        # Entry paths are built from the list root's model-facing path, so nested
        # ``**/*`` hits keep their real parent: treating ``AgentCore/index`` as a
        # bare ``index`` would leak internal zones into the user file UI.
        base_rel = self._model_path(base, logical=directory)
        try:
            # UI REST shares ``list`` — only system noise is pruned here; AI
            # ``file_list`` applies AI-noise filtering in the tool layer.
            entries, truncated = await asyncio.to_thread(
                _list_entries_sync,
                base,
                base_rel=base_rel,
                name_pattern=normalize_glob(pattern) or "*",
                recursive="**" in pattern,
                cap=cap if cap and cap > 0 else _MAX_LIST_ENTRIES,
            )
        except OSError as e:
            raise WorkspaceIOError(str(e)) from e
        return DirListing(entries=entries, truncated=truncated)

    async def exists(self, path: str) -> bool:
        """True iff ``path`` is an existing regular file (unfiltered by AI-noise)."""
        if self._external_needs_channel(path):
            return await self._require_external_bridge().exists(path)
        target = self._safe(path)
        try:
            return target.is_file()
        except OSError as e:
            raise WorkspaceIOError(str(e)) from e

    async def read_lines(
        self, path: str, *, offset: int = 1, limit: int | None = None
    ) -> ReadLinesResult:
        if self._external_needs_channel(path):
            return await self._require_external_bridge().read_lines(
                path, offset=offset, limit=limit
            )
        target = self._safe(path)
        if not target.exists():
            raise PathNotFound(path)
        if not target.is_file():
            raise NotAFile(path)
        self._reject_oversized_file(target)
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise WorkspaceIOError(str(e)) from e

        lines = content.splitlines()
        total = len(lines)
        start_idx = max(0, offset - 1)
        if start_idx >= total:
            return ReadLinesResult(
                lines=[],
                start_line=offset,
                end_line=offset - 1,
                total_lines=total,
            )

        end_idx = total if limit is None else min(total, start_idx + limit)
        selected = lines[start_idx:end_idx]
        return ReadLinesResult(
            lines=selected,
            start_line=start_idx + 1,
            end_line=end_idx,
            total_lines=total,
        )

    async def list_tree(
        self,
        directory: str,
        *,
        pattern: str = "*",
        max_depth: int = 3,
        max_entries: int = 200,
    ) -> TreeResult:
        if self._external_needs_channel(directory):
            bridge = self._require_external_bridge()
            bridge.ai_list_reveal_archives = self.ai_list_reveal_archives
            return await bridge.list_tree(
                directory,
                pattern=pattern,
                max_depth=max_depth,
                max_entries=max_entries,
            )
        base = self._safe(directory)
        if not base.is_dir():
            if not base.exists() and is_declared_latent_dir(directory):
                return TreeResult(entries=[], truncated=False, elided_count=0)
            if not base.exists():
                raise PathNotFound(directory)
            raise NotADirectory(directory)

        entries: list[TreeEntry] = []
        truncated = False
        elided_count = 0
        warnings: list[str] = []
        name_filter = pattern or "*"
        reveal_archives = self.ai_list_reveal_archives

        def walk(dir_path: Path, depth: int, *, is_root: bool) -> None:
            nonlocal truncated, elided_count
            if depth > max_depth:
                return
            try:
                children = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
            except OSError as e:
                if not is_root and is_access_denied_oserror(e):
                    try:
                        rel = dir_path.resolve().relative_to(self._root.resolve()).as_posix()
                    except ValueError:
                        rel = self._model_path(dir_path, logical=directory)
                    if rel == ".":
                        rel = directory if directory not in ("", ".") else "."
                    warnings.append(f"跳过无权限目录：{rel}")
                    return
                raise WorkspaceIOError(str(e)) from e

            # Prefer model-facing ``external/<alias>/…`` when the list root is
            # in that namespace — mount abs may sit under the primary tree in
            # tests / edge layouts, which would otherwise hide archives as
            # workspace AI-noise.
            if parse_external_path(directory) is not None:
                parent_rel = self._model_path(dir_path, logical=directory)
            else:
                try:
                    parent_rel = dir_path.resolve().relative_to(
                        self._root.resolve()
                    ).as_posix()
                except ValueError:
                    parent_rel = self._model_path(dir_path, logical=directory)
            if parent_rel in (".", ""):
                parent_rel = ""
            elif parent_rel.endswith("/."):
                parent_rel = parent_rel[:-2]

            for child in children:
                # Name-first prune — do not ``is_dir`` locked ignore-set dirs.
                if is_ignored_dir_entry(parent_rel=parent_rel, name=child.name):
                    continue
                try:
                    is_dir = child.is_dir() and not child.is_symlink()
                    is_file = child.is_file()
                except OSError as e:
                    if is_access_denied_oserror(e):
                        rel = f"{parent_rel}/{child.name}" if parent_rel else child.name
                        warnings.append(f"跳过无权限条目：{rel}")
                        continue
                    raise WorkspaceIOError(str(e)) from e

                # AI list_tree: system noise always; AI noise except attachments/
                # materials; archives under external/ or reveal_archives.
                if is_file and is_ai_list_hidden_file(
                    parent_rel=parent_rel,
                    name=child.name,
                    materials=self.ai_list_materials,
                    reveal_archives=reveal_archives,
                ):
                    continue

                rel = self._model_path(child, logical=directory)

                # `*`: emit dirs + matching files (connected tree).
                # Name filter: emit matching names only; still descend unmatched
                # dirs so the 200-entry budget is spent on hits (Glob), not prefixes.
                name_search = (name_filter or "*") != "*"
                name_matches = fnmatch.fnmatch(child.name, name_filter)
                emit = name_matches if name_search else (is_dir or name_matches)

                if emit:
                    if len(entries) >= max_entries:
                        truncated = True
                        elided_count += 1
                        continue
                    entries.append(TreeEntry(path=rel, is_dir=is_dir, depth=depth))
                if is_dir and depth < max_depth:
                    walk(child, depth + 1, is_root=False)

        walk(base, 1, is_root=True)
        return TreeResult(
            entries=entries,
            truncated=truncated,
            elided_count=elided_count,
            warnings=warnings,
        )

    async def index_files(
        self, cap: int | None = None, *, order: str = "path"
    ) -> IndexFilesResult:
        """Flat list of file paths for @ mentions (文件中枢统一 F4) + worker manifest.

        Files only, ``IGNORED_DIRS`` pruned, capped at ``cap`` (``truncated`` when
        hit; ``cap=None`` uses the default ``_MAX_INDEX_FILES``) — the cloud
        counterpart to the desktop ``fsApi.listFiles`` that indexes local roots, so @
        and the worker manifest behave the same whether a workspace is cloud or local.
        ``order="path"`` (default) = alphabetical (the @ view); ``order="recent"`` =
        newest-first by mtime for the manifest's relevance budget.
        Each entry carries local-stat ``mtime_ms`` / ``size_bytes`` so
        ``ensure_index`` can skip unchanged-file reads (same fingerprint contract
        as desktop ``index_files``).
        Noise dirs (``.git`` / ``node_modules`` / …) plus path-aware
        ``AgentCore/{index,trash,baselines}``, and AI-tier
        suffixes (``*.db`` / media / binaries) are pruned — same rule set as
        desktop ``collectWorkspaceFiles`` / ``opIndexFiles``.

        Disk walk runs in a worker thread (``asyncio.to_thread``) so a large
        tree does not stall the event-loop turn cycle.
        """
        cap = cap or _MAX_INDEX_FILES
        recent = order == "recent"
        root = self._root.resolve()
        return await asyncio.to_thread(
            _collect_index_files_sync, root, cap=cap, recent=recent
        )

    async def mkdir(self, path: str) -> None:
        if self._external_needs_channel(path):
            await self._require_external_bridge().mkdir(path)
            self._mark_mutated()
            return
        async with self._mutation_lock(path):
            target = self._safe(path, write=True, op="mkdir")
            # Refuse mkdir of the primary workspace root itself; external mount roots
            # are also "already there" as the grant target.
            if target == self._root.resolve():
                raise OutsideWorkspace(path)
            routed = route_external(path, self._mounts) if parse_external_path(path) else None
            if (
                routed
                and routed.mount.abs_path
                and target == Path(routed.mount.abs_path).resolve()
            ):
                raise OutsideWorkspace(path)
            if target.exists():
                raise AlreadyExists(path)
            try:
                target.mkdir(parents=True, exist_ok=False)
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()

    async def delete(self, path: str, *, permanent: bool = False) -> None:
        if self._external_needs_channel(path):
            await self._require_external_bridge().delete(path, permanent=permanent)
            self._mark_mutated()
            return
        async with self._mutation_lock(path):
            target = self._safe(path, write=True, op="delete", permanent=permanent)
            if target == self._root.resolve():
                raise OutsideWorkspace(path)  # never delete the workspace root
            routed = route_external(path, self._mounts) if parse_external_path(path) else None
            if routed and routed.mount.abs_path:
                mount_root = Path(routed.mount.abs_path).resolve()
                if target == mount_root:
                    raise OutsideWorkspace(path)
            else:
                mount_root = self._root.resolve()
            if not target.exists():
                raise PathNotFound(path)
            # Soft-delete into AgentCore/trash cannot nest under itself; treat
            # internal zones (index/trash/baselines) as permanent cleanup — not
            # the whole AgentCore/ tree (rules/memory/docs stay soft-deletable).
            # When target is an ancestor of trash (e.g. bare AgentCore/), expand
            # by children instead of moving the whole tree into itself.
            hard = permanent or is_internal_zone_path(path)
            # Expanding AgentCore/ (or hard-clearing index/) must release the
            # process-wide BM25 handle first — otherwise Windows shares-locks
            # ``code_search.db`` and the delete returns 422 mid-flight ensure.
            will_clear_index = hard and is_internal_zone_path(path) and (
                path.replace("\\", "/").rstrip("/") == "AgentCore/index"
                or path.replace("\\", "/").startswith("AgentCore/index/")
            )
            zone_root = self._internal_root_for(mount_root)
            will_expand_agentcore = (not hard) and trash_dest_under_target(
                root=mount_root, target=target, internal_root=zone_root
            )
            if will_clear_index or will_expand_agentcore:
                await self._release_code_index_for_tree_delete()
            trash_rel = (
                routed.rel if routed is not None else path.replace("\\", "/")
            ) or path.replace("\\", "/")
            try:
                await asyncio.to_thread(
                    _delete_target_sync,
                    target,
                    hard=hard,
                    mount_root=mount_root,
                    zone_root=zone_root,
                    trash_rel=trash_rel,
                )
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()

    async def _copy_workspace_to_channel_dest(self, src: str, dst: str) -> None:
        """Workspace-on-server → desktop organize: COPY carries bytes, not a path.

        Dest stays on the organize allow-set (copy, not write_bytes). Per-file
        ceiling is ``workspace_upload_max_bytes``.
        """
        bridge = self._require_external_bridge()
        bridge.ensure_copy_dest(dst)
        if await bridge.exists(dst):
            raise AlreadyExists(dst)
        source = self._safe(src, write=False)
        if not source.exists():
            raise PathNotFound(src)
        if source.is_symlink():
            raise WorkspaceIOError("不能复制符号链接")
        if source.is_dir():
            await self._copy_tree_to_channel(source, dst, bridge)
            return
        data = await asyncio.to_thread(source.read_bytes)
        await bridge.copy_from_bytes(dst, data)

    async def _copy_tree_to_channel(
        self, source: Path, dst: str, bridge: LocalWorkspace
    ) -> None:
        await bridge.mkdir(dst)
        for child in sorted(source.iterdir(), key=lambda p: p.name):
            if child.is_symlink():
                continue
            child_dst = f"{dst.rstrip('/')}/{child.name}"
            if child.is_dir():
                await self._copy_tree_to_channel(child, child_dst, bridge)
            else:
                data = await asyncio.to_thread(child.read_bytes)
                await bridge.copy_from_bytes(child_dst, data)

    async def copy(self, src: str, dst: str) -> None:
        dst_on_channel = self._external_needs_channel(dst)
        src_is_workspace = not is_external_namespace(src)
        if dst_on_channel and src_is_workspace:
            await self._copy_workspace_to_channel_dest(src, dst)
            self._mark_mutated()
            return
        if self._external_needs_channel(src, dst):
            await self._require_external_bridge().copy(src, dst)
            self._mark_mutated()
            return
        async with self._mutation_lock(dst):
            source = self._safe(src, write=False)
            dest = self._safe(dst, write=True, op="copy")
            src_ext = parse_external_path(src)
            dst_ext = parse_external_path(dst)
            # Dest mode (readonly vs organize) is already gated by ``_safe(..., op="copy")``.
            copy_err = cross_root_copy_error(
                src_ext[0] if src_ext else None,
                dst_ext[0] if dst_ext else None,
            )
            if copy_err:
                raise OutsideWorkspace(copy_err)
            if src_ext is None:
                root = self._root.resolve()
                if source == root or dest == root:
                    raise OutsideWorkspace(src if source == root else dst)
            if not source.exists():
                raise PathNotFound(src)
            if dest.exists():
                raise AlreadyExists(dst)
            # Refuse copying a directory into itself or a descendant (self-recursion).
            try:
                dest.relative_to(source)
                if source.is_dir():
                    raise WorkspaceIOError("不能复制到自身或其子目录")
            except ValueError:
                pass  # dest is not under source — expected
            try:
                await asyncio.to_thread(_copy_sync, source, dest)
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()

    async def move(self, src: str, dst: str) -> None:
        if self._external_needs_channel(src, dst):
            await self._require_external_bridge().move(src, dst)
            self._mark_mutated()
            return
        async with self._mutation_lock(dst):
            source = self._safe(src, write=True, op="move")
            dest = self._safe(dst, write=True, op="move")
            src_ext = parse_external_path(src)
            dst_ext = parse_external_path(dst)
            move_err = cross_root_move_error(
                src_ext[0] if src_ext else None,
                dst_ext[0] if dst_ext else None,
            )
            if move_err:
                raise OutsideWorkspace(move_err)
            if src_ext is None:
                root = self._root.resolve()
                if source == root or dest == root:
                    raise OutsideWorkspace(src if source == root else dst)
            if not source.exists():
                raise PathNotFound(src)
            if dest.exists():
                raise AlreadyExists(dst)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, dest)
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e
            self._mark_mutated()

    async def replace(self, path: str, old: str, new: str, *, all_: bool) -> ReplaceOutcome:
        if self._external_needs_channel(path):
            outcome = await self._require_external_bridge().replace(
                path, old, new, all_=all_
            )
            self._mark_mutated()
            return outcome
        async with self._mutation_lock(path):
            target = self._safe(path, write=True, op="replace")
            if not target.exists():
                raise PathNotFound(path)
            if not target.is_file():
                raise NotAFile(path)

            try:
                # Read bytes + decode (no newline translation). ``apply_text_replace``
                # keeps exact hits byte-faithful; CRLF↔LF mismatch uses the LF-normalize
                # fallback and restores the file's original eol on write-back.
                content = (await asyncio.to_thread(target.read_bytes)).decode("utf-8")
            except UnicodeDecodeError as e:
                raise NotUTF8(path) from e
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e

            result = apply_text_replace(content, old, new, all_=all_)
            if isinstance(result, TextReplaceNoMatch):
                raise NoMatch(path)
            if isinstance(result, TextReplaceAmbiguous):
                raise AmbiguousMatch(result.count)

            try:
                await asyncio.to_thread(
                    _atomic_write_bytes, target, result.content.encode("utf-8")
                )
            except OSError as e:
                raise WorkspaceIOError(str(e)) from e

            self._mark_mutated()
            return ReplaceOutcome(count=result.count, first_line=result.first_line)

    async def grep(self, query: GrepQuery) -> GrepResult:
        # Path checks stay on the event loop so OutsideWorkspace / PathNotFound
        # surface immediately. The ripgrep child is awaited with
        # ``create_subprocess_exec`` so a tool-level ``asyncio.wait_for`` /
        # cancellation can kill the process (no silent Python walk fallback).
        if self._external_needs_channel(query.directory):
            return await self._require_external_bridge().grep(query)
        base = self._safe(query.directory)
        if not base.exists():
            raise PathNotFound(query.directory)
        logical = query.directory
        return await run_grep_rg(
            query=query,
            search_root=base,
            workspace_root=self._root,
            model_path=lambda p: self._model_path(p, logical=logical),
        )

    async def code_search(
        self,
        query: str,
        *,
        language: str | None = None,
        path_prefix: str = ".",
        max_results: int = 10,
    ) -> CodeSearchResult:
        manager = self._get_index_manager()
        return await manager.search(
            query,
            language=language,
            path_prefix=path_prefix,
            max_results=max_results,
        )

    async def ensure_code_index(self, *, force: bool = False) -> bool:
        manager = self._get_index_manager()
        return await manager.ensure_index(self, force=force)

    async def diagnostics(self, paths: list[str]) -> dict:
        """Language-service inner loop: desktop LS when this desk is local-disk.

        Cloud desks have no Node LanguageService — honest unavailable (no fake
        ``tsc``). Sidecar ``location=local`` issues ``WorkspaceOp.DIAGNOSTICS``
        on the attached desktop channel (same transport as ``process_*``).
        """
        cleaned = [str(p or "").strip() for p in paths if str(p or "").strip()]
        if not cleaned:
            return {"status": "ok", "diagnostics": []}

        if self.location != "local":
            return {
                "status": "unavailable",
                "reason": "这张云桌没有语言服务通道，验收请用 run",
                "diagnostics": [],
            }

        channel = self._desktop_channel
        if channel is None:
            return {
                "status": "unavailable",
                "reason": "本机工作区未接通语言服务通道，验收请用 run",
                "diagnostics": [],
            }

        groups: dict[tuple[str | None, str | None], list[str]] = {}
        bridge_paths: list[str] = []
        for path in cleaned:
            if is_external_namespace(path):
                if self._external_needs_channel(path):
                    bridge_paths.append(path)
                    continue
                routed = route_external(path, self._mounts)
                if routed is None:
                    raise PathNotFound(path)
                rid = (routed.mount.root_id or "").strip() or None
                rel = routed.rel if routed.rel not in ("", ".") else "."
                groups.setdefault((rid, routed.mount.alias), []).append(rel)
                continue
            norm = normalize_workspace_path(path, root_label=self.root_label)
            groups.setdefault((None, None), []).append(norm)

        parts: list[dict] = []
        if groups:
            parts.append(
                await request_diagnostics_via_channel(
                    channel,
                    groups,
                    remap_path=self._diagnostics_out_path,
                )
            )
        if bridge_paths:
            parts.append(await self._require_external_bridge().diagnostics(bridge_paths))
        return _merge_diagnostics_payloads(parts)

    def _diagnostics_out_path(self, path: str, alias: str | None) -> str:
        if alias is None:
            return path
        return external_ns(alias, path)

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        # Cloud desk must already be up (prepare / resume). Guest create is not
        # a workspace write and must not ride this mutation lock or the chat
        # path's ``run`` wait_for.
        # Run code in the workspace root so relative file paths resolve against
        # the same files the file tools see.
        #
        # Mark dirty conservatively: the backend can't introspect what the
        # sandbox wrote, and executed code commonly produces artifacts in the
        # workspace, so we treat any run as potentially mutating. The cost is an
        # occasional snapshot of a pure-compute run (cheap, async, post-answer);
        # the alternative — silently missing code-generated files — is worse for
        # a backup feature. Read-only file ops still never set this.
        #
        # A′: hold the same single-layer mutation lock as file writes for the
        # whole sandbox run (code_execute / test_run). Whole-turn lock used to
        # cover this; without it, execute would race sibling turns' writes.
        async with self._mutation_lock("."):
            from agentcore.tools.sandbox.exec_env import annotate_real_exec_failure
            from agentcore.tools.sandbox.protocol import InterpreterProbe

            # gVisor: keep the once-per-backend runsc smoke (language=None).
            # Sidecar / InterpreterProbe: no per-language preflight — the real
            # run classifies a missing interpreter or refused spawn.
            sandbox = self._sandbox
            if not isinstance(sandbox, InterpreterProbe):
                verdict = self._exec_env_probe.get(None)
                if verdict is None:
                    verdict = self._exec_env_probe.record(
                        None, await self._probe_exec_env(None)
                    )
                if not verdict.alive:
                    return verdict.failure_result(language=None)
            language = req.language
            cached = self._exec_env_probe.get(language)
            if cached is not None and not cached.alive:
                return cached.failure_result(language=language)
            self._mark_mutated()
            env = dict(req.env or {})
            env.update(build_external_env(self._mounts))
            cwd = str(self._root.resolve())
            # D11′：python 执行与 TestExitCode 同源注入 PYTHONPATH（. + 现存 src/lib）
            if req.language == "python":
                from agentcore.tools.sandbox.pythonpath import merge_pythonpath_into_env

                env = merge_pythonpath_into_env(Path(cwd), env)
            result = await self._sandbox.execute(
                replace(req, cwd=cwd, env=env or None)
            )
            if isinstance(sandbox, InterpreterProbe):
                annotated, death = annotate_real_exec_failure(
                    result, language=language
                )
                if death is not None:
                    self._exec_env_probe.record(language, death)
                    from agentcore.core.logging import get_logger

                    get_logger(__name__).info(
                        "sandbox.exec_env_probe_failed",
                        location=self.location,
                        language=language,
                        code=death.code,
                        reason=(
                            f"exit={result.exit_code} "
                            f"duration_ms={result.duration_ms}"
                        ),
                        detail=(result.stderr or result.stdout or "").strip()[:200]
                        or None,
                    )
                    return annotated
            return result

    async def ensure_workspace_desk(self) -> None:
        """Start the cloud desk guest without holding the file-mutation lock."""
        ensure = getattr(self._sandbox, "ensure_workspace_desk", None)
        if not callable(ensure):
            return
        await ensure(str(self._root.resolve()))

    def cloud_desk_ready(self) -> bool:
        """True when this workspace already holds a started gVisor guest.

        Subprocess (escape hatch / test doubles) has no guest map — the health
        / config gate stays authoritative.
        """
        from agentcore.tools.sandbox.gvisor import GVisorSandbox, has_running_desk

        if not isinstance(self._sandbox, GVisorSandbox):
            return True
        return has_running_desk(str(self._root.resolve()))

    async def _probe_exec_env(self, language: str | None) -> ExecEnvProbeVerdict:
        """gVisor / backend-wide runtime smoke (``language`` is always ``None``).

        Carries the sandbox's own failure reason when it classified one.
        Sandboxes that classify nothing keep the unclassified fallback.
        """
        from agentcore.core.logging import get_logger
        from agentcore.tools.sandbox.exec_env import (
            EXEC_ENV_PROBE_ALIVE,
            EXEC_ENV_PROBE_FAIL_CODE,
        )

        sandbox = self._sandbox
        try:
            alive = bool(await sandbox.health_check())
        except Exception:
            alive = False
            get_logger(__name__).info(
                "sandbox.health_check_failed",
                error="health_check raised",
                location=self.location,
                language=language,
            )
        if alive:
            return EXEC_ENV_PROBE_ALIVE
        failure = getattr(sandbox, "last_health_failure", None)
        reason, detail = failure if failure else (None, None)
        verdict = ExecEnvProbeVerdict(
            alive=False,
            code=(
                getattr(sandbox, "last_health_failure_code", None)
                or EXEC_ENV_PROBE_FAIL_CODE
            ),
            evidence=getattr(sandbox, "last_health_evidence", None) or "",
        )
        get_logger(__name__).info(
            "sandbox.exec_env_probe_failed",
            location=self.location,
            language=language,
            code=verdict.code,
            reason=reason,
            detail=detail,
        )
        return verdict
