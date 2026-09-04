"""Cloud workspace tree migration (conv scratch → folder desk).

Auto-desk mint only creates cloud folders; local desks are out of scope.
Idempotent: empty source, identical roots, or already-moved trees are no-ops.
"""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from agentcore.core.logging import get_logger
from agentcore.workspace._paths import is_internal_zone_relpath
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)


@dataclass
class MergeMoveResult:
    """Outcome of merging one scratch tree into a desk root."""

    moved: int = 0
    skipped_conflicts: int = 0
    skipped_internal: int = 0


def _server_root(backend: WorkspaceBackend) -> Path | None:
    """Return a cloud ``ServerWorkspace`` on-disk root, else None.

    Sidecar reuses ServerWorkspace with ``location="local"`` (real disk via
    engine-on-desktop) — auto-desk migrate is cloud-only, so skip those.
    ``LocalWorkspace`` (channel) likewise has no migratable Path root here.
    """
    if getattr(backend, "location", None) != "server":
        return None
    root = getattr(backend, "root", None)
    if root is None:
        root = getattr(backend, "_root", None)
    if isinstance(root, Path):
        return root
    return None


def _rmdir_if_empty(path: Path) -> None:
    if not path.is_dir():
        return
    try:
        next(path.iterdir())
    except StopIteration:
        with contextlib.suppress(OSError):
            path.rmdir()
    except OSError:
        pass


def merge_move_tree(src: Path, dest: Path, *, rel: str = "") -> MergeMoveResult:
    """Merge ``src`` into ``dest`` (整树搬移).

    Shared by mint-time relocate and the存量 backfill pass so both move trees
    the same way:

    - Skip ``AgentCore/{index,trash,baselines}`` (and under) — root-scoped
      derived state (code index / trash / baselines) must not follow the tree.
    - Directories always recurse, so internal zones under ``AgentCore/`` stay put.
    - Missing dest file → ``shutil.move``.
    - File / type conflict → leave source in place (never overwrite the desk).

    Idempotent: a second pass finds an empty (or conflict-only) source and moves
    nothing more.
    """
    result = MergeMoveResult()
    if not src.is_dir():
        return result
    dest.mkdir(parents=True, exist_ok=True)

    try:
        children = list(src.iterdir())
    except OSError:
        return result

    for child in children:
        child_rel = f"{rel}/{child.name}" if rel else child.name
        child_rel = child_rel.replace("\\", "/").strip("/")

        if is_internal_zone_relpath(child_rel):
            result.skipped_internal += 1
            continue

        target = dest / child.name
        if child.is_dir():
            nested = merge_move_tree(child, target, rel=child_rel)
            result.moved += nested.moved
            result.skipped_conflicts += nested.skipped_conflicts
            result.skipped_internal += nested.skipped_internal
            _rmdir_if_empty(child)
            continue
        if not target.exists():
            shutil.move(str(child), str(target))
            result.moved += 1
            continue
        result.skipped_conflicts += 1

    _rmdir_if_empty(src)
    return result


def migrate_cloud_workspace_tree(*, src_root: Path, dst_root: Path) -> int:
    """Move all entries from ``src_root`` into ``dst_root`` (cloud paths only).

    Returns count of newly placed leaf entries. Safe to re-run.
    """
    if not src_root.is_dir():
        return 0
    try:
        if src_root.resolve() == dst_root.resolve():
            return 0
    except OSError:
        return 0

    result = merge_move_tree(src_root, dst_root)
    if result.moved or result.skipped_conflicts:
        logger.debug(
            "workspace.cloud_tree_migrated",
            src=str(src_root),
            dst=str(dst_root),
            moved=result.moved,
            conflicts=result.skipped_conflicts,
            skipped_internal=result.skipped_internal,
        )
    return result.moved


def transfer_backend_affines(src: WorkspaceBackend, dst: WorkspaceBackend) -> None:
    """Copy list-materials + external mounts from ``src`` onto ``dst``."""
    materials = getattr(src, "ai_list_materials", None)
    if isinstance(materials, frozenset):
        dst.ai_list_materials = materials
    elif materials:
        dst.ai_list_materials = frozenset(materials)

    mounts = getattr(src, "_mounts", None)
    attach_ext = getattr(dst, "attach_external_mounts", None)
    if mounts and callable(attach_ext):
        attach_ext(dict(mounts))
        bridge = getattr(src, "_external_bridge", None)
        channel = getattr(bridge, "_channel", None) if bridge is not None else None
        attach_ch = getattr(dst, "attach_external_channel", None)
        if channel is not None and callable(attach_ch):
            attach_ch(channel)


def migrate_and_transfer_cloud_backend(
    src: WorkspaceBackend,
    dst: WorkspaceBackend,
) -> int:
    """If both backends are on-disk server roots, move the tree then transfer affines.

    Returns migrated leaf count (0 when skipped / local / identical).
    """
    src_root = _server_root(src)
    dst_root = _server_root(dst)
    moved = 0
    if src_root is not None and dst_root is not None:
        moved = migrate_cloud_workspace_tree(src_root=src_root, dst_root=dst_root)
    transfer_backend_affines(src, dst)
    return moved


# Re-export for callers / tests.
__all__ = [
    "MergeMoveResult",
    "merge_move_tree",
    "migrate_and_transfer_cloud_backend",
    "migrate_cloud_workspace_tree",
    "transfer_backend_affines",
]
