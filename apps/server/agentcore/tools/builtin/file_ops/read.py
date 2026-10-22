"""read / file_list (one-layer LS) tools."""

from __future__ import annotations

import json
import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolFace, WorkspaceBoundary
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.attachment_parse import (
    MARKITDOWN_EXTENSIONS,
    SCAN_NOTICE,
    SKIP_EXTENSIONS,
    ParseStatus,
    extension_of,
)
from agentcore.workspace.file_kind import (
    OLE_WORD_EXTENSIONS,
    is_container_archive,
    parse_too_large_size,
    sniff_bytes,
)
from agentcore.workspace.limits import (
    OFFICE_EXTRACT_PDF_MAX_PAGES,
    is_file_too_large_detail,
)
from agentcore.workspace.pdf_pages import PdfPagesError, format_pages_example, parse_pdf_pages
from agentcore.workspace.protocol import (
    NotADirectory,
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    ReadHeadResult,
    WorkspaceError,
)

from .errors import (
    _error,
    _map_workspace_read_error,
    _maybe_channel_dead_error,
    _outside_workspace_error,
    _path_missing_error,
    _url_not_workspace_path_error,
    looks_like_http_url,
)
from .listing import (
    LIST_TRUNCATED_NOTE,
    bare_external_error,
    empty_list_message,
    format_ls_lines,
    is_bare_external_directory,
    leftover_fields,
    list_archive_as_directory,
    ls_leftover_error,
    map_listing_failure,
    visible_list_entries,
)
from .observe import extract_failed_text, format_observe_envelope, scan_note
from .path_hints import enrich_missing_path_message
from .prepare_path import TOOL_PATH_DESCRIPTION

# Safety cap for one read view (disk original text). Distinct from
# retired sliding-clear ``min_chars`` and worker token ceilings — do not reuse those.
FILE_READ_SAFETY_LINE_CAP = 2000
FILE_READ_SAFETY_CHAR_CAP = 80_000
_DEFAULT_READ_LINES = FILE_READ_SAFETY_LINE_CAP

def _format_numbered_lines(lines: list[str], start_line: int) -> str:
    return "\n".join(
        f"{lineno:>6}|{text}"
        for lineno, text in zip(
            range(start_line, start_line + len(lines)), lines, strict=True
        )
    )


def _format_line_window(
    lines: list[str],
    *,
    start_line: int,
    end_line: int,
    total_lines: int,
    cap_kind: str | None = None,
) -> str:
    """Numbered lines + honest footer. No transport elision in body text.

    Untruncated full file → ``（全文 N 行）``. Safety-cap stop → ``已达行顶`` /
    ``已达字符顶``. Requested window that has not hit a cap → ``未达安全顶``.
    Never claim omitting limit will return the whole file: omit still stops at
    the safety cap.
    """
    body = _format_numbered_lines(lines, start_line) if lines else ""
    full = cap_kind is None and start_line == 1 and end_line == total_lines
    if full:
        footer = f"（全文 {total_lines} 行）"
    elif cap_kind == "line":
        footer = f"（第 {start_line}–{end_line} 行，共 {total_lines} 行；已达行顶）"
    elif cap_kind == "char":
        footer = f"（第 {start_line}–{end_line} 行，共 {total_lines} 行；已达字符顶）"
    else:
        footer = f"（第 {start_line}–{end_line} 行，共 {total_lines} 行；未达安全顶）"
    return body + "\n\n" + footer if body else footer


