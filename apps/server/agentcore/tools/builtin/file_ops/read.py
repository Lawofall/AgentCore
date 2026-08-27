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
    _file_read_same_window_hit,
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

    Untruncated full file → ``（全文 N 行）``. Truncated → ``（第 a–b 行，共 N 行）``
    and, when the safety cap stopped us, mark 行顶 or 字符顶.
    """
    body = _format_numbered_lines(lines, start_line) if lines else ""
    full = cap_kind is None and start_line == 1 and end_line == total_lines
    if full:
        footer = f"（全文 {total_lines} 行）"
    else:
        footer = f"（第 {start_line}–{end_line} 行，共 {total_lines} 行）"
        if cap_kind == "line":
            footer = f"（第 {start_line}–{end_line} 行，共 {total_lines} 行；已达行顶）"
        elif cap_kind == "char":
            footer = f"（第 {start_line}–{end_line} 行，共 {total_lines} 行；已达字符顶）"
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
            "请用 code_execute（如 openpyxl / pandas）按工作区相对路径解析。"
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


def _is_ceiling_counted_read(
    offset: object, limit: object, *, start_page: int = 1
) -> bool:
    """From line 1 filling the safety cap — counts unless tool_clear recovery.

    双省 / 只传 offset=1 / 只传 limit=行顶 (and offset=1+limit≥行顶) are this
    shape. ``_file_read_should_count`` skips the increment when the path is
    fully cleared. Point windows count only when the requested span was
    already delivered and the path body is still in the projection window.
    PDF ``start_page`` > 1 is pagination and never counts as a fill-cap read.
    """
    if start_page > 1:
        return False
    if _effective_offset(offset) > 1:
        return False
    if limit is None:
        return True
    return _as_int(limit) >= FILE_READ_SAFETY_LINE_CAP


def _file_read_body_present(context: ToolContext, path_key: str) -> bool:
    """``None`` verbatim set = projection not synced → treat body as still present."""
    verbatim = context.file_read_verbatim_paths
    return verbatim is None or path_key in verbatim


def _file_read_cleared_recovery(context: ToolContext, path_key: str) -> bool:
    """True when tool_clear recorded this path as fully cleared (stub, no verbatim).

    ``file_read_cleared_paths is None`` = projection not synced → no recovery
    exemption (same as treating the body as still present for the ceiling).
    """
    cleared = context.file_read_cleared_paths
    if cleared is None or path_key not in cleared:
        return False
    return not _file_read_body_present(context, path_key)


def _merge_line_range(
    ranges: list[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    """Union ``[start, end]`` into sorted inclusive ranges (adjacent merge)."""
    if end < start:
        return list(ranges)
    merged: list[tuple[int, int]] = []
    pending_start, pending_end = start, end
    placed = False
    for a, b in ranges:
        if b < pending_start - 1:
            merged.append((a, b))
            continue
        if pending_end < a - 1:
            if not placed:
                merged.append((pending_start, pending_end))
                placed = True
            merged.append((a, b))
            continue
        pending_start = min(pending_start, a)
        pending_end = max(pending_end, b)
    if not placed:
        merged.append((pending_start, pending_end))
    return merged


def _line_range_covered(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    if end < start:
        return False
    return any(a <= start and end <= b for a, b in ranges)


def _request_range_already_delivered(
    context: ToolContext, path_key: str, offset: object, limit: object
) -> bool:
    """True when the requested span (clipped to last-seen EOF) is already delivered."""
    ranges = context.file_read_delivered_ranges.get(path_key)
    if not ranges:
        return False
    start = _effective_offset(offset)
    end = start + _effective_line_limit(limit) - 1
    total = context.file_read_line_totals.get(path_key)
    if total is not None:
        if start > total:
            return False
        end = min(end, total)
    return _line_range_covered(ranges, start, end)


def _file_read_should_count(
    context: ToolContext,
    path_key: str,
    offset: object,
    limit: object,
    *,
    start_page: int = 1,
) -> bool:
    """Whether this successful read increments ``file_read_counts``.

    tool_clear recovery (path fully cleared in the projection) never counts.
    Fill-cap whole reads otherwise count. A point window counts only when the
    requested line range was already delivered *and* the path body is still in
    the projection window. A new range (pagination, including PDF start_page)
    never counts.
    """
    if _file_read_cleared_recovery(context, path_key):
        return False
    if _is_ceiling_counted_read(offset, limit, start_page=start_page):
        return True
    return _file_read_body_present(
        context, path_key
    ) and _request_range_already_delivered(context, path_key, offset, limit)


def _record_file_read_delivery(
    context: ToolContext,
    path_key: str,
    start_line: int,
    end_line: int,
    total_lines: int,
) -> None:
    if total_lines > 0:
        context.file_read_line_totals[path_key] = int(total_lines)
    if end_line < start_line:
        return
    prev = context.file_read_delivered_ranges.get(path_key) or []
    context.file_read_delivered_ranges[path_key] = _merge_line_range(
        prev, start_line, end_line
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


def _note_file_read_success(
    context: ToolContext,
    path_key: str,
    output: str,
    *,
    using_reread: bool,
) -> str:
    """Bump ``file_read_counts`` for a counted read; consume grant; tip.

    Counted = fill-cap whole read, or a window whose requested span was already
    delivered while the path body is still in the projection window.
    """
    from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX

    context.file_read_counts[path_key] = int(context.file_read_counts.get(path_key, 0)) + 1
    if using_reread:
        remaining = int(context.file_read_reread_remaining.get(path_key, 0))
        context.file_read_reread_remaining[path_key] = max(0, remaining - 1)
        if context.file_read_reread_remaining[path_key] <= 0:
            output += (
                f"\n\n[系统提示] `{path_key}` 的再读授额已用尽；"
                "请依据本次正文推进；若正文仍在对话中请勿空转再读，"
                "正文已被清理时可再读，或落盘 / 换其它文件。"
            )
        return output
    if context.file_read_counts[path_key] >= FILE_READ_SAME_PATH_MAX:
        output += (
            f"\n\n[系统提示] 本 run 对 `{path_key}` 的 file_read 已达上限 "
            f"（{FILE_READ_SAME_PATH_MAX} 次）；请求的行范围已在对话正文中，"
            "请依据已有正文推进或落盘，勿再读。"
            "仅当该正文已被清理、对话中不再有全文时才可再读。"
        )
    return output


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
                "仅工作区相对路径。http(s) 公网 URL 用 read_url，不要把网页地址当 path。"
                "读取工作区内某个文件的内容（相对路径）。"
                "Office/PDF（docx/pdf/pptx/odt/rtf）自动抽取文本；表格（xlsx/csv 等）"
                "默认不抽文本（本回合若有 code_execute 用它按路径解析；"
                "本 run 刚落盘的表格可回读自检）。"
                "定位请用 grep / code_search；单文件默认整读"
                "（省略则尽量整读，超安全顶截断）。"
                "仅当页脚已标明截断或已有行号时再用 offset/limit 开窗。"
                "PDF 每窗约 40 页；后面的页用 start_page 再读（offset/limit 仍是抽出文本的行号）。"
                "禁止无目标地整目录逐文件通读。"
                "看源码正文用本工具，勿改走 code_execute print/dump。"
                "含糊「根」/ `.` / 仅根标签勿当文件整读——先 glob/grep 钉真实路径。"
                "回执为编号行；未截断页脚「全文 N 行」，截断为「第 a–b 行，共 N 行」"
                "并标明行顶或字符顶（视图截断非磁盘残缺，勿把页脚当正文去 str_replace）。"
                "同一相对路径本 run 对成功 file_read 有次数上限（从第 1 行要满安全顶的整读计次；"
                "开窗仅当本次请求行范围此前已交付且正文仍在对话中时计次；新范围分页不计）。"
                "触顶且正文仍在对话中、又无再读授额时不灌全文，只回短指针；其它文件仍可 file_read。"
                "正文已被清理时可再读且不计次；写成功后可再读核对。"
                "已落盘产物优先以写/append 回执中的 artifact manifest 验真。"
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
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-based，含）。省略则从第 1 行开始。",
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

        # Same-path ceiling: fill-cap whole reads always count unless tool_clear
        # recorded the path as fully cleared (recovery does not consume quota).
        # A point window counts only when its requested span was already
        # delivered AND the path body is still in the projected window. New
        # ranges (pagination) skip the gate. At cap + body present + no grant
        # → cheap pointer (success, no full dump). Cleared body → recovery.
        from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX
        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        rel_path, _shell_note = await rewrite_project_shell_relpath(
            rel_path, context, register=False
        )
        path_key = (rel_path or "").strip().replace("\\", "/")
        ext = extension_of(path_key or rel_path)
        pdf_start = _effective_start_page(start_page_arg)
        should_count = bool(path_key) and _file_read_should_count(
            context, path_key, offset, limit, start_page=pdf_start
        )
        using_reread = False
        if should_count:
            prior = int(context.file_read_counts.get(path_key, 0))
            if prior >= FILE_READ_SAME_PATH_MAX:
                remaining = int(context.file_read_reread_remaining.get(path_key, 0))
                if remaining > 0:
                    # Grant overrides even when stale verbatim is still present.
                    using_reread = True
                elif _file_read_body_present(context, path_key):
                    return _file_read_same_window_hit(
                        path_key,
                        max_reads=FILE_READ_SAME_PATH_MAX,
                        start=start,
                    )
                # Cleared: allow recovery read (no grant required).

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
                should_count=should_count,
                using_reread=using_reread,
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
                    should_count=should_count,
                    using_reread=using_reread,
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
                should_count=should_count,
                using_reread=using_reread,
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
        if path_key:
            _record_file_read_delivery(
                context, path_key, start_line, end_line, result.total_lines
            )
            if should_count:
                output = _note_file_read_success(
                    context, path_key, output, using_reread=using_reread
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
        should_count: bool,
        using_reread: bool,
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
        output, start_line, end_line, total = _format_extracted_read(
            text, offset=offset, limit=limit
        )
        output = _append_pdf_page_footer(
            output,
            page_start=page_start,
            page_end=page_end,
            page_total=page_total,
        )
        if path_key:
            _record_file_read_delivery(context, path_key, start_line, end_line, total)
            if should_count:
                output = _note_file_read_success(
                    context, path_key, output, using_reread=using_reread
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
        should_count: bool,
        using_reread: bool,
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
                should_count=should_count,
                using_reread=using_reread,
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
            output, start_line, end_line, total = _format_extracted_read(
                decoded, offset=offset, limit=limit
            )
            if path_key:
                _record_file_read_delivery(
                    context, path_key, start_line, end_line, total
                )
                if should_count:
                    output = _note_file_read_success(
                        context, path_key, output, using_reread=using_reread
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
