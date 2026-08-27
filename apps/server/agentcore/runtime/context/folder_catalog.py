"""Derived CEO ``<文件夹清单>`` — Folder roster + 画像.md first line.

Read-time projection for「跨文件夹找文件夹」: assembled fresh each prepare turn from
the user's live Folder list (recent-activity order, hard count cap) plus each
folder's ``画像.md`` first substantive line. Not a memory file, not consolidated,
never expires — rename / move / profile edits show up on the next turn.

Rows carry the **full path** (``设计/图标``) **and the folder id**: folders nest,
the same last segment can live at two levels, and a name-only listing would send
every ``resolve_folder`` straight into an ambiguity round-trip. Ids in the roster
mean the model does not need ``list_folders`` to inspect a listed desk. The sitting
desk is pinned to the front of a truncated roster and marked in the render.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import FolderRepository
from agentcore.memory.store import CORE_MEMORY_FILE, MemoryStore
from agentcore.memory.user_memory import topic_summary_line
from agentcore.workspace.cloud_tree import normalize_rel_path

logger = get_logger(__name__)


@dataclass(frozen=True)
class FolderCatalogEntry:
    """One injected folder row: stable id + where it sits + optional one-liner."""

    folder_id: str
    name: str
    summary: str = ""
    rel_path: str = ""

    @property
    def label(self) -> str:
        """Path the roster shows (full rel_path when nested)."""
        return self.rel_path or self.name


def build_folder_catalog_entries(
    folders: Sequence[tuple[str, str] | tuple[str, str, str]],
    profiles: Mapping[str, str],
    *,
    limit: int,
) -> list[FolderCatalogEntry]:
    """Pure assemble: already-sorted folders × profile bodies → capped entries.

    ``folders`` is ``(folder_id, name)`` or ``(folder_id, name, rel_path)`` in
    recent-activity order (caller sorts). ``profiles`` maps ``folder_id → 画像.md``
    markdown (missing → name-only row).
    """
    if limit <= 0 or not folders:
        return []
    out: list[FolderCatalogEntry] = []
    for row in folders[:limit]:
        folder_id, name = row[0], row[1]
        rel_path = normalize_rel_path(row[2]) if len(row) > 2 else ""
        body = profiles.get(folder_id) or ""
        summary = topic_summary_line(body) if body else ""
        out.append(
            FolderCatalogEntry(
                folder_id=folder_id, name=name, summary=summary, rel_path=rel_path
            )
        )
    return out


def catalog_label_for(
    entries: Sequence[FolderCatalogEntry], folder_id: str | None
) -> str | None:
    """Path label for the sitting desk, or ``None`` when it is not in the roster."""
    cid = (folder_id or "").strip()
    if not cid:
        return None
    for entry in entries:
        if entry.folder_id == cid:
            return entry.label
    return None


def prioritize_current_folder[T](
    rows: Sequence[T],
    *,
    current_id: str | None,
    current_row: T | None,
    limit: int,
) -> list[T]:
    """Put the sitting desk first so a truncated roster still contains it."""
    if limit <= 0:
        return []
    cid = (current_id or "").strip()
    if not cid:
        return list(rows)[:limit]
    rest = [row for row in rows if getattr(row, "id", None) != cid]
    head = next((row for row in rows if getattr(row, "id", None) == cid), current_row)
    if head is None:
        return list(rows)[:limit]
    return [head, *rest][:limit]


def render_folder_catalog(
    entries: Sequence[FolderCatalogEntry],
    *,
    current_folder_id: str | None = None,
) -> str:
    """CEO ``<文件夹清单>`` block; ``""`` when empty so the assembler drops the section.

    Facts only: path + id + optional one-liner. No tool HOW — current-desk listing
    is ``file_list``; ids already in this block do not need ``list_folders``.
    """
    if not entries:
        return ""
    current = (current_folder_id or "").strip()
    intro = (
        "用户云盘里的文件夹（按最近活跃截断；路径＋id＋一句话定位，非全文记忆）。"
        "路径带 `/` 的是嵌套层级。"
    )
    if current:
        intro += "当前出生桌已在行内标出。"
    lines = ["<文件夹清单>", intro]
    for entry in entries:
        marker = "，当前出生桌" if current and entry.folder_id == current else ""
        head = f"- {entry.label}（id=`{entry.folder_id}`{marker}）"
        if entry.summary:
            lines.append(f"{head}：{entry.summary}")
        else:
            lines.append(head)
    lines.append("</文件夹清单>")
    return "\n".join(lines)


async def load_folder_catalog(
    store: MemoryStore,
    user_id: str,
    *,
    limit: int | None = None,
    current_folder_id: str | None = None,
) -> list[FolderCatalogEntry]:
    """Load recent Folders + 画像 first lines for CEO injection.

    Soft-degrades to ``[]`` on any failure (must never break prepare). Profile
    bodies ride ``MemoryStore`` so local DB and account-ticket warm paths stay
    consistent with other prepare reads; uncached scopes yield name-only rows.
    When ``current_folder_id`` is set, that desk is pinned first even if the
    recent-activity window would have truncated it.
    """
    cap = settings.folder_catalog_max_entries if limit is None else limit
    if cap <= 0:
        return []
    current_id = (current_folder_id or "").strip() or None
    try:
        async with async_session_factory() as session:
            repo = FolderRepository(session)
            folders = list(await repo.list_by_user_recently_active(user_id, limit=cap))
            extra = None
            if current_id and all(folder.id != current_id for folder in folders):
                extra = await repo.get_by_id(current_id, user_id=user_id)
            folders = prioritize_current_folder(
                folders,
                current_id=current_id,
                current_row=extra,
                limit=cap,
            )
    except Exception as e:  # noqa: BLE001 - catalog must never break a turn
        logger.warning(
            "folder_catalog.list_failed",
            user_id=user_id,
            error=str(e),
        )
        return []
    if not folders:
        return []

    profiles: dict[str, str] = {}
    for folder in folders:
        try:
            body = await store.load(user_id, CORE_MEMORY_FILE, scope=folder.id)
        except Exception as e:  # noqa: BLE001 - name-only row is fine
            logger.warning(
                "folder_catalog.profile_load_failed",
                user_id=user_id,
                folder_id=folder.id,
                error=str(e),
            )
            body = ""
        if body:
            profiles[folder.id] = body

    return build_folder_catalog_entries(
        [(f.id, f.name, f.rel_path or "") for f in folders],
        profiles,
        limit=cap,
    )
