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

# Overwrite integrity: two tiers on whole-file ``file_write`` clobber.
# Soft nudge (never auto-redispatches): mild shrink / short-body omission markers
# on success. Hard reject: severe shrink of a substantial existing body (ratio +
# absolute drop) — prefer str_replace; intentional shorten via ``allow_shrink``.
# Substantial prose with omission markers is hard-rejected at accept (not nudged).
# Soft path mirrors ``engine.audit_gate_nudge``. Hard shrink aligns Aider-style
# whole-rewrite drop guards (open PR: rewrite <50% of existing file).
_INTEGRITY_SHRINK_RATIO = 0.6
_INTEGRITY_HARD_SHRINK_RATIO = 0.5
# Absolute drop floor: near-threshold drafts (e.g. 500→200) stay soft-nudge only.
_INTEGRITY_HARD_SHRINK_ABS = 800
# Delivery-incomplete literals (write-path integrity). Distinct from
# ``core.text.DEFAULT_ELISION_MARKER`` (system *view* truncation for model-facing
# budgets). Do not reuse transport elision wording here — models must not treat
# view cuts as license to land incomplete artifacts.
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

# "成篇" threshold: delete gate + classify_write_kind / prose-append / omission hard-reject.
# file_write whole-file overwrite is allowed (prefer str_replace).
# Length is advisory only (skill / schema 可选骨架分段) — no hard reject on oversized bodies.
_SUBSTANTIAL_FILE_CHARS = 400

def is_substantial_existing_body(content: str) -> bool:
    """True when ``content`` looks like a finished article / page worth protecting."""
    return len((content or "").strip()) >= _SUBSTANTIAL_FILE_CHARS


def substantial_delete_rejection(path: str, old_chars: int) -> str:
    """User-facing error when ``file_delete`` would wipe a substantial draft."""
    return (
        f"拒绝删除成篇草稿：`{path}` 已有约 {old_chars} 字（阈值 "
        f"{_SUBSTANTIAL_FILE_CHARS} 字）。禁止整篇 delete 后重写长文——"
        "请用 str_replace 局部修订；超长可一次完整写入或先短骨架再按节 "
        "file_append / str_replace；预算不够时停在完整章边界并诚实交接，勿推倒重来。"
    )


def has_omission_marker(content: str) -> bool:
    """True when ``content`` contains a known lazy-elision / truncation marker."""
    if not content:
        return False
    if any(m in content for m in _OMISSION_LITERALS):
        return True
    return _OMISSION_RE.search(content) is not None


def is_severe_shrink(old_chars: int, new_chars: int) -> bool:
    """True when new length is below soft ``_INTEGRITY_SHRINK_RATIO`` of the old length."""
    return old_chars > 0 and new_chars < old_chars * _INTEGRITY_SHRINK_RATIO


def is_hard_severe_shrink(old_chars: int, new_chars: int) -> bool:
    """True when overwrite would chop a substantial draft (ratio + absolute drop).

    Softer shrinks stay on the nudge path; tiny near-threshold files (abs drop
    below ``_INTEGRITY_HARD_SHRINK_ABS``) are not hard-rejected.
    """
    if old_chars <= 0:
        return False
    if new_chars >= old_chars * _INTEGRITY_HARD_SHRINK_RATIO:
        return False
    return (old_chars - new_chars) >= _INTEGRITY_HARD_SHRINK_ABS


def severe_shrink_rejection(path: str, *, old_chars: int, new_chars: int) -> str:
    """User-facing hard reject when ``file_write`` would truncate a substantial draft."""
    pct = int(_INTEGRITY_HARD_SHRINK_RATIO * 100)
    return (
        f"拒绝整篇截断覆盖：`{path}` 旧稿约 {old_chars} 字 → 新稿 {new_chars} 字"
        f"（低于旧稿 {pct}% 且绝对减少 ≥{_INTEGRITY_HARD_SHRINK_ABS} 字）。"
        "修订请用 str_replace 局部改；确需大幅删减/精简/重建时，"
        "对本次 file_write 显式传 allow_shrink=true 后重试。"
    )


def integrity_nudge_text(
    *,
    path: str,
    reasons: list[str],
    old_chars: int,
    new_chars: int,
) -> str:
    """Soft warning appended to a successful ``file_write`` receipt."""
    reason = "；".join(reasons)
    return (
        f"\n\n[系统提示] 产物疑似不完整（`{path}`：{reason}；"
        f"旧 {old_chars} 字 → 新 {new_chars} 字）。"
        "请用 str_replace 就地补全（勿再 file_read 回读），或向主管说明需重派。"
        "系统只提示、绝不代派、绝不自动重跑、绝不拦截本次写入。"
    )


