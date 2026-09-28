"""LocalWorkspace — files and code execution on the user's machine (local mode).

The second ``WorkspaceBackend`` implementation. It owns no disk of its own: every
op is serialized and routed over a ``WorkspaceChannel`` to the bound desktop
client, which runs it against the real local directory (reusing the desktop's
authorized FS roots + traversal guard) and posts back a structured result. The
backend then returns the same typed values / raises the same ``WorkspaceError``
subclasses as ``ServerWorkspace`` — so the file tools and the engine run against
it **unchanged** (the whole point of the P0 seam).

All ops (read / list / grep / the mutating ops / ``execute``) are
wired end-to-end through the channel and handled by the desktop. Sidecar local
turns use ``ServerWorkspace`` on disk instead. Two policies make ``execute`` safe
on the user's real machine (双模式工作区 P2d 执行门):

* **Approval** is enforced *upstream* at the engine's ``ApprovalGate`` (before the
  op is ever issued), for the CEO and — in local mode — for delegated workers too,
  so no code runs on the user's machine without consent. The channel itself adds
  no gate (that would double-prompt the CEO).
* **Timeout**: ``execute`` extends the channel's transport deadline to the code's
  own ``timeout_seconds`` plus a slack, so the desktop's execution limit stays
  authoritative and a long but legal run is not cut off by the flat file-op
  deadline. A dropped desktop still fails as a ``WorkspaceLivenessTimeout`` (never hangs).
"""

from __future__ import annotations

import base64
from typing import Any

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.exec_env import ExecEnvProbeMemo
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.workspace._paths import normalize_workspace_path
from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
from agentcore.workspace.external_mounts import (
    ExternalMount,
    cross_root_copy_error,
    cross_root_move_error,
    external_mutation_allowed,
    external_ns,
    is_external_namespace,
    route_external,
)
from agentcore.workspace.protocol import (
    DirEntry,
    DirListing,
    GlobFilesQuery,
    GlobFilesResult,
    GrepHit,
    GrepQuery,
    GrepResult,
    IndexFileEntry,
    IndexFilesResult,
    OutsideWorkspace,
    PathNotFound,
    ReadHeadResult,
    ReadLinesResult,
    ReplaceOutcome,
    TreeEntry,
    TreeResult,
    WorkspaceIOError,
)

logger = get_logger(__name__)

# Default extra transport budget (seconds) over a code execution's own timeout
# (see Settings.workspace_execute_timeout_slack_seconds). Used when a LocalWorkspace
# is built without an explicit slack (e.g. tests); locate.py injects the configured
# value for real turns.
_DEFAULT_EXECUTE_TIMEOUT_SLACK = 30.0
# Align with turn_baseline.LOCAL_BASELINE_TIMEOUT_S / desktop ARCHIVE gate (60s).
_LOCAL_BASELINE_CHANNEL_TIMEOUT_S = 60.0


