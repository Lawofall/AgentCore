"""file_read / file_list (one-layer LS) tools."""

from __future__ import annotations

import json
import time
from typing import Any

from agentcore.core.types import PermissionAxes, ToolApproval, ToolCategory
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
    parsed_copy_path,
)
from agentcore.workspace.file_kind import (
    OLE_WORD_EXTENSIONS,
    decode_text_bytes,
    parse_too_large_size,
    sniff_bytes,
)
from agentcore.workspace.limits import (
    FILE_TOO_LARGE_DETAIL,
    WORKSPACE_READ_MAX_BYTES,
    is_file_too_large_detail,
)
from agentcore.workspace.protocol import (
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    ReadHeadResult,
    WorkspaceError,
    WorkspaceIOError,
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
    ls_leftover_error,
    map_listing_failure,
    visible_list_entries,
)
from .observe import (
    binary_next,
    extract_failed_next,
    extract_failed_text,
    format_observe_envelope,
    ole_next,
    scan_next,
    source_too_large_next,
    table_next,
)
from .path_hints import enrich_missing_path_message

# Safety cap for one file_read view (disk original text). Distinct from
# tool_clear ``min_chars`` and worker token ceilings — do not reuse those.
FILE_READ_SAFETY_LINE_CAP = 2000
FILE_READ_SAFETY_CHAR_CAP = 80_000
# Alias: folder_fs tests patch this name; value tracks the line cap.
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
    ``已达字符顶``. Requested window that has not hit a cap → ``未达安全顶，省略
    limit 可整读`` (not a disk truncation).
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
        footer = (
            f"（第 {start_line}–{end_line} 行，共 {total_lines} 行；"
            "未达安全顶，省略 limit 可整读）"
        )
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


def _permission_axes_of(context: ToolContext) -> PermissionAxes | None:
    """Parse ``ToolContext.permission_axes`` JSON; ``None`` if absent / unusable."""
    raw = getattr(context, "permission_axes", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw) if raw.lstrip().startswith("{") else None
        if isinstance(data, dict):
            return PermissionAxes.from_mapping(data)
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
    """Reject-table copy that follows the assembled tool table."""
    if code_execute_assembled:
        return (
            f"`{path}` 是表格/分隔数据文件，file_read 不自动抽文本；"
            "请用 run（如 openpyxl / pandas）按工作区相对路径解析。"
        )
    return (
        f"`{path}` 是表格/分隔数据文件，file_read 不自动抽文本。"
        "本回合没有按单元格解析表格的执行工具；"
        "请用已给的结构面写原件结构报告并落盘待跑变换脚本，不要手抄数据冒充已整理的表。"
    )


def _effective_offset(offset: object) -> int:
    return 1 if offset is None else _as_int(offset)


def _effective_line_limit(limit: object) -> int:
    if limit is None:
        return FILE_READ_SAFETY_LINE_CAP
    return max(1, min(_as_int(limit), FILE_READ_SAFETY_LINE_CAP))


def _effective_start_page(start_page: object) -> int:
    if start_page is None:
        return 1
    return max(1, _as_int(start_page))


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


def _file_read_ok(output: str, start: float) -> ToolResult:
    """Successful file_read result; ``output_limit`` covers full view (no 4k head+tail)."""
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
        output_limit=max(len(output), ToolResult._MAX_OUTPUT_LEN),
    )


async def _backend_read_bytes(
    backend: object, path: str, *, max_bytes: int | None = None
) -> bytes:
    """Call ``read_bytes``; tolerate fakes that omit the ``max_bytes`` keyword."""
    read = getattr(backend, "read_bytes", None)
    if not callable(read):
        raise WorkspaceError("read_bytes unavailable")
    try:
        if max_bytes is None:
            return await read(path)
        return await read(path, max_bytes=max_bytes)
    except TypeError:
        return await read(path)


async def _extract_office(
    backend: object, rel_path: str, *, ext: str, start_page: int = 1
):
    """Office/PDF extract: on-disk backends open the file in the child, not here."""
    extract = getattr(backend, "extract_office", None)
    if not callable(extract):
        raise WorkspaceError("extract_office unavailable")
    return await extract(rel_path, ext=ext, start_page=start_page)


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


def _sidecar_is_scan_notice(text: str) -> bool:
    """True when the ``*.md`` sidecar is the scan-notice body, not extracted prose."""
    body = (text or "").strip()
    return bool(body) and body == SCAN_NOTICE.strip()


def _observe_ok(
    *,
    kind: str,
    path: str,
    type_label: str,
    next_actions: str,
    start: float,
    size: int | None = None,
    text: str = "",
) -> ToolResult:
    return _file_read_ok(
        format_observe_envelope(
            kind=kind,
            path=path,
            type_label=type_label,
            next_actions=next_actions,
            size=size,
            text=text,
        ),
        start,
    )


async def _file_not_found_error(
    rel_path: str,
    *,
    start: float,
    context: ToolContext,
) -> ToolResult:
    """``PathNotFound`` for file_read — landmark / root-search tip (shared path_hints)."""
    base = f"文件不存在：{rel_path}"
    return _path_missing_error(
        await enrich_missing_path_message(context, rel_path, base=base),
        start,
    )


