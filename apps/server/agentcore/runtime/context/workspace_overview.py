"""Workspace overview — the CEO's live ``<工作区文件>`` orientation block.

工作区文件索引（取代「向量 RAG」的轻量方案）。Instead of a pre-built embedding index (which
goes stale the moment a file changes and needs an embedder + pgvector), this gives the
entry CEO agent a compact listing of the files already on disk in the conversation's
workspace, regenerated fresh every turn from the live ``WorkspaceBackend`` — so it is
never stale and carries zero new infra. The block is PATHS ONLY (no file bodies); the
agent must call ``file_read`` / ``grep`` for content (agentic retrieval, the主路).

清单稀疏化 (双模式工作区): default injection is attachments + 裸聊 scratch files;
project shared trees collapse non-attachment noise into one「另有 N 个文件」line (with a
small newest-first supplement). CEO-only; workers do not receive this listing.

Best-effort by contract: no backend, no indexing support, an empty workspace, or a
listing failure all yield ``""`` (the caller omits the block) — workspace awareness is
an enhancement, never a hard dependency (same posture as ``memory`` / global search).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.runtime.context.workspace_profile import (
    detect_workspace_profile,
    render_workspace_profile,
)
from agentcore.workspace.sparse_listing import (
    format_remaining_summary,
    partition_sparse_paths,
)

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

# Bounds so a large workspace can't bloat the CEO's per-turn prompt: a file-count cap
# AND a char budget (whichever binds first). Kept local to this module.
OVERVIEW_MAX_FILES = 40
OVERVIEW_CHAR_BUDGET = 1800


async def _safe_index(backend: WorkspaceBackend) -> list[str] | None:
    """Newest-first workspace file paths; ``None`` if indexing unavailable/failed.

    ``[]`` means the index ran successfully but the workspace has no files.
    """
    index = getattr(backend, "index_files", None)
    if index is None:
        return None
    try:
        paths, _truncated = await index(order="recent")
        return list(paths)
    except Exception as e:  # noqa: BLE001 — overview is best-effort, never fail a turn
        logger.debug("workspace.overview_index_failed", error=str(e))
        return None


async def build_workspace_overview(
    backend: WorkspaceBackend | None,
    *,
    shared_workspace: bool = False,
) -> str:
    """Build the CEO's ``<工作区文件>`` block, or ``""`` when nothing to show.

    ``shared_workspace`` is True for project (folder) chats — sparse listing applies.
    Returns ``""`` for a missing backend, an empty / unindexable workspace with no
    detectable project profile, or a listing failure. Otherwise renders a best-effort
    project fingerprint (when detectable) plus a capped sparse file list.
    """
    if backend is None:
        return ""

    profile_text = render_workspace_profile(await detect_workspace_profile(backend))
    paths = await _safe_index(backend)
    if paths is None:
        if not profile_text:
            return ""
    elif not paths and not profile_text:
        # Environment mismatch guidance lives in ``<工作区>`` (explicit facts);
        # this block only states the file-index emptiness so the model does not re-guess.
        return (
            "<工作区文件>\n"
            "工作区当前为空（无文件路径可列）——若本回合为云端会话，这只是会话云端草稿尚无文件，"
            "不是本机或已打开的仓库工程。若对话历史显示曾委派产出，仍须先 "
            "file_list 核实后再回答；环境与绑定以本回合 `<工作区>` 为准。\n"
            "</工作区文件>"
        )

    sections: list[str] = []
    if profile_text:
        sections.append(f"当前工作区工程概览：\n{profile_text}")

    if paths:
        sparse_rows, remaining = partition_sparse_paths(
            paths, shared_workspace=shared_workspace
        )
        lines: list[str] = []
        used = 0
        for path, label in sparse_rows:
            line = f"- {path}（{label}）"
            if len(lines) >= OVERVIEW_MAX_FILES or used + len(line) + 1 > OVERVIEW_CHAR_BUDGET:
                # Cap hit before finishing sparse rows — fold the unlisted sparse
                # rows into the shared remaining count (project) or a generic elision.
                leftover = len(sparse_rows) - len(lines)
                remaining += leftover
                break
            lines.append(line)
            used += len(line) + 1

        if remaining > 0:
            lines.append(format_remaining_summary(remaining))

        if lines:
            file_intro = (
                "以下为本对话工作区中相关文件路径索引（附件 / 本对话产出优先；"
                "工作区共享树不逐条展开）。列表仅为路径，不含正文内容；"
                "需要了解某个文件的内容时，必须调用 file_read（或 grep）读取："
            )
            sections.append(f"{file_intro}\n" + "\n".join(lines))

    body = "\n\n".join(sections)
    return f"<工作区文件>\n{body}\n</工作区文件>"

