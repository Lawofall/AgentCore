"""Three folder-delete paths must NULL bare-chat ``auto_desk_folder_id``.

Regression: leaving a dead auto-desk id lets the next turn recreate an rmtree'd
directory with no folders row (ghost workspace).
"""

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update

import agentcore.folders.permanent_delete as permanent_delete_mod
from agentcore.config import settings
from agentcore.db.models import Board, Conversation, Folder
from agentcore.db.repositories import (
    BoardRepository,
    ConversationRepository,
    FolderRepository,
    UserRepository,
)
from agentcore.folders.permanent_delete import permanent_delete_folder
from agentcore.storage.factory import build_storage_provider
from agentcore.workspace import retention as retention_mod


async def _seed_user_desk_and_bare(
    session_factory, *, username: str
) -> tuple[str, str, str, str]:
    """Return (user_id, desk_folder_id, bare_conv_id, board_id)."""
    async with session_factory() as s:
        uid = (await UserRepository(s).create(username=username, display_name=username)).user_id
    async with session_factory() as s:
        desk = await FolderRepository(s).create(user_id=uid, name="AutoDesk")
        board = await BoardRepository(s).create(user_id=uid, title="filed")
        # Leftover unread ``Board.folder_id`` (no longer written on create).
        await s.execute(
            update(Board).where(Board.id == board.id).values(folder_id=desk.id)
        )
        await s.commit()
        bare = await ConversationRepository(s).create(user_id=uid, title="bare")
        await ConversationRepository(s).set_auto_desk_folder_id(
            bare.id, desk.id, user_id=uid
        )
    return uid, desk.id, bare.id, board.id


async def test_soft_delete_clears_auto_desk_folder_id(session_factory):
    uid, desk_id, bare_id, board_id = await _seed_user_desk_and_bare(
        session_factory, username="unbindsoft"
    )

    async with session_factory() as s:
        assert await FolderRepository(s).soft_delete(desk_id, user_id=uid) is True

    async with session_factory() as s:
        bare = await s.get(Conversation, bare_id)
        board = await s.get(Board, board_id)
        folder = await s.get(Folder, desk_id)

    assert bare is not None
    assert bare.auto_desk_folder_id is None
    assert bare.folder_id is None
    assert board is not None
    assert board.folder_id == desk_id
    assert folder is not None
    assert folder.deleted_at is not None


async def test_permanent_delete_clears_auto_desk_folder_id(
    session_factory, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(permanent_delete_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    build_storage_provider.cache_clear()

    uid, desk_id, bare_id, board_id = await _seed_user_desk_and_bare(
        session_factory, username="unbindperm"
    )

    assert await permanent_delete_folder(folder_id=desk_id, user_id=uid) is True

    async with session_factory() as s:
        bare = await s.get(Conversation, bare_id)
        board = await s.get(Board, board_id)
        folders = (await s.execute(select(Folder.id))).scalars().all()

    assert bare is not None
    assert bare.auto_desk_folder_id is None
    assert board is not None
    assert board.folder_id == desk_id
    assert desk_id not in folders

    build_storage_provider.cache_clear()


async def test_retention_purge_clears_auto_desk_folder_id(
    session_factory, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(retention_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    monkeypatch.setattr(settings, "workspace_retention_days", 30)
    monkeypatch.setattr(settings, "workspace_retention_enabled", True)
    build_storage_provider.cache_clear()

    uid, desk_id, bare_id, board_id = await _seed_user_desk_and_bare(
        session_factory, username="unbindret"
    )

    aged = datetime.now() - timedelta(days=40)
    async with session_factory() as s:
        await FolderRepository(s).soft_delete(desk_id, user_id=uid)
        # Soft-delete already clears auto_desk; re-point to simulate a legacy row
        # that still held the dead id when purge runs (the bug class under test).
        await s.execute(
            update(Conversation)
            .where(Conversation.id == bare_id)
            .values(auto_desk_folder_id=desk_id)
        )
        await s.execute(
            update(Board).where(Board.id == board_id).values(folder_id=desk_id)
        )
        await s.execute(update(Folder).where(Folder.id == desk_id).values(deleted_at=aged))
        await s.commit()

    result = await retention_mod.run_retention_sweep()
    assert result["folders"] == 1

    async with session_factory() as s:
        bare = await s.get(Conversation, bare_id)
        board = await s.get(Board, board_id)
        folders = (await s.execute(select(Folder.id))).scalars().all()

    assert bare is not None
    assert bare.auto_desk_folder_id is None
    assert board is not None
    assert board.folder_id == desk_id
    assert desk_id not in folders

    build_storage_provider.cache_clear()
