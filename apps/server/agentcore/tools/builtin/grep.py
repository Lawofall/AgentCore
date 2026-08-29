"""Built-in tool: grep — regex search over workspace file CONTENTS.

Complements the rest of the file family: ``glob`` finds files by NAME,
``file_list`` lists one known-directory layer, and ``file_read`` opens one
file, while ``grep`` finds WHERE a string / symbol / pattern appears across
many files, returning ripgrep-style ``path:line: text`` hits the model can
then open with ``file_read``.

Thin shell over ``ToolContext.backend``: this tool builds a ``GrepQuery`` and
renders the bounded ``GrepResult`` the backend returns. Search itself is
**embedded ripgrep** (Rust regex dialect) on both cloud and desktop backends —
no Python/JS walk fallback.
"""

import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.facts import CROSS_TURN_RETRY_KEY, CrossTurnRetry
from agentcore.tools.builtin.file_ops.errors import _outside_workspace_msg
from agentcore.tools.builtin.file_ops.path_hints import enrich_missing_path_message
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
    is_channel_dead_detail,
    is_liveness_timeout_detail,
    op_liveness_timeout_metadata,
)
from agentcore.workspace.protocol import (
    GrepQuery,
    GrepResult,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)

_DEFAULT_MAX_RESULTS = 50
_MAX_RESULTS_CAP = 200
# grep output is line-oriented and denser than the 4000-char default; lift it so
# a full (already capped) result set is never truncated into a partial last line.
_OUTPUT_LIMIT = 16000


class GrepTool:
    """Search file CONTENTS across the workspace by regular expression."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="grep",
            description=(
                "用正则精确搜索工作区文件【内容】（ripgrep / Rust regex）。"
                "适合确切符号名、字符串或模式（如 `ApprovalGate`、`TODO`）；"
                "返回 `path:line: text`，命中后单文件默认 file_read 整读；"
                "仅页脚已截断或已有行号时开窗，禁止整目录通读。"
                "概念/意图定位请用 code_search——两工具并存。"
                "按文件名或路径找文件用 `glob`（勿先猜目录）。"
                "不确定位置时省略 path（默认整仓）；禁止猜测 src/、@scope、app/。"
                "仅本回合已证实存在的目录或文件才填 path。"
                "跳过二进制与噪音目录。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "要搜索的正则表达式（ripgrep / Rust regex 语法）。"
                            "禁止把字面 \\n 当正则；不支持 lookahead/lookbehind（`(?!` `(?=`）。"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "搜索范围：工作区相对 POSIX【目录】或【单个文件】"
                            "（默认 `.`=整仓）。不确定时省略，不要猜测 src/、"
                            "packages/@scope、app/ 等惯例路径。"
                            "`.`/省略=根；`/<根标签>/…` 与裸 `/`、`\\` 视为根；"
                            "其它绝对路径（/etc、盘符）拒绝。"
                            "仅填本回合已证实存在的路径：目录则递归其下；"
                            "单个文件则只搜该文件（类似 `rg PATTERN FILE`，此时 glob 被忽略）。"
                        ),
                        "default": ".",
                    },
                    "glob": {
                        "type": "string",
                        "description": (
                            "可选：按【文件名】过滤，如 '*.py' 或 '*.ts'。开头的 "
                            "'**/' 或目录前缀会被忽略，只匹配文件名。"
                        ),
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "不区分大小写匹配（默认 false）。",
                        "default": False,
                    },
                    "files_only": {
                        "type": "boolean",
                        "description": (
                            "只返回匹配到的文件列表及每个文件的匹配数，而非匹配行（默认 false）。"
                        ),
                        "default": False,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            "返回的最大匹配行数（files_only 模式下为文件数）。默认 50，最多 200。"
                        ),
                    },
                },
                "required": ["pattern"],
            },
            category=ToolCategory.FILESYSTEM,
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
        try:
            raw = int(arguments.get("max_results", _DEFAULT_MAX_RESULTS))
            max_results = max(1, min(raw, _MAX_RESULTS_CAP))
        except (TypeError, ValueError):
            max_results = _DEFAULT_MAX_RESULTS

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
            msg = str(e)
            if is_channel_dead_detail(msg):
                return _fail(
                    channel_dead_error_message(msg),
                    start,
                    metadata=channel_dead_retire_metadata(),
                )
            if is_liveness_timeout_detail(msg):
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
    """Actionable empty-success note (align with web_search: success + 促重拟).

    Does not silently call another tool — feedback only.
    """
    scope = "" if rel_dir in ("", ".") else f"（在 '{rel_dir}' 下）"
    glob_note = f"（文件名匹配 '{glob}'）" if glob else ""
    tips = (
        "可执行下一步：① 不确定位置则省略 path 从根再搜，勿猜 src/@scope；"
        "② 换更短/同义的 pattern，或开 case_insensitive；"
        "③ 若是概念/意图而非确切字符串，改用 code_search；"
        "④ 按文件名找用 glob。"
    )
    return f"本次 grep 未匹配 /{pattern}/{scope}{glob_note}。不要据此断定代码不存在。{tips}"


def _render(
    *,
    pattern: str,
    rel_dir: str,
    glob: str,
    result: GrepResult,
    files_only: bool,
) -> str:
    if files_only:
        lines = [f"{rel}: {count}" for rel, count in result.file_counts]
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
