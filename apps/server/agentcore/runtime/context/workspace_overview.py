"""CEO file index — untagged body spliced into ``<工作区>`` before the closing tag.

工作区路径索引（取代「向量 RAG」的轻量方案）。Fresh each turn from the live
``WorkspaceBackend``; PATHS ONLY (no file bodies, no tool HOW, no auto-detected
fingerprint). If ``AGENTS.md`` / ``CLAUDE.md`` exists, a one-line pointer
(name only). Sparse listing
(双模式工作区): attachments + 裸聊 scratch; project shared trees collapse into
「另有 N 个文件」plus a newest-first supplement.

CEO-only: :func:`compose_ceo_chat_prompt` attaches this body; workers never
receive it. Best-effort: no backend / no ``index_files`` / listing failure →
``""`` unless a convention file is present. Successful empty index →
``文件：空``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.workspace.sparse_listing import partition_sparse_paths

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

# Bounds so a large workspace can't bloat the CEO's per-turn prompt: a file-count cap
# AND a char budget (whichever binds first). Kept local to this module.
OVERVIEW_MAX_FILES = 40
OVERVIEW_CHAR_BUDGET = 1800

_WORKSPACE_CLOSE = "</工作区>"
FILE_INDEX_EMPTY = "文件：空"
FILE_INDEX_HEADER = "文件："
_CONVENTION_FILES = ("AGENTS.md", "CLAUDE.md")


def attach_workspace_file_index(prompt: str, file_index: str) -> str:
    """Insert CEO file-index lines before ``</工作区>``. No-op if either side is empty.

    Does not invent a ``<工作区>`` wrapper — workers and a missing facts block stay
    without a 文件节. Resume restamp of worker facts must not call this.
    """
    index = (file_index or "").strip()
    if not prompt or not index:
        return prompt or ""
    close = prompt.rfind(_WORKSPACE_CLOSE)
    if close < 0:
        return prompt
    before = prompt[:close].rstrip()
    after = prompt[close:]
    return f"{before}\n{index}\n{after}"


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


async def _convention_pointer(backend: WorkspaceBackend) -> str:
    """Name-only pointer when a repo convention file exists. No excerpt, no HOW."""
    exists = getattr(backend, "exists", None)
    read = getattr(backend, "read", None)
    for name in _CONVENTION_FILES:
        try:
            found = False
            if exists is not None:
                found = bool(await exists(name))
            elif read is not None:
                found = bool(await read(name))
            if found:
                return f"工程约定：`{name}`"
        except Exception:  # noqa: BLE001 — pointer is best-effort
            continue
    return ""


async def build_workspace_overview(
    backend: WorkspaceBackend | None,
    *,
    shared_workspace: bool = False,
) -> str:
    """Build the untagged CEO file-index body, or ``""`` when the 文件节 should omit.

    ``shared_workspace`` is True for project (folder) chats — sparse listing applies.
    Returns ``""`` for a missing backend or a listing failure with no convention
    pointer. Successful empty index returns ``文件：空``.
    """
    if backend is None:
        return ""

    pointer = await _convention_pointer(backend)
    paths = await _safe_index(backend)
    sections: list[str] = []
    if pointer:
        sections.append(pointer)

    if paths is None:
        return "\n\n".join(sections)

    if not paths:
        sections.append(FILE_INDEX_EMPTY)
        return "\n\n".join(sections)

    sparse_rows, remaining = partition_sparse_paths(
        paths, shared_workspace=shared_workspace
    )
    lines: list[str] = []
    used = 0
    for path, label in sparse_rows:
        line = f"- {path}（{label}）"
        if len(lines) >= OVERVIEW_MAX_FILES or used + len(line) + 1 > OVERVIEW_CHAR_BUDGET:
            leftover = len(sparse_rows) - len(lines)
            remaining += leftover
            break
        lines.append(line)
        used += len(line) + 1

    if remaining > 0:
        lines.append(f"另有 {remaining} 个文件")

    if lines:
        sections.append(FILE_INDEX_HEADER + "\n" + "\n".join(lines))

    return "\n\n".join(sections)