def overwrite_integrity_nudge(
    path: str, old_content: str, new_content: str
) -> str | None:
    """Return a soft nudge when overwriting a non-empty file looks truncated.

    Only for existing non-empty targets. Never raises; callers append to tool output.
    Hard severe-shrink is rejected before write (see ``is_hard_severe_shrink``).
    """
    if not old_content:
        return None
    old_chars = len(old_content)
    new_chars = len(new_content)
    reasons: list[str] = []
    if has_omission_marker(new_content):
        reasons.append("正文含省略标记")
    if is_severe_shrink(old_chars, new_chars):
        reasons.append(f"字数骤降至旧稿 {int(_INTEGRITY_SHRINK_RATIO * 100)}% 以下")
    if not reasons:
        return None
    return integrity_nudge_text(
        path=path, reasons=reasons, old_chars=old_chars, new_chars=new_chars
    )


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
        "勿为空转反复 file_read（同 path 同窗触顶只回短指针、不灌全文）。"
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

def prose_omission_rejection(path: str) -> str:
    """Hard reject when substantial prose lands with delivery-omission markers."""
    return (
        f"拒绝写入 `{path}`：成篇正文含省略标记（残缺交付）。"
        "请一次写完完整正文，或用短骨架 + `<!-- SECTION: -->` 按节 "
        "file_append / str_replace 填空；禁止用「中间省略」等标记交差。"
    )

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

    Successful land also resets the same-path ``file_read`` ceiling (counts → 0,
    delivered ranges cleared, sticky reread grant) so post-write verify /
    citation refresh is not blocked.
    Failure paths never call this — no grant on failed ``str_replace`` receipts.
    """
    context.has_landed_files = True
    path_key = _norm_rel_path(path)
    if not path_key:
        return
    # Post-write verify: clear same-path read ceiling and refresh sticky grant.
    context.file_read_counts[path_key] = 0
    context.file_read_delivered_ranges.pop(path_key, None)
    context.file_read_line_totals.pop(path_key, None)
    from agentcore.runtime.engine.tool_clear import refresh_file_read_reread_grant

    refresh_file_read_reread_grant(context, [path_key])
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
    elif event == "write_section.collision":
        logger.info("write_section.collision", path=path, run_id=run_id, owner=owner)
    elif event == "file_delete.collision":
        logger.info("file_delete.collision", path=path, run_id=run_id, owner=owner)
    elif event == "file_move.collision":
        logger.info("file_move.collision", path=path, run_id=run_id, owner=owner)
    else:
        logger.info(event, path=path, run_id=run_id, owner=owner)


def _claim_write_path(
    context: ToolContext,
    rel_path: str,
    *,
    event: str,
    start: float,
) -> tuple[ToolResult | None, bool]:
    """C3 / batch ownership gate.

    Returns ``(error_result, release_on_fail)``. On conflict, ``error_result`` is set and
    ``release_on_fail`` is False. On success / no coordinator, ``error_result`` is None;
    ``release_on_fail`` is True only when this call newly acquired an *unowned* path
    (so a later I/O failure can free it without wiping a dispatch-time declare).
    """
    coordinator = context.write_coordinator
    if coordinator is None:
        return None, False
    desk = getattr(context, "ownership_desk_id", None)
    prior = coordinator.owner_of(rel_path, desk_id=desk)
    owner = coordinator.claim(
        rel_path,
        context.run_id,
        context.write_ancestors,
        desk_id=desk,
    )
    if owner is not None:
        _log_write_collision(
            event, path=rel_path, run_id=context.run_id, owner=owner
        )
        from agentcore.runtime.audit.hooks import on_write_conflict
        from agentcore.workspace.write_claims import (
            lookup_owner_status,
            ownership_conflict_message,
        )

        on_write_conflict(
            path=rel_path,
            run_id=context.run_id,
            owner_run_id=owner,
        )
        try:
            from agentcore.runtime.closing_posture import note_unresolved_write_ownership

            note_unresolved_write_ownership(run_id=context.run_id)
        except Exception:  # noqa: BLE001 — honesty latch must never block the refusal
            pass
        ownership_kind = (
            "written" if coordinator.is_written(rel_path, desk_id=desk) else "declared"
        )
        owner_role, owner_status = lookup_owner_status(
            owner, execution_id=context.execution_id
        )
        return (
            _error(
                ownership_conflict_message(
                    rel_path,
                    owner,
                    owner_role=owner_role,
                    ownership_kind=ownership_kind,
                    owner_status=owner_status,
                ),
                start,
                contract_failure=True,
            ),
            False,
        )
    # Newly claimed empty path → release on failed I/O; already ours (declare) → keep.
    return None, prior is None