def _append_pdf_page_footer(
    output: str,
    *,
    page_start: int | None,
    page_end: int | None,
    page_total: int | None,
) -> str:
    """Tell the model this extract was a page window, not the whole PDF."""
    if page_start is None or page_end is None:
        return output
    from agentcore.workspace.limits import OFFICE_EXTRACT_PDF_MAX_PAGES

    if page_total is not None:
        line = f"抽取第 {page_start}–{page_end} 页，共 {page_total} 页"
        if page_end < page_total:
            line += (
                f"。后面的页请用 start_page={page_end + 1} 再读"
                "（offset/limit 仍是本窗文本行号）"
            )
        return f"{output.rstrip()}\n\n{line}。"
    line = (
        f"抽取第 {page_start}–{page_end} 页"
        f"（每窗最多 {OFFICE_EXTRACT_PDF_MAX_PAGES} 页）。"
        f"若后面还有内容，用 start_page={page_end + 1} 再读"
    )
    return f"{output.rstrip()}\n\n{line}。"


def _format_extracted_read(
    text: str,
    *,
    offset: object,
    limit: object,
) -> tuple[str, int, int, int]:
    """Apply the same file_read window (offset/limit + safety caps) to extract text."""
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

class FileReadTool:
    """Read the contents of a file within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_read",
            description=(
                "读取工作区文件。http(s) 用 read_url；定位用 grep / code_search / glob。"
                "`.` 不是文件。正文用本工具，勿 dump。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "工作区相对 POSIX 文件路径（`.`=根；`/<根标签>/…` 与裸 `/`、"
                            "`\\` 视为根；其它绝对路径如 /etc、盘符拒绝）。"
                            "http(s) URL 请用 read_url。"
                            "Office/PDF 自动抽文本；表格（xlsx/csv 等）默认不抽文本。"
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "起始行号（1-based，含）。省略则从第 1 行。"
                            "仅页脚已达安全顶或已有行号时再开窗。"
                        ),
                        "minimum": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多读取行数。省略则尽量整读，超安全顶截断。",
                        "minimum": 1,
                        "maximum": FILE_READ_SAFETY_LINE_CAP,
                    },
                    "start_page": {
                        "type": "integer",
                        "description": (
                            "PDF 抽取起始页（1-based）。每窗最多约 40 页；"
                            "后面的页请提高 start_page 再读。"
                            "offset/limit 仍是本窗抽出文本的行号。其它格式忽略。"
                        ),
                        "minimum": 1,
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        rel_path = arguments.get("path", "")
        offset = arguments.get("offset")
        limit = arguments.get("limit")
        start_page_arg = arguments.get("start_page")

        if looks_like_http_url(str(rel_path or "")):
            return _url_not_workspace_path_error(str(rel_path).strip(), start)

        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        rel_path, _shell_note = await rewrite_project_shell_relpath(
            rel_path, context, register=False
        )
        path_key = (rel_path or "").strip().replace("\\", "/")
        ext = extension_of(path_key or rel_path)
        pdf_start = _effective_start_page(start_page_arg)

        if ext in SKIP_EXTENSIONS and not _is_run_landed_path(context, path_key):
            assembled = _code_execute_assembled(context)
            return _observe_ok(
                kind="table",
                path=path_key or rel_path,
                type_label=ext.lstrip(".") or "table",
                next_actions=table_next(code_execute_assembled=assembled),
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
                start_page=pdf_start,
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
            return _error(f"不是文件：{rel_path}", start)
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            if _is_undecodable_read(e) or is_file_too_large_detail(str(e)):
                return await self._observe_undecodable(
                    rel_path,
                    path_key=path_key,
                    offset=offset,
                    limit=limit,
                    start_page=pdf_start,
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
                start_page=pdf_start,
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
        start_page: int = 1,
        start: float,
        context: ToolContext,
        extract_ext: str | None = None,
    ) -> ToolResult:
        """Transparent office/PDF extract (killable worker; no default ``*.md`` write)."""
        sidecar = parsed_copy_path(rel_path.replace("\\", "/"))
        text: str | None = None
        page_start: int | None = None
        page_end: int | None = None
        page_total: int | None = None
        use_sidecar = start_page <= 1

        if use_sidecar:
            try:
                sidecar_text = await context.backend.read(sidecar)
                if (sidecar_text or "").strip():
                    if _sidecar_is_scan_notice(sidecar_text):
                        return _observe_ok(
                            kind="scan",
                            path=path_key or rel_path,
                            type_label="pdf-scan",
                            next_actions=scan_next(),
                            start=start,
                            text=SCAN_NOTICE,
                        )
                    text = sidecar_text
            except PathNotFound:
                pass
            except OutsideWorkspace as e:
                return _outside_workspace_error(
                    rel_path, start, location=context.backend.location, reason=str(e)
                )
            except NotAFile:
                pass
            except WorkspaceError:
                pass

        if text is None:
            try:
                extracted = await _extract_office(
                    context.backend,
                    rel_path,
                    ext=extract_ext or extension_of(path_key or rel_path),
                    start_page=start_page,
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
                return _error(f"不是文件：{rel_path}", start)
            except WorkspaceError as e:
                if is_file_too_large_detail(str(e)):
                    return _observe_ok(
                        kind="truncated",
                        path=path_key or rel_path,
                        type_label="office-source",
                        next_actions=source_too_large_next(),
                        start=start,
                        size=parse_too_large_size(str(e)),
                        text="源文件超过抽取摄入顶。",
                    )
                return _map_workspace_read_error(
                    e, path=path_key or rel_path, start=start
                )

            display = path_key or rel_path
            size = extracted.size_bytes
            if extracted.status == ParseStatus.FAILED:
                return _observe_ok(
                    kind="extract",
                    path=display,
                    type_label=extension_of(display).lstrip(".") or "office",
                    next_actions=extract_failed_next(),
                    start=start,
                    size=size,
                    text=extract_failed_text(extracted.detail or ""),
                )
            if extracted.status == ParseStatus.SKIPPED:
                return _observe_ok(
                    kind="extract",
                    path=display,
                    type_label=extension_of(display).lstrip(".") or "office",
                    next_actions=extract_failed_next(),
                    start=start,
                    size=size,
                    text=extract_failed_text(extracted.detail or ""),
                )
            if extracted.status == ParseStatus.SCANNED:
                return _observe_ok(
                    kind="scan",
                    path=display,
                    type_label="pdf-scan",
                    next_actions=scan_next(),
                    start=start,
                    size=size,
                    text=extracted.text or SCAN_NOTICE,
                )
            if extracted.detail == "pdf_page_window_empty":
                extra = (
                    f"文档共 {extracted.page_total} 页。"
                    if extracted.page_total is not None
                    else ""
                )
                output = f"第 {extracted.page_start} 页起没有更多文本。{extra}"
                return _file_read_ok(output, start)
            text = extracted.text
            page_start = extracted.page_start
            page_end = extracted.page_end
            page_total = extracted.page_total

        assert text is not None
        output, *_ = _format_extracted_read(
            text, offset=offset, limit=limit
        )
        output = _append_pdf_page_footer(
            output,
            page_start=page_start,
            page_end=page_end,
            page_total=page_total,
        )
        return _file_read_ok(output, start)

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
            return _error(f"不是文件：{rel_path}", start)
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
            next_actions=ole_next(),
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
        start_page: int = 1,
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
            return _error(f"不是文件：{rel_path}", start)
        except WorkspaceError as e:
            return _map_workspace_read_error(e, path=display, start=start)

        kind = sniff_bytes(head.data)
        if kind == "pdf":
            return await self._read_office_or_pdf(
                rel_path,
                path_key=path_key,
                offset=offset,
                limit=limit,
                start_page=start_page,
                start=start,
                context=context,
                extract_ext=".pdf",
            )
        if kind == "ole":
            return _observe_ok(
                kind="binary",
                path=display,
                type_label="ole",
                next_actions=ole_next(),
                start=start,
                size=head.size_bytes,
                text="OLE 复合文档，本工具不能抽正文。",
            )
        if kind == "binary":
            return _observe_ok(
                kind="binary",
                path=display,
                type_label=kind,
                next_actions=binary_next(),
                start=start,
                size=head.size_bytes,
                text="无法按文本解码。",
            )
        if head.size_bytes > WORKSPACE_READ_MAX_BYTES:
            return _map_workspace_read_error(
                WorkspaceIOError(
                    f"{FILE_TOO_LARGE_DETAIL}（{head.size_bytes}字节）"
                ),
                path=display,
                start=start,
            )

        try:
            data = await _backend_read_bytes(
                context.backend,
                rel_path,
                max_bytes=WORKSPACE_READ_MAX_BYTES,
            )
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            return await _file_not_found_error(rel_path, start=start, context=context)
        except NotAFile:
            return _error(f"不是文件：{rel_path}", start)
        except WorkspaceError as e:
            return _map_workspace_read_error(e, path=display, start=start)

        decoded = decode_text_bytes(data)
        if decoded is not None:
            output, *_ = _format_extracted_read(
                decoded, offset=offset, limit=limit
            )
            return _file_read_ok(output, start)
        return _observe_ok(
            kind="binary",
            path=display,
            type_label=kind,
            next_actions=binary_next(),
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
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_list",
            description=(
                "列出一个已知目录的当前层（默认工作区根）。"
                "按文件名在整棵树上查找请用 glob。"
                "区外目录须 `external/<别名>/`（勿传裸 `external`）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": (
                            "工作区相对 POSIX 目录（默认 `.`=整仓根；`/<根标签>/…` 与裸 `/`、"
                            "`\\` 视为根；区外授权目录用 `external/<别名>/`，禁止裸 `external`；"
                            "其它绝对路径拒绝。）"
                            "只填本回合已证实存在的目录。"
                        ),
                        "default": ".",
                    },
                },
                "required": [],
            },
            category=ToolCategory.FILESYSTEM,
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

        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        directory, _shell_note = await rewrite_project_shell_relpath(
            directory, context, register=False
        )
        if not directory:
            directory = "."

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
