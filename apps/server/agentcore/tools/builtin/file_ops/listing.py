"""Shared listing helpers for ``file_list`` (one-layer LS) and ``glob``.

Keep this module free of imports from ``read`` / ``glob`` so the two tools
cannot form a cycle.
"""

from __future__ import annotations

import re
import time
from typing import Any

from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace._paths import AI_ARCHIVE_FILE_SUFFIXES
from agentcore.workspace.declared_dirs import (
    LATENT_EMPTY_LIST_MESSAGE,
    is_declared_latent_dir,
)
from agentcore.workspace.external_mounts import EXTERNAL_PREFIX, parse_external_path
from agentcore.workspace.protocol import (
    DirEntry,
    NotADirectory,
    OutsideWorkspace,
    PathNotFound,
    TreeEntry,
    WorkspaceError,
)
from agentcore.workspace.sparse_listing import should_hide_ai_noise_from_list

from .errors import (
    _error,
    _maybe_channel_dead_error,
    _outside_workspace_error,
    _path_missing_error,
)
from .path_hints import enrich_missing_path_message

# One-layer ``file_list`` hit the backend's entry ceiling. A bounded listing is
# fine; a silent one is not.
LIST_TRUNCATED_NOTE = (
    "（本层条目已达单次列举上限，还有更多未列出：可换子目录再列，或用 glob 按文件名查找。）"
)

LS_REMOVED_FIELDS: tuple[str, ...] = ("pattern", "recursive", "max_depth")
GLOB_REMOVED_FIELDS: tuple[str, ...] = ("directory", "recursive", "max_depth")
_STAR_CLASS_PATTERNS: frozenset[str] = frozenset({"*", "**", "**/*"})
_BRACE_GLOB_RE = re.compile(r"\{([^{}]+)\}")

GLOB_DEPTH = 8
GLOB_DEFAULT_MAX_ENTRIES = 50
GLOB_MAX_ENTRIES_CAP = 200

_LS_LEFTOVER_MSG = (
    "file_list 只列当前一层（directory，默认 `.`）。"
    "按文件名递归查找请用 glob。"
    "勿再传 {fields}。"
)
_FOLDER_DIR_LEFTOVER_MSG = (
    "list_folder_dir 只列目标文件夹当前一层（folder_id + directory）。"
    "勿再传 {fields}。"
    "跨文件夹按名查找请 delegate 到该 folder_id；不要用 glob（glob 只搜本会话出生桌）。"
)
_GLOB_LEFTOVER_MSG = (
    "glob 永远递归，用 path（可省=整仓根）而不是 directory；"
    "勿传 {fields}。一层列举请用 file_list。"
)
_GLOB_STAR_MSG = (
    "glob 不接受 pattern={pattern!r}（`*` / `**` / `**/*` / 空是一层列举）。"
    "请改用 file_list。"
)
_GLOB_PATHY_MSG = (
    "glob 只按文件名（basename）匹配；剥 `**/` 后 pattern 仍含 `/`："
    "把目录放到 path，pattern 只留文件名（如 path=src, pattern=*.py）。"
    "一层列举请用 file_list。"
)


def empty_list_message(directory: str) -> str:
    """Empty ``file_list`` body — latent declared dirs get auto-create copy."""
    if is_declared_latent_dir(directory):
        return LATENT_EMPTY_LIST_MESSAGE
    return "（空目录）"


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


def pattern_targets_archives(pattern: str) -> bool:
    """True when glob(s) end with an AI-archive suffix (``*.zip``, ``*.{rar,7z}``…)."""
    for pat in expand_brace_globs(pattern):
        lower = (pat or "").lower().rstrip("/")
        if any(lower.endswith(suf) for suf in AI_ARCHIVE_FILE_SUFFIXES):
            return True
    return False


def is_bare_external_directory(directory: str) -> bool:
    """True for ``external`` / ``external/`` (no alias) — not a listable mount path."""
    raw = (directory or "").strip().replace("\\", "/").strip("/")
    return raw == EXTERNAL_PREFIX.rstrip("/")


