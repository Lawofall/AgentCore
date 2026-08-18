"""file_read / file_list (+ tree rendering) tools."""

from __future__ import annotations

import json
import re
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
from agentcore.workspace._paths import AI_ARCHIVE_FILE_SUFFIXES
from agentcore.workspace.attachment_parse import (
    MARKITDOWN_EXTENSIONS,
    SKIP_EXTENSIONS,
    ParseStatus,
    extension_of,
    extract_office_bytes,
    parsed_copy_path,
)
from agentcore.workspace.declared_dirs import (
    LATENT_EMPTY_LIST_MESSAGE,
    is_declared_latent_dir,
)
from agentcore.workspace.external_mounts import EXTERNAL_PREFIX, parse_external_path
from agentcore.workspace.limits import OFFICE_EXTRACT_MAX_BYTES
from agentcore.workspace.protocol import (
    DirEntry,
    NotADirectory,
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    TreeEntry,
    WorkspaceError,
)
from agentcore.workspace.sparse_listing import should_hide_ai_noise_from_list

from .errors import (
    _error,
    _file_read_path_ceiling_error,
    _file_read_path_ceiling_message,
    _map_workspace_read_error,
    _maybe_channel_dead_error,
    _office_extract_budget_error,
    _outside_workspace_error,
    _path_missing_error,
)
from .path_hints import enrich_missing_path_message

# Safety cap for one file_read view (disk original text). Distinct from
# tool_clear ``min_chars`` and worker token ceilings — do not reuse those.
FILE_READ_SAFETY_LINE_CAP = 2000
FILE_READ_SAFETY_CHAR_CAP = 80_000
# Alias: folder_fs tests patch this name; value tracks the line cap.
_DEFAULT_READ_LINES = FILE_READ_SAFETY_LINE_CAP

# Non-recursive ``file_list`` hit the backend's entry ceiling. The recursive branch
# already footers its own elision through ``_render_file_tree``; this is the flat
# branch's equivalent — a bounded listing is fine, a silent one is not.
_LIST_TRUNCATED_NOTE = (
    "（本层条目已达单次列举上限，还有更多未列出：可用 pattern 收窄，"
    "或 recursive=true 配合 max_depth 逐层查看。）"
)


def _empty_list_message(directory: str) -> str:
    """Empty ``file_list`` body — latent declared dirs get auto-create copy."""
    if is_declared_latent_dir(directory):
        return LATENT_EMPTY_LIST_MESSAGE
    return "（空目录）"


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


def _is_ceiling_counted_read(offset: object, limit: object) -> bool:
    """From line 1 filling the safety cap — counts unless tool_clear recovery.

    双省 / 只传 offset=1 / 只传 limit=行顶 (and offset=1+limit≥行顶) are this
    shape. ``_file_read_should_count`` skips the increment when the path is
    fully cleared. Point windows count only when the requested span was
    already delivered and the path body is still in the projection window.
    """
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
    context: ToolContext, path_key: str, offset: object, limit: object
) -> bool:
    """Whether this successful read increments ``file_read_counts``.

    tool_clear recovery (path fully cleared in the projection) never counts.
    Fill-cap whole reads otherwise count. A point window counts only when the
    requested line range was already delivered *and* the path body is still in
    the projection window. A new range (pagination) never counts.
    """
    if _file_read_cleared_recovery(context, path_key):
        return False
    if _is_ceiling_counted_read(offset, limit):
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


class _TreeNode:
    __slots__ = ("children", "is_dir", "name")

    def __init__(self, name: str, is_dir: bool) -> None:
        self.name = name
        self.is_dir = is_dir
        self.children: list[_TreeNode] = []


_BRACE_GLOB_RE = re.compile(r"\{([^{}]+)\}")


def expand_brace_globs(pattern: str) -> list[str]:
    """Expand one level of ``{a,b}`` alternatives (pathlib globs do not).

    ``*.{ts,tsx}`` → ``['*.ts', '*.tsx']``. Nested / empty braces are left as-is
    (single-element list). Order is stable; duplicates are dropped.
    """
    raw = (pattern or "*").strip() or "*"
    match = _BRACE_GLOB_RE.search(raw)
    if match is None:
        return [raw]
    alternatives = [part.strip() for part in match.group(1).split(",") if part.strip()]
    if not alternatives:
        return [raw]
    prefix = raw[: match.start()]
    suffix = raw[match.end() :]
    expanded: list[str] = []
    seen: set[str] = set()
    for alt in alternatives:
        item = f"{prefix}{alt}{suffix}"
        if item not in seen:
            seen.add(item)
            expanded.append(item)
    return expanded or [raw]