def _as_int(value: object) -> int:
    """Coerce a tool-arg number. Raises ``TypeError`` / ``ValueError`` like ``int``."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"expected int, got {type(value).__name__}")


def _permission_axes_of(context: ToolContext) -> WorkspaceBoundary | None:
    """Parse ``ToolContext.permission_axes`` JSON; ``None`` if absent / unusable."""
    raw = getattr(context, "permission_axes", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw) if raw.lstrip().startswith("{") else None
        if isinstance(data, dict):
            return WorkspaceBoundary.from_mapping(data)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def _code_execute_assembled(context: ToolContext) -> bool:
    """Same include predicate as this-turn worker registry (not a second truth)."""
    from agentcore.tools.builtin import execution_class_enabled_for

    return execution_class_enabled_for(context.backend, _permission_axes_of(context))


def _is_run_landed_path(context: ToolContext, path_key: str) -> bool:
    """True when this execution's write ledger already recorded ``path_key``.

    Ledger membership — not a content heuristic about whether the file 'looks'
    self-produced.
    """
    if not path_key:
        return False
    kinds = getattr(context, "landed_artifact_kinds", None) or {}
    return path_key in kinds


def _spreadsheet_skip_error(path: str, *, code_execute_assembled: bool) -> str:
    """Fact: this read did not extract the grid. No next-tool recipe."""
    base = f"`{path}` 是表格/分隔数据文件，read 不抽表格全文。"
    if code_execute_assembled:
        return base
    return base + "本回合没有按单元格解析表格的执行工具。"


def _observe_text(*parts: str) -> str:
    return "\n".join(p.strip() for p in parts if (p or "").strip())


def _effective_offset(offset: object) -> int:
    return 1 if offset is None else _as_int(offset)


def _effective_line_limit(limit: object) -> int:
    if limit is None:
        return FILE_READ_SAFETY_LINE_CAP
    return max(1, min(_as_int(limit), FILE_READ_SAFETY_LINE_CAP))


READ_REMOVED_FIELDS: tuple[str, ...] = ("start_page",)
_READ_START_PAGE_LEFTOVER_MSG = (
    "不要传 start_page。PDF 翻页用 pages（如 \"41-80\"）。"
    "offset/limit 仍是抽出文本的行号。"
)
_READ_PAGES_INVALID_MSG = (
    "pages 须为页码或闭区间，如 \"1-40\" 或 \"12\"。"
    f"一次最多 {OFFICE_EXTRACT_PDF_MAX_PAGES} 页。"
)
_READ_PAGES_SPAN_MSG = (
    f"pages 一次最多 {OFFICE_EXTRACT_PDF_MAX_PAGES} 页。"
    f'请缩小区间，如 "{format_pages_example()}"。'
)


def read_leftover_error(arguments: dict[str, Any], start: float) -> ToolResult | None:
    fields = leftover_fields(arguments, READ_REMOVED_FIELDS)
    if not fields:
        return None
    return _error(_READ_START_PAGE_LEFTOVER_MSG, start, contract_failure=True)


def _pages_arg_error(exc: PdfPagesError, start: float) -> ToolResult:
    msg = _READ_PAGES_SPAN_MSG if exc.code == "pages_span" else _READ_PAGES_INVALID_MSG
    return _error(msg, start, contract_failure=True)


def _pages_required_error(page_total: int, start: float) -> ToolResult:
    example = format_pages_example()
    return _error(
        f"这份 PDF 共 {page_total} 页。read 一次最多 {OFFICE_EXTRACT_PDF_MAX_PAGES} 页，"
        f'请带 pages（如 "{example}"）。offset/limit 仍是抽出文本的行号。',
        start,
        contract_failure=True,
    )


def _trim_to_char_cap(lines: list[str]) -> tuple[list[str], bool]:
    """Stop at the char cap on complete lines; keep an oversized first line whole."""
    selected: list[str] = []
    chars = 0
    for line in lines:
        extra = len(line) if not selected else 1 + len(line)
        if chars + extra > FILE_READ_SAFETY_CHAR_CAP:
            if not selected:
                return [line], True
            return selected, True
        selected.append(line)
        chars += extra
    return selected, False


def _cap_kind_for_window(
    *,
    char_hit: bool,
    selected_len: int,
    start_line: int,
    total_lines: int,
    line_limit: int,
) -> str | None:
    if char_hit:
        return "char"
    end_line = start_line + selected_len - 1 if selected_len else start_line - 1
    if (
        line_limit >= FILE_READ_SAFETY_LINE_CAP
        and selected_len >= FILE_READ_SAFETY_LINE_CAP
        and end_line < total_lines
    ):
        return "line"
    return None


def _finalize_window(
    sliced: list[str],
    *,
    start_line: int,
    total_lines: int,
    line_limit: int,
) -> tuple[list[str], int, int, str | None]:
    if not sliced:
        return [], start_line, start_line - 1, None
    selected, char_hit = _trim_to_char_cap(sliced)
    end_line = start_line + len(selected) - 1
    cap_kind = _cap_kind_for_window(
        char_hit=char_hit,
        selected_len=len(selected),
        start_line=start_line,
        total_lines=total_lines,
        line_limit=line_limit,
    )
    return selected, start_line, end_line, cap_kind


def _select_line_window(
    all_lines: list[str],
    *,
    offset: object,
    limit: object,
) -> tuple[list[str], int, int, int, str | None]:
    """Apply offset/limit + safety caps to in-memory lines (Office extract)."""
    total = len(all_lines)
    start = _effective_offset(offset)
    line_limit = _effective_line_limit(limit)
    start_idx = max(0, start - 1)
    if start_idx >= total:
        return [], start, start - 1, total, None
    sliced = all_lines[start_idx : start_idx + line_limit]
    selected, start_line, end_line, cap_kind = _finalize_window(
        sliced,
        start_line=start_idx + 1,
        total_lines=total,
        line_limit=line_limit,
    )
    return selected, start_line, end_line, total, cap_kind


def _file_read_ok(
    output: str, start: float, *, metadata: dict[str, Any] | None = None
) -> ToolResult:
    """Successful read result; ``output_limit`` covers full view (no 4k head+tail)."""
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
        output_limit=max(len(output), ToolResult._MAX_OUTPUT_LEN),
        metadata=metadata or {},
    )


async def _extract_office(
    backend: object,
    rel_path: str,
    *,
    ext: str,
    start_page: int = 1,
    page_end: int | None = None,
    page_total: int | None = None,
):
    """Office/PDF extract: on-disk backends open the file in the child, not here."""
    extract = getattr(backend, "extract_office", None)
    if not callable(extract):
        raise WorkspaceError("extract_office unavailable")
    return await extract(
        rel_path,
        ext=ext,
        start_page=start_page,
        page_end=page_end,
        page_total=page_total,
    )


async def _backend_read_head(
    backend: object, path: str, *, max_bytes: int | None = None
) -> ReadHeadResult:
    """Peek first bytes + total size. Not a whole-file ingest."""
    read_head = getattr(backend, "read_head", None)
    if not callable(read_head):
        raise WorkspaceError("read_head unavailable")
    return await read_head(path, max_bytes=max_bytes)


def _is_undecodable_read(exc: BaseException) -> bool:
    """True when the backend failed because the file is not UTF-8 text."""
    text = str(exc).lower()
    return (
        "codec" in text
        or "utf-8" in text
        or "decode" in text
        or "unicode" in text
        or "二进制" in text
        or "not utf" in text
    )


def _observe_ok(
    *,
    kind: str,
    path: str,
    type_label: str,
    start: float,
    size: int | None = None,
    text: str = "",
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    return _file_read_ok(
        format_observe_envelope(
            kind=kind,
            path=path,
            type_label=type_label,
            size=size,
            text=text,
        ),
        start,
        metadata=metadata,
    )


async def _file_not_found_error(
    rel_path: str,
    *,
    start: float,
    context: ToolContext,
) -> ToolResult:
    """``PathNotFound`` for read — landmark / root-search tip (shared path_hints)."""
    base = f"文件不存在：{rel_path}"
    return _path_missing_error(
        await enrich_missing_path_message(context, rel_path, base=base),
        start,
        path=rel_path,
    )


def _not_a_file_error(rel_path: str, start: float) -> ToolResult:
    """``NotAFile``: path is a directory (or other non-file). Next tool: file_list."""
    return _error(
        f"不是文件（这是目录）：{rel_path}。列举请用 file_list。",
        start,
    )


def _next_pages_example(page_end: int, page_total: int | None) -> str:
    start = page_end + 1
    if page_total is not None:
        end = min(page_total, start + OFFICE_EXTRACT_PDF_MAX_PAGES - 1)
    else:
        end = start + OFFICE_EXTRACT_PDF_MAX_PAGES - 1
    return f"{start}-{end}"


def _append_pdf_page_footer(
    output: str,
    *,
    page_start: int | None,
    page_end: int | None,
    page_total: int | None,
    images_sent: int = 0,
    accepts_images: bool = False,
) -> str:
    """Page-window HOW: teach ``pages``, never ``start_page``."""
    if page_start is None or page_end is None:
        return output
    extra: list[str] = []
    if page_total is not None:
        line = f"抽取第 {page_start}–{page_end} 页，共 {page_total} 页"
        if page_end < page_total:
            example = _next_pages_example(page_end, page_total)
            line += f'。后面的页再 read 并带 pages（如 "{example}"）'
        extra.append(line)
    else:
        example = _next_pages_example(page_end, None)
        extra.append(
            f"抽取第 {page_start}–{page_end} 页（总页未知，每窗最多 "
            f"{OFFICE_EXTRACT_PDF_MAX_PAGES} 页）。"
            f'后面的页再 read 并带 pages（如 "{example}"）'
        )
    if images_sent:
        extra.append(f"本窗前 {images_sent} 页图像已发给当前模型")
    elif accepts_images:
        extra.append("当前主模型收图，但本窗页图像未能生成")
    else:
        extra.append("当前主模型不收图，本回合只有文本层")
    return f"{output.rstrip()}\n\n{'。'.join(extra)}。"


def _format_extracted_read(
    text: str,
    *,
    offset: object,
    limit: object,
) -> tuple[str, int, int, int]:
    """Apply the same read window (offset/limit + safety caps) to extract text."""
    selected, start_line, end_line, total, cap_kind = _select_line_window(
        text.splitlines(), offset=offset, limit=limit
    )
    return (
        _format_line_window(
            selected,
            start_line=start_line,
            end_line=end_line,
            total_lines=total,
            cap_kind=cap_kind,
        ),
        start_line,
        end_line,
        total,
    )


def _jpeg_native_parts(jpegs: list[bytes]) -> list[dict[str, Any]]:
    from agentcore.workspace.image_raster import jpeg_native_part

    return [jpeg_native_part(jpeg) for jpeg in jpegs]


async def _render_pdf_window_jpegs(
    backend: object, rel_path: str, *, start: int, end: int
) -> list[bytes]:
    from agentcore.workspace.pdf_raster import render_pdf_page_jpegs

    safe = getattr(backend, "_safe", None)
    if callable(safe):
        try:
            target = safe(rel_path)
            if getattr(target, "is_file", lambda: False)():
                return render_pdf_page_jpegs(path=target, start=start, end=end)
        except Exception:
            pass
    read_bytes = getattr(backend, "read_bytes", None)
    if not callable(read_bytes):
        return []
    try:
        data = await read_bytes(rel_path)
    except Exception:
        return []
    return render_pdf_page_jpegs(data=data, start=start, end=end)


class FileReadTool:
    """Read the contents of a file within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
        workspace_io=True,
        catalog_summary="读工作区文件",
        blurb="打开文本、代码或图片，看里面写了什么",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="read",
            description="读取工作区文件。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            f"{TOOL_PATH_DESCRIPTION}"
                            "Office/PDF 抽文本；PDF 可带 pages；表格默认不抽。"
                            "图片把像素发给当前模型（不收图则诚实说明）。"
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-based，含）。",
                        "minimum": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多读取行数。省略则整读至安全顶。",
                        "minimum": 1,
                        "maximum": FILE_READ_SAFETY_LINE_CAP,
                    },
                    "pages": {
                        "type": "string",
                        "description": (
                            "PDF 页窗，与 offset/limit 行号独立。"
                            f'如 "1-{OFFICE_EXTRACT_PDF_MAX_PAGES}" 或 "12"。'
                            f"一次最多 {OFFICE_EXTRACT_PDF_MAX_PAGES} 页。"
                            f"超过 {OFFICE_EXTRACT_PDF_MAX_PAGES} 页必须带 pages。"
                        ),
                    },
                },
                "required": ["file_path"],
            },
            face=ToolFace.FILE,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        result = await self._read_on_desk(arguments, context, start)
        from .named_desk_read import maybe_named_desk_file_read

        retried = await maybe_named_desk_file_read(
            result, arguments, context, start, self._read_on_desk
        )
        return retried if retried is not None else result

    async def _read_on_desk(
        self, arguments: dict[str, Any], context: ToolContext, start: float
    ) -> ToolResult:
        rel_path = arguments.get("file_path", "")
        offset = arguments.get("offset")
        limit = arguments.get("limit")
        pages = arguments.get("pages")

        if looks_like_http_url(str(rel_path or "")):
            return _url_not_workspace_path_error(str(rel_path).strip(), start)

        leftover = read_leftover_error(arguments, start)
        if leftover is not None:
            return leftover

        from .prepare_path import prepare_tool_path

        prepared = await prepare_tool_path(rel_path, context, start=start)
        if isinstance(prepared, ToolResult):
            return prepared
        rel_path = prepared

        path_key = (rel_path or "").strip().replace("\\", "/")
        ext = extension_of(path_key or rel_path)

        from agentcore.workspace.image_files import is_raster_image_path

        if is_raster_image_path(path_key or rel_path):
            from agentcore.tools.builtin.file_ops.raster import read_raster_image

            return await read_raster_image(
                rel_path, path_key=path_key, context=context, start=start
            )

        if ext in SKIP_EXTENSIONS and not _is_run_landed_path(context, path_key):
            assembled = _code_execute_assembled(context)
            return _observe_ok(
                kind="table",
                path=path_key or rel_path,
                type_label=ext.lstrip(".") or "table",
                start=start,
                text=_spreadsheet_skip_error(
                    path_key or rel_path, code_execute_assembled=assembled
                ),
            )

        if ext in OLE_WORD_EXTENSIONS:
            return await self._observe_ole(
                rel_path, path_key=path_key, start=start, context=context
            )

        if ext in MARKITDOWN_EXTENSIONS:
            return await self._read_office_or_pdf(
                rel_path,
                path_key=path_key,
                offset=offset,
                limit=limit,
                pages=pages,
                start=start,
                context=context,
            )

        # Default fills to EOF or the safety cap (line / char, complete lines).
        # Never whole-file read + silent head-only chop without footer.
        eff_offset = _effective_offset(offset)
        eff_limit = _effective_line_limit(limit)
        try:
            result = await context.backend.read_lines(
                rel_path, offset=eff_offset, limit=eff_limit
            )
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            return await _file_not_found_error(rel_path, start=start, context=context)
        except NotAFile:
            return _not_a_file_error(rel_path, start)
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            if _is_undecodable_read(e):
                return await self._observe_undecodable(
                    rel_path,
                    path_key=path_key,
                    offset=offset,
                    limit=limit,
                    pages=pages,
                    start=start,
                    context=context,
                )
            return _map_workspace_read_error(e, path=path_key or rel_path, start=start)

        selected, start_line, end_line, cap_kind = _finalize_window(
            result.lines,
            start_line=result.start_line,
            total_lines=result.total_lines,
            line_limit=eff_limit,
        )
        first = result.lines[0] if result.lines else ""
        if (
            "\x00" in first
            or first.startswith("%PDF")
            or any("\x00" in line for line in result.lines)
        ):
            return await self._observe_undecodable(
                rel_path,
                path_key=path_key,
                offset=offset,
                limit=limit,
                pages=pages,
                start=start,
                context=context,
            )
        output = _format_line_window(
            selected,
            start_line=start_line,
            end_line=end_line,
            total_lines=result.total_lines,
            cap_kind=cap_kind,
        )
        return _file_read_ok(output, start)

    async def _read_office_or_pdf(
        self,
        rel_path: str,
        *,
        path_key: str,
        offset: object,
        limit: object,
        pages: object = None,
        start: float,
        context: ToolContext,
        extract_ext: str | None = None,
    ) -> ToolResult:
        """Transparent office/PDF extract (killable worker; no sibling extract file)."""
        ext = extract_ext or extension_of(path_key or rel_path)
        if ext == ".pdf":
            return await self._read_pdf(
                rel_path,
                path_key=path_key,
                offset=offset,
                limit=limit,
                pages=pages,
                start=start,
                context=context,
            )

        extracted = await self._extract_office_or_envelope(
            rel_path,
            path_key=path_key,
            ext=ext,
            start=start,
            context=context,
        )
        if isinstance(extracted, ToolResult):
            return extracted
        display = path_key or rel_path
        size = extracted.size_bytes
        if extracted.status == ParseStatus.FAILED:
            return _observe_ok(
                kind="extract",
                path=display,
                type_label=extension_of(display).lstrip(".") or "office",
                start=start,
                size=size,
                text=extract_failed_text(extracted.detail or ""),
            )
        if extracted.status == ParseStatus.SKIPPED:
            return _observe_ok(
                kind="extract",
                path=display,
                type_label=extension_of(display).lstrip(".") or "office",
                start=start,
                size=size,
                text=extract_failed_text(extracted.detail or ""),
            )
        if extracted.status == ParseStatus.SCANNED:
            return _observe_ok(
                kind="scan",
                path=display,
                type_label="pdf-scan",
                start=start,
                size=size,
                text=extracted.text or SCAN_NOTICE,
            )
        output, *_ = _format_extracted_read(extracted.text, offset=offset, limit=limit)
        return _file_read_ok(output, start)

    async def _extract_office_or_envelope(
        self,
        rel_path: str,
        *,
        path_key: str,
        ext: str,
        start: float,
        context: ToolContext,
        start_page: int = 1,
        page_end: int | None = None,
        page_total: int | None = None,
    ):
        try:
            return await _extract_office(
                context.backend,
                rel_path,
                ext=ext,
                start_page=start_page,
                page_end=page_end,
                page_total=page_total,
            )
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            return await _file_not_found_error(
                rel_path, start=start, context=context
            )
        except NotAFile:
            return _not_a_file_error(rel_path, start)
        except WorkspaceError as e:
            if is_file_too_large_detail(str(e)):
                return _observe_ok(
                    kind="truncated",
                    path=path_key or rel_path,
                    type_label="office-source",
                    start=start,
                    size=parse_too_large_size(str(e)),
                    text="源文件超过抽取摄入顶，本工具不能整份吞入。",
                )
            return _map_workspace_read_error(
                e, path=path_key or rel_path, start=start
            )

    async def _read_pdf(
        self,
        rel_path: str,
        *,
        path_key: str,
        offset: object,
        limit: object,
        pages: object,
        start: float,
        context: ToolContext,
    ) -> ToolResult:
        try:
            page_window = parse_pdf_pages(pages)
        except PdfPagesError as exc:
            return _pages_arg_error(exc, start)

        start_page = 1 if page_window is None else page_window[0]
        page_end = None if page_window is None else page_window[1]
        extracted = await self._extract_office_or_envelope(
            rel_path,
            path_key=path_key,
            ext=".pdf",
            start=start,
            context=context,
            start_page=start_page,
            page_end=page_end,
        )
        if isinstance(extracted, ToolResult):
            return extracted

        display = path_key or rel_path
        size = extracted.size_bytes
        if (
            page_window is None
            and extracted.page_total is not None
            and extracted.page_total > OFFICE_EXTRACT_PDF_MAX_PAGES
        ):
            return _pages_required_error(extracted.page_total, start)

        accepts_images = bool(getattr(context, "accepts_images", False))
        image_start = extracted.page_start or start_page
        image_end = extracted.page_end or image_start
        jpegs: list[bytes] = []
        if accepts_images:
            jpegs = await _render_pdf_window_jpegs(
                context.backend, rel_path, start=image_start, end=image_end
            )
        image_meta = (
            {"native_image_parts": _jpeg_native_parts(jpegs)} if jpegs else None
        )

        if extracted.status == ParseStatus.FAILED:
            return _observe_ok(
                kind="extract",
                path=display,
                type_label="pdf",
                start=start,
                size=size,
                text=extract_failed_text(extracted.detail or ""),
            )
        if extracted.status == ParseStatus.SKIPPED:
            return _observe_ok(
                kind="extract",
                path=display,
                type_label="pdf",
                start=start,
                size=size,
                text=extract_failed_text(extracted.detail or ""),
            )
        if extracted.status == ParseStatus.SCANNED:
            return _observe_ok(
                kind="scan",
                path=display,
                type_label="pdf-scan",
                start=start,
                size=size,
                text=_observe_text(
                    extracted.text or SCAN_NOTICE,
                    scan_note(
                        accepts_images=accepts_images, images_sent=len(jpegs)
                    ),
                ),
                metadata=image_meta,
            )
        if extracted.detail == "pdf_page_window_empty":
            extra = (
                f"文档共 {extracted.page_total} 页。"
                if extracted.page_total is not None
                else ""
            )
            output = f"第 {extracted.page_start} 页起没有更多文本。{extra}"
            return _file_read_ok(output, start)

        output, *_ = _format_extracted_read(
            extracted.text, offset=offset, limit=limit
        )
        output = _append_pdf_page_footer(
            output,
            page_start=extracted.page_start,
            page_end=extracted.page_end,
            page_total=extracted.page_total,
            images_sent=len(jpegs),
            accepts_images=accepts_images,
        )
        return _observe_ok(
            kind="pdf",
            path=display,
            type_label="pdf",
            start=start,
            size=size,
            text=output,
            metadata=image_meta,
        )

    async def _observe_ole(
        self,
        rel_path: str,
        *,
        path_key: str,
        start: float,
        context: ToolContext,
    ) -> ToolResult:
        """Legacy Word (.doc): success envelope, never UTF-8 decode."""
        display = path_key or rel_path
        size: int | None = None
        try:
            head = await _backend_read_head(context.backend, rel_path)
            size = head.size_bytes
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            return await _file_not_found_error(rel_path, start=start, context=context)
        except NotAFile:
            return _not_a_file_error(rel_path, start)
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            if is_file_too_large_detail(str(e)):
                size = parse_too_large_size(str(e))
            else:
                return _map_workspace_read_error(e, path=display, start=start)
        return _observe_ok(
            kind="binary",
            path=display,
            type_label="ole-word",
            start=start,
            size=size,
            text="旧版 Word（OLE），本工具不能抽正文。",
        )

    async def _observe_undecodable(
        self,
        rel_path: str,
        *,
        path_key: str,
        offset: object,
        limit: object,
        pages: object = None,
        start: float,
        context: ToolContext,
    ) -> ToolResult:
        """UTF-8 / size gate failed: sniff magic via peek, then extract or envelope."""
        display = path_key or rel_path
        try:
            head = await _backend_read_head(context.backend, rel_path)
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            return await _file_not_found_error(rel_path, start=start, context=context)
        except NotAFile:
            return _not_a_file_error(rel_path, start)
        except WorkspaceError as e:
            return _map_workspace_read_error(e, path=display, start=start)

        kind = sniff_bytes(head.data)
        if is_container_archive(kind, display):
            return _observe_ok(
                kind="archive",
                path=display,
                type_label=kind,
                start=start,
                size=head.size_bytes,
                text=(
                    "这是压缩包。附件会在驻留时展开到同名目录。"
                    "包内成员名不是工作区路径。"
                ),
            )
        if kind == "pdf":
            return await self._read_office_or_pdf(
                rel_path,
                path_key=path_key,
                offset=offset,
                limit=limit,
                pages=pages,
                start=start,
                context=context,
                extract_ext=".pdf",
            )
        if kind == "ole":
            return _observe_ok(
                kind="binary",
                path=display,
                type_label="ole",
                start=start,
                size=head.size_bytes,
                text="OLE 复合文档，本工具不能抽正文。",
            )
        return _observe_ok(
            kind="binary",
            path=display,
            type_label=kind,
            start=start,
            size=head.size_bytes,
            text="无法按文本解码。",
        )


