"""Mutating file tools: write / append / str_replace."""

from __future__ import annotations

import errno
import time
from difflib import SequenceMatcher
from typing import Any, Literal

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.engine.write_args_clear import cleared_write_stub_rejection
from agentcore.tools.builtin.write_diagnostics import attach_write_diagnostics
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.protocol import (
    AmbiguousMatch,
    NoMatch,
    NotAFile,
    NotUTF8,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)

from .errors import (
    _error,
    _maybe_channel_dead_error,
    _outside_workspace_error,
    _path_missing_error,
    _write_io_error,
)
from .integrity import (
    _claim_write_path,
    _mark_landed_files,
    _norm_rel_path,
    _prepare_write_relpath,
    _reject_write_scope,
    classify_write_kind,
    format_artifact_manifest,
    has_skeleton_markers,
    is_skeleton_content,
    prose_append_rejection,
    stale_overwrite_rejection,
)
from .read import _format_numbered_lines

logger = get_logger(__name__)

# 写类工具「回显结果」：worker 写 / 追加 / 替换后，常会为「确认写对没」再花一整轮 read 回读自检
# （trace 4d715ea0 实测：8 个 append worker 全是 读→追加→回读→handoff，那一轮回读零信息增量）。
# Artifact-first：写/append 成功回执 = artifact manifest（path/chars/lines/hash/标题树/末段预览），
# 并硬拒对本 run 已落盘 path 的 body file_read；成篇 prose 后同 path append 亦硬拒。
# 回显有界（行数 + 字符双上限），大文件不炸 token。
_EDIT_ECHO_CONTEXT = 3
_EDIT_ECHO_MAX_LINES = 24
# str_replace 失败回执：从磁盘带回有界片段（编辑以盘为真源）。
_EDIT_FAIL_CONTEXT = 3
_EDIT_FAIL_MAX_LINES = 24
_EDIT_FAIL_FUZZY_MAX = 3
_EDIT_FAIL_FUZZY_MIN_RATIO = 0.45
_EDIT_FAIL_OLD_PREVIEW_CHARS = 160
# 不唯一时：邻行扩到「整段在文件中只出现一次、且只含这一处 old_string」。
# 不自动改盘（两处都合法时不能猜）；只把可复制锚交给下一刀。
_UNIQUE_ANCHOR_MAX_EXTRA = 4
_UNIQUE_ANCHOR_MAX_CHARS = 800