def _pattern_filters(pattern: str) -> bool:
    """True when ``pattern`` is narrower than「列全部」."""
    p = (pattern or "*").strip() or "*"
    return p != "*"


def _pattern_targets_archives(pattern: str) -> bool:
    """True when glob(s) end with an AI-archive suffix (``*.zip``, ``*.{rar,7z}``…)."""
    for pat in expand_brace_globs(pattern):
        lower = (pat or "").lower().rstrip("/")
        if any(lower.endswith(suf) for suf in AI_ARCHIVE_FILE_SUFFIXES):
            return True
    return False


def _is_bare_external_directory(directory: str) -> bool:
    """True for ``external`` / ``external/`` (no alias) — not a listable mount path."""
    raw = (directory or "").strip().replace("\\", "/").strip("/")
    return raw == EXTERNAL_PREFIX.rstrip("/")


def _looks_like_external_directory(directory: str) -> bool:
    """Bare ``external`` or any ``external/<alias>/…`` shape (even unknown alias)."""
    raw = (directory or "").strip().replace("\\", "/").strip("/")
    if raw == EXTERNAL_PREFIX.rstrip("/") or raw.startswith(EXTERNAL_PREFIX):
        return True
    return parse_external_path(directory) is not None


def _external_directory_hint(backend: Any) -> str:
    """Actionable mounts guidance for bare / failed ``external`` list attempts."""
    guide = (
        "须使用 `external/<别名>/`（例如 `external/desktop/`）访问已授权区外目录"
    )
    mounts = getattr(backend, "_mounts", None) or {}
    if not mounts:
        return (
            f"{guide}；本对话尚无会话级区外目录授权"
            "（用户经 ask_user grant_* 确认后才会出现 mounts）。"
        )
    parts = [f"`external/{a}/`" for a in mounts]
    return f"{guide}；当前 mounts：{'；'.join(parts)}。"


