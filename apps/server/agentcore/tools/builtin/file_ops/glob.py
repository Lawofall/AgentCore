"""glob — recursive filename search (CEO + worker, NEVER, READ_ONLY)."""

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
from agentcore.workspace.protocol import TreeEntry, WorkspaceError

from .listing import (
    GLOB_DEFAULT_MAX_ENTRIES,
    GLOB_DEPTH,
    GLOB_MAX_ENTRIES_CAP,
    bare_external_error,
    clamp_glob_max_entries,
    format_glob_lines,
    glob_leftover_error,
    glob_name_filters,
    glob_no_match_hint,
    glob_pattern_reject,
    glob_truncated_footer,
    is_bare_external_directory,
    map_listing_failure,
    pattern_targets_archives,
    visible_list_entries,
)


class GlobTool:
    """Recursively find files/dirs by basename glob. Never one-layer LS."""

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
                "按【文件名】递归查找（永远递归）。pattern 必填：`*.py`、`*.{ts,tsx}`、"
                "`**/*.py`。`*` / 一层列举用 file_list。省略 path=整仓根。"
                "回执一行一条相对路径，目录尾 `/`。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "文件名 glob（basename）。可写 `*.py`、`*.{ts,tsx}`、"
                            "`**/*.py`。禁止 `*` / `**` / `**/*` / 空（改 file_list）。"
                            "剥 `**/` 后禁止仍含 `/`（目录放到 path）。"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "搜索根，工作区相对 POSIX 目录（默认 `.`=整仓）。"
                            "仅填本回合已证实存在的目录；禁止猜测 src/、@scope、app/。"
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
        filters = glob_name_filters(pattern)
        if filters is None:
            return glob_pattern_reject(pattern, start)

        directory = str(arguments.get("path") or ".").strip() or "."
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
        try:
            merged: dict[str, TreeEntry] = {}
            truncated = False
            elided_count = 0
            warnings: list[str] = []
            for name_filter in filters:
                tree = await context.backend.list_tree(
                    directory,
                    pattern=name_filter,
                    max_depth=GLOB_DEPTH,
                    max_entries=max_entries,
                )
                for entry in tree.entries:
                    merged[entry.path] = entry
                truncated = truncated or tree.truncated
                elided_count += tree.elided_count
                warnings.extend(tree.warnings)
            ordered = sorted(
                merged.values(),
                key=lambda e: e.path.replace("\\", "/").lower(),
            )
            if len(ordered) > max_entries:
                truncated = True
                elided_count += len(ordered) - max_entries
                ordered = ordered[:max_entries]
            entries = ordered
            if entries:
                output = format_glob_lines(entries)
            else:
                listing = await context.backend.list(directory, "*")
                bare = visible_list_entries(
                    list(listing.entries),
                    materials=context.material_paths,
                    reveal_archives=reveal_archives,
                )
                output = glob_no_match_hint(
                    pattern=pattern,
                    directory=directory,
                    bare_entries=bare,
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
            return await map_listing_failure(
                e, directory=directory, context=context, start=start, verb="查找"
            )
        finally:
            context.backend.ai_list_reveal_archives = prev_reveal

        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
