"""Built-in tool: download_url (HTTP(S) URL → workspace relative path).

Structured workspace download — **not** software install. Installer-shaped
bytes may land and be labeled; they are never executed or silently installed.
SSRF reuses ``read_url._safe_request`` + ``PinnedIPTransport`` (one policy).
Size ceiling aligns with ``workspace_upload_max_bytes`` (not the 5 MiB AI read gate).
"""

from __future__ import annotations

import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.net import (
    EgressError,
    PinnedAddressError,
    PinnedIPTransport,
    describe_net_error,
    outbound_async_client,
    site_of,
    web_timeout,
)
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.file_ops import (
    _mark_landed_files,
    _outside_workspace_msg,
    _prepare_write_relpath,
    write_scope_rejection,
)
from agentcore.tools.builtin.file_ops.errors import CROSS_TURN_RETRY_KEY, CrossTurnRetry
from agentcore.tools.builtin.web.read_url import _safe_request
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.protocol import OutsideWorkspace, WorkspaceIOError

logger = get_logger(__name__)

DOWNLOAD_URL_TOOL_NAME = "download_url"

# Large binary pulls need a longer read window than HTML deep-read (15s).
_DOWNLOAD_READ_TIMEOUT = 120.0
_MAX_REDIRECTS = 5

# Installer / package shapes: allowed to land; labeled; never executed here.
_INSTALLER_EXTS = frozenset(
    {
        ".exe",
        ".msi",
        ".msix",
        ".msp",
        ".dmg",
        ".pkg",
        ".appimage",
        ".deb",
        ".rpm",
        ".apk",
        ".ipa",
        ".bat",
        ".cmd",
        ".ps1",
        ".sh",
    }
)
_INSTALLER_MIME_HINTS = frozenset(
    {
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/vnd.microsoft.portable-executable",
        "application/x-msi",
        "application/x-apple-diskimage",
        "application/vnd.debian.binary-package",
        "application/x-rpm",
        "application/vnd.android.package-archive",
    }
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def _max_bytes() -> int:
    return int(settings.workspace_upload_max_bytes)


def _content_type_of(resp: httpx.Response) -> str:
    raw = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return raw or "application/octet-stream"


def _is_installer(*, path: str, content_type: str) -> bool:
    ext = PurePosixPath(path).suffix.lower()
    if ext in _INSTALLER_EXTS:
        return True
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    return ct in _INSTALLER_MIME_HINTS


async def _read_body_bounded(resp: httpx.Response, max_bytes: int) -> bytes:
    """Stream response body; refuse when Content-Length or accumulated bytes exceed max."""
    clen = resp.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > max_bytes:
        raise ValueError(f"content_length:{clen}>{max_bytes}")
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"body:{total}>{max_bytes}")
        chunks.append(chunk)
    return b"".join(chunks)


