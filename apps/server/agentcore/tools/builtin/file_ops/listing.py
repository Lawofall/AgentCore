"""Shared listing helpers for ``file_list`` (one-layer LS) and ``glob``.

Keep this module free of imports from ``read`` / ``glob`` so the two tools
cannot form a cycle.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
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
    GlobFilesQuery,
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
    "（本层条目已达单次列举上限，还有更多未列出：可换子目录再列。）"
)

LS_REMOVED_FIELDS: tuple[str, ...] = ("pattern", "recursive", "max_depth")
GLOB_REMOVED_FIELDS: tuple[str, ...] = ("recursive", "max_depth")
_STAR_CLASS_PATTERNS: frozenset[str] = frozenset({"*", "**", "**/*"})
# Entire-pattern dump (`*` / `**`) is not a search. `**/*` stays explicit globstar.
_NAMELESS_LISTING_PATTERNS: frozenset[str] = frozenset({"*", "**"})
_BRACE_GLOB_RE = re.compile(r"\{([^{}]+)\}")
_ANY_DIR_RECURSIVE_RE = re.compile(r"^\*\*/([^*/]+)/\*\*/([^/]+)$")
_ANY_DIR_CONTENTS_RE = re.compile(r"^(?:\*\*/)?([^*/]+)/\*\*(?:/\*)?$")
_ANY_DIR_CHILD_RE = re.compile(r"^\*\*/([^*/]+)/([^/]+)$")
_RECURSIVE_UNDER_RE = re.compile(r"^(.+)/\*\*/([^/]+)$")

GLOB_DEPTH = 8
GLOB_DEFAULT_MAX_ENTRIES = 50

_LS_LEFTOVER_MSG = (
    "file_list 只列当前一层（directory，默认 `.`）。"
    "按文件名递归查找请用 glob。"
    "勿再传 {fields}。"
)
_GLOB_LEFTOVER_MSG = (
    "glob 按文件名查找。搜索根用 path（可省=整仓根）。"
    "勿传 {fields}。一层列举请用 file_list。"
)
_GLOB_EMPTY_MSG = (
    "glob 需要非空 pattern（例如 `*.py`、`**/*.ts`、`**/name/**`）。"
    "一层列举请用 file_list。"
)
_GLOB_NAMELESS_MSG = (
    "glob 按文件名查找，不是一层列举。"
    "看当前层用 file_list。"
    "递归全部用 `**/*`。"
    "勿只填 `*` 或 `**`。"
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
    """Actionable guidance when a reserved internal path is used by mistake."""
    from agentcore.workspace.working_root import working_mount

    mounts = getattr(backend, "_mounts", None) or {}
    working = working_mount(mounts)
    extras = [m for m in mounts.values() if not m.working_root]
    if working:
        return "当前干活根是已授权的文件夹；用相对路径 file_list / read。"
    if extras:
        return "额外授权目录请用该目录的本机绝对路径，不要拼内部别名。"
    return (
        "本对话还没有授权文件夹。"
        "请用户在输入区添加文件夹，或对本机路径 read。"
    )


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


def glob_leftover_error(arguments: dict[str, Any], start: float) -> ToolResult | None:
    fields = leftover_fields(arguments, GLOB_REMOVED_FIELDS)
    if not fields:
        return None
    return _error(
        _GLOB_LEFTOVER_MSG.format(fields=" / ".join(fields)),
        start,
        contract_failure=True,
    )


@dataclass(frozen=True, slots=True)
class GlobPlan:
    """One ``rg --files`` shape compiled from a globstar pattern.

    ``locate_dir``: files under directories with this basename
    (``**/name/**`` / ``**/name/*.py``).
    ``star_dirs``: one path-segment directory wildcard (``pkg/*/name``).
    ``max_depth == 1`` is one child layer (maps to rg ``--max-depth 1`` under
    ``directory`` — rg's 0 does not list files in the cwd); ``GLOB_DEPTH``
    means unbounded (stop at the entry cap).
    """

    locate_dir: str | None
    directory: str
    name_filter: str
    max_depth: int
    star_dirs: bool = False


def join_glob_directory(*parts: str) -> str:
    """Join workspace-relative POSIX segments; empty / ``.`` drop out."""
    segs: list[str] = []
    for part in parts:
        raw = (part or "").replace("\\", "/").strip()
        if not raw or raw == ".":
            continue
        for seg in raw.strip("/").split("/"):
            if seg and seg != ".":
                segs.append(seg)
    return "/".join(segs) if segs else "."


def _star_or_all(name: str) -> str:
    return "*" if name in _STAR_CLASS_PATTERNS else name


def _compile_star_dir_plan(raw: str) -> GlobPlan | None:
    """``prefix/*/name`` — one-level directory wildcard, not a literal ``*`` path.

    ``*`` as the last path segment is a name filter (``src/*.py``), handled
    elsewhere. Multiple directory-segment stars (``a/*/b/*/c``) are out of scope.
    """
    segs = [part for part in raw.split("/") if part]
    if len(segs) < 2:
        return None
    name = segs[-1]
    dir_segs = segs[:-1]
    star_at = [i for i, seg in enumerate(dir_segs) if seg == "*"]
    if len(star_at) != 1 or star_at[0] != len(dir_segs) - 1:
        return None
    if any(seg == "**" for seg in dir_segs):
        return None
    prefix = "/".join(dir_segs[:-1]) or "."
    return GlobPlan(None, prefix, _star_or_all(name), 1, True)


def is_nameless_listing_pattern(pattern: str) -> bool:
    """True when any brace alternative is a nameless dump (`*` / `**`)."""
    raw = (pattern or "").strip()
    if not raw:
        return False
    for item in expand_brace_globs(raw):
        token = item.strip().replace("\\", "/").strip("/")
        if token in _NAMELESS_LISTING_PATTERNS:
            return True
    return False


def compile_glob_pattern(pattern: str) -> GlobPlan | None:
    """Compile one globstar pattern onto ``rg --files`` (None if empty or nameless)."""
    raw = (pattern or "").strip().replace("\\", "/").strip("/")
    if not raw or raw in _NAMELESS_LISTING_PATTERNS:
        return None

    leading_any = raw.startswith("**/")
    body = raw[3:] if leading_any else raw
    if "/" not in body:
        if not body or body in _STAR_CLASS_PATTERNS:
            return GlobPlan(None, ".", "*", GLOB_DEPTH)
        return GlobPlan(None, ".", body, GLOB_DEPTH)

    match = _ANY_DIR_RECURSIVE_RE.fullmatch(raw)
    if match is not None:
        return GlobPlan(match.group(1), ".", _star_or_all(match.group(2)), GLOB_DEPTH)

    match = _ANY_DIR_CONTENTS_RE.fullmatch(raw)
    if match is not None:
        name = match.group(1)
        if leading_any:
            return GlobPlan(name, ".", "*", GLOB_DEPTH)
        return GlobPlan(None, name, "*", GLOB_DEPTH)

    match = _ANY_DIR_CHILD_RE.fullmatch(raw)
    if match is not None:
        last = match.group(2)
        depth = GLOB_DEPTH if last in _STAR_CLASS_PATTERNS else 1
        return GlobPlan(match.group(1), ".", _star_or_all(last), depth)

    match = _RECURSIVE_UNDER_RE.fullmatch(raw)
    if match is not None:
        return GlobPlan(None, match.group(1), _star_or_all(match.group(2)), GLOB_DEPTH)

    if "**" not in raw:
        star_plan = _compile_star_dir_plan(raw)
        if star_plan is not None:
            return star_plan
        if "/" in raw:
            prefix, last = raw.rsplit("/", 1)
            return GlobPlan(None, prefix, _star_or_all(last), 1)

    prefix, last = raw.rsplit("/", 1)
    prefix = prefix.replace("**", "").replace("//", "/").strip("/") or "."
    return GlobPlan(None, prefix, _star_or_all(last), GLOB_DEPTH)


def compile_glob_patterns(pattern: str) -> list[GlobPlan] | None:
    """Brace-expand then compile. ``None`` when empty or nameless ``*`` / ``**``."""
    raw = (pattern or "").strip()
    if not raw:
        return None
    plans: list[GlobPlan] = []
    seen: set[tuple[str | None, str, str, int, bool]] = set()
    for item in expand_brace_globs(raw):
        plan = compile_glob_pattern(item)
        if plan is None:
            return None
        key = (
            plan.locate_dir,
            plan.directory,
            plan.name_filter,
            plan.max_depth,
            plan.star_dirs,
        )
        if key not in seen:
            seen.add(key)
            plans.append(plan)
    return plans or None


def glob_plan_to_files_query(
    search_root: str,
    plan: GlobPlan,
    *,
    max_entries: int,
    reveal_archives: bool,
) -> GlobFilesQuery:
    """Map a GlobPlan onto one path-aware ``rg --files`` query.

    One-layer plans (``src/*.py``) use ``--max-depth 1`` so slash-less
    ``*.py`` cannot leak into nested dirs (rg depth 0 lists nothing in a
    directory). Recursive filename plans omit depth and stop at
    ``max_entries``.
    """
    root = search_root or "."
    if plan.locate_dir:
        loc = plan.locate_dir
        nf = plan.name_filter
        if nf in _STAR_CLASS_PATTERNS:
            globs: tuple[str, ...] = (f"**/{loc}/**",)
        elif plan.max_depth == 1:
            globs = (f"**/{loc}/{nf}",)
        else:
            globs = (f"**/{loc}/{nf}", f"**/{loc}/**/{nf}")
        return GlobFilesQuery(
            directory=root,
            globs=globs,
            max_depth=None,
            max_entries=max_entries,
            reveal_archives=reveal_archives,
        )
    if plan.star_dirs:
        parent = join_glob_directory(root, plan.directory)
        nf = plan.name_filter
        glob = f"*/{nf}" if nf not in _STAR_CLASS_PATTERNS else "*/*"
        return GlobFilesQuery(
            directory=parent,
            globs=(glob,),
            max_depth=None,
            max_entries=max_entries,
            reveal_archives=reveal_archives,
        )
    cwd = join_glob_directory(root, plan.directory)
    nf = plan.name_filter
    include: tuple[str, ...] = () if nf in _STAR_CLASS_PATTERNS else (nf,)
    rg_depth = 1 if plan.max_depth == 1 else None
    return GlobFilesQuery(
        directory=cwd,
        globs=include,
        max_depth=rg_depth,
        max_entries=max_entries,
        reveal_archives=reveal_archives,
    )


def glob_pattern_reject(pattern: str, start: float) -> ToolResult:
    """``contract_failure`` for an empty or nameless glob pattern."""
    msg = (
        _GLOB_NAMELESS_MSG
        if is_nameless_listing_pattern(pattern)
        else _GLOB_EMPTY_MSG
    )
    return _error(msg, start, contract_failure=True)


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
        f"（已达列举上限 {max_entries} 条{extra}。"
        "可收窄 pattern。）"
    )


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
        f"directory={directory!r} 读不到。"
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
                f"读不到这个路径：{directory}。"
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
            path=str(directory),
        )
    if isinstance(exc, PathNotFound):
        if looks_like_external_directory(str(directory)):
            return _path_missing_error(
                f"读不到这个路径：{directory}。"
                + external_directory_hint(context.backend),
                start,
                path=str(directory),
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
            path=str(directory),
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


async def list_archive_as_directory(
    directory: str,
    *,
    context: ToolContext,
    start: float,
) -> ToolResult | None:
    """If ``directory`` is a container archive, list members (or the sibling folder).

    None when the path is not a pack — caller keeps the NotADirectory mapping.
    """
    from agentcore.storage._archive import ZipExtractLimitError
    from agentcore.workspace.archive_unpack import (
        UnpackFailedError,
        UnpackPasswordError,
        list_member_names,
        unpack_dir_for,
    )
    from agentcore.workspace.file_kind import is_container_archive, sniff_bytes
    from agentcore.workspace.protocol import NotAFile, ReadHeadResult

    try:
        head = await context.backend.read_head(directory)
    except (PathNotFound, NotADirectory, NotAFile, WorkspaceError):
        return None
    if not isinstance(head, ReadHeadResult):
        return None
    sniff = sniff_bytes(head.data)
    if not is_container_archive(sniff, directory):
        return None

    dest = unpack_dir_for(directory)
    try:
        listing = await context.backend.list(dest, "*")
        entries = visible_list_entries(
            list(listing.entries),
            materials=context.material_paths,
        )
        body = format_ls_lines(entries) if entries else empty_list_message(dest)
        if listing.truncated:
            body += f"\n\n{LIST_TRUNCATED_NOTE}"
        output = f"（已展开到 `{dest}`）\n{body}"
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except (PathNotFound, NotADirectory, WorkspaceError):
        pass

    try:
        data = await context.backend.read_bytes(directory)
        names = list_member_names(data, sniff=sniff, name=directory)
    except UnpackPasswordError:
        return ToolResult(
            tool_call_id="",
            success=True,
            output="压缩包有密码，无法列出成员。",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except ZipExtractLimitError:
        return ToolResult(
            tool_call_id="",
            success=True,
            output="压缩包过大，无法列出成员。",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except UnpackFailedError as exc:
        if exc.detail == "need_7z":
            text = "这台电脑还没有解压器，无法列出成员。"
        else:
            text = "无法列出这个压缩包的成员。"
        return ToolResult(
            tool_call_id="",
            success=True,
            output=text,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except WorkspaceError:
        return None

    from agentcore.workspace.limits import ARCHIVE_PROMPT_MEMBER_CAP

    listing_names, truncated = (
        (names, False)
        if len(names) <= ARCHIVE_PROMPT_MEMBER_CAP
        else (names[:ARCHIVE_PROMPT_MEMBER_CAP], True)
    )
    header = (
        f"压缩包 `{directory}` 未在区内展开（引用不改用户树）。"
        "下列是包内成员名，不是工作区路径；read 这些名字会找不到文件。"
    )
    body = "\n".join(listing_names) if listing_names else empty_list_message(directory)
    output = f"{header}\n{body}"
    if truncated:
        output += f"\n\n{LIST_TRUNCATED_NOTE}"
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )
