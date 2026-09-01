"""Built-in tool: code_search — BM25 symbol-level code search.

Complements ``grep``: ``grep`` finds exact regex matches line-by-line; ``code_search``
indexes functions/classes/methods (tree-sitter) and ranks by BM25 so the model can
locate code by concept or keyword across files.

Index build/refresh is owned by ``IndexMaintainer`` (write mutations /
``code_search`` kicks when the snapshot is not ready). This tool is
query-only against the current snapshot and schedules background
maintenance without awaiting ``ensure_code_index``.
"""

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
from agentcore.workspace.indexing.bm25 import tokenize_query
from agentcore.workspace.protocol import CodeIndexStatus, CodeSearchResult, WorkspaceError

_DEFAULT_MAX_RESULTS = 10
_MAX_RESULTS_CAP = 50
_OUTPUT_LIMIT = 16000

CODE_SEARCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "概念 / 意图查询（自然语言或关键词，如「审批门控」「User model」）。"
            ),
        },
        "language": {
            "type": "string",
            "description": "可选：按语言过滤，如 python、typescript、tsx。",
        },
        "path_prefix": {
            "type": "string",
            "description": (
                "搜索范围：工作区相对 POSIX 目录前缀（默认 `.`=整仓）。"
                "不确定时省略；禁止猜测 src/、@scope、app/。"
                "`.`/省略=根；`/<根标签>/…` 与裸 `/`、`\\` 视为根；"
                "其它绝对路径（/etc、盘符）拒绝。"
            ),
            "default": ".",
        },
        "max_results": {
            "type": "integer",
            "description": (
                f"返回的最大结果数（默认 {_DEFAULT_MAX_RESULTS}，最多 {_MAX_RESULTS_CAP}）。"
            ),
        },
    },
    "required": ["query"],
}


class CodeSearchTool:
    """Search workspace code by intent (BM25 over symbol-level chunks)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.READ_ONLY,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="code_search",
            description=(
                "按概念/意图搜索工作区代码（BM25 符号块）。"
                "精确符号、字符串或正则用 grep。"
            ),
            parameters=CODE_SEARCH_PARAMETERS,
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.NEVER,
            timeout_seconds=30.0,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        query = (arguments.get("query") or "").strip()
        if not query:
            return _fail("缺少必填参数：query", start)

        try:
            raw = int(arguments.get("max_results", _DEFAULT_MAX_RESULTS))
            max_results = max(1, min(raw, _MAX_RESULTS_CAP))
        except (TypeError, ValueError):
            max_results = _DEFAULT_MAX_RESULTS

        language = arguments.get("language") or None
        path_prefix = arguments.get("path_prefix") or "."

        # Query-only: never call ensure_code_index on the tool path.
        try:
            result = await context.backend.code_search(
                query,
                language=language,
                path_prefix=path_prefix,
                max_results=max_results,
            )
        except WorkspaceError as e:
            return _fail(f"搜索失败：{e}", start)

        # Kick only when ensure can help (no snapshot / content dirty).
        # Truncated-only STALE must not re-scan — the file cap cannot heal.
        should_kick = False
        get_mgr = getattr(context.backend, "_get_index_manager", None)
        if callable(get_mgr):
            try:
                should_kick = bool(get_mgr().needs_background_ensure())
            except Exception:  # noqa: BLE001 — kick is best-effort
                should_kick = result.index_status == CodeIndexStatus.BUILDING
        else:
            should_kick = result.index_status == CodeIndexStatus.BUILDING
        if should_kick:
            kick = getattr(context.backend, "start_code_index_maintenance", None)
            if callable(kick):
                kick()

        output = _render(result, query=query, path_prefix=path_prefix)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            output_limit=_OUTPUT_LIMIT,
            metadata={
                "match_count": len(result.chunks),
                "index_status": str(result.index_status),
            },
        )


def _fail(error: str, start: float) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def _render(result: CodeSearchResult, *, query: str, path_prefix: str) -> str:
    status = result.index_status
    if not result.chunks:
        return _empty_result_note(
            query,
            path_prefix=path_prefix,
            status=status,
        )

    lines: list[str] = []
    for chunk, score in zip(result.chunks, result.scores, strict=True):
        symbol_part = ""
        if chunk.symbol:
            symbol_part = f"  {chunk.symbol}"
            if chunk.symbol_type:
                symbol_part += f" ({chunk.symbol_type})"
        header = (
            f"{chunk.path}:{chunk.start_line}-{chunk.end_line}{symbol_part} "
            f"({chunk.language})"
        )
        preview = chunk.snippet.replace("\n", "\n  ")
        lines.append(f"{header}\n  {preview}\n  score={score:.2f}")

    summary = (
        f"（共 {len(result.chunks)} 条结果；单文件默认 file_read 整读；"
        "仅页脚已截断或已有行号时开窗）"
    )
    body = "\n\n".join(lines) + f"\n\n{summary}"
    body += _status_footer(status)
    return body


def _status_footer(status: CodeIndexStatus) -> str:
    if status == CodeIndexStatus.READY:
        return ""
    if status == CodeIndexStatus.BUILDING:
        return "\n⚠️ 代码索引尚无可用快照（首次构建中）；请改用 grep，勿空等。"
    return "\n⚠️ 索引可能过旧或不完整，建议配合 grep 验证。"


def _empty_result_note(
    query: str,
    *,
    path_prefix: str,
    status: CodeIndexStatus,
) -> str:
    """Actionable empty-success note (align with web_search: success + 促重拟).

    Does not silently call another tool — feedback only.
    """
    scope = "" if path_prefix in ("", ".") else f"（path_prefix='{path_prefix}'）"
    keywords = _grep_keyword_suggestions(query)
    kw_line = ""
    if keywords:
        quoted = "、".join(f"`{k}`" for k in keywords)
        kw_line = f"建议用 grep 精确搜这些关键词：{quoted}。"

    if status == CodeIndexStatus.BUILDING:
        return (
            f"代码索引尚无可用快照（首次构建中）{scope}，本次无可用命中。"
            f"请立刻改用 grep（精确符号/字符串），不要空等 code_search。"
            f"{kw_line}"
        )

    tips = (
        "可执行下一步：① 收窄或放宽 path_prefix / 去掉 language 过滤；"
        "② 换更短的概念词或同义改写后再 code_search；"
        "③ 若目标是确切符号/字符串，改用 grep；"
        "④ 确认 path_prefix 相对工作区根且存在。"
    )
    body = f"本次 code_search 未命中任何代码块{scope}。不要据此断定代码不存在。{kw_line}{tips}"
    if status == CodeIndexStatus.STALE:
        body += " ⚠️ 索引可能过旧或不完整，建议直接用 grep 验证。"
    return body


def _grep_keyword_suggestions(query: str, *, limit: int = 5) -> list[str]:
    """Prefer identifier-like tokens as grep pattern hints."""
    tokens = tokenize_query(query)
    ranked: list[tuple[int, str]] = []
    for t in tokens:
        if len(t) < 2:
            continue
        # Prefer snake/Camel identifiers over pure CJK bigrams.
        score = 0
        if "_" in t or (any(c.isupper() for c in t[1:]) and t[0].isalpha()):
            score = 3
        elif t.isascii() and t.isidentifier():
            score = 2
        elif any("\u4e00" <= ch <= "\u9fff" for ch in t) and len(t) >= 2:
            score = 1
        else:
            score = 1
        ranked.append((score, t))
    ranked.sort(key=lambda x: (-x[0], -len(x[1])))
    out: list[str] = []
    for _, t in ranked:
        if t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out
