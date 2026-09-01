"""Immediate permanent folder wipe (彻底删除文件夹).

Hard-deletes every member conversation (cascade messages / runs / journal / …),
purges the shared cloud ``folder:<id>`` workspace directory + server snapshots,
unbinds boards + bare-chat ``auto_desk_folder_id`` soft-pointers (via
:func:`clear_folder_session_pointers`), physically removes documents in those
injection scopes, then removes the folder rows.

Scope is the whole subtree — the same one
:func:`agentcore.folders.tree_ops.soft_delete_folder_tree` takes. Nested folders
live *inside* the target's directory, which is removed wholesale here, so wiping
only the target's own row would leave its children pointing at a ``rel_path``
whose directory is already gone.

Local-mode folders bind a user OS directory: this path never touches that
directory — only DB rows and server-side workspace data are cleared.
"""

from __future__ import annotations

from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    ConversationShareRepository,
    DocumentRepository,
    FolderRepository,
)
from agentcore.folders.unbind import clear_folder_session_pointers
from agentcore.workspace import grant_store
from agentcore.workspace.retention import purge_folder_space


async def permanent_delete_folder(*, folder_id: str, user_id: str) -> bool:
    """Wipe a live project subtree: member chats, cloud space/snapshots, folder rows."""
    async with async_session_factory() as session:
        folder_repo = FolderRepository(session)
        conv_repo = ConversationRepository(session)
        folder = await folder_repo.get_by_id(folder_id, user_id=user_id)
        if not folder:
            return False
        folder_rel_path = folder.rel_path
        subtree_ids = await folder_repo.list_live_subtree_ids(folder_id, user_id=user_id)
        conv_ids: list[str] = []
        for member_id in subtree_ids:
            conv_ids.extend(await conv_repo.list_ids_by_folder(member_id, user_id=user_id))

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
        # Soft-pointers (boards + bare-chat auto desk); members already hard-deleted.
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

    async with async_session_factory() as session:
        await DocumentRepository(session).hard_delete_for_folders(
            user_id, subtree_ids, commit=False
        )
        await FolderRepository(session).hard_delete_many(subtree_ids)
    from agentcore.memory.account_prepare_cache import hibernate_folder_injection_cache

    await hibernate_folder_injection_cache(user_id, subtree_ids)
    return True