class FileListTool:
    """List the current layer of a known workspace directory (never glob)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
        workspace_io=True,
        catalog_summary="列出工作区当前层",
        blurb="看这一层有哪些文件和子目录",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_list",
            description="列出目录当前层。",
            parameters={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": TOOL_PATH_DESCRIPTION,
                        "default": ".",
                    },
                },
                "required": [],
            },
            face=ToolFace.FILE,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        leftover = ls_leftover_error(arguments, start)
        if leftover is not None:
            return leftover

        directory = str(arguments.get("directory") or ".").strip() or "."
        if is_bare_external_directory(directory):
            return bare_external_error(directory, context.backend, start)

        from .prepare_path import prepare_tool_path

        prepared = await prepare_tool_path(
            directory, context, start=start, as_directory=True
        )
        if isinstance(prepared, ToolResult):
            return prepared
        directory = prepared

        try:
            listing = await context.backend.list(directory, "*")
            entries = visible_list_entries(
                list(listing.entries),
                materials=context.material_paths,
            )
            output = (
                format_ls_lines(entries) if entries else empty_list_message(directory)
            )
            if listing.truncated:
                output += f"\n\n{LIST_TRUNCATED_NOTE}"
        except NotADirectory as e:
            packed = await list_archive_as_directory(
                directory, context=context, start=start
            )
            if packed is not None:
                return packed
            return await map_listing_failure(
                e, directory=directory, context=context, start=start
            )
        except WorkspaceError as e:
            return await map_listing_failure(
                e, directory=directory, context=context, start=start
            )

        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