def looks_like_external_directory(directory: str) -> bool:
    """Bare ``external`` or any ``external/<alias>/…`` shape (even unknown alias)."""
    raw = (directory or "").strip().replace("\\", "/").strip("/")
    if raw == EXTERNAL_PREFIX.rstrip("/") or raw.startswith(EXTERNAL_PREFIX):
        return True
    return parse_external_path(directory) is not None


def external_directory_hint(backend: Any) -> str:
    """Actionable mounts guidance for bare / failed ``external`` list attempts."""
    guide = "须使用 `external/<别名>/`（例如 `external/desktop/`）访问已授权区外目录"
    mounts = getattr(backend, "_mounts", None) or {}
    if not mounts:
        return (
            f"{guide}；本对话尚无会话级区外目录授权"
            "（用户经 ask_user grant_* 确认后才会出现 mounts）。"
        )
    parts = [f"`external/{a}/`" for a in mounts]
    return f"{guide}；当前 mounts：{'；'.join(parts)}。"


def leftover_fields(arguments: dict[str, Any], names: tuple[str, ...]) -> tuple[str, ...]:
    """Field names present on the call (even if null) that the tool no longer accepts."""
    return tuple(name for name in names if name in arguments)


def ls_leftover_error(arguments: dict[str, Any], start: float) -> ToolResult | None:
    fields = leftover_fields(arguments, LS_REMOVED_FIELDS)
    if not fields:
        return None
    return _error(
        _LS_LEFTOVER_MSG.format(fields=" / ".join(fields)),
        start,
        contract_failure=True,
    )


def folder_dir_leftover_error(arguments: dict[str, Any], start: float) -> ToolResult | None:
    fields = leftover_fields(arguments, LS_REMOVED_FIELDS)
    if not fields:
        return None
    return _error(
        _FOLDER_DIR_LEFTOVER_MSG.format(fields=" / ".join(fields)),
        start,
        contract_failure=True,
    )


def glob_leftover_error(arguments: dict[str, Any], start: float) -> ToolResult | None:
    fields = leftover_fields(arguments, GLOB_REMOVED_FIELDS)
    if not fields:
        return None
    return _error(
        _GLOB_LEFTOVER_MSG.format(fields=" / ".join(fields)),
        start,
        contract_failure=True,
    )


def _strip_glob_recursive_prefix(pat: str) -> str:
    p = (pat or "").strip().replace("\\", "/")
    while p.startswith("**/"):
        p = p[3:]
    return p


def glob_name_filters(pattern: str) -> list[str] | None:
    """Basename filters to feed ``list_tree``, or ``None`` when the pattern is invalid.

    Strips leading ``**/``. After stripping, ``*`` / ``**`` / ``**/*`` / empty
    or a remaining ``/`` is invalid — caller fails (star-class → ``file_list``).
    """
    raw = (pattern or "").strip()
    if not raw:
        return None
    filters: list[str] = []
    for item in expand_brace_globs(raw):
        name = _strip_glob_recursive_prefix(item)
        if not name or name in _STAR_CLASS_PATTERNS or "/" in name:
            return None
        if name not in filters:
            filters.append(name)
    return filters or None


def glob_pattern_reject(pattern: str, start: float) -> ToolResult:
    """``contract_failure`` for star-class or path-qualified glob patterns."""
    raw = (pattern or "").strip()
    stripped = _strip_glob_recursive_prefix(raw)
    if (not raw) or stripped in _STAR_CLASS_PATTERNS or not stripped:
        return _error(_GLOB_STAR_MSG.format(pattern=raw or ""), start, contract_failure=True)
    return _error(_GLOB_PATHY_MSG, start, contract_failure=True)


def format_ls_lines(entries: list[DirEntry]) -> str:
    return "\n".join(f"{'d ' if e.is_dir else 'f '}{e.path}" for e in entries)


def format_glob_lines(entries: list[TreeEntry]) -> str:
    """One workspace-relative path per line; directories carry a trailing ``/``."""
    ordered = sorted(entries, key=lambda e: e.path.replace("\\", "/").lower())
    lines: list[str] = []
    for entry in ordered:
        path = entry.path.replace("\\", "/").rstrip("/")
        lines.append(f"{path}/" if entry.is_dir else path)
    return "\n".join(lines)