def _no_match_hint(
    *,
    pattern: str,
    directory: str,
    bare_entries: list,
    recursive: bool,
) -> str:
    """Actionable message when a glob matched nothing in a non-empty directory."""
    sample_parts: list[str] = []
    for entry in bare_entries[:8]:
        sample_parts.append(f"{'d ' if entry.is_dir else 'f '}{entry.path}")
    sample = "；".join(sample_parts)
    more = (
        f" 等共 {len(bare_entries)} 项"
        if len(bare_entries) > 8
        else f"（共 {len(bare_entries)} 项）"
    )
    tips = ["去掉 pattern", "换更宽的 glob"]
    if not recursive:
        tips.insert(0, "设 recursive=true 以搜索子目录")
    tip_text = "、".join(tips)
    root = "./" if directory in (".", "") else f"{directory.rstrip('/')}/"
    return (
        f"（在 {root} 下无匹配 pattern={pattern!r} 的条目；目录非空{more}。"
        f"可见顶层示例：{sample}。可{tip_text}。）"
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


def _render_file_tree(
    entries: list[TreeEntry],
    directory: str,
    max_depth: int,
    truncated: bool,
    elided_count: int,
    *,
    empty_message: str | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Render ``list_tree`` entries as an ASCII tree (``├──`` / ``└──`` / ``│``)."""
    root_label = "./" if directory == "." else f"{directory.rstrip('/')}/"
    lines: list[str] = [root_label]

    if not entries:
        empty = empty_message or "（空目录）"
        body = f"{root_label}\n{empty}\n\n（{max_depth} 层深度，共 0 条目）"
        if warnings:
            body += "\n" + "\n".join(f"⚠ {w}" for w in warnings)
        return body

    dir_base = "" if directory == "." else directory.rstrip("/")
    root_name = "." if directory == "." else directory.rstrip("/").split("/")[-1]
    root = _TreeNode(root_name, True)

    for entry in sorted(entries, key=lambda e: e.path.lower()):
        parts = entry.path.split("/")
        if dir_base:
            base_parts = dir_base.split("/")
            if parts[: len(base_parts)] != base_parts:
                continue
            parts = parts[len(base_parts) :]
        if not parts:
            continue

        current = root
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            child = next((c for c in current.children if c.name == part), None)
            if child is None:
                child = _TreeNode(part, entry.is_dir if is_last else True)
                current.children.append(child)
            elif is_last:
                child.is_dir = entry.is_dir
            current = child

    def emit(children: list[_TreeNode], prefix: str) -> None:
        ordered = sorted(children, key=lambda n: (not n.is_dir, n.name.lower()))
        for i, child in enumerate(ordered):
            is_last = i == len(ordered) - 1
            branch = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "
            name = f"{child.name}/" if child.is_dir else child.name
            lines.append(prefix + branch + name)
            if child.children:
                emit(child.children, prefix + extension)

    emit(root.children, "")

    footer = f"\n\n（{max_depth} 层深度，共 {len(entries)} 条目"
    if truncated and elided_count:
        footer += f"；另有 {elided_count} 个条目因深度/预算未展开"
    footer += "）"
    out = "\n".join(lines) + footer
    if warnings:
        out += "\n" + "\n".join(f"⚠ {w}" for w in warnings)
    return out

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
                "读取工作区内某个文件的内容（相对路径）。"
                "Office/PDF（docx/pdf/pptx/odt/rtf）自动抽取文本；表格（xlsx/csv 等）"
                "默认不抽文本（本回合若有 code_execute 用它按路径解析；"
                "本 run 刚落盘的表格可回读自检）。"
                "定位请用 grep / code_search；单文件默认整读"
                "（省略则尽量整读，超安全顶截断）。"
                "仅当页脚已标明截断或已有行号时再用 offset/limit 开窗。"
                "禁止无目标地整目录逐文件通读。"
                "含糊「根」/ `.` / 仅根标签勿当文件整读——先 file_list/grep 钉真实路径。"
                "回执为编号行；未截断页脚「全文 N 行」，截断为「第 a–b 行，共 N 行」"
                "并标明行顶或字符顶（视图截断非磁盘残缺，勿把页脚当正文去 str_replace）。"
                "同一相对路径本 run 对成功 file_read 有次数上限（从第 1 行要满安全顶的整读计次；"
                "开窗仅当本次请求行范围此前已交付且正文仍在对话中时计次；新范围分页不计）。"
                "触顶且正文仍在对话中、又无再读授额时仅拒绝该路径，其它文件仍可 file_read。"
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
                            "`\\` 视为根；其它绝对路径如 /etc、盘符拒绝）"
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

        # Same-path ceiling: fill-cap whole reads always count unless tool_clear
        # recorded the path as fully cleared (recovery does not consume quota).
        # A point window counts only when its requested span was already
        # delivered AND the path body is still in the projected window. New
        # ranges (pagination) skip the gate. Cleared body → allow recovery
        # even with remaining == 0.
        from agentcore.runtime.runs.constants import FILE_READ_SAME_PATH_MAX
        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        rel_path, _shell_note = await rewrite_project_shell_relpath(
            rel_path, context, register=False
        )
        path_key = (rel_path or "").strip().replace("\\", "/")
        should_count = bool(path_key) and _file_read_should_count(
            context, path_key, offset, limit
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
                    return _file_read_path_ceiling_error(
                        _file_read_path_ceiling_message(
                            path_key, max_reads=FILE_READ_SAME_PATH_MAX
                        ),
                        start,
                    )
                # Cleared: allow recovery read (no grant required).

        ext = extension_of(path_key or rel_path)
        if ext in SKIP_EXTENSIONS and not _is_run_landed_path(context, path_key):
            return _error(
                _spreadsheet_skip_error(
                    path_key or rel_path,
                    code_execute_assembled=_code_execute_assembled(context),
                ),
                start,
            )

        if ext in MARKITDOWN_EXTENSIONS:
            return await self._read_office_or_pdf(
                rel_path,
                path_key=path_key,
                offset=offset,
                limit=limit,
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
            return _map_workspace_read_error(e, path=path_key or rel_path, start=start)

        selected, start_line, end_line, cap_kind = _finalize_window(
            result.lines,
            start_line=result.start_line,
            total_lines=result.total_lines,
            line_limit=eff_limit,
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
        should_count: bool,
        using_reread: bool,
        start: float,
        context: ToolContext,
    ) -> ToolResult:
        """Transparent office/PDF extract via markitdown (no default ``*.md`` write)."""
        sidecar = parsed_copy_path(rel_path.replace("\\", "/"))
        text: str | None = None

        try:
            sidecar_text = await context.backend.read(sidecar)
            if (sidecar_text or "").strip():
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
                data = await context.backend.read_bytes(rel_path)
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
                return _map_workspace_read_error(e, path=path_key or rel_path, start=start)

            if len(data) > OFFICE_EXTRACT_MAX_BYTES:
                return _office_extract_budget_error(
                    path_key or rel_path, len(data), start
                )

            extracted = await extract_office_bytes(data, ext=extension_of(path_key or rel_path))
            if extracted.status == ParseStatus.FAILED:
                return _error(
                    (
                        f"无法从 `{path_key or rel_path}` 抽取文本"
                        f"（{extracted.detail or 'convert failed'}）。"
                        "若缺 markitdown 依赖或文件损坏，请告知用户；"
                        "不要改用 code_execute 硬解 Office/PDF。"
                    ),
                    start,
                )
            if extracted.status == ParseStatus.SKIPPED:
                return _error(
                    f"`{path_key or rel_path}` 不支持透明文本抽取。",
                    start,
                )
            # OK or SCANNED — both carry honest text (scan notice is not empty success).
            text = extracted.text
            if extracted.status == ParseStatus.SCANNED and not (text or "").strip():
                return _error(
                    f"`{path_key or rel_path}` 看起来是扫描件且无可抽文本层（无 OCR）。",
                    start,
                )

        assert text is not None
        output, start_line, end_line, total = _format_extracted_read(
            text, offset=offset, limit=limit
        )
        if path_key:
            _record_file_read_delivery(context, path_key, start_line, end_line, total)
            if should_count:
                output = _note_file_read_success(
                    context, path_key, output, using_reread=using_reread
                )
        return _file_read_ok(output, start)


class FileListTool:
    """List files in a directory within the workspace."""

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
                "列出某个目录下的文件与子目录。路径必须是相对于工作区的相对路径。"
                "默认只列当前层（recursive=false）：`*.py` 不会进入子目录；"
                "要搜整棵树请设 recursive=true。支持 `{ts,tsx}` 花括号二选一。"
                "区外目录须 `external/<别名>/`（勿传裸 `external`）；"
                "大 zip 持久展开请用 archive_extract，勿假定仅靠 code_execute 解压即工作区可见。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": (
                            "工作区相对 POSIX 目录（默认 `.`=整仓；`/<根标签>/…` 与裸 `/`、"
                            "`\\` 视为根；区外授权目录用 `external/<别名>/`，禁止裸 `external`；"
                            "其它绝对路径拒绝）"
                        ),
                        "default": ".",
                    },
                    "pattern": {
                        "type": "string",
                        "description": (
                            "用于过滤结果的 glob 模式（如 '*.py'、'*.{ts,tsx}'）。"
                            "非递归时只匹配当前层文件名。"
                        ),
                        "default": "*",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "递归列出子目录（树形）。默认 false（仅当前层）。",
                        "default": False,
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "递归最大深度（仅 recursive=true 时生效）。默认 3，上限 8。",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 8,
                    },
                },
                "required": [],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        directory = arguments.get("directory", ".")
        pattern = arguments.get("pattern", "*") or "*"
        recursive = bool(arguments.get("recursive", False))
        max_depth = int(arguments.get("max_depth", 3))
        max_depth = max(1, min(max_depth, 8))
        patterns = expand_brace_globs(str(pattern))
        reveal_archives = _pattern_targets_archives(str(pattern))

        if _is_bare_external_directory(str(directory)):
            return _error(
                f"directory={directory!r} 无效：裸 `external` 不是可列目录。"
                + _external_directory_hint(context.backend),
                start,
            )

        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        directory, _shell_note = await rewrite_project_shell_relpath(
            str(directory or "."), context, register=False
        )
        if not directory:
            directory = "."

        prev_reveal = getattr(context.backend, "ai_list_reveal_archives", False)
        if reveal_archives:
            context.backend.ai_list_reveal_archives = True
        try:
            if recursive:
                merged: dict[str, TreeEntry] = {}
                truncated = False
                elided_count = 0
                soft_warnings: list[str] = []
                for pat in patterns:
                    tree = await context.backend.list_tree(
                        directory, pattern=pat, max_depth=max_depth
                    )
                    for entry in tree.entries:
                        merged[entry.path] = entry
                    truncated = truncated or tree.truncated
                    elided_count += tree.elided_count
                    soft_warnings.extend(tree.warnings)
                entries_tree = list(merged.values())
                empty_message = None
                if not entries_tree and _pattern_filters(str(pattern)):
                    bare = [
                        e
                        for e in await context.backend.list(directory, "*")
                        if e.is_dir
                        or not should_hide_ai_noise_from_list(
                            e.path,
                            materials=context.material_paths,
                            reveal_archives=reveal_archives,
                        )
                    ]
                    if bare:
                        empty_message = _no_match_hint(
                            pattern=str(pattern),
                            directory=str(directory),
                            bare_entries=bare,
                            recursive=True,
                        )
                if empty_message is None and not entries_tree:
                    empty_message = _empty_list_message(str(directory))
                # Dedupe soft warnings while preserving order.
                uniq_warnings: list[str] = []
                seen_w: set[str] = set()
                for w in soft_warnings:
                    if w in seen_w:
                        continue
                    seen_w.add(w)
                    uniq_warnings.append(w)
                output = _render_file_tree(
                    entries_tree,
                    directory,
                    max_depth,
                    truncated,
                    elided_count,
                    empty_message=empty_message,
                    warnings=uniq_warnings,
                )
            else:
                # ``list`` is shared with user UI (system-noise only); strip AI
                # noise here so media/archives don't pollute the agent view —
                # except under ``attachments/``, this-turn ``material_paths``,
                # ``external/<alias>/`` archives, or pattern-targeted archives.
                seen: set[str] = set()
                entries: list[DirEntry] = []
                list_truncated = False
                for pat in patterns:
                    listing = await context.backend.list(directory, pat)
                    list_truncated = list_truncated or listing.truncated
                    for dir_entry in listing.entries:
                        if dir_entry.path in seen:
                            continue
                        if dir_entry.is_dir or not should_hide_ai_noise_from_list(
                            dir_entry.path,
                            materials=context.material_paths,
                            reveal_archives=reveal_archives,
                        ):
                            seen.add(dir_entry.path)
                            entries.append(dir_entry)
                if entries:
                    output = "\n".join(
                        f"{'d ' if e.is_dir else 'f '}{e.path}" for e in entries
                    )
                elif _pattern_filters(str(pattern)):
                    bare = [
                        e
                        for e in await context.backend.list(directory, "*")
                        if e.is_dir
                        or not should_hide_ai_noise_from_list(
                            e.path,
                            materials=context.material_paths,
                            reveal_archives=reveal_archives,
                        )
                    ]
                    if bare:
                        output = _no_match_hint(
                            pattern=str(pattern),
                            directory=str(directory),
                            bare_entries=bare,
                            recursive=False,
                        )
                    else:
                        output = _empty_list_message(str(directory))
                else:
                    output = _empty_list_message(str(directory))
                # A capped listing must say so even when every surviving entry was
                # AI noise — otherwise「空目录」is a flat lie about a full directory.
                if list_truncated:
                    output += f"\n\n{_LIST_TRUNCATED_NOTE}"
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                directory, start, location=context.backend.location, reason=str(e)
            )
        except NotADirectory:
            if _looks_like_external_directory(str(directory)):
                return _error(
                    f"不是可列的区外目录：{directory}。"
                    + _external_directory_hint(context.backend),
                    start,
                )
            # Local/channel backends may still raise for missing declared dirs.
            if is_declared_latent_dir(str(directory)):
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output=_empty_list_message(str(directory)),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            # ServerWorkspace.list maps missing paths to NotADirectory (not PathNotFound).
            base = f"不是目录：{directory}"
            return _path_missing_error(
                await enrich_missing_path_message(context, str(directory), base=base),
                start,
            )
        except PathNotFound:
            if _looks_like_external_directory(str(directory)):
                return _path_missing_error(
                    f"区外路径不存在或未授权：{directory}。"
                    + _external_directory_hint(context.backend),
                    start,
                )
            if is_declared_latent_dir(str(directory)):
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output=_empty_list_message(str(directory)),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            base = f"列目录失败：路径不存在：{directory}"
            return _path_missing_error(
                await enrich_missing_path_message(context, str(directory), base=base),
                start,
            )
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            if _looks_like_external_directory(str(directory)):
                return _error(
                    f"列目录失败：{e}。" + _external_directory_hint(context.backend),
                    start,
                    user_face=False,
                )
            return _error(f"列目录失败：{e}", start, user_face=False)
        finally:
            context.backend.ai_list_reveal_archives = prev_reveal

        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
