"""glob — globstar recursive search (CEO + worker, NEVER, READ_ONLY)."""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.protocol import (
    GlobFilesQuery,
    GlobFilesResult,
    NotADirectory,
    PathNotFound,
    TreeEntry,
    WorkspaceError,
)

from .listing import (
    GLOB_DEFAULT_MAX_ENTRIES,
    GlobPlan,
    bare_external_error,
    compile_glob_patterns,
    format_glob_lines,
    glob_leftover_error,
    glob_no_match_hint,
    glob_pattern_reject,
    glob_plan_to_files_query,
    glob_truncated_footer,
    is_bare_external_directory,
    map_listing_failure,
    pattern_targets_archives,
    visible_list_entries,
)
from .prepare_path import TOOL_PATH_DESCRIPTION


class GlobTool:
    """Find files/dirs by globstar. Never one-layer LS; nameless ``*`` is not a dump."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
        workspace_io=True,
        catalog_summary="按文件名模式找文件",
        blurb="用通配符找路径，不打开文件内容",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="glob",
            description="globstar 按文件名查找。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "globstar。无斜杠=文件名；有斜杠=相对路径"
                            "（`*` 一层，`**` 递归）。"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": TOOL_PATH_DESCRIPTION,
                    },
                },
                "required": ["pattern"],
            },
            face=ToolFace.SEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        leftover = glob_leftover_error(arguments, start)
        if leftover is not None:
            return leftover

        pattern = str(arguments.get("pattern") or "").strip()
        plans = compile_glob_patterns(pattern)
        if plans is None:
            return glob_pattern_reject(pattern, start)

        # Search root is schema ``path`` only; leftover ``directory`` is ignored.
        directory = str(arguments.get("path") or ".").strip() or "."
        max_entries = GLOB_DEFAULT_MAX_ENTRIES
        reveal_archives = pattern_targets_archives(pattern)

        if is_bare_external_directory(directory):
            return bare_external_error(directory, context.backend, start)

        from .prepare_path import prepare_tool_path

        prepared = await prepare_tool_path(
            directory, context, start=start, as_directory=True
        )
        if isinstance(prepared, ToolResult):
            return prepared
        directory = prepared

        prev_reveal = getattr(context.backend, "ai_list_reveal_archives", False)
        if reveal_archives:
            context.backend.ai_list_reveal_archives = True
        notes: list[str] = []
        try:
            merged: dict[str, TreeEntry] = {}
            truncated = False
            elided_count = 0
            warnings: list[str] = []
            for plan in plans:
                entries, plan_trunc, plan_elided, plan_warnings, note = await _run_plan(
                    backend=context.backend,
                    search_root=directory,
                    plan=plan,
                    max_entries=max_entries,
                    reveal_archives=reveal_archives,
                )
                if note:
                    notes.append(note)
                for entry in entries:
                    merged[entry.path] = entry
                truncated = truncated or plan_trunc
                elided_count += plan_elided
                warnings.extend(plan_warnings)
            ordered = sorted(
                merged.values(),
                key=lambda e: e.path.replace("\\", "/").lower(),
            )
            if len(ordered) > max_entries:
                truncated = True
                elided_count += len(ordered) - max_entries
                ordered = ordered[:max_entries]
            prefix = "\n".join(dict.fromkeys(notes))
            if ordered:
                output = format_glob_lines(ordered)
                if prefix:
                    output = f"{prefix}\n{output}"
            else:
                output = await _no_match_output(
                    context,
                    pattern=pattern,
                    directory=directory,
                    reveal_archives=reveal_archives,
                    prefix=prefix,
                )
            if truncated:
                output = output + "\n\n" + glob_truncated_footer(
                    max_entries=max_entries, elided_count=elided_count
                )
            uniq_warnings: list[str] = []
            seen_w: set[str] = set()
            for warning in warnings:
                if warning in seen_w:
                    continue
                seen_w.add(warning)
                uniq_warnings.append(warning)
            if uniq_warnings:
                output += "\n" + "\n".join(f"⚠ {w}" for w in uniq_warnings)
        except WorkspaceError as e:
            failed = _listing_error_directory(e, directory)
            return await map_listing_failure(
                e, directory=failed, context=context, start=start, verb="查找"
            )
        finally:
            context.backend.ai_list_reveal_archives = prev_reveal

        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
        )


async def _no_match_output(
    context: ToolContext,
    *,
    pattern: str,
    directory: str,
    reveal_archives: bool,
    prefix: str,
) -> str:
    sample_dir = directory
    try:
        listing = await context.backend.list(sample_dir, "*")
    except WorkspaceError:
        sample_dir = "."
        listing = await context.backend.list(sample_dir, "*")
    bare = visible_list_entries(
        list(listing.entries),
        materials=context.material_paths,
        reveal_archives=reveal_archives,
    )
    hint = glob_no_match_hint(
        pattern=pattern,
        directory=sample_dir,
        bare_entries=bare,
    )
    return f"{prefix}\n{hint}" if prefix else hint


async def _run_plan(
    *,
    backend: Any,
    search_root: str,
    plan: GlobPlan,
    max_entries: int,
    reveal_archives: bool,
) -> tuple[list[TreeEntry], bool, int, list[str], str | None]:
    query = glob_plan_to_files_query(
        search_root,
        plan,
        max_entries=max_entries,
        reveal_archives=reveal_archives
        or bool(getattr(backend, "ai_list_reveal_archives", False)),
    )
    result, note = await _glob_files_maybe_fallback(backend, query)
    entries = [TreeEntry(path=p, is_dir=False, depth=0) for p in result.paths]
    return entries, result.truncated, 0, list(result.warnings), note


def _listing_error_directory(exc: BaseException, fallback: str) -> str:
    """Prefer the path the backend rejected over glob's search root."""
    if isinstance(exc, (NotADirectory, PathNotFound)):
        detail = str(exc).strip()
        if detail:
            return detail
    return fallback


