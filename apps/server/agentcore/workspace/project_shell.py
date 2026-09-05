"""Empty-desk project-shell rewrite (双模式工作区 §5.2 / §5.4).

Turn-scoped: first unknown top-level segment on a visibly empty desk is
``stripped_slug``; later write / read / mkdir / artifacts / write-claims strip
that prefix for the rest of the turn. Not ``sanitize_write_relpath`` (diskless).
Empty-desk predicate does not reuse ``path_has_non_internal_entries``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.workspace.attachments import ATTACHMENTS_DIR
from agentcore.workspace.declared_dirs import is_declared_latent_dir
from agentcore.workspace.external_mounts import EXTERNAL_PREFIX
from agentcore.workspace.stage_dirs import AGENTCORE_ROOT

if TYPE_CHECKING:
    from agentcore.tools.protocol import ToolContext
    from agentcore.workspace.protocol import WorkspaceBackend

# 约定工程目录：空桌写入这些顶层段不剥、不登记为工程壳。
# 建站锁死 site/；绿场软件固定 app/（禁止再派生应用名 slug 当工程根）。
CONVENTION_PROJECT_DIRS: frozenset[str] = frozenset(
    {"site", "app", "src", "docs", "design", "game", AGENTCORE_ROOT}
)

_EXTERNAL_TOP = EXTERNAL_PREFIX.strip("/")
_PROTECTED_TOP: frozenset[str] = CONVENTION_PROJECT_DIRS | {
    ATTACHMENTS_DIR,
    _EXTERNAL_TOP,
}

_LIST_CAP = 256

__all__ = [
    "CONVENTION_PROJECT_DIRS",
    "desk_is_visibly_empty",
    "project_shell_of",
    "rewrite_deliverable_shell",
    "rewrite_plan_project_shell",
    "rewrite_project_shell_relpath",
]


def project_shell_of(context: ToolContext) -> Any:
    """Shared turn slot (``replace`` / workspace-slot fork keep the same object)."""
    return context.project_shell


def _norm_rel(path: str) -> str:
    from agentcore.workspace._paths import normalize_workspace_path

    raw = (path or "").strip()
    if not raw:
        return ""
    labeled = normalize_workspace_path(raw, root_label="workspace")
    p = labeled.replace("\\", "/").strip("/")
    if p in ("", "."):
        return ""
    return p


def _split_top(rel: str) -> tuple[str, str]:
    top, _sep, rest = rel.partition("/")
    return top, rest


def _is_protected_top(top: str) -> bool:
    if not top or top in (".", ".."):
        return True
    if top in _PROTECTED_TOP:
        return True
    return is_declared_latent_dir(top)


def _has_dotdot(rel: str) -> bool:
    """Traversal stays for the containment guard — never a project-shell slug."""
    return any(part == ".." for part in rel.split("/"))


def _top_name(entry_path: str) -> str:
    rel = (entry_path or "").replace("\\", "/").strip("/")
    if not rel or rel == ".":
        return ""
    return rel.split("/", 1)[0]


def _ignored_visible_top(top: str) -> bool:
    """True when a root listing entry is not user structure.

    Ignores all ``AgentCore/`` (including visible ``文档/``), ``attachments/``,
    declared latent dirs, and ``external/``. Disk-only: child Folder rows that
    exist only in DB do **not** make the desk non-empty (that would disable
    shell-strip on the parent). Those names are skipped at register time
    instead — see ``_direct_child_folder_names``.
    """
    if not top:
        return True
    if top in (AGENTCORE_ROOT, ATTACHMENTS_DIR, _EXTERNAL_TOP):
        return True
    return is_declared_latent_dir(top)


async def desk_is_visibly_empty(backend: WorkspaceBackend) -> bool:
    """Visible top-level has no user structure (not ``path_has_non_internal_entries``)."""
    from agentcore.workspace.protocol import DirListing, WorkspaceError

    try:
        listing = await backend.list(".", "*", cap=_LIST_CAP)
    except WorkspaceError:
        return True
    if not isinstance(listing, DirListing):
        listing = DirListing(entries=list(listing or []), truncated=False)
    found_user = False
    for entry in listing.entries:
        top = _top_name(getattr(entry, "path", "") or "")
        if _ignored_visible_top(top):
            continue
        found_user = True
        break
    if found_user:
        return False
    # Truncated listing of only ignored names: do not assume empty (might hide user files).
    return not bool(getattr(listing, "truncated", False))


async def _desk_empty_cached(context: ToolContext) -> bool:
    """``False`` is stable (structure only grows); never cache ``True``.

    A write after the first empty listing would otherwise keep the desk
    "empty" and strip a later unknown shell — flattening already-landed
    child-Folder structure (§5.2).
    """
    slot = context._workspace
    cached = getattr(slot, "desk_visibly_empty", None)
    if cached is False:
        return False
    empty = await desk_is_visibly_empty(context.backend)
    if not empty:
        slot.desk_visibly_empty = False
    return empty


def _ownership_desk_id(context: ToolContext) -> str:
    desk = getattr(context, "ownership_desk_id", None)
    if isinstance(desk, str) and desk.strip():
        return desk.strip()
    return ""


async def _load_direct_child_folder_names(*, user_id: str, folder_id: str) -> frozenset[str]:
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories import FolderRepository
    from agentcore.workspace.cloud_tree import (
        normalize_rel_path,
        rel_path_name,
    )
    from agentcore.workspace.cloud_tree import parent_rel_path as parent_rel_path_of

    async with async_session_factory() as session:
        repo = FolderRepository(session)
        folder = await repo.get_by_id(folder_id, user_id=user_id)
        if folder is None:
            return frozenset()
        parent = normalize_rel_path(getattr(folder, "rel_path", None) or "")
        siblings = await repo.list_by_user(user_id)
    names = {
        rel_path_name(getattr(row, "rel_path", None) or "")
        for row in siblings
        if getattr(row, "id", None) != folder_id
        and parent_rel_path_of(getattr(row, "rel_path", None) or "") == parent
    }
    return frozenset(name for name in names if name)


async def _direct_child_folder_names(context: ToolContext) -> frozenset[str]:
    """Direct child Folder names of the sitting desk (DB, may not be on disk).

    ``WorkspaceSlot.child_folder_names`` is a test override; production leaves
    it ``None`` and loads on each register attempt (so a same-turn
    ``create_folder`` is visible). No desk id → no children.
    """
    slot = context._workspace
    injected = getattr(slot, "child_folder_names", None)
    if injected is not None:
        return frozenset(injected)
    desk_id = _ownership_desk_id(context)
    user_id = getattr(context, "user_id", None)
    if not desk_id or not isinstance(user_id, str) or not user_id.strip():
        return frozenset()
    try:
        return await _load_direct_child_folder_names(
            user_id=user_id.strip(), folder_id=desk_id
        )
    except Exception:  # noqa: BLE001 — register guard is best-effort
        return frozenset()


async def rewrite_project_shell_relpath(
    path: str,
    context: ToolContext,
    *,
    register: bool,
    register_bare: bool = False,
) -> tuple[str, str]:
    """Rewrite ``path`` against the turn ``stripped_slug``.

    ``register=True`` (write / mkdir / artifacts) may stamp the slug on an empty
    desk. ``register=False`` (read / delete) only applies an already-registered
    slug. ``register_bare=True`` (mkdir) also stamps a single-segment path so
    ``mkdir court-game`` becomes the workspace root instead of creating a shell
    dir. File writes keep the default: ``README.md`` is not a slug.
    """
    requested = (path or "").strip()
    if not requested:
        return "", ""
    rel = _norm_rel(requested)
    if not rel:
        return requested, ""
    top, rest = _split_top(rel)
    if _is_protected_top(top) or _has_dotdot(rel):
        return rel, ""

    shell = project_shell_of(context)
    slug = getattr(shell, "stripped_slug", None)
    if isinstance(slug, str) and slug and top == slug:
        return (rest if rest else ""), ""

    if not register or slug:
        return rel, ""
    if not rest and not register_bare:
        return rel, ""
    if not await _desk_empty_cached(context):
        return rel, ""
    if top in await _direct_child_folder_names(context):
        return rel, ""
    shell.stripped_slug = top
    return (rest if rest else ""), ""


async def rewrite_deliverable_shell(deliverable: Any, context: ToolContext) -> None:
    """Rewrite ``artifacts`` / ``artifact_dir`` in place (same slug as file tools)."""
    if deliverable is None:
        return
    arts = getattr(deliverable, "artifacts", None)
    if isinstance(arts, list) and arts:
        rewritten: list[Any] = []
        for raw in arts:
            if isinstance(raw, str) and raw.strip():
                actual, _note = await rewrite_project_shell_relpath(
                    raw, context, register=True
                )
                rewritten.append(actual if actual else raw.strip())
            else:
                rewritten.append(raw)
        deliverable.artifacts = rewritten
    artifact_dir = getattr(deliverable, "artifact_dir", None)
    if isinstance(artifact_dir, str) and artifact_dir.strip():
        actual, _note = await rewrite_project_shell_relpath(
            artifact_dir, context, register=True
        )
        if actual:
            deliverable.artifact_dir = actual


async def rewrite_plan_project_shell(plan: Any, context: ToolContext) -> None:
    """Rewrite every node's deliverable artifacts onto the shared turn slug."""
    nodes = getattr(plan, "nodes", None) or ()
    for node in nodes:
        await rewrite_deliverable_shell(getattr(node, "deliverable", None), context)
