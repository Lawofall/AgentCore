"""glob — globstar recursive search (CEO + worker, NEVER, READ_ONLY)."""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.protocol import (
    NotADirectory,
    PathNotFound,
    TreeEntry,
    TreeResult,
    WorkspaceError,
)

from .listing import (
    GLOB_DEFAULT_MAX_ENTRIES,
    GLOB_DEPTH,
    GLOB_MAX_ENTRIES_CAP,
    GlobPlan,
    bare_external_error,
    clamp_glob_max_entries,
    compile_glob_patterns,
    format_glob_lines,
    glob_leftover_error,
    glob_no_match_hint,
    glob_pattern_reject,
    glob_truncated_footer,
    is_bare_external_directory,
    join_glob_directory,
    map_listing_failure,
    pattern_targets_archives,
    visible_list_entries,
)


class GlobTool:
    """Recursively find files/dirs by globstar. Never one-layer LS."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="glob",
            description=(
                "按 globstar 递归查找。省略 path=整仓。一层列举用 file_list。"
                "例：`pkg/*/name`。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "globstar。无斜杠=任意深度文件名（`*.py`、`*sidecar*`）；"
                            "有斜杠=相对路径（`src/*.py` 一层，`pkg/*/name` 一层子目录，"
                            "`src/**/*.py` 递归，`**/name/**` 任意深度该目录下）。"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "搜索根，工作区相对 POSIX 目录（默认 `.`=整仓）。"
                            "`directory` 与 path 同义。不存在时从根按目录名/同一 pattern 续找。"
                            "`/<根标签>/…` 与裸 `/`、`\\` 视为根；区外用 "
                            "`external/<别名>/`，禁止裸 `external`；其它绝对路径拒绝。"
                        ),
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": (
                            f"最多返回条数（默认 {GLOB_DEFAULT_MAX_ENTRIES}，"
                            f"上限 {GLOB_MAX_ENTRIES_CAP}）。触顶页脚诚实。"
                        ),
                        "minimum": 1,
                        "maximum": GLOB_MAX_ENTRIES_CAP,
                    },
                },
                "required": ["pattern"],
            },
            category=ToolCategory.FILESYSTEM,
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

        directory = str(
            arguments.get("path") or arguments.get("directory") or "."
        ).strip() or "."
        max_entries = clamp_glob_max_entries(arguments.get("max_entries"))
        reveal_archives = pattern_targets_archives(pattern)

        if is_bare_external_directory(directory):
            return bare_external_error(directory, context.backend, start)

        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        directory, _shell_note = await rewrite_project_shell_relpath(
            directory, context, register=False
        )
        if not directory:
            directory = "."

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
) -> tuple[list[TreeEntry], bool, int, list[str], str | None]:
    if plan.locate_dir:
        located, note = await _list_tree_maybe_fallback(
            backend,
            search_root,
            name_filter=plan.locate_dir,
            max_depth=GLOB_DEPTH,
            max_entries=max_entries,
        )
        dirs = [entry.path for entry in located.entries if entry.is_dir]
        files = [entry for entry in located.entries if not entry.is_dir]
        if not dirs:
            return (
                files,
                located.truncated,
                located.elided_count,
                list(located.warnings),
                note,
            )
        return await _fanout_named(
            backend,
            dirs=dirs,
            name_filter=plan.name_filter,
            max_depth=plan.max_depth,
            max_entries=max_entries,
            seed_files=files,
            truncated=located.truncated,
            elided=located.elided_count,
            warnings=list(located.warnings),
            note=note,
        )

    if plan.star_dirs:
        parent = join_glob_directory(search_root, plan.directory)
        located, note = await _list_tree_maybe_fallback(
            backend,
            parent,
            name_filter="*",
            max_depth=1,
            max_entries=max_entries,
        )
        dirs = [entry.path for entry in located.entries if entry.is_dir]
        if not dirs:
            return (
                [],
                located.truncated,
                located.elided_count,
                list(located.warnings),
                note,
            )
        return await _fanout_named(
            backend,
            dirs=dirs,
            name_filter=plan.name_filter,
            max_depth=plan.max_depth,
            max_entries=max_entries,
            seed_files=[],
            truncated=located.truncated,
            elided=located.elided_count,
            warnings=list(located.warnings),
            note=note,
        )

    target = join_glob_directory(search_root, plan.directory)
    tree, note = await _list_tree_maybe_fallback(
        backend,
        target,
        name_filter=plan.name_filter,
        max_depth=plan.max_depth,
        max_entries=max_entries,
    )
    return list(tree.entries), tree.truncated, tree.elided_count, list(tree.warnings), note


def _listing_error_directory(exc: BaseException, fallback: str) -> str:
    """Prefer the path the backend rejected over glob's search root."""
    if isinstance(exc, (NotADirectory, PathNotFound)):
        detail = str(exc).strip()
        if detail:
            return detail
    return fallback


