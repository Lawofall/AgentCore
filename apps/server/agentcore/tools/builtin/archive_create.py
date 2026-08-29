"""archive_create — zip workspace files/dirs to a destination ``.zip``.

Local and cloud both land the zip through ``WorkspaceBackend.write_bytes``
(canonical tree). Packing reuses ``storage._archive.zip_dir`` (VCS/dependency
prune, ``max_files`` / ``max_bytes``). Zip-slip does not apply to create;
source and dest paths are still sanitized and refused outside the workspace.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.facts import CROSS_TURN_RETRY_KEY, CrossTurnRetry
from agentcore.storage._archive import ArchiveLimitError, zip_dir
from agentcore.tools.builtin.file_ops import (
    _outside_workspace_msg,
    _prepare_write_relpath,
    write_scope_rejection,
)
from agentcore.tools.file_products import FileProduct, file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace._paths import normalize_workspace_path
from agentcore.workspace.limits import FILE_TOO_LARGE_DETAIL, is_file_too_large_detail
from agentcore.workspace.protocol import (
    NotADirectory,
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceIOError,
)

logger = get_logger(__name__)

ARCHIVE_CREATE_TOOL_NAME = "archive_create"

# Align extract's zip-bomb ceilings (raw file bytes before zip).
_CREATE_MAX_FILES = 5_000
_CREATE_MAX_BYTES = 200 * 1024 * 1024  # 200 MiB


class ArchiveCreateTool:
    """Pack workspace files or directories into a destination ``.zip``."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_WORKER_ONLY,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=ARCHIVE_CREATE_TOOL_NAME,
            description=(
                "把工作区内的文件或目录打成 zip 落到指定相对路径。"
                "大包持久打包请用本工具，勿只靠 code_execute 假定工作区可见。"
                "HOW→consult(archive_create)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "工作区相对路径（文件或目录，如 `src` / `docs/a.md`）"
                        ),
                    },
                    "dest": {
                        "type": "string",
                        "description": "目标 `.zip` 相对路径（如 `out/pkg.zip`）",
                    },
                },
                "required": ["sources", "dest"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        sources = _parse_sources(arguments.get("sources"))
        dest_raw = str(arguments.get("dest") or "").strip()
        if not sources:
            return _fail(
                "sources 不能为空：请提供要打包的工作区相对路径（文件或目录）",
                start,
            )
        if not dest_raw:
            return _fail("dest 不能为空：请提供目标 `.zip` 相对路径", start)

        dest_path, dest_note = await _prepare_write_relpath(dest_raw, context)
        if not dest_path or dest_path == ".":
            return _fail("dest 无效：请提供目标 `.zip` 相对路径", start)
        if _escapes_workspace(dest_path):
            return _fail(
                f"dest `{dest_path}` 超出工作区范围。请使用工作区相对路径。",
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        if not dest_path.lower().endswith(".zip"):
            return _fail(
                f"dest 须为 `.zip` 文件（收到 `{dest_path}`）。",
                start,
            )

        scope_err = write_scope_rejection(context, dest_path)
        if scope_err:
            return _fail(scope_err, start, cross_turn_retry=CrossTurnRetry.FUTILE)

        backend = context.backend
        location = getattr(backend, "location", None)
        try:
            files, err = await _collect_source_files(
                backend, sources, dest_path=dest_path, location=location, start=start
            )
        except OutsideWorkspace as e:
            return _fail(
                _outside_workspace_msg(
                    ",".join(sources), location=location, reason=str(e)
                ),
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        if err is not None:
            return err
        if not files:
            return _fail("没有可打包的文件（空目录、均被剪掉、或源等于 dest）。", start)

        try:
            zip_bytes = await _stage_and_zip(backend, files, location=location, start=start)
        except _CreateError as e:
            return e.result

        try:
            n = await backend.write_bytes(dest_path, zip_bytes)
        except OutsideWorkspace as e:
            return _fail(
                _outside_workspace_msg(dest_path, location=location, reason=str(e)),
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        except WorkspaceIOError as e:
            return _fail(f"写入 `{dest_path}` 失败：{e}", start)

        total_bytes = int(n)
        logger.info(
            "archive_create.done",
            dest=dest_path,
            sources=len(sources),
            files=len(files),
            bytes=total_bytes,
            run_id=context.run_id,
        )

        lines = [
            f"已打包 → `{dest_path}`：{len(files)} 个文件，共 {total_bytes} 字节。",
            f"限额：最多 {_CREATE_MAX_FILES} 个文件 / "
            f"打包前 {_CREATE_MAX_BYTES // (1024 * 1024)} MiB（已剪 VCS/依赖目录）。",
            "源路径：",
            *[f"  - {p}" for p in sources[:20]],
        ]
        if len(sources) > 20:
            lines.append(f"  … 另有 {len(sources) - 20} 个源未列出")
        if dest_note:
            lines.append(dest_note)
        lines.append("【验真】请以本回执路径确认落盘；可用 file_list 抽查目标 zip。")

        return ToolResult(
            tool_call_id="",
            success=True,
            output="\n".join(lines),
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "dest": dest_path,
                "sources": list(sources),
                "files_packed": len(files),
                "bytes_written": total_bytes,
                "path": dest_path,
            },
            file_products=[file_product(dest_path)],
        )


def _parse_sources(raw: Any) -> list[str]:
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _escapes_workspace(rel: str) -> bool:
    if rel in ("..",) or rel.startswith("../"):
        return True
    return any(part == ".." for part in rel.replace("\\", "/").split("/"))


async def _collect_source_files(
    backend: Any,
    sources: list[str],
    *,
    dest_path: str,
    location: str | None,
    start: float,
) -> tuple[list[str], ToolResult | None]:
    files: list[str] = []
    seen: set[str] = set()
    for raw in sources:
        source = normalize_workspace_path(raw, root_label="workspace")
        if not source:
            return [], _fail("sources 含无效路径", start)
        if _escapes_workspace(source):
            return [], _fail(
                f"source `{source}` 超出工作区范围。请使用工作区相对路径。",
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        try:
            is_file = await backend.exists(source)
        except OutsideWorkspace as e:
            return [], _fail(
                _outside_workspace_msg(source, location=location, reason=str(e)),
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        except WorkspaceIOError as e:
            return [], _fail(f"读取 `{source}` 失败：{e}", start)

        if is_file:
            if source != dest_path:
                _append_unique(files, seen, source)
            continue

        try:
            listing = await backend.list(source, "**", cap=_CREATE_MAX_FILES + 1)
        except NotADirectory:
            return [], _fail(f"找不到：`{source}`（不是已有文件或目录）", start)
        except OutsideWorkspace as e:
            return [], _fail(
                _outside_workspace_msg(source, location=location, reason=str(e)),
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        except WorkspaceIOError as e:
            return [], _fail(f"列举 `{source}` 失败：{e}", start)

        if listing.truncated:
            return [], _fail(
                f"拒绝打包：文件数超过上限 {_CREATE_MAX_FILES}。"
                "请缩小 sources 范围。",
                start,
            )
        for entry in listing:
            if entry.is_dir or entry.path == dest_path:
                continue
            _append_unique(files, seen, entry.path)

    return files, None


def _append_unique(files: list[str], seen: set[str], path: str) -> None:
    if path not in seen:
        seen.add(path)
        files.append(path)


class _CreateError(Exception):
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        super().__init__(result.error or "")


async def _stage_and_zip(
    backend: Any,
    files: list[str],
    *,
    location: str | None,
    start: float,
) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        stage_root = stage.resolve()
        for rel in files:
            try:
                data = await backend.read_bytes(rel, max_bytes=_CREATE_MAX_BYTES)
            except PathNotFound:
                raise _CreateError(_fail(f"找不到：`{rel}`", start)) from None
            except NotAFile:
                raise _CreateError(_fail(f"`{rel}` 不是文件", start)) from None
            except OutsideWorkspace as e:
                raise _CreateError(
                    _fail(
                        _outside_workspace_msg(rel, location=location, reason=str(e)),
                        start,
                        cross_turn_retry=CrossTurnRetry.FUTILE,
                    )
                ) from None
            except WorkspaceIOError as e:
                detail = str(e)
                if is_file_too_large_detail(detail):
                    raise _CreateError(
                        _fail(
                            f"源文件过大，无法读取（{FILE_TOO_LARGE_DETAIL}）：`{rel}`。"
                            "请缩小 sources，或拆成多个较小压缩包。",
                            start,
                            contract_failure=True,
                        )
                    ) from None
                raise _CreateError(_fail(f"读取 `{rel}` 失败：{detail}", start)) from None

            out = (stage / Path(*rel.split("/"))).resolve()
            if out != stage_root and stage_root not in out.parents:
                raise _CreateError(
                    _fail(
                        f"source `{rel}` 超出工作区范围。请使用工作区相对路径。",
                        start,
                        cross_turn_retry=CrossTurnRetry.FUTILE,
                    )
                )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)

        try:
            return await asyncio.to_thread(
                lambda: zip_dir(
                    stage,
                    max_files=_CREATE_MAX_FILES,
                    max_bytes=_CREATE_MAX_BYTES,
                )
            )
        except ArchiveLimitError as e:
            if e.reason == "max_files":
                raise _CreateError(
                    _fail(
                        f"拒绝打包：文件数超过上限 {_CREATE_MAX_FILES}"
                        f"（已扫描 {e.file_count} 个文件）。请缩小 sources。",
                        start,
                    )
                ) from None
            raise _CreateError(
                _fail(
                    f"拒绝打包：体积超过上限"
                    f" {_CREATE_MAX_BYTES // (1024 * 1024)} MiB"
                    f"（已累计 {e.total_bytes} 字节 / {e.file_count} 个文件）。",
                    start,
                )
            ) from None


def _fail(
    error: str,
    start: float,
    *,
    contract_failure: bool = False,
    products: list[FileProduct] | None = None,
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
        file_products=list(products or []),
    )
