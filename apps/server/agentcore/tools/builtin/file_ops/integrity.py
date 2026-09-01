"""Integrity & write-scope policy for file_ops (pure helpers + claim/landed).

Externally stable symbols (``has_omission_marker``, ``is_severe_shrink``,
``write_scope_rejection``, …) are re-exported from ``agentcore.tools.builtin.file_ops``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from agentcore.core.logging import get_logger
from agentcore.runtime.facts import CrossTurnRetry
from agentcore.tools.protocol import ToolContext, ToolResult

from .errors import _error

logger = get_logger(__name__)

# Artifact-first: skeleton vs prose (append lock), write_scope.
# Completeness heuristics are not write-path gates (evals / remember
# may still reuse ``has_omission_marker`` / ``is_severe_shrink``).
_OMISSION_LITERALS = (
    "中间省略",
    "已保留首尾",
    "（略）",
    "[略]",
)
_OMISSION_RE = re.compile(
    r"(?:\.\.\.|…)\s*omitted|truncated\s+for\s+brevity",
    re.IGNORECASE,
)

# "成篇" threshold: classify_write_kind / prose-append lock.
# file_write overwrite and file_delete of a substantial draft are allowed
# (prefer str_replace for revisions; delete is reversible by default).
# Length is advisory only (skill / schema 可选骨架分段) — no hard reject on oversized bodies.
_SUBSTANTIAL_FILE_CHARS = 400
# Eval / remember helper: same ratio the old overwrite nudge used.
_INTEGRITY_SHRINK_RATIO = 0.6


def has_omission_marker(content: str) -> bool:
    """True when ``content`` contains a known lazy-elision / truncation marker."""
    if not content:
        return False
    if any(m in content for m in _OMISSION_LITERALS):
        return True
    return _OMISSION_RE.search(content) is not None


def is_severe_shrink(old_chars: int, new_chars: int) -> bool:
    """True when new length is below ``_INTEGRITY_SHRINK_RATIO`` of the old length.

    Used by evals, not by ``file_write``.
    """
    return old_chars > 0 and new_chars < old_chars * _INTEGRITY_SHRINK_RATIO


# Skeleton vs prose (Artifact-first Writing) ---------------------------------
# Explicit outline / website markers always count as skeleton. Otherwise:
# short stubs are skeleton; short + many headings with thin body = skeleton;
# substantial body without those cues = prose (locks same-path append this run).
_SKELETON_SOFT_CHARS = 800
_MD_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(\S.*)$")
_HTML_HEADING_RE = re.compile(r"(?is)<h([1-6])\b[^>]*>(.*?)</h\1>")
_SKELETON_MARKER_RE = re.compile(
    r"<!--\s*(?:SECTION:\S+|OUTLINE)\b",
    re.IGNORECASE,
)


def has_skeleton_markers(content: str) -> bool:
    """True when content carries outline / SECTION placeholders."""
    return bool(_SKELETON_MARKER_RE.search(content or ""))


def extract_title_tree(content: str, *, limit: int = 24) -> list[str]:
    """Cheap heading outline (Markdown ``#`` + HTML ``<hN>``), capped at ``limit``."""
    text = content or ""
    items: list[str] = []
    for match in _MD_HEADING_RE.finditer(text):
        level = len(match.group(1))
        title = match.group(2).strip()
        items.append(f"{'#' * level} {title}")
        if len(items) >= limit:
            return items
    for match in _HTML_HEADING_RE.finditer(text):
        level = int(match.group(1))
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        if not title:
            continue
        items.append(f"{'#' * level} {title}")
        if len(items) >= limit:
            break
    return items


def _heading_count(content: str) -> int:
    text = content or ""
    return len(_MD_HEADING_RE.findall(text)) + len(_HTML_HEADING_RE.findall(text))


def _prose_body_chars(content: str) -> int:
    """Rough non-heading body size (strip heading lines / SECTION markers)."""
    text = content or ""
    text = _MD_HEADING_RE.sub("", text)
    text = _HTML_HEADING_RE.sub("", text)
    text = _SKELETON_MARKER_RE.sub("", text)
    return len(text.strip())


def classify_write_kind(content: str) -> Literal["skeleton", "prose"]:
    """Classify a ``file_write`` body as skeleton (append-ok) or prose (append-locked)."""
    text = content or ""
    stripped = text.strip()
    if not stripped:
        return "skeleton"
    if has_skeleton_markers(text):
        return "skeleton"
    if len(stripped) < _SUBSTANTIAL_FILE_CHARS:
        return "skeleton"
    headings = _heading_count(text)
    body = _prose_body_chars(text)
    if (
        len(stripped) <= _SKELETON_SOFT_CHARS
        and headings >= 2
        and body < max(_SUBSTANTIAL_FILE_CHARS, len(stripped) // 2)
    ):
        return "skeleton"
    if headings >= 3 and body < _SUBSTANTIAL_FILE_CHARS:
        return "skeleton"
    return "prose"


def is_skeleton_content(content: str) -> bool:
    """True when ``content`` looks like a fill-in skeleton rather than finished prose."""
    return classify_write_kind(content) == "skeleton"


_APPEND_ECHO_LINES = 12
_APPEND_ECHO_CHARS = 600

def _tail_preview(content: str, *, max_lines: int, max_chars: int) -> str:
    """Last ``max_lines`` lines of ``content``, capped at ``max_chars`` (kept from the tail)."""
    lines = content.splitlines()
    tail = "\n".join(lines[-max_lines:])
    elided = len(lines) > max_lines
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
        elided = True
    return ("…\n" if elided else "") + tail

def content_sha256_short(content: str, *, n: int = 16) -> str:
    """Short hex prefix of SHA-256 over UTF-8 bytes (manifest field)."""
    digest = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    return digest[:n]


def format_artifact_manifest(
    *,
    path: str,
    content: str,
    chars_written: int,
    kind: str,
    action: str = "write",
) -> str:
    """Success receipt = artifact manifest（作者以此验真，勿再 file_read 回读正文）。

    规模按**字符**报（``WorkspaceBackend.write`` / ``append`` 返回的就是 chars）。
    曾标成「字节」：中文正文字符数 ≈ UTF-8 字节数 / 3，作者据此判定「写少了」，
    反而回读核对——正是本 manifest 要省掉的空转。
    """
    from agentcore.core.secrets import redact_secrets

    lines = len((content or "").splitlines())
    tree = extract_title_tree(content)
    tree_block = "\n".join(f"  {t}" for t in tree) if tree else "  （无标题）"
    # 案 B：manifest 末段预览 / 标题树不得回显完整 API Key。
    tree_block = redact_secrets(tree_block)
    preview = redact_secrets(
        _tail_preview(content, max_lines=_APPEND_ECHO_LINES, max_chars=_APPEND_ECHO_CHARS)
    )
    verb = "已写入" if action == "write" else "已追加"
    return (
        f"{verb} {chars_written} 字符到 {path}\n"
        f"【artifact manifest】\n"
        f"path: {path}\n"
        f"kind: {kind}\n"
        f"chars: {chars_written}\n"
        f"lines: {lines}\n"
        f"content_sha256: {content_sha256_short(content)}\n"
        f"title_tree:\n{tree_block}\n"
        f"end_preview:\n{preview}\n"
        "【验真】请以本 manifest 确认落盘；优先用 manifest 验真，"
        "勿为空转反复 file_read。"
    )


def prose_append_rejection(path: str) -> str:
    """Hard reject when appending after a same-run prose ``file_write``."""
    return (
        f"拒绝追加：`{path}` 本 run 已落成篇正文（非骨架）。"
        "成篇后请用 str_replace 局部修订；骨架填空路径才用 file_append。"
    )

def _norm_rel_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/")


async def _prepare_write_relpath(
    path: str,
    context: ToolContext,
    *,
    register: bool = True,
    register_bare: bool = False,
) -> tuple[str, str]:
    """Rewrite empty-desk shell, then sanitize; return ``(actual, rename_note)``.

    Shell strip lives here (workspace + turn slot) — not in diskless
    ``sanitize_write_relpath``. ``rename_note`` is a one-line tip when the
    cleaned path differs from the request. ``/workspace/…`` strip alone does
    not count as a rename; dangerous-char / dossier-flatten / shell-strip do.
    ``register=False`` (delete) applies an existing slug only.
    ``register_bare=True`` (mkdir) stamps a single-segment shell.
    """
    from agentcore.workspace._paths import (
        normalize_workspace_path,
        sanitize_write_relpath,
    )
    from agentcore.workspace.project_shell import rewrite_project_shell_relpath

    requested = (path or "").strip()
    if not requested:
        return "", ""
    rewritten, shell_note = await rewrite_project_shell_relpath(
        requested, context, register=register, register_bare=register_bare
    )
    # ``.`` is a valid workspace-root dest (archive_extract). Empty is a
    # shell-stripped bare mkdir / invalid file path — callers treat them apart.
    if rewritten == ".":
        return ".", shell_note
    if not rewritten:
        return "", shell_note
    actual = sanitize_write_relpath(rewritten)
    baseline = normalize_workspace_path(rewritten, root_label="workspace")
    sanitize_note = ""
    if _norm_rel_path(actual) != _norm_rel_path(baseline):
        sanitize_note = (
            f"注意：请求路径已清理，实际写入 `{actual}`。"
            "约定文档区（`AgentCore/文档/` 下 research/reviews/debate）前缀之后"
            "嵌套 `/` 会压成 `_`（单文件名）；勿再 file_move/copy「改回」斜杠路径"
            "（规范化后常等同）。"
        )
    note = " ".join(part for part in (shell_note, sanitize_note) if part)
    return actual, note


def write_scope_rejection(context: ToolContext, path: str) -> str | None:
    """Chinese error when ``path`` violates ``context.write_scope``; else ``None``.

    ``project`` — no gate. ``none`` — reject all writes. ``explore_memory`` — path
    must be under ``AgentCore/``. Thick folder dossiers need no path clause here:
    they are documents entries now, and no worker tool can write one.
    """
    scope = getattr(context, "write_scope", "project") or "project"
    if scope == "project":
        return None
    if scope == "none":
        return (
            "当前写范围 write_scope=none：禁止一切写盘。"
            "请改用只读工具，或待主管解除写范围限制后再写。"
        )
    if scope != "explore_memory":
        return None

    from agentcore.workspace.stage_dirs import AGENTCORE_ROOT

    norm = _norm_rel_path(path).lstrip("./")
    root_prefix = f"{AGENTCORE_ROOT}/"
    if not (norm == AGENTCORE_ROOT or norm.startswith(root_prefix)):
        return (
            f"冷启动探索写范围仅允许落在 `{AGENTCORE_ROOT}/` 下"
            f"（约定记忆与探索笔记）；拒绝路径 `{path}`。"
            f"请改写到 `{AGENTCORE_ROOT}/文档/research/` 等探索笔记路径，"
            "或待画像写入完成后再写用户工程文件。"
        )
    return None


def _reject_write_scope(
    context: ToolContext,
    path: str,
    start: float,
    *,
    event: str = "file_write.scope_rejected",
) -> ToolResult | None:
    """Log + return failed ToolResult when write_scope blocks ``path``."""
    msg = write_scope_rejection(context, path)
    if msg is None:
        return None
    logger.info(event, path=path, write_scope=getattr(context, "write_scope", None))
    return _error(
        msg, start, contract_failure=True, cross_turn_retry=CrossTurnRetry.FUTILE
    )


def _mark_landed_files(
    context: ToolContext,
    path: str = "",
    *,
    kind: str | None = None,
) -> None:
    """Stamp landed-files gate + Artifact-first path kind (shared mutable dict).

    ``kind="prose"`` locks same-path append. ``kind="skeleton"`` or omitted keeps
    append allowed. Existing ``prose`` is never downgraded.
    First writer of ``path`` is recorded in ``landed_artifact_authors`` (setdefault).
    Failure paths never call this.
    """
    context.has_landed_files = True
    path_key = _norm_rel_path(path)
    if not path_key:
        return
    # C3: successful I/O → path is no longer declare-only on the ownership ledger.
    coordinator = context.write_coordinator
    if coordinator is not None:
        coordinator.mark_written(
            path_key, desk_id=getattr(context, "ownership_desk_id", None)
        )
    # Successful disk land → sibling verify cache is stale (typecheck/build).
    # Must run before kind early-returns (prose lock) — the file already changed.
    eid = (getattr(context, "execution_id", None) or "").strip()
    if eid:
        try:
            from agentcore.runtime.coordination.session import active_coordination

            session = active_coordination(eid)
            if session is not None and session.active:
                session.invalidate_verify_cache(reason="landed")
        except Exception:  # noqa: BLE001
            pass
    author = (context.agent_id or "").strip()
    if author:
        context.landed_artifact_authors.setdefault(path_key, author)
    current = context.landed_artifact_kinds.get(path_key)
    if current == "prose":
        return
    if kind == "prose":
        context.landed_artifact_kinds[path_key] = "prose"
    elif kind == "skeleton":
        context.landed_artifact_kinds[path_key] = "skeleton"
    else:
        context.landed_artifact_kinds.setdefault(path_key, "skeleton")

def _log_write_collision(
    event: str,
    *,
    path: str,
    run_id: str,
    owner: str,
) -> None:
    """Log a write-ownership collision with a literal event name (catalog scan)."""
    # Literals required so sync_log_event_registry picks them up.
    if event == "file_write.collision":
        logger.info("file_write.collision", path=path, run_id=run_id, owner=owner)
    elif event == "file_append.collision":
        logger.info("file_append.collision", path=path, run_id=run_id, owner=owner)
    elif event == "str_replace.collision":
        logger.info("str_replace.collision", path=path, run_id=run_id, owner=owner)
    elif event == "file_delete.collision":
        logger.info("file_delete.collision", path=path, run_id=run_id, owner=owner)
    elif event == "file_move.collision":
        logger.info("file_move.collision", path=path, run_id=run_id, owner=owner)
    else:
        logger.info(event, path=path, run_id=run_id, owner=owner)


def stale_overwrite_rejection(path: str) -> str:
    """User/model-facing refuse when whole-file write lost the race to another writer."""
    return (
        f"`{path}` 盘上已经不是你刚读到的版本（期间有人改过）。"
        "请重新 file_read 后再 file_write，或改用 str_replace 按当前原文局部改。"
    )


def _claim_write_path(
    context: ToolContext,
    rel_path: str,
    *,
    event: str,
    start: float,
) -> tuple[ToolResult | None, bool]:
    """Occupancy is this tool call only (disk serial + CAS). Never refuse on run-lifetime owner.

    Returns ``(error_result, release_on_fail)``. Always ``(None, False)``: a run-lifetime
    ledger must not block writes; ``release_on_fail`` stays false so I/O failure does
    not pretend to free a dispatch declare that no longer gates writes.
    """
    _ = context, rel_path, event, start
    return None, False
