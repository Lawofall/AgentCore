"""Built-in tool: grep — regex search over workspace file CONTENTS.

Complements the rest of the file family: ``glob`` finds files by NAME,
``file_list`` lists one known-directory layer, and ``read`` opens one
file, while ``grep`` finds WHERE a string / symbol / pattern appears across
many files, returning ripgrep-style ``path:line: text`` hits the model can
then open with ``read``.

Thin shell over ``ToolContext.backend``: this tool builds a ``GrepQuery`` and
renders the bounded ``GrepResult`` the backend returns. Search itself is
**embedded ripgrep** (Rust regex dialect) on both cloud and desktop backends —
no Python/JS walk fallback.
"""

import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.tools.builtin.file_ops.errors import _outside_workspace_msg
from agentcore.tools.builtin.file_ops.path_hints import enrich_missing_path_message
from agentcore.tools.cross_turn_retry import CROSS_TURN_RETRY_KEY, CrossTurnRetry
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.limits import (
    channel_dead_error_message,
    channel_dead_retire_metadata,
    op_liveness_timeout_metadata,
    workspace_channel_failure_kind,
)
from agentcore.workspace.protocol import (
    GrepQuery,
    GrepResult,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)

_DEFAULT_MAX_RESULTS = 50
# grep output is line-oriented and denser than the 4000-char default; lift it so
# a full (already capped) result set is never truncated into a partial last line.
_OUTPUT_LIMIT = 16000


