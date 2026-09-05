"""Live file names under the four 约定文档出口 directories.

``<工作区>`` only lists an outlet when it has files. Empty dirs stay
out — layout HOW lives in ``team_delivery_env``；本机进桌 HOW 在
``team_local_desk``。 This module lists what is actually there. Bodies stay
out — paths only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentcore.workspace.protocol import (
    NotADirectory,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)
from agentcore.workspace.sparse_listing import should_hide_ai_noise_from_list
from agentcore.workspace.stage_dirs import (
    DEBATE_DIR,
    DRAFTS_DIR,
    RESEARCH_DIR,
    REVIEWS_DIR,
)

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

OUTLET_DIRS: tuple[str, ...] = (DRAFTS_DIR, RESEARCH_DIR, DEBATE_DIR, REVIEWS_DIR)

# Keep the four fact lines short; the rest of a busy 工作稿/ is "file_list 该目录".
_OUTLET_NAME_CAP = 8


@dataclass(frozen=True, slots=True)
class OutletDirListing:
    """Basenames currently visible under one outlet directory."""

    names: tuple[str, ...] = ()
    truncated: bool = False


def format_outlet_suffix(listing: OutletDirListing) -> str:
    """Parenthetical inventory; truncated listings must not look complete."""
    if not listing.names:
        if listing.truncated:
            return "（现有：列举未完）"
        return "（当前为空）"
    shown = listing.names[:_OUTLET_NAME_CAP]
    sample = "；".join(shown)
    extra = len(listing.names) > _OUTLET_NAME_CAP or listing.truncated
    if extra:
        if listing.truncated:
            return f"（现有：{sample} 等，列举未完）"
        return f"（现有：{sample} 等共 {len(listing.names)}）"
    return f"（现有：{sample}）"


def format_outlet_line(
    title: str,
    rel_dir: str,
    inventory: Mapping[str, OutletDirListing] | None,
) -> str | None:
    """``title`dir/` `` plus inventory suffix. Empty / missing listing → omit."""
    if inventory is None:
        return None
    listing = inventory.get(rel_dir, OutletDirListing())
    if not listing.names and not listing.truncated:
        return None
    return f"{title}`{rel_dir}/`" + format_outlet_suffix(listing)


async def collect_outlet_inventory(
    backend: WorkspaceBackend | None,
) -> dict[str, OutletDirListing]:
    """List each outlet dir. Missing/latent-empty dirs are empty listings, not errors."""
    if backend is None:
        return {d: OutletDirListing() for d in OUTLET_DIRS}
    materials = getattr(backend, "ai_list_materials", None)
    out: dict[str, OutletDirListing] = {}
    for rel in OUTLET_DIRS:
        out[rel] = await _list_one_outlet(backend, rel, materials=materials)
    return out


async def _list_one_outlet(
    backend: WorkspaceBackend,
    rel: str,
    *,
    materials: frozenset[str] | None,
) -> OutletDirListing:
    try:
        listing = await backend.list(rel, "*")
    except (PathNotFound, NotADirectory, OutsideWorkspace, WorkspaceError):
        return OutletDirListing()
    names: list[str] = []
    for entry in listing:
        if entry.is_dir:
            continue
        if should_hide_ai_noise_from_list(
            entry.path,
            materials=materials,
            reveal_archives=False,
        ):
            continue
        names.append(entry.path.replace("\\", "/").rsplit("/", 1)[-1])
    names.sort()
    truncated = bool(getattr(listing, "truncated", False))
    return OutletDirListing(names=tuple(names), truncated=truncated)
