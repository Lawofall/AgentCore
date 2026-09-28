"""Conversation.auto_desk_folder_id atomic first-write + clear (integration)."""

from __future__ import annotations

import asyncio

import pytest

from agentcore.db.repositories import ConversationRepository, FolderRepository, UserRepository


@pytest.mark.asyncio
async def test_set_auto_desk_folder_id_conditional_first_write_wins(session_factory):
    """WHERE … IS NULL：后写者输掉竞态，返回赢家 id 且 won=False。"""
    desk_a_id = ""
    desk_b_id = ""
    async with session_factory() as session:
        user = await UserRepository(session).create(
            username="auto_desk_race_u", display_name="auto_desk_race_u"
        )
        uid = user.user_id
        desk_a_id = (await FolderRepository(session).create(user_id=uid, name="A")).id
        desk_b_id = (await FolderRepository(session).create(user_id=uid, name="B")).id
        conv = await ConversationRepository(session).create(
            user_id=uid, title="裸聊", folder_id=None
        )
        cid = conv.id

    async with session_factory() as session:
        repo = ConversationRepository(session)
        won = await repo.set_auto_desk_folder_id(cid, desk_a_id, user_id=uid)
        lost = await repo.set_auto_desk_folder_id(cid, desk_b_id, user_id=uid)

    assert won == (desk_a_id, True)
    assert lost == (desk_a_id, False)

    async with session_factory() as session:
        conv = await ConversationRepository(session).get_by_id(cid, user_id=uid)
    assert conv is not None
    assert conv.auto_desk_folder_id == desk_a_id


@pytest.mark.asyncio
async def test_set_auto_desk_folder_id_concurrent_writers(session_factory):
    """两会话并发条件更新：恰好一个 won=True，双方 effective 一致。"""
    desk_x = ""
    desk_y = ""
    async with session_factory() as session:
        user = await UserRepository(session).create(
            username="auto_desk_conc_u", display_name="auto_desk_conc_u"
        )
        uid = user.user_id
        desk_x = (await FolderRepository(session).create(user_id=uid, name="X")).id
        desk_y = (await FolderRepository(session).create(user_id=uid, name="Y")).id
        conv = await ConversationRepository(session).create(
            user_id=uid, title="裸聊", folder_id=None
        )
        cid = conv.id

    async def _write(folder_id: str) -> tuple[str | None, bool]:
        async with session_factory() as session:
            return await ConversationRepository(session).set_auto_desk_folder_id(
                cid, folder_id, user_id=uid
            )

    results = await asyncio.gather(_write(desk_x), _write(desk_y))
    wins = [r for r in results if r[1]]
    assert len(wins) == 1
    effective = {r[0] for r in results}
    assert effective == {wins[0][0]}
    assert wins[0][0] in (desk_x, desk_y)


@pytest.mark.asyncio
async def test_clear_auto_desk_folder_id_cas(session_factory):
    """失效指针：仅当 expected 仍匹配时清掉，避免误清新桌。"""
    fresh_id = ""
    async with session_factory() as session:
        user = await UserRepository(session).create(
            username="auto_desk_clear_u", display_name="auto_desk_clear_u"
        )
        uid = user.user_id
        folder = await FolderRepository(session).create(user_id=uid, name="dead")
        dead_id = folder.id
        fresh_id = (await FolderRepository(session).create(user_id=uid, name="fresh")).id
        conv = await ConversationRepository(session).create(
            user_id=uid, title="裸聊", folder_id=None
        )
        cid = conv.id
        await ConversationRepository(session).set_auto_desk_folder_id(
            cid, dead_id, user_id=uid
        )

    async with session_factory() as session:
        repo = ConversationRepository(session)
        assert await repo.clear_auto_desk_folder_id(
            cid, user_id=uid, expected_folder_id=dead_id
        )
        # Already NULL — no-op
        assert not await repo.clear_auto_desk_folder_id(
            cid, user_id=uid, expected_folder_id=dead_id
        )
        conv = await repo.get_by_id(cid, user_id=uid)
    assert conv is not None
    assert conv.auto_desk_folder_id is None

    # CAS: wrong expected must not clear a fresh pointer
    async with session_factory() as session:
        repo = ConversationRepository(session)
        await repo.set_auto_desk_folder_id(cid, fresh_id, user_id=uid)
        assert not await repo.clear_auto_desk_folder_id(
            cid, user_id=uid, expected_folder_id=dead_id
        )
        conv = await repo.get_by_id(cid, user_id=uid)
    assert conv is not None
    assert conv.auto_desk_folder_id == fresh_id
