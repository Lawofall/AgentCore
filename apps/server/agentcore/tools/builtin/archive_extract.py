"""archive_extract — unzip a workspace zip into a destination directory.

Local and cloud both write member bytes through ``WorkspaceBackend.write_bytes``
(canonical tree). Zip-slip protection reuses ``storage._archive`` helpers.
"""

from __future__ import annotations

import time
import zipfile
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.facts import CROSS_TURN_RETRY_KEY, CrossTurnRetry
from agentcore.storage._archive import (
    ZipExtractLimitError,
    ZipSlipError,
    iter_zip_file_members,
)
from agentcore.tools.builtin.file_ops import (
    _outside_workspace_msg,
    _prepare_write_relpath,
    write_scope_rejection,
)
from agentcore.tools.file_products import FileProduct, file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace._paths import normalize_workspace_path, sanitize_write_relpath
from agentcore.workspace.limits import FILE_TOO_LARGE_DETAIL, is_file_too_large_detail
from agentcore.workspace.protocol import (
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceIOError,
)

logger = get_logger(__name__)

ARCHIVE_EXTRACT_TOOL_NAME = "archive_extract"

# Zip-bomb ceilings (uncompressed). Input zip still passes WORKSPACE_READ_MAX via read_bytes.
_EXTRACT_MAX_FILES = 5_000
_EXTRACT_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MiB

# 自报产物条数上限（交付物台账 · 契约见 ``tools/file_products.py``）。取值与
# ``sandbox/written_scan._MAX_FILES`` 一致：解压是
# 唯一一支单次能落上千个文件的笔，而产物尾注**进 transcript 并回喂模型**，把 5000 条
# 路径列全既冲爆上下文也没有信息量（用户面路径列表同理）。落盘不受此限——限的只是记账
# 与回执的条数；截断时回执明说，绝不假装只产了这些。
_PRODUCT_REPORT_MAX = 200


def _extracted_products(written: list[str]) -> list[FileProduct]:
    """Self-report the members that really landed (bounded by ``_PRODUCT_REPORT_MAX``).

    解压出来的成员不是任何源文件的导出件（源是那个 zip，不是中间稿），故不填
    ``derived_from``——填了会让 zip 在用户面被误折叠成中间稿。
    """
    return [file_product(p) for p in written[:_PRODUCT_REPORT_MAX]]