async def _fanout_named(
    backend: Any,
    *,
    dirs: list[str],
    name_filter: str,
    max_depth: int,
    max_entries: int,
    seed_files: list[TreeEntry],
    truncated: bool,
    elided: int,
    warnings: list[str],
    note: str | None,
) -> tuple[list[TreeEntry], bool, int, list[str], str | None]:
    merged: dict[str, TreeEntry] = {entry.path: entry for entry in seed_files}
    for root in dirs:
        tree, sub_note = await _list_tree_maybe_fallback(
            backend,
            root,
            name_filter=name_filter,
            max_depth=max_depth,
            max_entries=max_entries,
        )
        note = note or sub_note
        for entry in tree.entries:
            merged[entry.path] = entry
        truncated = truncated or tree.truncated
        elided += tree.elided_count
        warnings.extend(tree.warnings)
    return list(merged.values()), truncated, elided, warnings, note


async def _list_tree_maybe_fallback(
    backend: Any,
    directory: str,
    *,
    name_filter: str,
    max_depth: int,
    max_entries: int,
) -> tuple[TreeResult, str | None]:
    try:
        tree = await backend.list_tree(
            directory,
            pattern=name_filter,
            max_depth=max_depth,
            max_entries=max_entries,
        )
        return tree, None
    except (PathNotFound, NotADirectory):
        if directory in (".", ""):
            raise
        needle = directory.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if not needle or needle in {"*", "**", "**/*"}:
            raise
        located = await backend.list_tree(
            ".",
            pattern=needle,
            max_depth=GLOB_DEPTH,
            max_entries=max_entries,
        )
        dirs = [entry.path for entry in located.entries if entry.is_dir]
        files = [entry for entry in located.entries if not entry.is_dir]
        if dirs:
            merged: dict[str, TreeEntry] = {entry.path: entry for entry in files}
            truncated = located.truncated
            elided = located.elided_count
            warnings = list(located.warnings)
            for root in dirs:
                sub = await backend.list_tree(
                    root,
                    pattern=name_filter,
                    max_depth=max_depth,
                    max_entries=max_entries,
                )
                for entry in sub.entries:
                    merged[entry.path] = entry
                truncated = truncated or sub.truncated
                elided += sub.elided_count
                warnings.extend(sub.warnings)
            return (
                TreeResult(
                    entries=list(merged.values()),
                    truncated=truncated,
                    elided_count=elided,
                    warnings=warnings,
                ),
                f"（path={directory!r} 不存在，已按目录名 {needle!r} 从工作区根查找。）",
            )
        tree = await backend.list_tree(
            ".",
            pattern=name_filter,
            max_depth=max_depth,
            max_entries=max_entries,
        )
        return (
            tree,
            (
                f"（path={directory!r} 不存在，已从工作区根用同一 pattern 查找。"
                f"工作区根下也没有名为 {needle!r} 的目录。）"
            ),
        )