def _fallback_globs(query: GlobFilesQuery, needle: str) -> tuple[str, ...]:
    """Rebuild include globs so a missing cwd is searched by basename from the repo root."""
    if not query.globs:
        return (f"**/{needle}/**",)
    rebuilt: list[str] = []
    for raw in query.globs:
        g = raw.replace("\\", "/").lstrip("/")
        if g.startswith("**/"):
            rebuilt.append(g)
        else:
            rebuilt.append(f"**/{needle}/{g}")
    if query.max_depth is None:
        for raw in query.globs:
            name = raw.replace("\\", "/").lstrip("/")
            if "/" in name:
                continue
            rebuilt.append(f"**/{needle}/**/{name}")
    seen: set[str] = set()
    out: list[str] = []
    for item in rebuilt:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


async def _glob_files_maybe_fallback(
    backend: Any,
    query: GlobFilesQuery,
) -> tuple[GlobFilesResult, str | None]:
    try:
        return await backend.glob_files(query), None
    except (PathNotFound, NotADirectory):
        directory = query.directory
        if directory in (".", ""):
            raise
        needle = directory.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if not needle or needle in {"*", "**", "**/*"}:
            raise
        located = await backend.glob_files(
            GlobFilesQuery(
                directory=".",
                globs=_fallback_globs(query, needle),
                max_depth=None,
                max_entries=query.max_entries,
                reveal_archives=query.reveal_archives,
            )
        )
        if located.paths:
            return (
                located,
                f"（path={directory!r} 不存在，已按目录名 {needle!r} 从工作区根查找。）",
            )
        tree = await backend.glob_files(
            GlobFilesQuery(
                directory=".",
                globs=query.globs,
                max_depth=query.max_depth,
                max_entries=query.max_entries,
                reveal_archives=query.reveal_archives,
            )
        )
        return (
            tree,
            (
                f"（path={directory!r} 不存在，已从工作区根用同一 pattern 查找。"
                f"工作区根下也没有名为 {needle!r} 的目录。）"
            ),
        )