class ArchiveExtractTool:
    """Extract a workspace ``.zip`` into a destination directory."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=ARCHIVE_EXTRACT_TOOL_NAME,
            description=(
                "把工作区内的 zip 解压到指定目录（相对路径）。"
                "写出路径经 sanitize；拒绝 zip-slip（`..` / 绝对路径成员）。"
                "大 zip 持久落盘请用本工具。"
                "沙箱临时产物不等于 canonical 工作区树。"
                "回执含写出文件数；超限额 / 缺文件 / 坏 zip / zip-slip 会明确失败原因。"
                "``archive`` 须为工作区内已有 `.zip`；``dest`` 为解压目标目录（可 `.`）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "archive": {
                        "type": "string",
                        "description": "工作区内的 zip 相对路径（如 `uploads/pkg.zip`）",
                    },
                    "dest": {
                        "type": "string",
                        "description": "解压目标目录（工作区相对路径；`.` 表示工作区根）",
                    },
                },
                "required": ["archive", "dest"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        archive_raw = str(arguments.get("archive") or "").strip()
        dest_raw = str(arguments.get("dest") or "").strip()
        if not archive_raw:
            return _fail("archive 不能为空：请提供工作区内的 .zip 相对路径", start)
        if not dest_raw:
            return _fail("dest 不能为空：请提供解压目标目录（可用 `.` 表示工作区根）", start)

        # Read path: normalize only (no write sanitize / dossier flatten — that can
        # rename nested archives and make an existing zip look "missing").
        archive_path = normalize_workspace_path(archive_raw, root_label="workspace")
        if not archive_path or archive_path == ".":
            return _fail("archive 无效：请提供工作区内的 .zip 相对路径", start)
        if not archive_path.lower().endswith(".zip"):
            return _fail(
                f"archive 须为 `.zip` 文件（收到 `{archive_path}`）；"
                "请先把压缩包放到工作区再调用本工具。",
                start,
            )

        dest_path, dest_note = await _prepare_write_relpath(dest_raw, context)
        if not dest_path:
            return _fail("dest 无效", start)
        if dest_path in ("..",) or dest_path.startswith("../"):
            return _fail(
                f"dest `{dest_path}` 超出工作区范围。请使用工作区相对路径。",
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )

        scope_err = write_scope_rejection(context, dest_path)
        if scope_err:
            return _fail(scope_err, start, cross_turn_retry=CrossTurnRetry.FUTILE)

        backend = context.backend
        location = getattr(backend, "location", None)
        try:
            data = await backend.read_bytes(archive_path)
        except PathNotFound:
            return _fail(f"找不到压缩包：`{archive_path}`", start)
        except NotAFile:
            return _fail(f"`{archive_path}` 不是文件", start)
        except OutsideWorkspace as e:
            return _fail(
                _outside_workspace_msg(archive_path, location=location, reason=str(e)),
                start,
                cross_turn_retry=CrossTurnRetry.FUTILE,
            )
        except WorkspaceIOError as e:
            detail = str(e)
            if is_file_too_large_detail(detail):
                return _fail(
                    f"压缩包过大，无法整包读取（{FILE_TOO_LARGE_DETAIL}）。"
                    "请缩小 zip，或拆成多个较小压缩包后再解压。",
                    start,
                    contract_failure=True,
                )
            return _fail(f"读取压缩包失败：{detail}", start)

        try:
            members = iter_zip_file_members(
                data,
                max_files=_EXTRACT_MAX_FILES,
                max_uncompressed_bytes=_EXTRACT_MAX_UNCOMPRESSED_BYTES,
            )
        except ZipSlipError as e:
            return _fail(
                f"拒绝解压：检测到 zip-slip 成员 `{e.member}`（路径逃逸）。"
                "请使用不含 `..` / 绝对路径条目的合法 zip。",
                start,
            )
        except ZipExtractLimitError as e:
            if e.reason == "max_files":
                return _fail(
                    f"拒绝解压：文件数超过上限 {_EXTRACT_MAX_FILES}"
                    f"（已扫描 {e.file_count} 个文件）。请缩小压缩包内容。",
                    start,
                )
            return _fail(
                f"拒绝解压：解压后体积超过上限"
                f" {_EXTRACT_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MiB"
                f"（已累计 {e.total_bytes} 字节 / {e.file_count} 个文件）。",
                start,
            )
        except zipfile.BadZipFile:
            return _fail(f"`{archive_path}` 不是合法 zip（BadZipFile）", start)

        if not members:
            return _fail(
                f"`{archive_path}` 内没有可解压的文件条目（空包或仅有目录）。",
                start,
            )

        written: list[str] = []
        total_bytes = 0
        for rel, content in members:
            out_raw = rel if dest_path in (".", "") else f"{dest_path.rstrip('/')}/{rel}"
            out_path = sanitize_write_relpath(out_raw)
            scope_err = write_scope_rejection(context, out_path)
            if scope_err:
                return _fail(
                    f"{scope_err}（已写出 {len(written)} 个文件后停下）",
                    start,
                    products=_extracted_products(written),
                    cross_turn_retry=CrossTurnRetry.FUTILE,
                )
            try:
                n = await backend.write_bytes(out_path, content)
            except OutsideWorkspace as e:
                return _fail(
                    _outside_workspace_msg(out_path, location=location, reason=str(e))
                    + f"（已写出 {len(written)} 个文件后停下）",
                    start,
                    products=_extracted_products(written),
                    cross_turn_retry=CrossTurnRetry.FUTILE,
                )
            except WorkspaceIOError as e:
                return _fail(
                    f"写入 `{out_path}` 失败：{e}（已写出 {len(written)} 个文件后停下）",
                    start,
                    products=_extracted_products(written),
                )
            written.append(out_path)
            total_bytes += int(n)

        logger.info(
            "archive_extract.done",
            archive=archive_path,
            dest=dest_path,
            files=len(written),
            bytes=total_bytes,
            run_id=context.run_id,
        )

        preview = written[:20]
        more = len(written) - len(preview)
        lines = [
            f"已解压 `{archive_path}` → `{dest_path}`：{len(written)} 个文件，"
            f"共 {total_bytes} 字节。",
            f"限额：最多 {_EXTRACT_MAX_FILES} 个文件 / "
            f"解压后 {_EXTRACT_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MiB。",
            "写出路径：",
            *[f"  - {p}" for p in preview],
        ]
        if more > 0:
            lines.append(f"  … 另有 {more} 个文件未列出")
        if len(written) > _PRODUCT_REPORT_MAX:
            lines.append(
                f"注意：文件数超过 {_PRODUCT_REPORT_MAX}，交付物台账只逐条登记前 "
                f"{_PRODUCT_REPORT_MAX} 个路径；其余文件同样已落盘，"
                f"请按目录 `{dest_path}` 整体交付，勿逐个点名。"
            )
        if dest_note:
            lines.append(dest_note)
        lines.append("【验真】请以本回执路径确认落盘；可用 file_list 抽查目标目录。")

        return ToolResult(
            tool_call_id="",
            success=True,
            output="\n".join(lines),
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={
                "archive": archive_path,
                "dest": dest_path,
                "files_written": len(written),
                "bytes_written": total_bytes,
                "paths": written,
            },
            file_products=_extracted_products(written),
        )


def _fail(
    error: str,
    start: float,
    *,
    contract_failure: bool = False,
    products: list[FileProduct] | None = None,
    cross_turn_retry: CrossTurnRetry | None = None,
) -> ToolResult:
    """Failed extract. ``products`` = 中途停下前已真正落盘的成员（部分成功不抹账）。"""
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
