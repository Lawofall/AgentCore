"""Bounded desk map — the single file-orientation object for CEO and workers.

Three densities share one walker and the same ignore / AI-noise rules:

- ``outline`` — one layer (roster / ``folders(list)``)
- ``map`` — BFS tree with a small budget (sitting ``<工作区>`` / ``folders(resolve)``)
- precision stays ``file_list`` / ``glob`` / ``grep`` / ``read``

Not a path dump, not ``<工作集>``, not file contents. Truncation is visible.
Local-mode roster rows cannot peek the user's disk from the cloud API.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agentcore.core.logging import get_logger
from agentcore.workspace._paths import (
    is_ignored_dir_entry,
    is_system_ignored_file_name,
)
from agentcore.workspace.protocol import (
    DirEntry,
    DirListing,
    NotADirectory,
    PathNotFound,
    WorkspaceBackend,
    WorkspaceError,
)
from agentcore.workspace.sparse_listing import should_hide_ai_noise_from_list

logger = get_logger(__name__)

Density = Literal["outline", "map"]

OUTLINE_TOP_MAX = 12
MAP_ENTRY_BUDGET = 40
MAP_MAX_DEPTH = 3
MAP_HEADER = "地图："
OUTLINE_JOIN = " · "
TRUNCATED_SUFFIX = "…"
TRUNCATED_NOTE = "（截断，还有未列出的）"
EMPTY_NOTE = "尚无用户文件"
LOCAL_NO_PEEK = "本机桌，名册不探盘"
MAP_FAILED_NOTE = ""
OVERFLOW_HEADER = "本对话未在树上："

_WALK_CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class DeskMapNode:
    """One visible path relative to the sitting root (POSIX)."""

    path: str
    is_dir: bool
    depth: int


@dataclass(frozen=True, slots=True)
class DeskMap:
    """Budgeted orientation of one desk. ``empty`` is a successful peek of nothing."""

    density: Density
    entries: tuple[DeskMapNode, ...] = ()
    truncated: bool = False
    empty: bool = True
    unavailable: bool = False


def empty_desk_map(density: Density, *, unavailable: bool = False) -> DeskMap:
    return DeskMap(density=density, empty=True, unavailable=unavailable)


def _visible_name(path: str) -> str:
    rel = path.replace("\\", "/").strip("/")
    return rel.rsplit("/", 1)[-1] if rel else path


def _is_hidden_file(path: str, *, is_dir: bool) -> bool:
    if is_dir:
        return False
    name = _visible_name(path)
    if is_system_ignored_file_name(name):
        return True
    return should_hide_ai_noise_from_list(path)


def _skip_backend_entry(entry: DirEntry, *, parent_rel: str) -> bool:
    name = _visible_name(entry.path)
    if entry.is_dir:
        return is_ignored_dir_entry(parent_rel=parent_rel, name=name)
    return _is_hidden_file(entry.path, is_dir=False)


def _walk_path_sync(root: Path, *, density: Density) -> DeskMap:
    """BFS walk of a cloud tree root. Must run off the event loop."""
    try:
        if not root.is_dir():
            return empty_desk_map(density)
    except OSError:
        return empty_desk_map(density, unavailable=True)

    budget = OUTLINE_TOP_MAX if density == "outline" else MAP_ENTRY_BUDGET
    max_depth = 1 if density == "outline" else MAP_MAX_DEPTH
    collected: list[DeskMapNode] = []
    truncated = False
    queue: deque[tuple[Path, str, int]] = deque([(root, "", 0)])

    while queue and not truncated:
        dir_path, parent_rel, depth = queue.popleft()
        if depth >= max_depth:
            continue
        try:
            with os.scandir(dir_path) as scan:
                children = sorted(scan, key=lambda c: c.name.lower())
        except OSError:
            continue
        for child in children:
            name = child.name
            try:
                is_dir = child.is_dir()
                descend = is_dir and not child.is_symlink()
            except OSError:
                continue
            if is_dir:
                if is_ignored_dir_entry(parent_rel=parent_rel, name=name):
                    continue
            elif is_system_ignored_file_name(name):
                continue
            rel = f"{parent_rel}/{name}" if parent_rel else name
            if _is_hidden_file(rel, is_dir=is_dir):
                continue
            if len(collected) >= budget:
                truncated = True
                break
            child_depth = depth + 1
            collected.append(DeskMapNode(path=rel, is_dir=is_dir, depth=child_depth))
            if descend and child_depth < max_depth:
                queue.append((Path(child.path), rel, child_depth))

    return DeskMap(
        density=density,
        entries=tuple(collected),
        truncated=truncated,
        empty=not collected,
    )


async def build_desk_map_at_root(
    root: Path | None,
    *,
    density: Density = "map",
) -> DeskMap:
    """Peek a physical directory. Missing / unreadable → empty, never raises."""
    if root is None:
        return empty_desk_map(density, unavailable=True)
    try:
        return await asyncio.to_thread(_walk_path_sync, Path(root), density=density)
    except Exception:  # noqa: BLE001 — orientation must not fail a turn or list
        logger.debug("desk_map.path_failed", error="walk")
        return empty_desk_map(density, unavailable=True)


async def build_folder_desk_map(
    *,
    owner_user_id: str,
    rel_path: str | None,
    density: Density,
) -> DeskMap:
    """Cloud-folder peek via the tree layout. Local-mode callers pass no rel_path."""
    rel = (rel_path or "").strip()
    if not rel:
        return empty_desk_map(density, unavailable=True)
    from agentcore.workspace.locate import workspace_root_path

    try:
        root = workspace_root_path(
            user_id=owner_user_id,
            folder_rel_path=rel,
            conversation_id="",
        )
    except ValueError:
        return empty_desk_map(density, unavailable=True)
    return await build_desk_map_at_root(root, density=density)


async def _list_layer(
    backend: WorkspaceBackend,
    directory: str,
    *,
    cap: int,
) -> DirListing:
    listing = await backend.list(directory, "*", cap=max(1, cap))
    if isinstance(listing, DirListing):
        return listing
    entries = list(listing) if listing is not None else []
    return DirListing(entries=list(entries), truncated=False)


async def build_desk_map(
    backend: WorkspaceBackend | None,
    *,
    density: Density = "map",
) -> DeskMap:
    """Peek the sitting backend. Failures degrade to empty, never raise."""
    if backend is None or getattr(backend, "list", None) is None:
        return empty_desk_map(density, unavailable=True)
    budget = OUTLINE_TOP_MAX if density == "outline" else MAP_ENTRY_BUDGET
    max_depth = 1 if density == "outline" else MAP_MAX_DEPTH
    collected: list[DeskMapNode] = []
    truncated = False
    queue: deque[tuple[str, int]] = deque([(".", 0)])
    try:
        while queue and not truncated:
            directory, depth = queue.popleft()
            if depth >= max_depth:
                continue
            remaining = budget - len(collected)
            try:
                listing = await _list_layer(
                    backend, directory, cap=remaining + 1
                )
            except (PathNotFound, NotADirectory):
                continue
            parent_rel = "" if directory in (".", "") else directory.replace("\\", "/").strip("/")
            visible = [
                entry
                for entry in listing.entries
                if not _skip_backend_entry(entry, parent_rel=parent_rel)
            ]
            for entry in visible:
                if len(collected) >= budget:
                    truncated = True
                    break
                rel = entry.path.replace("\\", "/").lstrip("./")
                child_depth = depth + 1
                collected.append(
                    DeskMapNode(path=rel, is_dir=entry.is_dir, depth=child_depth)
                )
                if entry.is_dir and child_depth < max_depth:
                    queue.append((rel or ".", child_depth))
            if listing.truncated:
                truncated = True
                if len(collected) >= budget:
                    break
    except WorkspaceError:
        logger.debug("desk_map.backend_failed", error="workspace")
        return empty_desk_map(density, unavailable=True)
    except Exception:  # noqa: BLE001
        logger.debug("desk_map.backend_failed", error="walk")
        return empty_desk_map(density, unavailable=True)
    return DeskMap(
        density=density,
        entries=tuple(collected),
        truncated=truncated,
        empty=not collected,
    )


def render_outline_line(
    desk_map: DeskMap | None,
    *,
    local: bool = False,
) -> str:
    """One indented roster line under a folder name. Empty string = omit."""
    if local and (desk_map is None or desk_map.unavailable or desk_map.empty):
        return LOCAL_NO_PEEK
    if desk_map is None or desk_map.unavailable:
        return ""
    if desk_map.empty:
        return EMPTY_NOTE
    names: list[str] = []
    for node in desk_map.entries:
        if node.depth != 1:
            continue
        label = _visible_name(node.path)
        names.append(f"{label}/" if node.is_dir else label)
    if not names:
        return EMPTY_NOTE
    text = OUTLINE_JOIN.join(names)
    if desk_map.truncated:
        text = f"{text}{OUTLINE_JOIN}{TRUNCATED_SUFFIX}"
    return text


def render_desk_map(
    desk_map: DeskMap | None,
    *,
    marks: Mapping[str, str] | None = None,
) -> str:
    """Indented tree for the sitting envelope / resolve. Empty → ``""``."""
    if desk_map is None or desk_map.unavailable or desk_map.empty:
        return ""
    labels = marks or {}
    lines = [MAP_HEADER]
    for node in desk_map.entries:
        indent = "  " * max(0, node.depth - 1)
        name = _visible_name(node.path)
        shown = f"{name}/" if node.is_dir else name
        mark = labels.get(node.path) or labels.get(node.path.replace("\\", "/"))
        suffix = f"（{mark}）" if mark else ""
        lines.append(f"{indent}{shown}{suffix}")
    if desk_map.truncated:
        lines.append(TRUNCATED_NOTE)
    return "\n".join(lines)


def render_desk_map_with_marks(
    desk_map: DeskMap | None,
    marks: Sequence[Any] | None,
) -> tuple[str, str]:
    """Sitting map + leftover mutation pin for paths the tree did not name.

    ``marks`` are ``ConversationEdit`` (path + label) or any object with those
    attributes. Leftover uses 「本对话未在树上：」 so deleted / over-budget writes
    still show. Workers pass no marks.
    """
    by_path: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []
    for item in marks or ():
        path = str(getattr(item, "path", "") or "").replace("\\", "/").strip().lstrip("./")
        label = str(getattr(item, "label", "") or "").strip()
        if not path or path in by_path:
            continue
        by_path[path] = label
        ordered.append((path, label))
    tree_paths = {
        node.path.replace("\\", "/").lstrip("./")
        for node in (desk_map.entries if desk_map is not None else ())
    }
    map_text = render_desk_map(desk_map, marks=by_path)
    leftover_lines = [
        f"- {path}（{label}）" for path, label in ordered if path not in tree_paths
    ]
    leftover = ""
    if leftover_lines:
        leftover = "\n".join([OVERFLOW_HEADER, *leftover_lines])
    return map_text, leftover


def desk_map_to_wire(desk_map: DeskMap) -> dict[str, Any]:
    """JSON-shaped dict matching ``DeskMapView``."""
    return {
        "density": desk_map.density,
        "empty": desk_map.empty,
        "truncated": desk_map.truncated,
        "unavailable": desk_map.unavailable,
        "entries": [
            {"path": n.path, "is_dir": n.is_dir, "depth": n.depth}
            for n in desk_map.entries
        ],
    }


def desk_map_from_wire(raw: object) -> DeskMap | None:
    if not isinstance(raw, dict):
        return None
    density = raw.get("density")
    if density not in ("outline", "map"):
        return None
    entries: list[DeskMapNode] = []
    for item in raw.get("entries") or ():
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip().lstrip("./")
        if not path:
            continue
        entries.append(
            DeskMapNode(
                path=path,
                is_dir=bool(item.get("is_dir")),
                depth=int(item.get("depth") or 1),
            )
        )
    return DeskMap(
        density=density,
        entries=tuple(entries),
        truncated=bool(raw.get("truncated")),
        empty=bool(raw.get("empty", not entries)),
        unavailable=bool(raw.get("unavailable")),
    )


async def peek_folder_desk_maps(
    folders: Sequence[Any],
    *,
    density: Density,
) -> dict[str, DeskMap]:
    """``folder id → DeskMap``. Local-mode / missing path → unavailable, never raises."""
    sem = asyncio.Semaphore(_WALK_CONCURRENCY)

    async def _one(folder: Any) -> tuple[str, DeskMap]:
        fid = _folder_id(folder)
        mode = _folder_mode(folder)
        rel = _folder_rel_path(folder)
        owner = _folder_owner(folder)
        if not fid:
            return "", empty_desk_map(density, unavailable=True)
        if mode == "local" or not rel:
            return fid, empty_desk_map(density, unavailable=True)
        async with sem:
            peeked = await build_folder_desk_map(
                owner_user_id=owner,
                rel_path=rel,
                density=density,
            )
        return fid, peeked

    if not folders:
        return {}
    pairs = await asyncio.gather(*(_one(folder) for folder in folders))
    return {fid: peeked for fid, peeked in pairs if fid}


def apply_desk_maps(folders: Sequence[Any], maps: Mapping[str, DeskMap]) -> None:
    """Stamp ``desk_map`` onto dict rows (tool payloads). Pydantic stays in the API."""
    for folder in folders:
        if not isinstance(folder, dict):
            continue
        peeked = maps.get(str(folder.get("id") or ""))
        if peeked is None:
            continue
        folder["desk_map"] = desk_map_to_wire(peeked)


def _folder_id(folder: Any) -> str:
    if isinstance(folder, dict):
        return str(folder.get("id") or "").strip()
    return str(getattr(folder, "id", None) or "").strip()


def _folder_mode(folder: Any) -> str:
    if isinstance(folder, dict):
        mode = folder.get("mode")
        if mode:
            return str(mode)
        return "local" if folder.get("local_root_id") else "cloud"
    mode = getattr(folder, "mode", None)
    if mode:
        return str(mode)
    return "local" if getattr(folder, "local_root_id", None) else "cloud"


def _folder_rel_path(folder: Any) -> str:
    if isinstance(folder, dict):
        return str(folder.get("rel_path") or "").strip()
    return str(getattr(folder, "rel_path", None) or "").strip()


def _folder_owner(folder: Any) -> str:
    if isinstance(folder, dict):
        return str(folder.get("owner_user_id") or "").strip()
    return str(getattr(folder, "owner_user_id", None) or "").strip()