def _region_slice(
    lines: list[str], center_idx0: int, *, context: int, max_lines: int
) -> tuple[int, list[str]]:
    """Return ``(start_line_1based, sliced_lines)`` around ``center_idx0``."""
    half = min(context, max(0, (max_lines - 1) // 2))
    start0 = max(0, center_idx0 - half)
    end0 = min(len(lines), start0 + max_lines)
    start0 = max(0, end0 - max_lines)
    return start0 + 1, lines[start0:end0]


def _old_string_preview(old_string: str) -> str:
    from agentcore.core.secrets import redact_secrets

    text = old_string.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > _EDIT_FAIL_OLD_PREVIEW_CHARS:
        text = text[:_EDIT_FAIL_OLD_PREVIEW_CHARS] + "…"
    # 案 B：失败回执不得回显完整 API Key。
    return redact_secrets(text)


def _fuzzy_line_candidates(
    content: str, old_string: str
) -> list[tuple[float, int, list[str]]]:
    """Bounded fuzzy regions near ``old_string`` anchors (score, start_1based, lines)."""
    lines = content.splitlines()
    if not lines:
        return []
    old_lines = [ln for ln in old_string.replace("\r\n", "\n").splitlines() if ln.strip()]
    if not old_lines:
        start, region = _region_slice(
            lines, 0, context=0, max_lines=_EDIT_FAIL_MAX_LINES
        )
        return [(0.0, start, region)]

    scored: list[tuple[float, int]] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        best = max(
            SequenceMatcher(None, line, ol).ratio() for ol in old_lines
        )
        if best >= _EDIT_FAIL_FUZZY_MIN_RATIO:
            scored.append((best, i))
    scored.sort(key=lambda t: (-t[0], t[1]))

    out: list[tuple[float, int, list[str]]] = []
    used: list[int] = []
    min_gap = max(1, _EDIT_FAIL_CONTEXT * 2)
    for score, idx in scored:
        if len(out) >= _EDIT_FAIL_FUZZY_MAX:
            break
        if any(abs(idx - u) < min_gap for u in used):
            continue
        start, region = _region_slice(
            lines,
            idx,
            context=_EDIT_FAIL_CONTEXT,
            max_lines=_EDIT_FAIL_MAX_LINES,
        )
        out.append((score, start, region))
        used.append(idx)

    if not out:
        start, region = _region_slice(
            lines, 0, context=0, max_lines=_EDIT_FAIL_MAX_LINES
        )
        out.append((0.0, start, region))
    return out


def _old_string_line_span(old_string: str) -> int:
    text = old_string.replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return 0
    if text.endswith("\n"):
        return text.count("\n")
    return text.count("\n") + 1


def _exact_match_offsets(content: str, old_string: str) -> list[int]:
    """Non-overlapping start offsets (same count口径 as workspace ``replace``)."""
    if not old_string:
        return []
    out: list[int] = []
    start = 0
    step = max(1, len(old_string))
    while True:
        idx = content.find(old_string, start)
        if idx < 0:
            return out
        out.append(idx)
        start = idx + step


def _match_first_region(
    lines: list[str], *, start_idx0: int, span: int
) -> tuple[int, list[str]]:
    """Snippet starts at the hit line so a short UI preview still shows old_string."""
    if not lines:
        return 1, []
    start0 = max(0, min(start_idx0, len(lines) - 1))
    end0 = min(len(lines), start0 + max(span, 1) + _EDIT_FAIL_CONTEXT)
    if end0 - start0 > _EDIT_FAIL_MAX_LINES:
        end0 = start0 + _EDIT_FAIL_MAX_LINES
    return start0 + 1, lines[start0:end0]


def _expand_unique_anchor(
    content: str, old_string: str, match_idx: int
) -> str | None:
    """Smallest neighbor window that is unique and contains this old_string once."""
    if not old_string or match_idx < 0 or match_idx >= len(content):
        return None
    raw_lines = content.splitlines(keepends=True)
    if not raw_lines:
        return None
    start_line = content[:match_idx].count("\n")
    span = _old_string_line_span(old_string)
    for extra_total in range(1, 2 * _UNIQUE_ANCHOR_MAX_EXTRA + 1):
        for before in range(0, extra_total + 1):
            after = extra_total - before
            if before > _UNIQUE_ANCHOR_MAX_EXTRA or after > _UNIQUE_ANCHOR_MAX_EXTRA:
                continue
            i0 = max(0, start_line - before)
            i1 = min(len(raw_lines), start_line + span + after)
            candidate = "".join(raw_lines[i0:i1])
            if not candidate or len(candidate) > _UNIQUE_ANCHOR_MAX_CHARS:
                continue
            if candidate.count(old_string) != 1:
                continue
            if content.count(candidate) == 1:
                return candidate
    return None


def _exact_match_regions(
    content: str, old_string: str, *, max_show: int = _EDIT_FAIL_FUZZY_MAX
) -> list[tuple[int, list[str]]]:
    """First ``max_show`` exact-match regions as ``(start_line_1based, lines)``."""
    lines = content.splitlines()
    if not lines or not old_string:
        return []
    span = _old_string_line_span(old_string)
    out: list[tuple[int, list[str]]] = []
    for idx in _exact_match_offsets(content, old_string)[:max_show]:
        line_idx0 = content[:idx].count("\n")
        start, region = _match_first_region(lines, start_idx0=line_idx0, span=span)
        out.append((start, region))
    return out


def _format_fail_snippet_block(
    *,
    label: str,
    start_line: int,
    region: list[str],
    score: float | None = None,
) -> str:
    score_note = ""
    if score is not None:
        score_note = f"（模糊相似度 {score:.0%}，非精确）"
    header = f"—— {label}{score_note} · 约第 {start_line} 行起 ——"
    body = _format_numbered_lines(region, start_line)
    return f"{header}\n{body}" if body else header


async def _assemble_str_replace_fail_receipt(
    context: ToolContext,
    rel_path: str,
    old_string: str,
    *,
    kind: Literal["no_match", "ambiguous"],
    match_count: int | None = None,
) -> str:
    """Disk-backed failure receipt for ``str_replace`` (bounded snippets).

    Backend still raises ``NoMatch`` / ``AmbiguousMatch``; this only enriches the tool
    error so the model can re-anchor from disk instead of inventing a skeleton rewrite.
    """
    if kind == "no_match":
        head = (
            f"在 {rel_path} 中找不到 old_string；它必须与磁盘文件完全一致，"
            "包括空白与缩进。"
        )
    else:
        head = (
            f"old_string 在 {rel_path} 中不唯一（匹配 {match_count} 处）。请补充"
            "更多上下文以锁定单一片段，或设置 replace_all=true。"
        )
    head += (
        f"\n你提供的 old_string 预览：\n```\n{_old_string_preview(old_string)}\n```"
        "\n以下为磁盘原文片段（真源；标明非精确的仅供锚定，勿当已匹配）："
    )

    try:
        content = await context.backend.read(rel_path)
    except WorkspaceError as e:
        return (
            f"{head}\n（无法读取磁盘：{e}）\n"
            "请 escalate 或改用其它路径；优先对照盘文再 str_replace，"
            "确需整盖须写出完整正文（勿残缺骨架交差）。"
        )

    blocks: list[str] = []
    if kind == "ambiguous" and old_string:
        offsets = _exact_match_offsets(content, old_string)
        regions = _exact_match_regions(content, old_string)
        for i, ((start, region), idx) in enumerate(
            zip(regions, offsets, strict=True), start=1
        ):
            block = _format_fail_snippet_block(
                label=f"精确命中 #{i}",
                start_line=start,
                region=region,
            )
            anchor = _expand_unique_anchor(content, old_string, idx)
            if anchor:
                from agentcore.core.secrets import redact_secrets

                block += (
                    "\n下一刀请把下面整段当作 old_string（邻行已补，文件中唯一）：\n"
                    f"```\n{redact_secrets(anchor)}\n```"
                )
            blocks.append(block)
        if match_count is not None and match_count > len(blocks):
            blocks.append(f"（另有 {match_count - len(blocks)} 处未列出）")
    else:
        for i, (score, start, region) in enumerate(
            _fuzzy_line_candidates(content, old_string), start=1
        ):
            label = "文件开头" if score == 0.0 and i == 1 else f"候选 #{i}"
            blocks.append(
                _format_fail_snippet_block(
                    label=label,
                    start_line=start,
                    region=region,
                    score=None if score == 0.0 else score,
                )
            )

    guidance = (
        "\n请对照上方盘片段重写精确 old_string 后再 str_replace；"
        "确需整文件覆盖可用 file_write（须完整正文，勿残缺骨架交差）；仍对不上则 escalate。"
    )
    return head + "\n\n" + "\n\n".join(blocks) + guidance

def _promote_research_landed_refs(rel_path: str, content: str) -> None:
    """方向笔记落盘后：正文已引用的台账 id 升 selected，供 CEO 汇总继承。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    norm = (rel_path or "").replace("\\", "/").lstrip("./")
    if not norm.startswith(RESEARCH_PREFIX) or not norm.endswith(".md"):
        return
    try:
        from agentcore.runtime.suspension import turn_evidence_ledger
    except Exception:  # noqa: BLE001
        return
    ledger = turn_evidence_ledger.get()
    if ledger is None:
        return
    try:
        newly = ledger.promote_refs_cited_in_landed_note(content)
    except Exception:  # noqa: BLE001 — 晋升失败不挡写入回执
        return
    if newly:
        logger.info(
            "evidence.promote_landed_note_refs",
            path=norm,
            newly=len(newly),
        )


def _maybe_inject_research_ledger_anchors(
    rel_path: str, content: str, context: ToolContext
) -> str:
    """约定文档 ``research/`` 落盘时若正文无 ``#rN``，用本 worker 台账条目补脚注（一层兜底）。"""
    from agentcore.workspace.stage_dirs import RESEARCH_PREFIX

    norm = (rel_path or "").replace("\\", "/").lstrip("./")
    if not norm.startswith(RESEARCH_PREFIX) or not norm.endswith(".md"):
        return content
    try:
        from agentcore.runtime.debate.research_dossier import (
            ensure_research_file_anchors,
        )
        from agentcore.runtime.suspension import turn_evidence_ledger
    except Exception:  # noqa: BLE001 — 导入失败不挡写入
        return content
    ledger = turn_evidence_ledger.get()
    if ledger is None:
        return content
    try:
        entries = list(ledger.all_entries())
    except Exception:  # noqa: BLE001
        return content
    registrant = f"worker:{context.agent_id}" if context.agent_id else ""
    mine = [
        e
        for e in entries
        if isinstance(e, dict)
        and (not registrant or str(e.get("registrant") or "") == registrant)
    ]
    # 本 worker 无登记时不跨员拼脚注（避免四路透镜互染）。
    if not mine:
        return content
    try:
        return ensure_research_file_anchors(content, mine)
    except Exception:  # noqa: BLE001
        logger.warning(
            "research.ledger_anchor_inject_failed",
            path=norm,
            error="ensure_failed",
        )
        return content

class FileWriteTool:
    """Write content to a file within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        # Schema layer: 这是什么 + HOW。落盘姿势在 consult(long_form_landing)。
        return ToolSchema(
            name="file_write",
            description=(
                "把内容写入文件：创建（含上级目录）或整体覆盖已有文件。"
                "HOW→consult(long_form_landing)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "工作区内的相对文件路径；约定文档区写盘扁平"
                            "（前缀后嵌套 `/` → `_` 单文件名）"
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整正文。",
                    },
                },
                "required": ["path", "content"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        stub_err = cleared_write_stub_rejection(arguments)
        if stub_err is not None:
            return _error(stub_err, start, contract_failure=True)

        requested_path = arguments.get("path", "")
        content = arguments.get("content", "")

        # A missing/empty path resolves to the workspace root (a directory); writing
        # onto it raises a cryptic OS error (Permission denied / IsADirectory) that
        # leaks the absolute server path and gives the model nothing to act on. Fail
        # fast with the required-arg message instead (parity with str_replace/move).
        if not requested_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径（如 report.md）", start)

        rel_path, rename_note = await _prepare_write_relpath(requested_path, context)
        if not rel_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径（如 report.md）", start)

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="file_write.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        # Occupancy is this tool call (disk serial + CAS). Run-lifetime ledger
        # owners never refuse a write.
        denied, release_on_fail = _claim_write_path(
            context, rel_path, event="file_write.collision", start=start
        )
        if denied is not None:
            return denied
        coordinator = context.write_coordinator

        # 幕1 约定文档落盘锚：AgentCore/文档/research/ 下若正文无 #rN，
        # 用本回合台账条目写脚注（一层兜底）。
        write_content = _maybe_inject_research_ledger_anchors(
            rel_path, content, context
        )

        # Pre-read for stale-overwrite CAS (concurrent writer).
        old_content: str | None = None
        try:
            old_content = await context.backend.read(rel_path)
        except PathNotFound:
            old_content = None
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                if coordinator is not None and release_on_fail:
                    coordinator.release(rel_path, context.run_id)
                return dead
            old_content = None
        except OSError as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            if getattr(e, "errno", None) == errno.ENAMETOOLONG:
                return _error(
                    f"文件名过长，无法写入 `{rel_path}`。"
                    "请改用更短的文件名（建议 ≤80 个汉字或英文词组）后重试。",
                    start,
                )
            return _error(f"读取既有文件失败：{e}", start, user_face=False)

        if old_content is not None:
            try:
                latest = await context.backend.read(rel_path)
            except PathNotFound:
                latest = None
            except WorkspaceError as e:
                dead = _maybe_channel_dead_error(e, start)
                if dead is not None:
                    return dead
                latest = None
            if latest != old_content:
                logger.info("file_write.stale_overwrite_rejected", path=rel_path)
                return _error(
                    stale_overwrite_rejection(rel_path),
                    start,
                    contract_failure=True,
                )

        try:
            written = await context.backend.write(rel_path, write_content)
        except OutsideWorkspace as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except WorkspaceError as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            return _write_io_error(e, start)
        except OSError as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            if getattr(e, "errno", None) == errno.ENAMETOOLONG:
                return _error(
                    f"文件名过长，无法写入 `{rel_path}`。"
                    "请改用更短的文件名（建议 ≤80 个汉字或英文词组）后重试。",
                    start,
                )
            return _write_io_error(e, start)

        _promote_research_landed_refs(rel_path, write_content)

        anchor_note = (
            "；已补写来源台账锚脚注"
            if write_content != content
            else ""
        )
        kind = classify_write_kind(write_content)
        path_key = _norm_rel_path(rel_path)
        output = format_artifact_manifest(
            path=rel_path,
            content=write_content,
            chars_written=written,
            kind=kind,
            action="write",
        )
        if rename_note:
            output = f"{output}\n{rename_note}"
        if anchor_note:
            output += anchor_note
        _mark_landed_files(context, path_key, kind=kind)
        result = ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            file_products=[file_product(rel_path)],
        )
        return await attach_write_diagnostics(result, context=context, path=rel_path)


class FileAppendTool:
    """Append content to the end of a file within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        # Schema layer: 这是什么 + HOW。落盘姿势在 consult(long_form_landing)。
        return ToolSchema(
            name="file_append",
            description=(
                "在文件末尾追加：不存在则创建（含上级目录）；已存在则拼接、不重写全文。"
                "HOW→consult(long_form_landing)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区内的相对文件路径。",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "要追加到文件末尾的内容（一节/一段为宜，不硬拒字数；"
                            "自行带好段落分隔，如 leading \\n\\n）。"
                            "禁止把已落盘短状态/清理占位原样当 content。"
                        ),
                    },
                },
                "required": ["path", "content"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        stub_err = cleared_write_stub_rejection(arguments)
        if stub_err is not None:
            return _error(stub_err, start, contract_failure=True)

        requested_path = arguments.get("path", "")
        content = arguments.get("content", "")

        if not requested_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径（如 report.md）", start)

        rel_path, rename_note = await _prepare_write_relpath(requested_path, context)

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="file_append.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        path_key = _norm_rel_path(rel_path)
        if context.landed_artifact_kinds.get(path_key) == "prose":
            return _error(
                prose_append_rejection(rel_path),
                start,
                contract_failure=True,
            )

        denied, release_on_fail = _claim_write_path(
            context, rel_path, event="file_append.collision", start=start
        )
        if denied is not None:
            return denied
        coordinator = context.write_coordinator

        # Pre-read: missing → create-via-append (allowed); existing skeleton → fill-in.
        old_content: str | None = None
        try:
            old_content = await context.backend.read(rel_path)
        except PathNotFound:
            old_content = None
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                if coordinator is not None and release_on_fail:
                    coordinator.release(rel_path, context.run_id)
                return dead
            old_content = None

        # Disk already looks like finished prose and this run wrote it as prose
        # is handled above. If disk is prose but not locked this run (扩写 / 他 run
        # 骨架)，仍放行。若本 run 未登记且盘上已是成篇、又无骨架标记——仍放行扩写。

        try:
            appended = await context.backend.append(rel_path, content)
        except OutsideWorkspace as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except NotAFile:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _error(f"不是文件：{rel_path}", start)
        except WorkspaceError as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            return _write_io_error(e, start, action="追加")

        try:
            merged = await context.backend.read(rel_path)
        except WorkspaceError:
            merged = (old_content or "") + (content or "")

        if old_content is None:
            # Created via append: classify the new body (skeleton fill-in vs prose dump).
            kind = classify_write_kind(merged)
        elif is_skeleton_content(old_content) or has_skeleton_markers(old_content):
            kind = "skeleton"
        elif path_key not in context.landed_artifact_kinds:
            # Pre-existing non-skeleton (扩写): land for read-reject, keep append-ok.
            kind = "skeleton"
        else:
            kind = context.landed_artifact_kinds.get(path_key) or "skeleton"

        output = format_artifact_manifest(
            path=rel_path,
            content=merged,
            chars_written=appended,
            kind=kind,
            action="append",
        )
        if rename_note:
            output = f"{output}\n{rename_note}"
        _mark_landed_files(context, path_key, kind=kind)
        result = ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            file_products=[file_product(rel_path)],
        )
        return await attach_write_diagnostics(result, context=context, path=rel_path)

class StrReplaceTool:
    """Replace an exact text span in an existing workspace file (precise edit)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        # Schema layer: 这是什么 + HOW。落盘姿势在 consult(long_form_landing)。
        return ToolSchema(
            name="str_replace",
            description=(
                "精确替换已有文件中【完全匹配】的文本片段。"
                "HOW→consult(long_form_landing)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区内的相对文件路径。",
                    },
                    "old_string": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "要替换的精确文本（不可为空），需带足够的上下文以在文件中唯一。"
                            "禁止把已落盘短状态/清理占位原样当参数。"
                        ),
                    },
                    "new_string": {
                        "type": "string",
                        "description": (
                            "替换后的文本（必须与 old_string 不同；"
                            "单次替换建议一节为宜，不硬拒字数）。"
                            "禁止把已落盘短状态/清理占位原样当 new_string。"
                        ),
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": ("替换所有出现处，而非要求唯一匹配（默认 false）。"),
                        "default": False,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        stub_err = cleared_write_stub_rejection(arguments)
        if stub_err is not None:
            return _error(stub_err, start, contract_failure=True)

        rel_path = arguments.get("path", "")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")
        replace_all = bool(arguments.get("replace_all", False))

        # 参数契约拒绝：空 / 无改动的 old_string 是零成本可修正打回，须标
        # contract_failure，否则连续空参会烧穿 run 级工具熔断（warn→disable）。
        if not old_string:
            return _error(
                "old_string 不能为空：请填入磁盘文件中要替换的精确原文"
                "（含足够上下文以保证唯一匹配），不要传空字符串",
                start,
                contract_failure=True,
            )
        if old_string == new_string:
            return _error(
                "old_string 与 new_string 相同，没有需要改动的内容。"
                "请改用实质不同的替换，或 handoff 诚实说明已改/未改；"
                "禁止用相同参数空转重试。",
                start,
                contract_failure=True,
            )

        if not rel_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径", start)

        rel_path, rename_note = await _prepare_write_relpath(rel_path, context)

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="str_replace.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        denied, release_on_fail = _claim_write_path(
            context, rel_path, event="str_replace.collision", start=start
        )
        if denied is not None:
            return denied
        coordinator = context.write_coordinator

        try:
            outcome = await context.backend.replace(
                rel_path, old_string, new_string, all_=replace_all
            )
        except OutsideWorkspace as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _path_missing_error(f"文件不存在：{rel_path}", start)
        except NotAFile:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _error(f"不是文件：{rel_path}", start)
        except NotUTF8:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _error(f"无法编辑二进制 / 非 UTF-8 文件：{rel_path}", start)
        except NoMatch:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            # 失败回执自带有界盘片段，模型可凭回执改 old_string。
            receipt = await _assemble_str_replace_fail_receipt(
                context, rel_path, old_string, kind="no_match"
            )
            return _error(receipt, start)
        except AmbiguousMatch as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            receipt = await _assemble_str_replace_fail_receipt(
                context,
                rel_path,
                old_string,
                kind="ambiguous",
                match_count=e.count,
            )
            return _error(receipt, start)
        except WorkspaceError as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            return _write_io_error(e, start)

        loc = "" if outcome.first_line is None else f"（约第 {outcome.first_line} 行）"
        # 回显改动落点的上下文（所改即所见），免得 worker 为「确认替换落对没」再花一轮 read 回读
        # （见本模块顶部说明）。有界：落点前后各 _EDIT_ECHO_CONTEXT 行 + 新增行数，封顶 MAX_LINES。
        echo = ""
        if outcome.first_line is not None:
            from agentcore.core.secrets import redact_secrets

            region = await context.backend.read_lines(
                rel_path,
                offset=max(1, outcome.first_line - _EDIT_ECHO_CONTEXT),
                limit=min(
                    _EDIT_ECHO_CONTEXT * 2 + 1 + new_string.count("\n"),
                    _EDIT_ECHO_MAX_LINES,
                ),
            )
            # 案 B：落点回显不得带出完整 API Key。
            echo = "。改动落点（已落盘，无需再读回确认）：\n" + redact_secrets(
                _format_numbered_lines(region.lines, region.start_line)
            )
        _mark_landed_files(context, rel_path)
        rename_suffix = f"。{rename_note}" if rename_note else ""
        result = ToolResult(
            tool_call_id="",
            success=True,
            output=f"已在 {rel_path} 替换 {outcome.count} 处{loc}{echo}{rename_suffix}",
            duration_ms=int((time.monotonic() - start) * 1000),
            metadata={"replacements": outcome.count},
            file_products=[file_product(rel_path)],
        )
        return await attach_write_diagnostics(result, context=context, path=rel_path)
