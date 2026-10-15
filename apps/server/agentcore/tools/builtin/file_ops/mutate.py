"""Mutating file tools: write / edit."""

from __future__ import annotations

import errno
import time
from difflib import SequenceMatcher
from typing import Any, Literal

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.tools.cleared_write_stub import cleared_write_stub_rejection
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
from agentcore.workspace.text_replace import (
    TextReplaceNoMatch,
    TextReplaceOk,
    apply_text_replace,
)

from .errors import (
    STR_REPLACE_AMBIGUOUS_USER_FACE,
    STR_REPLACE_NO_MATCH_USER_FACE,
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
    _reject_write_scope,
    classify_write_kind,
    format_artifact_manifest,
    prepared_write_relpath,
    stale_overwrite_rejection,
)
from .read import _format_numbered_lines

logger = get_logger(__name__)

# 写类工具「回显结果」：worker 写 / 替换后，常会为「确认写对没」再花一整轮 read 回读自检
# （trace 4d715ea0 实测：多 worker 读→改→回读→handoff，那一轮回读零信息增量）。
# Artifact-first：写成功回执 = artifact manifest（path/chars/lines/hash/标题树/末段预览），
# 并硬拒对本 run 已落盘 path 的 body read。
# 回显有界（行数 + 字符双上限），大文件不炸 token。
_EDIT_ECHO_CONTEXT = 3
_EDIT_ECHO_MAX_LINES = 24
# edit 失败回执：从磁盘带回有界片段（编辑以盘为真源）。
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


def _old_string_nonempty_lines(old_string: str) -> list[str]:
    return [ln for ln in old_string.replace("\r\n", "\n").splitlines() if ln.strip()]


def _is_short_str_replace_anchor(old_string: str) -> bool:
    """Fuzzy near-miss receipts only for a single nonempty line (punctuation drift)."""
    return len(_old_string_nonempty_lines(old_string)) <= 1


