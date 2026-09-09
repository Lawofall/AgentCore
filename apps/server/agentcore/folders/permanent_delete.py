"""Immediate permanent folder wipe (彻底删除文件夹).

Hard-deletes every member conversation (cascade messages / runs / journal / …),
purges the shared cloud ``folder:<id>`` workspace directory + server snapshots,
unbinds bare-chat ``auto_desk_folder_id`` soft-pointers (via
:func:`clear_folder_session_pointers`), physically removes documents in those
injection scopes, then removes the folder rows.

Two entry points, same member-chat semantics (弹窗勾选 = 最近删除里再确认):

* :func:`permanent_delete_folder` — live tree. Passes the live ``rel_path`` so
  the visible directory is removed. Not safe to call on a trash row: that slot
  is already free and may belong to a new folder.
* :func:`purge_trashed_folder` — already in「最近删除」. Tombstone only
  (``rel_path=None``), serialized with restore via ``workspace_lock_nowait``.

Scope is the whole subtree — the same one
:func:`agentcore.folders.tree_ops.soft_delete_folder_tree` takes. Nested folders
live *inside* the target's directory, which is removed wholesale here, so wiping
only the target's own row would leave its children pointing at a ``rel_path``
whose directory is already gone.

Local-mode folders bind a user OS directory: this path never touches that
directory — only DB rows and server-side workspace data are cleared.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    ConversationShareRepository,
    DocumentRepository,
    FolderRepository,
)
from agentcore.folders.unbind import clear_folder_session_pointers
from agentcore.workspace import grant_store
from agentcore.workspace.cloud_tree import is_same_or_descendant, normalize_rel_path
from agentcore.workspace.locate import workspace_storage_key
from agentcore.workspace.locks import workspace_lock_nowait
from agentcore.workspace.retention import (
    purge_folder_space,
    purge_folder_space_unlocked,
    retention_cutoff,
)


async def _hard_delete_conversations(conv_ids: list[str]) -> None:
    """Tear down side-state then physically remove each member chat."""
    async with async_session_factory() as session:
        conv_repo = ConversationRepository(session)
        share_repo = ConversationShareRepository(session)
        for conversation_id in conv_ids:
            await share_repo.revoke_all_for_conversation(conversation_id)
            await grant_store.clear_conversation(conversation_id)
            from agentcore.runtime.browser import default_browser_session_registry
            from agentcore.workspace import organize_journal, organize_plan_store

            organize_plan_store.clear_conversation(conversation_id)
            organize_journal.clear_conversation(conversation_id)
            # L3 team-browser: cascade-close any live sandbox session (no-op when absent).
            await default_browser_session_registry().close(conversation_id)
            await conv_repo.hard_delete(conversation_id)


async def _collect_member_conv_ids(subtree_ids: list[str], *, user_id: str) -> list[str]:
    conv_ids: list[str] = []
    async with async_session_factory() as session:
        conv_repo = ConversationRepository(session)
        for member_id in subtree_ids:
            conv_ids.extend(await conv_repo.list_ids_by_folder(member_id, user_id=user_id))
    return conv_ids


async def _finish_folder_rows(*, user_id: str, subtree_ids: list[str]) -> None:
    async with async_session_factory() as session:
        await DocumentRepository(session).hard_delete_for_folders(
            user_id, subtree_ids, commit=False
        )
        await FolderRepository(session).hard_delete_many(subtree_ids)
    from agentcore.memory.account_prepare_cache import hibernate_folder_injection_cache

    await hibernate_folder_injection_cache(user_id, subtree_ids)


async def permanent_delete_folder(*, folder_id: str, user_id: str) -> bool:
    """Wipe a live project subtree: member chats, cloud space/snapshots, folder rows."""
    async with async_session_factory() as session:
        folder_repo = FolderRepository(session)
        folder = await folder_repo.get_by_id(folder_id, user_id=user_id)
        if not folder:
            return False
        folder_rel_path = folder.rel_path
        subtree_ids = await folder_repo.list_live_subtree_ids(folder_id, user_id=user_id)

    conv_ids = await _collect_member_conv_ids(subtree_ids, user_id=user_id)
    await _hard_delete_conversations(conv_ids)
    async with async_session_factory() as session:
        for member_id in subtree_ids:
            await clear_folder_session_pointers(
                session, folder_id=member_id, user_id=user_id
            )
        await session.commit()

    # Server-side cloud root + snapshots (also clears any residual server mirror for
    # local projects). Never the user's OS directory behind ``local_root_id``.
    await purge_folder_space(
        user_id=user_id, folder_id=folder_id, folder_rel_path=folder_rel_path
    )
    for member_id in subtree_ids:
        if member_id == folder_id:
            continue
        # A descendant's directory sat inside the root's and just went with it; only
        # its id-keyed data is left (tombstone, hidden zones, snapshots). Passing no
        # rel_path keeps this from re-deriving a path that no longer exists.
        await purge_folder_space(
            user_id=user_id, folder_id=member_id, folder_rel_path=None
        )

    await _finish_folder_rows(user_id=user_id, subtree_ids=subtree_ids)
    return True


def _deleted_subtree_ids(
    folder, descendants: list, *, folder_id: str
) -> list[str]:
    """Parent + cascade children that went down in the same soft-delete batch."""
    stored_rel = normalize_rel_path(folder.rel_path) if folder.rel_path else ""
    ids = [folder_id]
    for row in descendants:
        rel = normalize_rel_path(row.rel_path) if row.rel_path else ""
        if stored_rel and rel and is_same_or_descendant(rel, stored_rel):
            ids.append(row.id)
    return ids


async def purge_trashed_folder(*, folder_id: str, user_id: str) -> bool:
    """Wipe a user-deleted project still in「最近删除」.

    Checkbox semantics (member chats + cloud files + desk documents), but the
    disk path is the tombstone: never ``rmtree`` the live ``rel_path`` (that
    name is free and may already host a new folder). Holds the same workspace
    locks as restore so the two cannot interleave; a restore that already
    committed makes this return ``False`` (route → 409).

    Raises :class:`WorkspaceBusyError` when a turn holds the lock — same 409
    the restore route already maps.
    """
    async with async_session_factory() as session:
        folder_repo = FolderRepository(session)
        folder = await folder_repo.get_deleted_by_id(folder_id, user_id=user_id)
        if not folder:
            return False
        if folder.deleted_at is None or folder.deleted_at <= retention_cutoff():
            return False
        descendants = list(
            await folder_repo.list_deleted_subtree(
                folder_id, user_id=user_id, deleted_at=folder.deleted_at
            )
        )
        lock_ids = _deleted_subtree_ids(folder, descendants, folder_id=folder_id)

    keys = sorted(
        workspace_storage_key(user_id=user_id, folder_id=fid, conversation_id="")
        for fid in lock_ids
    )
    async with AsyncExitStack() as stack:
        for key in keys:
            await stack.enter_async_context(workspace_lock_nowait(key))

        async with async_session_factory() as session:
            folder_repo = FolderRepository(session)
            folder = await folder_repo.get_deleted_by_id(folder_id, user_id=user_id)
            if not folder:
                return False
            if folder.deleted_at is None or folder.deleted_at <= retention_cutoff():
                return False
            descendants = list(
                await folder_repo.list_deleted_subtree(
                    folder_id, user_id=user_id, deleted_at=folder.deleted_at
                )
            )
            subtree_ids = _deleted_subtree_ids(
                folder, descendants, folder_id=folder_id
            )

        conv_ids = await _collect_member_conv_ids(subtree_ids, user_id=user_id)
        await _hard_delete_conversations(conv_ids)
        async with async_session_factory() as session:
            for member_id in subtree_ids:
                await clear_folder_session_pointers(
                    session, folder_id=member_id, user_id=user_id
                )
            await session.commit()

        # Parent directory already sits in the tombstone; the live slot was
        # released at soft-delete. Never pass ``rel_path``.
        await purge_folder_space_unlocked(
            user_id=user_id, folder_id=folder_id, folder_rel_path=None
        )
        for member_id in subtree_ids:
            if member_id == folder_id:
                continue
            await purge_folder_space_unlocked(
                user_id=user_id, folder_id=member_id, folder_rel_path=None
            )

        await _finish_folder_rows(user_id=user_id, subtree_ids=subtree_ids)
        return True