class DownloadUrlTool:
    """Fetch HTTP(S) bytes and write them to a workspace-relative path."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_WORKER_ONLY,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        max_mib = _max_bytes() // (1024 * 1024)
        return ToolSchema(
            name=DOWNLOAD_URL_TOOL_NAME,
            description=(
                "把 HTTP(S) URL 的原始字节下载到工作区相对路径（二进制/文件落盘主路径）。"
                f"大小上限与用户上传对齐（约 {max_mib} MiB），勿与 file_read 的 5 MiB 读闸混淆。"
                "内网/私有地址与危险重定向按 SSRF 策略拒绝。"
                "安装包（.exe/.msi/.dmg 等）允许落盘并标明类型，但本工具不执行、不静默安装。"
                "需要网页正文深读时用 read_url，不要用本工具；"
                "已有工作区 zip 解压用 archive_extract。"
                "【禁止】用 code_execute / terminal / host(action=shell) 当 wget/curl 主路径。"
                "参数：url + path（工作区相对路径，如 `uploads/data.csv`）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要下载的 HTTP(S) URL",
                    },
                    "path": {
                        "type": "string",
                        "description": "落盘目标（工作区相对路径，含文件名）",
                    },
                },
                "required": ["url", "path"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        url = str(arguments.get("url") or "").strip()
        path_raw = str(arguments.get("path") or "").strip()
        if not url:
            return _fail("缺少必填参数：url", start)
        if not path_raw:
            return _fail("缺少必填参数：path（工作区相对路径）", start)

        try:
            parsed = urlparse(url)
        except ValueError:
            return _fail("URL 无效，无法解析", start)
        if (parsed.scheme or "").lower() not in ("http", "https"):
            return _fail("仅支持 http/https URL", start)

        rel_path, rename_note = await _prepare_write_relpath(path_raw, context)
        if not rel_path or rel_path in (".",):
            return _fail("path 无效：请提供工作区相对文件路径（含文件名）", start)
        if rel_path.endswith("/"):
            return _fail("path 须为文件路径，不能是目录", start)

        scope_err = write_scope_rejection(context, rel_path)
        if scope_err:
            return _fail(scope_err, start, cross_turn_retry=CrossTurnRetry.FUTILE)

        max_bytes = _max_bytes()
        backend = context.backend
        location = getattr(backend, "location", None)

        if context.on_phase:
            context.on_phase("fetching")

        content_type = "application/octet-stream"
        try:
            async with outbound_async_client(
                timeout=web_timeout(read=_DOWNLOAD_READ_TIMEOUT),
                follow_redirects=False,
                transport=PinnedIPTransport(verify=False),
            ) as client:
                resp = await _safe_request(
                    client,
                    "GET",
                    url,
                    max_redirects=_MAX_REDIRECTS,
                    headers=_BROWSER_HEADERS,
                )
                try:
                    resp.raise_for_status()
                    content_type = _content_type_of(resp)
                    data = await _read_body_bounded(resp, max_bytes)
                finally:
                    await resp.aclose()
        except ValueError as e:
            msg = str(e)
            if msg.startswith("URL blocked"):
                return _fail(
                    "下载被拒：目标 URL 指向内网/私有地址或受阻主机（SSRF 防护）。",
                    start,
                )
            if msg.startswith("Too many redirects"):
                return _fail("下载失败：重定向次数过多", start)
            if msg.startswith("content_length:") or msg.startswith("body:"):
                max_mib = max_bytes // (1024 * 1024)
                return _fail(
                    f"下载失败：文件超过上限（约 {max_mib} MiB，与用户上传对齐）。"
                    "请缩小资源，或请用户经面板上传。",
                    start,
                    contract_failure=True,
                )
            return _fail(f"下载失败：{msg}", start)
        except Exception as e:
            reason = describe_net_error(e)
            logger.warning(
                "tool.download_url_error",
                url=url[:200],
                error=reason,
                error_repr=repr(e),
            )
            hint = ""
            if isinstance(e, (EgressError, PinnedAddressError)):
                hint = "（出网受限或地址不可达）"
            elif isinstance(e, httpx.HTTPStatusError):
                hint = f"（HTTP {e.response.status_code}）"
            return _fail(f"下载失败：{reason}{hint}", start)

        installer = _is_installer(path=rel_path, content_type=content_type)

        try:
            written = await backend.write_bytes(rel_path, data)
        except OutsideWorkspace as e:
            return _fail(
                _outside_workspace_msg(rel_path, location=location, reason=str(e)),
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        except WorkspaceIOError as e:
            return _fail(f"写入 `{rel_path}` 失败：{e}", start)

        # 治理面与交付物台账是两件事，缺哪支都少一半（与 file_ops 的写盘笔同形）：这支盖
        # landed-files 闸 / Artifact-first path kind / 首写者归属 / 同 path file_read 上限
        # 重置（写后核对不该被读闸挡住）/ 兄弟 verify 缓存失效；台账那支是下面的
        # ``file_products``。
        _mark_landed_files(context, rel_path)

        site = site_of(url)
        lines = [
            f"已下载 `{url}` → `{rel_path}`（{written} 字节）。",
            f"Content-Type：{content_type}；站点：{site or '（未知）'}。",
        ]
        if installer:
            lines.append(
                "类型：安装包/可执行形态（已按扩展名或 MIME 标明）。"
                "本工具仅落盘，未执行、未静默安装；"
                "若需装系统软件，走包管理器装软件面（非本工具）。"
            )
        if rename_note:
            lines.append(rename_note)
        lines.append("【验真】请以本回执路径确认落盘；可用 file_list 抽查。")

        logger.info(
            "download_url.done",
            path=rel_path,
            bytes=written,
            content_type=content_type,
            installer=installer,
            site=site,
            run_id=context.run_id,
        )

        return ToolResult(
            tool_call_id="",
            success=True,
            output="\n".join(lines),
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "url": url,
                "path": rel_path,
                "bytes_written": int(written),
                "content_type": content_type,
                "installer_like": installer,
                "site": site,
            },
            # 交付物台账（契约见 ``tools/file_products.py``）：报 sanitize 之后真正落盘的
            # 那一个路径，不是模型请求的原始 path。下载来的字节不是任何工作区源文件的
            # 导出件，故不填 ``derived_from``。
            file_products=[file_product(rel_path)],
        )


def _fail(
    error: str,
    start: float,
    *,
    contract_failure: bool = False,
    cross_turn_retry: CrossTurnRetry | None = None,
) -> ToolResult:
    meta: dict[str, Any] = {"contract_failure": True} if contract_failure else {}
    if cross_turn_retry is not None:
        meta[CROSS_TURN_RETRY_KEY] = cross_turn_retry.value
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        duration_ms=int((time.monotonic() - start) * 1000),
        metadata=meta,
    )
