"""Visible empty-desk predicate for workspace facts (「尚无用户文件」).

Listing ignores ``AgentCore/`` (including visible ``文档/``), ``attachments/``,
declared latent dirs, and ``external/``. Disk-only: child Folder rows that exist
only in DB do not make the desk non-empty. Do not reuse
``path_has_non_internal_entries``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentcore.workspace.attachments import ATTACHMENTS_DIR
from agentcore.workspace.declared_dirs import is_declared_latent_dir
from agentcore.workspace.external_mounts import EXTERNAL_PREFIX
from agentcore.workspace.stage_dirs import AGENTCORE_ROOT

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

_EXTERNAL_TOP = EXTERNAL_PREFIX.strip("/")
_LIST_CAP = 256

# Fact-layer marker and write-path WHEN must share this phrase.
EMPTY_DESK_FACT = "尚无用户文件"

__all__ = ["EMPTY_DESK_FACT", "desk_is_visibly_empty"]


def _top_name(entry_path: str) -> str:
    rel = (entry_path or "").replace("\\", "/").strip("/")
    if not rel or rel == ".":
        return ""
    return rel.split("/", 1)[0]


def _ignored_visible_top(top: str) -> bool:
    if not top:
        return True
    if top in (AGENTCORE_ROOT, ATTACHMENTS_DIR, _EXTERNAL_TOP):
        return True
    return is_declared_latent_dir(top)


async def desk_is_visibly_empty(backend: WorkspaceBackend) -> bool:
    """True when visible top-level has no user structure."""
    from agentcore.workspace.protocol import DirListing, WorkspaceError

    try:
        listing = await backend.list(".", "*", cap=_LIST_CAP)
    except WorkspaceError:
        return True
    if not isinstance(listing, DirListing):
        listing = DirListing(entries=list(listing or []), truncated=False)
    for entry in listing.entries:
        top = _top_name(getattr(entry, "path", "") or "")
        if _ignored_visible_top(top):
            continue
        return False
    return not bool(getattr(listing, "truncated", False))