def glob_no_match_hint(
    *,
    pattern: str,
    directory: str,
    bare_entries: list[DirEntry],
) -> str:
    """Zero hits: success copy + sample. Never claim an empty directory."""
    root = "./" if directory in (".", "") else f"{directory.rstrip('/')}/"
    if not bare_entries:
        if is_declared_latent_dir(directory):
            return LATENT_EMPTY_LIST_MESSAGE
        return (
            f"（在 {root} 下无匹配 pattern={pattern!r} 的条目；该目录当前没有可列文件。"
            "可换更宽的 glob，或用 file_list 列一层。）"
        )
    sample_parts = [f"{'d ' if e.is_dir else 'f '}{e.path}" for e in bare_entries[:8]]
    sample = "；".join(sample_parts)
    more = (
        f" 等共 {len(bare_entries)} 项"
        if len(bare_entries) > 8
        else f"（共 {len(bare_entries)} 项）"
    )
    return (
        f"（在 {root} 下无匹配 pattern={pattern!r} 的条目；目录非空{more}。"
        f"可见顶层示例：{sample}。可换更宽的 glob，或用 file_list 列一层。）"
    )


def glob_truncated_footer(*, max_entries: int, elided_count: int) -> str:
    extra = f"；另有 {elided_count} 个条目未列出" if elided_count else ""
    return (
        f"（已达列举上限 max_entries={max_entries}{extra}。"
        "可收窄 pattern 或提高 max_entries。）"
    )


def clamp_glob_max_entries(value: object) -> int:
    if not isinstance(value, (int, str, float)):
        return GLOB_DEFAULT_MAX_ENTRIES
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return GLOB_DEFAULT_MAX_ENTRIES
    return max(1, min(raw, GLOB_MAX_ENTRIES_CAP))


def visible_list_entries(
    listing_entries: list[DirEntry],
    *,
    materials: frozenset[str] | None,
    reveal_archives: bool = False,
) -> list[DirEntry]:
    return [
        entry
        for entry in listing_entries
        if entry.is_dir
        or not should_hide_ai_noise_from_list(
            entry.path,
            materials=materials,
            reveal_archives=reveal_archives,
        )
    ]


def bare_external_error(directory: str, backend: Any, start: float) -> ToolResult:
    return _error(
        f"directory={directory!r} 无效：裸 `external` 不是可列目录。"
        + external_directory_hint(backend),
        start,
    )


async def map_listing_failure(
    exc: BaseException,
    *,
    directory: str,
    context: ToolContext,
    start: float,
    verb: str = "列目录",
) -> ToolResult:
    """Shared OutsideWorkspace / missing / channel-dead mapping for LS and glob."""
    if isinstance(exc, OutsideWorkspace):
        return _outside_workspace_error(
            directory, start, location=context.backend.location, reason=str(exc)
        )
    if isinstance(exc, NotADirectory):
        if looks_like_external_directory(str(directory)):
            return _error(
                f"不是可列的区外目录：{directory}。"
                + external_directory_hint(context.backend),
                start,
            )
        if is_declared_latent_dir(str(directory)):
            return ToolResult(
                tool_call_id="",
                success=True,
                output=empty_list_message(str(directory)),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        base = f"不是目录：{directory}"
        return _path_missing_error(
            await enrich_missing_path_message(context, str(directory), base=base),
            start,
        )
    if isinstance(exc, PathNotFound):
        if looks_like_external_directory(str(directory)):
            return _path_missing_error(
                f"区外路径不存在或未授权：{directory}。"
                + external_directory_hint(context.backend),
                start,
            )
        if is_declared_latent_dir(str(directory)):
            return ToolResult(
                tool_call_id="",
                success=True,
                output=empty_list_message(str(directory)),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        base = f"{verb}失败：路径不存在：{directory}"
        return _path_missing_error(
            await enrich_missing_path_message(context, str(directory), base=base),
            start,
        )
    if isinstance(exc, WorkspaceError):
        dead = _maybe_channel_dead_error(exc, start)
        if dead is not None:
            return dead
        if looks_like_external_directory(str(directory)):
            return _error(
                f"{verb}失败：{exc}。" + external_directory_hint(context.backend),
                start,
                user_face=False,
            )
        return _error(f"{verb}失败：{exc}", start, user_face=False)
    raise exc