class LocalWorkspace:
    """``WorkspaceBackend`` backed by the desktop, reached over a channel."""

    location = "local"

    def __init__(
        self,
        channel: WorkspaceChannel,
        *,
        root_label: str = "workspace",
        execute_timeout_slack: float = _DEFAULT_EXECUTE_TIMEOUT_SLACK,
        base_subpath: str = "",
    ) -> None:
        self._channel = channel
        self.root_label = root_label
        # Added to an execute's own timeout to form its transport deadline, so the
        # desktop's execution limit (not the channel) decides when code is killed.
        self._execute_timeout_slack = execute_timeout_slack
        # Sub-directory within the bound root this workspace is scoped to (工作区
        # 对称化 D1a). Empty = the root itself (current behavior, every op below is a
        # no-op pass-through). Non-empty = every op path is prefixed with it on the
        # way to the desktop and stripped on the way back, so the engine, tools, and
        # the user only ever see workspace-relative paths — the container root the
        # channel is bound to never leaks. POSIX, no trailing slash.
        self._base = base_subpath.strip("/")
        # Flips True on the first mutating op so the service snapshots only
        # workspaces a turn actually changed (see WorkspaceBackend.dirty). For
        # local mode the snapshot is the 本地→云 handoff bridge (§四 / P2e).
        self._dirty = False
        # W3 session read-only mounts (``external/<alias>/…``). Empty by default;
        # ``attach_external_mounts`` wires grants at turn start.
        self._mounts: dict[str, ExternalMount] = {}
        # Turn material paths for AI list AI-noise reveal (passed as reveal_paths).
        # Set by prepare/wire from ``collect_turn_material_paths``; default empty.
        self.ai_list_materials: frozenset[str] = frozenset()
        # When True, channel list/list_tree keep archive suffixes visible.
        self.ai_list_reveal_archives: bool = False
        # Sticky hard-evidence deaths from a real EXECUTE (missing interpreter /
        # refused spawn). A timeout never lands here. Keyed by language so a
        # dead python never speaks for node or bash.
        self._exec_env_probe = ExecEnvProbeMemo()

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def base_subpath(self) -> str:
        """Project directory under the bound container root (empty = root is the project).

        Same D1a base used by ``file_*`` / ``execute`` / structured ``git_run`` cwd —
        never point git at the shared container root when this is non-empty.
        """
        return self._base

    def _channel_reveal_paths(self) -> list[str]:
        """Engine-relative materials → container-relative paths for the desktop."""
        materials = self.ai_list_materials
        if not materials:
            return []
        base = self._base
        if not base:
            return list(materials)
        out: list[str] = []
        for p in materials:
            cleaned = p.replace("\\", "/").strip("/")
            if not cleaned or cleaned == ".":
                out.append(base)
            else:
                out.append(f"{base}/{cleaned}")
        return out

    def _mark_mutated(self) -> None:
        self._dirty = True

    def attach_external_mounts(self, mounts: dict[str, ExternalMount]) -> None:
        """Attach session-scoped read-only mounts for this turn (W3)."""
        self._mounts = dict(mounts)

    def _route(
        self,
        path: str,
        *,
        write: bool = False,
        op: str | None = None,
        permanent: bool = False,
    ) -> tuple[str | None, str, str | None]:
        """Map a model path to ``(override_root_id, desktop_rel, alias)``.

        ``override_root_id is None`` → primary workspace binding (apply ``_in``).
        Unknown ``external/<alias>`` → ``PathNotFound``. Mutations gated by mount
        ``mode`` (readonly vs organize whitelist; permanent delete always denied).
        """
        if is_external_namespace(path):
            # Reserved namespace: never fall through to the primary workspace
            # (invalid / unknown alias would otherwise write under ``external/…``).
            routed = route_external(path, self._mounts)
            if routed is None:
                raise PathNotFound(path)
        else:
            # Same contract as ServerWorkspace.resolve_safe_path / desktop pathGuard:
            # bare `/`/`\` → `.`; `/<root_label>/…` strip — before the channel sees it.
            norm = normalize_workspace_path(path, root_label=self.root_label)
            return None, self._in(norm), None
        if write:
            err = external_mutation_allowed(
                routed.mount,
                op or "write",
                path=path,
                permanent=permanent,
            )
            if err:
                raise OutsideWorkspace(err)
        rel = routed.rel if routed.rel not in ("", ".") else "."
        return routed.mount.root_id, rel, routed.mount.alias

    def _out_routed(self, path: str, alias: str | None) -> str:
        if alias is None:
            return self._out(path)
        return external_ns(alias, path)

    def _in(self, path: str) -> str:
        """Workspace-relative path → container-relative (prefix the subpath base).

        No-op when unscoped. ``""``/``"."`` (the workspace root) map to the base
        itself so ``list``/``index`` target the right subtree.
        """
        if not self._base:
            return path
        rel = path.strip("/")
        if not rel or rel == ".":
            return self._base
        return f"{self._base}/{rel}"

    def _out(self, path: str) -> str:
        """Container-relative path → workspace-relative (strip the subpath base).

        The inverse of :meth:`_in` for results that carry paths (list / grep /
        index). No-op when unscoped; a path already outside the base is returned
        unchanged (defensive — the desktop should only ever return in-subtree paths
        once it scopes by base).
        """
        if not self._base:
            return path
        if path == self._base:
            return ""
        prefix = f"{self._base}/"
        return path[len(prefix) :] if path.startswith(prefix) else path

    async def read(self, path: str) -> str:
        root_id, rel, _ = self._route(path)
        value = await self._channel.request(
            WorkspaceOp.READ, {"path": rel}, root_id=root_id
        )
        return str(value)

    async def write(self, path: str, content: str) -> int:
        root_id, rel, _ = self._route(path, write=True, op="write")
        value = await self._channel.request(
            WorkspaceOp.WRITE, {"path": rel, "content": content}, root_id=root_id
        )
        self._mark_mutated()
        return int(value)

    async def append(self, path: str, content: str) -> int:
        root_id, rel, _ = self._route(path, write=True, op="append")
        value = await self._channel.request(
            WorkspaceOp.APPEND, {"path": rel, "content": content}, root_id=root_id
        )
        self._mark_mutated()
        return int(value)

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        # The desktop returns base64 (JSON has no byte type); decode back to raw.
        root_id, rel, _ = self._route(path)
        payload: dict[str, Any] = {"path": rel}
        if max_bytes is not None:
            payload["max_bytes"] = int(max_bytes)
        value = await self._channel.request(
            WorkspaceOp.READ_BYTES, payload, root_id=root_id
        )
        return base64.b64decode(str(value))

    async def read_head(
        self, path: str, *, max_bytes: int | None = None
    ) -> ReadHeadResult:
        root_id, rel, _ = self._route(path)
        payload: dict[str, Any] = {"path": rel}
        if max_bytes is not None:
            payload["max_bytes"] = int(max_bytes)
        value = await self._channel.request(
            WorkspaceOp.READ_HEAD, payload, root_id=root_id
        )
        value = value or {}
        return ReadHeadResult(
            data=base64.b64decode(str(value.get("data") or "")),
            size_bytes=int(value.get("size_bytes") or 0),
        )

    async def extract_office(
        self, path: str, *, ext: str, start_page: int = 1
    ):
        """IPC ingest: desktop has no Python extract stack, so still ``read_bytes``."""
        from agentcore.workspace.attachment_parse import extract_office_bytes
        from agentcore.workspace.limits import OFFICE_EXTRACT_CHANNEL_MAX_BYTES

        data = await self.read_bytes(
            path, max_bytes=OFFICE_EXTRACT_CHANNEL_MAX_BYTES
        )
        return await extract_office_bytes(
            data, ext=ext, start_page=start_page
        )

    async def write_bytes(self, path: str, data: bytes) -> int:
        root_id, rel, _ = self._route(path, write=True, op="write_bytes")
        value = await self._channel.request(
            WorkspaceOp.WRITE_BYTES,
            {"path": rel, "data": base64.b64encode(data).decode("ascii")},
            root_id=root_id,
        )
        self._mark_mutated()
        return int(value)

    async def list(
        self, directory: str, pattern: str, *, cap: int | None = None
    ) -> DirListing:
        root_id, rel, alias = self._route(directory)
        payload: dict[str, Any] = {"directory": rel, "pattern": pattern}
        if cap and cap > 0:
            payload["cap"] = int(cap)
        reveal = self._channel_reveal_paths()
        if reveal:
            payload["reveal_paths"] = reveal
        if self.ai_list_reveal_archives:
            payload["reveal_archives"] = True
        value = await self._channel.request(
            WorkspaceOp.LIST, payload, root_id=root_id
        )
        # Desktops from before the cap became honest answer with a bare array; read
        # those as "cap unknown" rather than claiming a complete listing.
        if isinstance(value, dict):
            rows = value.get("entries") or []
            truncated = bool(value.get("truncated", False))
        else:
            rows = value or []
            truncated = False
        out: list[DirEntry] = []
        for e in rows:
            raw_size = e.get("size_bytes")
            raw_mtime = e.get("mtime_ms")
            out.append(
                DirEntry(
                    path=self._out_routed(str(e["path"]), alias),
                    is_dir=bool(e["is_dir"]),
                    size_bytes=None if raw_size is None else int(raw_size),
                    mtime_ms=None if raw_mtime is None else int(raw_mtime),
                )
            )
        return DirListing(entries=out, truncated=truncated)

    async def exists(self, path: str) -> bool:
        root_id, rel, _ = self._route(path)
        value = await self._channel.request(
            WorkspaceOp.EXISTS, {"path": rel}, root_id=root_id
        )
        return bool(value)

    async def read_lines(
        self, path: str, *, offset: int = 1, limit: int | None = None
    ) -> ReadLinesResult:
        root_id, rel, _ = self._route(path)
        value = await self._channel.request(
            WorkspaceOp.READ_LINES,
            {"path": rel, "offset": offset, "limit": limit},
            root_id=root_id,
        )
        value = value or {}
        return ReadLinesResult(
            lines=[str(line) for line in value.get("lines", [])],
            start_line=int(value.get("start_line", offset)),
            end_line=int(value.get("end_line", offset - 1)),
            total_lines=int(value.get("total_lines", 0)),
        )

    async def list_tree(
        self,
        directory: str,
        *,
        pattern: str = "*",
        max_depth: int = 3,
        max_entries: int = 200,
    ) -> TreeResult:
        root_id, rel, alias = self._route(directory)
        tree_payload: dict[str, Any] = {
            "directory": rel,
            "pattern": pattern,
            "max_depth": max_depth,
            "max_entries": max_entries,
        }
        reveal = self._channel_reveal_paths()
        if reveal:
            tree_payload["reveal_paths"] = reveal
        if self.ai_list_reveal_archives:
            tree_payload["reveal_archives"] = True
        value = await self._channel.request(
            WorkspaceOp.LIST_TREE,
            tree_payload,
            root_id=root_id,
        )
        value = value or {}
        return TreeResult(
            entries=[
                TreeEntry(
                    path=self._out_routed(str(e["path"]), alias),
                    is_dir=bool(e["is_dir"]),
                    depth=int(e["depth"]),
                )
                for e in value.get("entries", [])
            ],
            truncated=bool(value.get("truncated", False)),
            elided_count=int(value.get("elided_count", 0)),
            warnings=[str(w) for w in value.get("warnings", [])],
        )

    async def glob_files(self, query: GlobFilesQuery) -> GlobFilesResult:
        root_id, rel, alias = self._route(query.directory)
        payload: dict[str, Any] = {
            "directory": rel,
            "globs": list(query.globs),
            "max_depth": query.max_depth,
            "max_entries": query.max_entries,
            "reveal_archives": query.reveal_archives or self.ai_list_reveal_archives,
        }
        value = await self._channel.request(
            WorkspaceOp.GLOB_FILES,
            payload,
            root_id=root_id,
        )
        value = value or {}
        return GlobFilesResult(
            paths=[self._out_routed(str(p), alias) for p in value.get("paths", [])],
            truncated=bool(value.get("truncated", False)),
            warnings=[str(w) for w in value.get("warnings", [])],
        )

    async def index_files(
        self, cap: int | None = None, *, order: str = "path"
    ) -> IndexFilesResult:
        # The desktop indexes the bound local root (its fsApi.listFiles walk: ignore
        # dirs pruned, capped) and returns {entries|paths, truncated}, so @ mentions +
        # the worker manifest see the same flat view as cloud. ``entries`` may carry
        # mtime_ms/size_bytes. ``order`` selects the sort ("path" alphabetical
        # for @, "recent" newest-first for the manifest budget). ``base`` scopes
        # the walk to this workspace's
        # subtree (工作区对称化 D1a) so a shared container root indexes only this
        # workspace; returned paths are stripped back to workspace-relative.
        # Read-only → not dirty.
        value = await self._channel.request(
            WorkspaceOp.INDEX_FILES, {"cap": cap, "order": order, "base": self._in(".")}
        )
        value = value or {}
        return _parse_index_files_value(value, out_path=self._out)

    async def mkdir(self, path: str) -> None:
        root_id, rel, _ = self._route(path, write=True, op="mkdir")
        await self._channel.request(WorkspaceOp.MKDIR, {"path": rel}, root_id=root_id)
        self._mark_mutated()

    async def delete(self, path: str, *, permanent: bool = False) -> None:
        root_id, rel, _ = self._route(
            path, write=True, op="delete", permanent=permanent
        )
        await self._channel.request(
            WorkspaceOp.DELETE,
            {"path": rel, "permanent": permanent},
            root_id=root_id,
        )
        self._mark_mutated()

    async def copy(self, src: str, dst: str) -> None:
        src_root, src_rel, src_alias = self._route(src, write=False)
        dst_root, dst_rel, dst_alias = self._route(dst, write=True, op="copy")
        copy_err = cross_root_copy_error(src_alias, dst_alias)
        if copy_err:
            raise OutsideWorkspace(copy_err)
        if src_root == dst_root:
            await self._channel.request(
                WorkspaceOp.COPY, {"src": src_rel, "dst": dst_rel}, root_id=src_root
            )
        elif src_alias is None and dst_alias is not None:
            # Workspace → organize: dest root is the mutation target (copy is
            # already on the organize allow-set). ``src_root_id`` lets the
            # desktop resolve src against the workspace root independently —
            # same guard algorithm, separate root, not a weakened boundary.
            src_id = src_root if src_root is not None else self._channel.root_id
            if not (isinstance(src_id, str) and src_id.strip()):
                # Cloud scratch lives on the API disk — path copy would look for
                # src inside the organize root. Use copy_from_bytes instead.
                raise WorkspaceIOError("工作区源不在本机，无法按路径复制到授权目录")
            await self._channel.request(
                WorkspaceOp.COPY,
                {"src": src_rel, "dst": dst_rel, "src_root_id": src_id},
                root_id=dst_root,
            )
        else:
            raise OutsideWorkspace("不能跨会话授权目录与工作区复制文件")
        self._mark_mutated()

    def ensure_copy_dest(self, dst: str) -> None:
        """Raise if ``dst`` is not a copy-allowed dest (readonly / write deny)."""
        self._route(dst, write=True, op="copy")

    async def copy_from_bytes(self, dst: str, data: bytes) -> None:
        """Place ``data`` at dest via COPY (organize allow-set). Src is not local.

        Not ``write_bytes``: organize still denies write; copy is already allowed
        and keeps the no-overwrite contract.
        """
        max_bytes = int(settings.workspace_upload_max_bytes)
        if len(data) > max_bytes:
            raise WorkspaceIOError(f"文件超出 {max_bytes} 字节的交付上限")
        root_id, rel, _ = self._route(dst, write=True, op="copy")
        await self._channel.request(
            WorkspaceOp.COPY,
            {
                "dst": rel,
                "src_data": base64.b64encode(data).decode("ascii"),
            },
            root_id=root_id,
        )
        self._mark_mutated()

    async def move(self, src: str, dst: str) -> None:
        src_root, src_rel, src_alias = self._route(src, write=True, op="move")
        dst_root, dst_rel, dst_alias = self._route(dst, write=True, op="move")
        move_err = cross_root_move_error(src_alias, dst_alias)
        if move_err or src_root != dst_root:
            raise OutsideWorkspace(move_err or "不能跨会话授权目录与工作区移动文件")
        await self._channel.request(
            WorkspaceOp.MOVE, {"src": src_rel, "dst": dst_rel}, root_id=src_root
        )
        self._mark_mutated()

    async def replace(self, path: str, old: str, new: str, *, all_: bool) -> ReplaceOutcome:
        root_id, rel, _ = self._route(path, write=True, op="replace")
        value = await self._channel.request(
            WorkspaceOp.REPLACE,
            {"path": rel, "old": old, "new": new, "all": all_},
            root_id=root_id,
        )
        self._mark_mutated()
        first_line = value.get("first_line")
        return ReplaceOutcome(
            count=int(value["count"]),
            first_line=None if first_line is None else int(first_line),
        )

    async def grep(self, query: GrepQuery) -> GrepResult:
        root_id, rel, alias = self._route(query.directory)
        value = await self._channel.request(
            WorkspaceOp.GREP,
            {
                "pattern": query.pattern,
                "directory": rel,
                "glob": query.glob,
                "case_insensitive": query.case_insensitive,
                "files_only": query.files_only,
                "max_results": query.max_results,
            },
            root_id=root_id,
        )
        return GrepResult(
            hits=[
                GrepHit(
                    path=self._out_routed(str(h["path"]), alias),
                    line_no=int(h["line_no"]),
                    text=str(h["text"]),
                )
                for h in value.get("hits", [])
            ],
            file_counts=[
                (self._out_routed(str(fc[0]), alias), int(fc[1]))
                for fc in value.get("file_counts", [])
            ],
            total_matches=int(value.get("total_matches", 0)),
            truncated=bool(value.get("truncated", False)),
            warnings=[str(w) for w in value.get("warnings", [])],
        )

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        # cwd is the desktop's job (it runs code in the bound local directory). It is
        # sent only as a workspace subtree hint (``cwd`` = the subpath base, 工作区
        # 对称化 D1a) so a scoped workspace runs code in its own dir rather than the
        # shared container root; empty = the root (current behavior). Marked dirty
        # conservatively — executed code commonly writes artifacts and the backend
        # cannot introspect what ran (mirrors ServerWorkspace.execute).
        #
        # W3: pass conversation_id + external root_ids so the desktop injects
        # ``AGENTCORE_EXTERNAL_<ALIAS>`` abs paths into the subprocess env — absolute
        # paths never enter the model prompt.
        from agentcore.tools.sandbox.exec_env import annotate_real_exec_failure

        language = req.language
        cached = self._exec_env_probe.get(language)
        if cached is not None and not cached.alive:
            return cached.failure_result(language=language)
        result = await self._channel_execute(req)
        annotated, verdict = annotate_real_exec_failure(result, language=language)
        if verdict is not None:
            self._exec_env_probe.record(language, verdict)
            logger.info(
                "sandbox.exec_env_probe_failed",
                location="local",
                language=language,
                code=verdict.code,
                reason=f"exit={result.exit_code} duration_ms={result.duration_ms}",
                detail=(result.stderr or result.stdout or "").strip()[:200] or None,
            )
        return annotated

    async def _channel_execute(self, req: ExecutionRequest) -> ExecutionResult:
        """Raw desktop EXECUTE — the real run, no preflight."""
        self._mark_mutated()
        external_roots = {
            alias: m.root_id
            for alias, m in self._mounts.items()
            if m.root_id and m.mode != "organize"
        }
        args: dict[str, Any] = {
            "code": req.code,
            "language": req.language,
            "timeout_seconds": req.timeout_seconds,
            "memory_limit_mb": req.memory_limit_mb,
            "stdin": req.stdin,
            "cwd": self._base,
            "conversation_id": self._channel.conversation_id,
            "external_roots": external_roots,
        }
        if req.idle_timeout_seconds is not None:
            args["idle_timeout_seconds"] = int(req.idle_timeout_seconds)
        # Registry/cache pin from test_run install — desktop whitelist-merges only.
        if req.env:
            args["env"] = dict(req.env)
        value: dict[str, Any] = await self._channel.request(
            WorkspaceOp.EXECUTE,
            args,
            on_output=req.on_output,
            # Outlive the desktop's own execution timeout (the authoritative kill)
            # by the slack, so a long but legal run is not cut off by the flat
            # file-op deadline — only a truly gone desktop trips the transport.
            timeout=float(req.timeout_seconds) + self._execute_timeout_slack,
        )
        return ExecutionResult(
            success=bool(value["success"]),
            stdout=str(value.get("stdout", "")),
            stderr=str(value.get("stderr", "")),
            exit_code=int(value.get("exit_code", 0)),
            duration_ms=int(value.get("duration_ms", 0)),
            written_files=self._out_written_files(value.get("written_files")),
        )

    def _out_written_files(self, raw: Any) -> list[str] | None:
        """产物写回: desktop-reported paths → workspace-relative (strip the D1a base).

        Same in/out path convention as ``list`` / ``grep`` / ``index_files``: the
        desktop answers root-relative, this side strips the subpath prefix. ``None``
        (older desktop that never reports) stays ``None`` — 「没测量」和「测了没变化」
        不是一回事，别把前者伪装成空清单。
        """
        if not isinstance(raw, list):
            return None
        return [self._out(str(p)) for p in raw if str(p).strip()]

    async def capture_turn_baseline(self, message_id: str) -> str | None:
        """Best-effort Local zip via desktop channel (never raises to block a turn).

        Writes ``AgentCore/baselines/{message_id}.zip`` on the user disk. Returns
        ``message_id`` when a non-empty zip is ready afterward, else ``None``.
        """
        mid = (message_id or "").strip()
        if not mid:
            return None
        try:
            value = await self._channel.request(
                WorkspaceOp.ENSURE_TURN_BASELINE,
                {
                    "message_id": mid,
                    "directory": self._base,
                    "capture": True,
                },
                timeout=_LOCAL_BASELINE_CHANNEL_TIMEOUT_S,
            )
        except Exception:
            logger.warning(
                "turn.local_baseline_failed",
                conversation_id=self._channel.conversation_id,
                message_id=mid,
                phase="channel_capture",
                exc_info=True,
            )
            return None
        if isinstance(value, dict) and value.get("ready") is True:
            return mid
        return None

    async def ensure_turn_baseline_ready(self, message_id: str) -> bool:
        """Ensure a non-empty Local zip exists (capture if missing). Fail-closed."""
        mid = (message_id or "").strip()
        if not mid:
            return False
        try:
            value = await self._channel.request(
                WorkspaceOp.ENSURE_TURN_BASELINE,
                {
                    "message_id": mid,
                    "directory": self._base,
                    "capture": True,
                },
                timeout=_LOCAL_BASELINE_CHANNEL_TIMEOUT_S,
            )
        except Exception:
            logger.warning(
                "turn.local_baseline_failed",
                conversation_id=self._channel.conversation_id,
                message_id=mid,
                phase="channel_ensure",
                exc_info=True,
            )
            return False
        return isinstance(value, dict) and value.get("ready") is True


def _parse_index_files_value(
    value: dict[str, Any], *, out_path: Any
) -> IndexFilesResult:
    """Parse desktop ``index_files`` value: prefer ``entries``, fall back to ``paths``.

    Contract: ``{ entries: [{path, mtime_ms?, size_bytes?}, ...], truncated }``;
    may still carry ``paths`` for older desktops / dual emission.
    """
    truncated = bool(value.get("truncated", False))
    raw_entries = value.get("entries")
    if isinstance(raw_entries, list) and raw_entries:
        entries: list[IndexFileEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if path is None:
                continue
            raw_mtime = item.get("mtime_ms")
            raw_size = item.get("size_bytes")
            entries.append(
                IndexFileEntry(
                    path=out_path(str(path)),
                    mtime_ms=None if raw_mtime is None else int(raw_mtime),
                    size_bytes=None if raw_size is None else int(raw_size),
                )
            )
        return IndexFilesResult(
            paths=[e.path for e in entries],
            truncated=truncated,
            entries=tuple(entries),
        )
    paths = [out_path(str(p)) for p in value.get("paths", [])]
    return IndexFilesResult(
        paths=paths,
        truncated=truncated,
        entries=tuple(IndexFileEntry(path=p) for p in paths),
    )
