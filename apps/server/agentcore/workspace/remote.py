"""Remote cloud-desk WorkspaceBackend for the desktop sidecar.

When the local engine sits on a folder with no ``local_binding``, on-disk
``ServerWorkspace`` would write the sidecar ``data_dir/workspaces`` tree — the
same path as the API process only coincidentally in local-dev. Production is two
disks: those writes never reach the cloud volume.

This backend speaks the existing ``/v1/workspaces/{ws_id}/files`` family with a
``type=workspaces`` narrow ticket (never access, never folders/account CRUD).
``location="server"`` so sidecar still withholds ``run`` / gVisor.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal, NoReturn
from urllib.parse import quote

import httpx

from agentcore.core.logging import get_logger
from agentcore.core.net import WEB_CONNECT_TIMEOUT, outbound_async_client
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.workspace.cloud_credentials import get_workspaces_credentials
from agentcore.workspace.file_kind import decode_stored_text, encode_stored_text
from agentcore.workspace.limits import (
    FILE_TOO_LARGE_DETAIL,
    OFFICE_EXTRACT_DISK_MAX_BYTES,
    effective_read_bytes_cap,
    effective_read_head_cap,
    replace_max_bytes,
)
from agentcore.workspace.protocol import (
    AlreadyExists,
    AmbiguousMatch,
    DirEntry,
    DirListing,
    GlobFilesQuery,
    GlobFilesResult,
    GrepHit,
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
from agentcore.workspace.text_replace import (
    TextReplaceAmbiguous,
    TextReplaceNoMatch,
    apply_text_replace,
)

logger = get_logger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=WEB_CONNECT_TIMEOUT)
_MISSING_TICKET = "文件在云上，本机引擎还写不到那张桌子（缺少工作区凭证）"
_GREP_FILE_SCAN_CAP = 200


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "")[:500]
    if isinstance(body, dict):
        msg = body.get("message") or body.get("detail") or body.get("error")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
        if isinstance(msg, dict):
            inner = msg.get("message") or msg.get("detail")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return (resp.text or "")[:500]


def _raise_http(resp: httpx.Response, *, path: str = "") -> NoReturn:
    detail = _detail(resp)
    code = resp.status_code
    if code == 404:
        raise PathNotFound(path or detail or "文件不存在")
    if code == 409:
        raise WorkspaceIOError(detail or "本地工作区的文件请在桌面端访问")
    if code in (401, 403):
        raise WorkspaceIOError("workspaces_cloud_unauthorized")
    if code == 422:
        if "超出工作区" in detail:
            raise OutsideWorkspace(detail)
        if "已存在" in detail:
            raise AlreadyExists(detail)
        if "目录" in detail:
            raise NotAFile(path or detail)
        raise WorkspaceIOError(detail or "workspace http 422")
    if code == 413:
        raise WorkspaceIOError(FILE_TOO_LARGE_DETAIL)
    raise WorkspaceIOError(detail or f"workspace http {code}")


class RemoteCloudWorkspace:
    """HTTP WorkspaceBackend for an unbound cloud desk, hosted by sidecar."""

    location: Literal["server"] = "server"

    def __init__(self, *, ws_id: str, root_label: str = "workspace") -> None:
        self._ws_id = (ws_id or "").strip()
        if not self._ws_id:
            raise ValueError("RemoteCloudWorkspace requires ws_id")
        self.root_label = root_label or "workspace"
        self.ai_list_materials: frozenset[str] = frozenset()
        self.ai_list_reveal_archives = False
        self._dirty = False

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _mark_mutated(self) -> None:
        self._dirty = True

    def _creds(self):
        creds = get_workspaces_credentials()
        if creds is None or not creds.api_key or not creds.base_url:
            raise WorkspaceIOError(_MISSING_TICKET)
        return creds

    def _url(self, suffix: str) -> str:
        creds = self._creds()
        base = creds.base_url.rstrip("/")
        rel = suffix.lstrip("/")
        return f"{base}/{self._ws_id}/{rel}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._creds().api_key}"}

    async def _request(
        self,
        method: str,
        suffix: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = self._url(suffix)
        hdrs = {**self._headers(), **(headers or {})}
        try:
            async with outbound_async_client(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    content=content,
                    headers=hdrs,
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "workspaces.cloud_unreachable",
                ws_id=self._ws_id,
                error=str(exc),
            )
            raise WorkspaceIOError(f"云工作区不可达：{exc}") from exc
        return resp

    def _file_suffix(self, path: str) -> str:
        rel = (path or "").replace("\\", "/").lstrip("/")
        if not rel or rel == ".":
            raise OutsideWorkspace(path or ".")
        return "files/" + quote(rel, safe="/")

    async def read(self, path: str) -> str:
        data = await self.read_bytes(path)
        decoded = decode_stored_text(data)
        if decoded is None:
            raise NotUTF8(path)
        return decoded[0].replace("\r\n", "\n").replace("\r", "\n")

    async def write(self, path: str, content: str) -> int:
        await self.write_bytes(path, content.encode("utf-8"))
        return len(content)

    async def append(self, path: str, content: str) -> int:
        try:
            existing = await self.read(path)
        except PathNotFound:
            existing = ""
        await self.write(path, existing + content)
        return len(content)

    async def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        cap = effective_read_bytes_cap(max_bytes)
        resp = await self._request("GET", self._file_suffix(path))
        if resp.status_code >= 400:
            _raise_http(resp, path=path)
        data = resp.content
        if len(data) > cap:
            raise WorkspaceIOError(FILE_TOO_LARGE_DETAIL)
        return data

    async def read_head(
        self, path: str, *, max_bytes: int | None = None
    ) -> ReadHeadResult:
        peek = effective_read_head_cap(max_bytes)
        data = await self.read_bytes(path, max_bytes=None)
        return ReadHeadResult(data=data[:peek], size_bytes=len(data))

    async def extract_office(
        self,
        path: str,
        *,
        ext: str,
        start_page: int = 1,
        page_end: int | None = None,
        page_total: int | None = None,
    ):
        from agentcore.workspace.attachment_parse import extract_office_file

        data = await self.read_bytes(path, max_bytes=OFFICE_EXTRACT_DISK_MAX_BYTES)
        suffix = ext if str(ext).startswith(".") else f".{ext}"
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            return await extract_office_file(
                Path(tmp),
                ext=ext,
                start_page=start_page,
                page_end=page_end,
                page_total=page_total,
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    async def write_bytes(self, path: str, data: bytes) -> int:
        resp = await self._request(
            "PUT",
            self._file_suffix(path),
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        if resp.status_code >= 400:
            _raise_http(resp, path=path)
        self._mark_mutated()
        return len(data)

    async def list(
        self, directory: str, pattern: str, *, cap: int | None = None
    ) -> DirListing:
        del cap
        recursive = "**" in (pattern or "")
        resp = await self._request(
            "GET",
            "files",
            params={"path": directory or ".", "recursive": str(recursive).lower()},
        )
        if resp.status_code >= 400:
            _raise_http(resp, path=directory)
        payload = resp.json() if resp.content else {}
        entries_raw = payload.get("data") if isinstance(payload, dict) else None
        truncated = (
            bool(payload.get("truncated")) if isinstance(payload, dict) else False
        )
        entries: list[DirEntry] = []
        glob_pat = pattern or "*"
        for item in entries_raw or ():
            if not isinstance(item, dict):
                continue
            rel = str(item.get("path") or "")
            name = rel.rsplit("/", 1)[-1]
            if (
                glob_pat not in ("*", "**/*")
                and not fnmatch.fnmatch(name, glob_pat.replace("**/", ""))
                and not fnmatch.fnmatch(rel, glob_pat)
            ):
                continue
            entries.append(
                DirEntry(
                    path=rel,
                    is_dir=bool(item.get("is_dir")),
                    size_bytes=item.get("size_bytes"),
                    mtime_ms=item.get("mtime_ms"),
                )
            )
        return DirListing(entries=entries, truncated=truncated)

    async def exists(self, path: str) -> bool:
        rel = (path or "").replace("\\", "/").lstrip("/")
        if not rel or rel == ".":
            return False
        parent, _, name = rel.rpartition("/")
        try:
            listing = await self.list(parent or ".", "*")
        except (PathNotFound, NotADirectory, NotAFile):
            return False
        for entry in listing.entries:
            entry_path = str(entry.path or "").replace("\\", "/").lstrip("/")
            if entry_path in (rel, name):
                return not entry.is_dir
        return False

    async def read_lines(
        self, path: str, *, offset: int = 1, limit: int | None = None
    ) -> ReadLinesResult:
        from agentcore.config import settings
        from agentcore.workspace.text_window import read_text_lines_from_bytes

        cap = int(settings.workspace_upload_max_bytes)
        resp = await self._request("GET", self._file_suffix(path))
        if resp.status_code >= 400:
            _raise_http(resp, path=path)
        data = resp.content
        if len(data) > cap:
            raise WorkspaceIOError(f"{FILE_TOO_LARGE_DETAIL}（{len(data)}字节）")
        return read_text_lines_from_bytes(data, offset=offset, limit=limit)

    async def list_tree(
        self,
        directory: str,
        *,
        pattern: str = "*",
        max_depth: int = 3,
        max_entries: int = 200,
    ) -> TreeResult:
        listing = await self.list(directory, "**/*")
        base = (directory or ".").replace("\\", "/").rstrip("/")
        if base in ("", "."):
            base = ""
        out: list[TreeEntry] = []
        truncated = listing.truncated
        for entry in listing.entries:
            rel = entry.path.replace("\\", "/")
            rest = (
                rel[len(base) :].lstrip("/")
                if base and (rel == base or rel.startswith(base + "/"))
                else rel
            )
            depth = 0 if not rest else rest.count("/") + 1
            if depth > max_depth:
                continue
            name = rest.rsplit("/", 1)[-1] if rest else rel.rsplit("/", 1)[-1]
            if (
                pattern not in ("*", "**/*")
                and not fnmatch.fnmatch(name, pattern)
                and not entry.is_dir
            ):
                continue
            out.append(TreeEntry(path=rel, is_dir=entry.is_dir, depth=depth))
            if len(out) >= max_entries:
                truncated = True
                break
        return TreeResult(entries=out, truncated=truncated, elided_count=0)

    async def glob_files(self, query: GlobFilesQuery) -> GlobFilesResult:
        name = "*"
        if query.globs:
            name = query.globs[0].replace("\\", "/").rsplit("/", 1)[-1] or "*"
        depth = 8 if query.max_depth is None else max(query.max_depth, 0) + 1
        tree = await self.list_tree(
            query.directory,
            pattern=name,
            max_depth=max(1, depth),
            max_entries=query.max_entries,
        )
        paths = [e.path for e in tree.entries if not e.is_dir]
        return GlobFilesResult(
            paths=paths,
            truncated=tree.truncated,
            warnings=list(tree.warnings),
        )

    async def index_files(
        self, cap: int | None = None, *, order: str = "path"
    ) -> IndexFilesResult:
        del cap, order
        resp = await self._request("GET", "file-index")
        if resp.status_code >= 400:
            _raise_http(resp)
        payload = resp.json() if resp.content else {}
        paths = payload.get("data") if isinstance(payload, dict) else None
        truncated = (
            bool(payload.get("truncated")) if isinstance(payload, dict) else False
        )
        clean = [str(p) for p in (paths or ()) if isinstance(p, str)]
        entries = tuple(IndexFileEntry(path=p) for p in clean)
        return IndexFilesResult(paths=clean, truncated=truncated, entries=entries)

    async def mkdir(self, path: str) -> None:
        resp = await self._request("POST", "dirs", json={"path": path})
        if resp.status_code >= 400:
            _raise_http(resp, path=path)
        self._mark_mutated()

    async def delete(self, path: str, *, permanent: bool = False) -> None:
        del permanent
        resp = await self._request("DELETE", self._file_suffix(path))
        if resp.status_code >= 400:
            _raise_http(resp, path=path)
        self._mark_mutated()

    async def copy(self, src: str, dst: str) -> None:
        resp = await self._request("POST", "copy", json={"src": src, "dst": dst})
        if resp.status_code >= 400:
            _raise_http(resp, path=src)
        self._mark_mutated()

    async def move(self, src: str, dst: str) -> None:
        resp = await self._request("POST", "move", json={"src": src, "dst": dst})
        if resp.status_code >= 400:
            _raise_http(resp, path=src)
        self._mark_mutated()

    async def replace(
        self, path: str, old: str, new: str, *, all_: bool
    ) -> ReplaceOutcome:
        raw = await self.read_bytes(path, max_bytes=replace_max_bytes())
        decoded = decode_stored_text(raw)
        if decoded is None:
            raise NotUTF8(path)
        text, encoding = decoded
        result = apply_text_replace(text, old, new, all_=all_)
        if isinstance(result, TextReplaceNoMatch):
            raise NoMatch(path)
        if isinstance(result, TextReplaceAmbiguous):
            raise AmbiguousMatch(result.count)
        try:
            payload = encode_stored_text(result.content, encoding)
        except UnicodeEncodeError as exc:
            raise WorkspaceIOError(str(exc)) from exc
        await self.write_bytes(path, payload)
        return ReplaceOutcome(count=result.count, first_line=result.first_line)

    async def grep(self, query: GrepQuery) -> GrepResult:
        index = await self.index_files()
        directory = (query.directory or ".").replace("\\", "/").rstrip("/")
        if directory in ("", "."):
            directory = ""
        glob_pat = query.glob or "*"
        flags = re.IGNORECASE if query.case_insensitive else 0
        try:
            regex = re.compile(query.pattern, flags)
        except re.error as exc:
            raise WorkspaceIOError(f"无效正则：{exc}") from exc
        hits: list[GrepHit] = []
        file_counts: dict[str, int] = {}
        truncated = index.truncated
        scanned = 0
        for rel in index.paths:
            if directory and not (rel == directory or rel.startswith(directory + "/")):
                continue
            name = rel.rsplit("/", 1)[-1]
            if glob_pat != "*" and not fnmatch.fnmatch(name, glob_pat):
                continue
            scanned += 1
            if scanned > _GREP_FILE_SCAN_CAP:
                truncated = True
                break
            try:
                text = await self.read(rel)
            except (PathNotFound, NotAFile, NotUTF8, WorkspaceIOError):
                continue
            n = 0
            for i, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    n += 1
                    if not query.files_only:
                        hits.append(GrepHit(path=rel, line_no=i, text=line))
                    if len(hits) >= query.max_results:
                        truncated = True
                        break
            if n:
                file_counts[rel] = n
            if truncated and len(hits) >= query.max_results:
                break
        total = sum(file_counts.values())
        return GrepResult(
            hits=hits[: query.max_results],
            file_counts=list(file_counts.items()),
            total_matches=total,
            truncated=truncated,
        )

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        del req
        return ExecutionResult(
            success=False,
            stdout="",
            stderr="本机引擎不在云隔离里跑命令",
            exit_code=1,
            duration_ms=0,
        )