class GrepTool:
    """Search file CONTENTS across the workspace by regular expression."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
        workspace_io=True,
        catalog_summary="在工作区搜正文",
        blurb="按关键词扫文件内容，给出命中行",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="grep",
            description=(
                "用正则搜工作区文件内容。返回 `path:line: text`。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "要搜索的正则表达式（ripgrep / Rust regex 语法）。",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "相对目录或单文件，"
                            "或本机绝对路径 / ~/Desktop|Downloads|Documents。"
                        ),
                        "default": ".",
                    },
                    "glob": {
                        "type": "string",
                        "description": "按文件名过滤。",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "不区分大小写匹配。",
                        "default": False,
                    },
                    "files_only": {
                        "type": "boolean",
                        "description": "只回文件路径与匹配数。",
                        "default": False,
                    },
                },
                "required": ["pattern"],
            },
            face=ToolFace.SEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()

        pattern = arguments.get("pattern") or ""
        if not pattern:
            return _fail("缺少必填参数：pattern", start)

        # Regex validity is authoritative in ripgrep (Rust regex), not Python re.
        rel_dir = arguments.get("path") or "."
        files_only = bool(arguments.get("files_only", False))
        max_results = _DEFAULT_MAX_RESULTS

        from agentcore.tools.builtin.file_ops.prepare_path import prepare_tool_path

        prepared = await prepare_tool_path(
            rel_dir, context, start=start, as_directory=True
        )
        if isinstance(prepared, ToolResult):
            return prepared
        rel_dir = prepared

        query = GrepQuery(
            pattern=pattern,
            directory=rel_dir,
            glob=arguments.get("glob") or None,
            case_insensitive=bool(arguments.get("case_insensitive")),
            files_only=files_only,
            max_results=max_results,
        )

        try:
            result = await context.backend.grep(query)
        except OutsideWorkspace as e:
            return _fail(
                _outside_workspace_msg(
                    rel_dir, location=context.backend.location, reason=str(e)
                ),
                start,
                metadata={CROSS_TURN_RETRY_KEY: CrossTurnRetry.FUTILE.value},
            )
        except PathNotFound:
            base = f"路径不存在：{rel_dir}"
            return _fail(
                await enrich_missing_path_message(context, str(rel_dir), base=base),
                start,
            )
        except WorkspaceError as e:
            kind = workspace_channel_failure_kind(e)
            msg = str(e)
            if kind == "presence":
                return _fail(
                    channel_dead_error_message(msg),
                    start,
                    metadata=channel_dead_retire_metadata(),
                )
            if kind == "reconnect":
                return _fail(
                    msg,
                    start,
                    metadata={CROSS_TURN_RETRY_KEY: CrossTurnRetry.NOT_FUTILE.value},
                )
            if kind == "liveness":
                return _fail(
                    (
                        f"本地工作区通道操作超时（活性挂起）：{msg}。"
                        "请缩小范围或换策略后重试；禁止原样重试同一操作。"
                    ),
                    start,
                    metadata={
                        **op_liveness_timeout_metadata(),
                        CROSS_TURN_RETRY_KEY: CrossTurnRetry.NOT_FUTILE.value,
                    },
                )
            # Surface regex failures without the generic "搜索失败" wrapper so the
            # model sees the dialect hint immediately.
            if "正则" in msg:
                return _fail(msg, start)
            # Access permission: permanent for this tool this run (retire on first hit).
            if _is_access_permission_error(msg):
                return _fail(
                    f"搜索失败：{e}",
                    start,
                    policy_failure=True,
                    error_class="permission",
                    permission_kind="access",
                    retire_tools=["grep"],
                    retire_message=(
                        "工具 `grep` 因无访问权限已停用——请改用已授权路径/工具，"
                        "禁止原样重试 grep。"
                    ),
                )
            return _fail(f"搜索失败：{e}", start)

        output = _render(
            pattern=pattern,
            rel_dir=rel_dir,
            glob=arguments.get("glob") or "",
            result=result,
            files_only=files_only,
        )
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            output_limit=_OUTPUT_LIMIT,
            metadata={
                "match_count": result.total_matches,
                "file_count": len(result.file_counts),
            },
        )


def _fail(
    error: str,
    start: float,
    *,
    policy_failure: bool = False,
    error_class: str | None = None,
    permission_kind: str | None = None,
    retire_tools: list[str] | None = None,
    retire_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    meta: dict[str, Any] = dict(metadata or {})
    if policy_failure:
        meta["policy_failure"] = True
    if error_class:
        meta["error_class"] = error_class
    if permission_kind:
        meta["permission_kind"] = permission_kind
    if retire_tools:
        meta["retire_tools"] = list(retire_tools)
    if retire_message:
        meta["retire_message"] = retire_message
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        duration_ms=int((time.monotonic() - start) * 1000),
        metadata=meta,
    )


def _is_access_permission_error(msg: str) -> bool:
    text = (msg or "").lower()
    return (
        "没有访问权限" in (msg or "")
        or "permission denied" in text
        or "access is denied" in text
        or "access denied" in text
    )


def _empty_result_note(*, pattern: str, rel_dir: str, glob: str) -> str:
    """Honest empty-success note. Empty ≠ 不存在; do not teach the next tool."""
    scope = "" if rel_dir in ("", ".") else f"（在 '{rel_dir}' 下）"
    glob_note = f"（文件名匹配 '{glob}'）" if glob else ""
    return f"本次 grep 未匹配 /{pattern}/{scope}{glob_note}。不要据此断定代码不存在。"


def _render(
    *,
    pattern: str,
    rel_dir: str,
    glob: str,
    result: GrepResult,
    files_only: bool,
) -> str:
    if files_only:
        lines = [rel for rel, _count in result.file_counts]
        summary = f"{len(result.file_counts)} 个文件匹配 /{pattern}/"
    else:
        lines = [f"{h.path}:{h.line_no}: {h.text}" for h in result.hits]
        summary = (
            f"{result.total_matches} 处匹配，分布在 "
            f"{len(result.file_counts)} 个文件中（/{pattern}/）"
        )

    warn_block = ""
    if result.warnings:
        warn_block = "\n" + "\n".join(f"⚠ {w}" for w in result.warnings)

    if not lines:
        return _empty_result_note(pattern=pattern, rel_dir=rel_dir, glob=glob) + warn_block

    body = "\n".join(lines)
    if result.truncated:
        body += "\n[结果已截断——请收窄 path/glob 或细化 pattern]"
    return f"{summary}\n{body}{warn_block}"