def _fuzzy_line_candidates(
    content: str, old_string: str
) -> list[tuple[float, int, list[str]]]:
    """Bounded fuzzy regions near a short ``old_string`` (score, start_1based, lines)."""
    lines = content.splitlines()
    if not lines:
        return []
    old_lines = _old_string_nonempty_lines(old_string)
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
) -> str:
    header = f"—— {label} · 约第 {start_line} 行起 ——"
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
    """Disk-backed failure receipt for ``edit`` (bounded snippets).

    Backend still raises ``NoMatch`` / ``AmbiguousMatch``; this only enriches the tool
    error so the model can re-anchor from disk instead of inventing a skeleton rewrite.
    """
    preview = (
        f"\n你提供的 old_string 预览：\n```\n{_old_string_preview(old_string)}\n```"
    )
    short_anchor = _is_short_str_replace_anchor(old_string)
    if kind == "no_match":
        head = (
            f"在 {rel_path} 中找不到 old_string；它必须与磁盘文件完全一致，"
            "包括空白与缩进。"
        ) + preview
        if short_anchor:
            head += "\n以下为磁盘邻近原文（真源；非精确命中，勿当已匹配）："
        else:
            head += (
                "\n整段 old_string 均不在文件中（不是某一行像不像）。"
                "请对照写回执 end_preview 重写精确锚，或先 read 再 edit。"
            )
    else:
        head = (
            f"old_string 在 {rel_path} 中不唯一（匹配 {match_count} 处）。请补充"
            "更多上下文以锁定单一片段，或设置 replace_all=true。"
            f"{preview}\n以下为磁盘精确命中（真源）："
        )

    try:
        content = await context.backend.read(rel_path)
    except WorkspaceError as e:
        return (
            f"{head}\n（无法读取磁盘：{e}）\n"
            "请 escalate 或改用其它路径；优先对照盘文再 edit，"
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
    elif short_anchor:
        for i, (score, start, region) in enumerate(
            _fuzzy_line_candidates(content, old_string), start=1
        ):
            if score == 0.0 and i == 1:
                label = "文件开头"
            elif i == 1:
                label = "邻近原文（非精确命中）"
            else:
                label = f"邻近原文 #{i}（非精确命中）"
            blocks.append(
                _format_fail_snippet_block(
                    label=label,
                    start_line=start,
                    region=region,
                )
            )

    if kind == "no_match" and not short_anchor:
        guidance = (
            "\n确需整文件覆盖可用 write（须完整正文，勿残缺骨架交差）；"
            "仍对不上则 escalate。"
        )
    else:
        guidance = (
            "\n请对照上方盘片段重写精确 old_string 后再 edit；"
            "确需整文件覆盖可用 write（须完整正文，勿残缺骨架交差）；仍对不上则 escalate。"
        )
    joined = "\n\n".join(blocks)
    return head + ("\n\n" + joined if joined else "") + guidance


def _is_str_replace_post_state(
    current: str, old_string: str, new_string: str, *, all_: bool
) -> bool:
    """True when ``current`` is already the result of applying old→new once.

    Inverse unique/replace_all (new→old then old→new) covers the case where
    ``new_string`` still contains ``old_string`` (``foo`` → ``foo bar``). Empty
    ``new_string`` (span delete) is already applied when old no longer matches.
    Uses the same EOL fallback as ``backend.replace``.
    """
    if not new_string:
        outcome = apply_text_replace(current, old_string, "", all_=all_)
        return isinstance(outcome, TextReplaceNoMatch)
    undone = apply_text_replace(current, new_string, old_string, all_=all_)
    if not isinstance(undone, TextReplaceOk):
        return False
    redone = apply_text_replace(undone.content, old_string, new_string, all_=all_)
    return isinstance(redone, TextReplaceOk) and redone.content == current


async def _already_applied_str_replace(
    context: ToolContext,
    rel_path: str,
    *,
    old_string: str,
    new_string: str,
    start: float,
    rename_note: str,
    replace_all: bool = False,
) -> ToolResult | None:
    """Succeed without rewriting when this replace already landed on disk.

    Same as ``write`` identical-body skip: live retries and crash replay
    both no-op when the unique/replace_all post-state already holds. Genuine
    NoMatch (old never matched and inverse cannot reconstruct) stays an error.
    """
    try:
        current = await context.backend.read(rel_path)
    except (PathNotFound, NotAFile, NotUTF8, WorkspaceError, OSError):
        return None
    if not _is_str_replace_post_state(
        current, old_string, new_string, all_=replace_all
    ):
        return None
    _mark_landed_files(context, rel_path)
    rename_suffix = f"。{rename_note}" if rename_note else ""
    return ToolResult(
        tool_call_id="",
        success=True,
        output=(
            f"已在 {rel_path} 替换（盘上已是替换后的文本，未再改写）"
            f"{rename_suffix}"
        ),
        duration_ms=int((time.monotonic() - start) * 1000),
        metadata={"replacements": 0, "already_applied": True},
        file_products=[file_product(rel_path)],
    )


class FileWriteTool:
    """Write content to a file within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
        workspace_io=True,
        catalog_summary="写工作区文件",
        blurb="新建文件，或整篇覆盖已有内容",
    )

    @property
    def schema(self) -> ToolSchema:
        # Schema layer: 这是什么。
        return ToolSchema(
            name="write",
            description=(
                "写入文件。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "工作区内的相对文件路径。",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整正文。",
                    },
                },
                "required": ["file_path", "content"],
            },
            face=ToolFace.FILE,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        stub_err = cleared_write_stub_rejection(arguments)
        if stub_err is not None:
            return _error(stub_err, start, contract_failure=True)

        requested_path = arguments.get("file_path", "")
        content = arguments.get("content", "")

        # A missing/empty path resolves to the workspace root (a directory); writing
        # onto it raises a cryptic OS error (Permission denied / IsADirectory) that
        # leaks the absolute server path and gives the model nothing to act on. Fail
        # fast with the required-arg message instead (parity with edit/move).
        if not requested_path:
            return _error(
                "file_path 不能为空：请提供工作区内的相对文件路径（如 report.md）",
                start,
            )

        prepared = await prepared_write_relpath(
            requested_path, context, host_grant_mode="attach_rw"
        )
        if isinstance(prepared, ToolResult):
            return prepared
        rel_path, rename_note = prepared
        if not rel_path:
            return _error(
                "file_path 不能为空：请提供工作区内的相对文件路径（如 report.md）",
                start,
            )

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="write.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        # Occupancy is this tool call (disk serial + CAS). Run-lifetime ledger
        # owners never refuse a write.
        denied, release_on_fail = _claim_write_path(
            context, rel_path, event="write.collision", start=start
        )
        if denied is not None:
            return denied
        coordinator = context.write_coordinator

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

        if old_content is not None and old_content == content:
            kind = classify_write_kind(content)
            path_key = _norm_rel_path(rel_path)
            output = format_artifact_manifest(
                path=rel_path,
                content=content,
                chars_written=len(content),
                kind=kind,
                action="write",
            )
            if rename_note:
                output = f"{output}\n{rename_note}"
            _mark_landed_files(context, path_key, kind=kind)
            return ToolResult(
                tool_call_id="",
                success=True,
                output=output,
                duration_ms=int((time.monotonic() - start) * 1000),
                file_products=[file_product(rel_path)],
                metadata={"already_applied": True},
            )

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
                logger.info("write.stale_overwrite_rejected", path=rel_path)
                return _error(
                    stale_overwrite_rejection(rel_path),
                    start,
                    contract_failure=True,
                )

        try:
            written = await context.backend.write(rel_path, content)
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

        kind = classify_write_kind(content)
        path_key = _norm_rel_path(rel_path)
        output = format_artifact_manifest(
            path=rel_path,
            content=content,
            chars_written=written,
            kind=kind,
            action="write",
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
        return result


class StrReplaceTool:
    """Replace an exact text span in an existing workspace file (precise edit)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
        workspace_io=True,
        catalog_summary="改工作区文件里的一段",
        blurb="只替换其中几行，不整篇重写",
    )

    @property
    def schema(self) -> ToolSchema:
        # Schema layer: 这是什么。
        return ToolSchema(
            name="edit",
            description="精确替换已有文件中【完全匹配】的文本片段。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "工作区内的相对文件路径。",
                    },
                    "old_string": {
                        "type": "string",
                        "minLength": 1,
                        "description": "要替换的精确文本。",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "替换后的文本（必须与 old_string 不同）。",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "替换所有出现处。",
                        "default": False,
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
            face=ToolFace.FILE,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        stub_err = cleared_write_stub_rejection(arguments)
        if stub_err is not None:
            return _error(stub_err, start, contract_failure=True)

        rel_path = arguments.get("file_path", "")
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
            return _error("file_path 不能为空：请提供工作区内的相对文件路径", start)

        prepared = await prepared_write_relpath(rel_path, context, host_grant_mode="attach_rw")
        if isinstance(prepared, ToolResult):
            return prepared
        rel_path, rename_note = prepared

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="edit.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        denied, release_on_fail = _claim_write_path(
            context, rel_path, event="edit.collision", start=start
        )
        if denied is not None:
            return denied
        coordinator = context.write_coordinator

        applied = await _already_applied_str_replace(
            context,
            rel_path,
            old_string=old_string,
            new_string=new_string,
            start=start,
            rename_note=rename_note,
            replace_all=replace_all,
        )
        if applied is not None:
            return applied

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
            return _path_missing_error(
                f"文件不存在：{rel_path}", start, path=rel_path
            )
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
            applied = await _already_applied_str_replace(
                context,
                rel_path,
                old_string=old_string,
                new_string=new_string,
                start=start,
                rename_note=rename_note,
                replace_all=replace_all,
            )
            if applied is not None:
                return applied
            # 失败回执自带有界盘片段，模型可凭回执改 old_string。
            receipt = await _assemble_str_replace_fail_receipt(
                context, rel_path, old_string, kind="no_match"
            )
            return _error(
                receipt,
                start,
                product_face=STR_REPLACE_NO_MATCH_USER_FACE,
            )
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
            return _error(
                receipt,
                start,
                product_face=STR_REPLACE_AMBIGUOUS_USER_FACE,
            )
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
        return result
