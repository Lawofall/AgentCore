"""Folder binding lookup, claim book, and target WorkspaceBackend construction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.desk.errors import TargetDesktopError
from agentcore.workspace.locate import (
    LocalBinding,
    build_workspace,
    resolve_conversation_local_binding,
)
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)


@dataclass(frozen=True)
class TargetFolderBinding:
    """Resolved Folder row bits needed to build a worker desk."""

    folder_id: str
    name: str
    local_binding: LocalBinding | None
    # Cloud placement (``folders.rel_path``) — where the desk's directory sits.
    # Carried on the binding so ``build_target_backend`` stays synchronous instead
    # of re-querying the folder it was just resolved from.
    rel_path: str | None = None


class LocalRootClaimBook:
    """Turn-scoped registry of local ``root_id`` values (C0: never rejects)."""

    def __init__(self) -> None:
        self._roots: set[str] = set()
        self._lock = asyncio.Lock()

    async def try_claim(self, root_id: str) -> bool:
        """Record ``root_id`` for this turn; always allows distinct roots (C0)."""
        async with self._lock:
            self._roots.add(root_id)
            return True

    async def seed_from_backend(self, backend: WorkspaceBackend) -> None:
        """Record the turn's primary local root (if any) before alien desks claim."""
        root = backend_local_root_id(backend)
        if root:
            await self.try_claim(root)


async def load_target_folder_binding(
    *,
    folder_id: str,
    user_id: str,
) -> TargetFolderBinding | None:
    """Owner-scoped Folder lookup → binding for ``build_workspace``.

    Returns ``None`` when the folder is missing or not owned (business miss).
    Raises ``TargetDesktopError`` when PostgreSQL is unreachable **or** when
    folders cloud credentials are bound but the cloud HTTP call fails — honest
    failure, no local-cache fallback and no forged ``local_binding``.

    With folders narrow-ticket credentials (sidecar), uses cloud ``GET /folders/{id}``
    instead of the local FolderRepository.
    """
    from agentcore.folders.credentials import (
        FoldersCloudError,
        cloud_get_folder,
        get_folders_credentials,
    )

    creds = get_folders_credentials()
    if creds is not None:
        try:
            summary = await cloud_get_folder(creds, folder_id=folder_id)
        except FoldersCloudError as e:
            logger.warning(
                "delegate.target_folder_cloud_failed",
                folder_id=folder_id,
                user_id=user_id,
                error=str(e),
                code=e.code,
            )
            raise TargetDesktopError(f"无法绑定目标文件夹。{e.message}") from e
        if summary is None:
            return None
        binding = resolve_conversation_local_binding(
            local_root_id=summary.get("local_root_id"),
            local_subpath=summary.get("local_subpath"),
            label=str(summary.get("name") or "workspace"),
        )
        rel_path = summary.get("rel_path")
        return TargetFolderBinding(
            folder_id=str(summary.get("id") or folder_id),
            name=str(summary.get("name") or ""),
            local_binding=binding,
            rel_path=rel_path if isinstance(rel_path, str) and rel_path else None,
        )

    from agentcore.db.base import async_session_factory
    from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE, is_db_connectivity_error
    from agentcore.db.repositories import FolderRepository

    try:
        async with async_session_factory() as session:
            folder = await FolderRepository(session).get_by_id(folder_id, user_id=user_id)
            if folder is None:
                return None
            binding = resolve_conversation_local_binding(
                local_root_id=folder.local_root_id,
                local_subpath=folder.local_subpath,
                label=folder.name or "workspace",
            )
            return TargetFolderBinding(
                folder_id=folder.id,
                name=folder.name or "",
                local_binding=binding,
                rel_path=folder.rel_path,
            )
    except Exception as e:  # noqa: BLE001 — classify connectivity vs bubble
        if is_db_connectivity_error(e):
            logger.warning(
                "delegate.target_folder_db_unreachable",
                folder_id=folder_id,
                user_id=user_id,
                error=str(e),
            )
            raise TargetDesktopError(
                f"无法绑定目标文件夹。{DATABASE_UNAVAILABLE_MESSAGE}"
            ) from e
        raise


def build_target_backend(
    *,
    user_id: str,
    folder_id: str,
    conversation_id: str,
    sink: Any,
    local_binding: LocalBinding | None,
    folder_rel_path: str | None = None,
) -> WorkspaceBackend:
    """Build a worker desk for ``folder_id`` without touching session binding."""
    return build_workspace(
        user_id=user_id,
        folder_id=folder_id,
        folder_rel_path=folder_rel_path,
        conversation_id=conversation_id,
        sink=sink,
        local_binding=local_binding,
    )


def backend_local_root_id(backend: WorkspaceBackend) -> str | None:
    """Extract local ``root_id`` from a local WorkspaceBackend, else ``None``."""
    if getattr(backend, "location", None) != "local":
        return None
    channel = getattr(backend, "_channel", None)
    root_id = getattr(channel, "root_id", None) if channel is not None else None
    if isinstance(root_id, str) and root_id.strip():
        return root_id.strip()
    return None
