"""Boards are account-scoped: create ignores folder_id; AI chat is always bare."""

from sqlalchemy import update

from agentcore.db.models import Board, Conversation
from agentcore.db.repositories import FolderRepository
from tests.integration.conftest import register_and_login


async def test_create_board_ignores_folder_and_conversation_is_bare(
    client, session_factory
):
    uid = await register_and_login(client, "boardnf")
    async with session_factory() as s:
        desk = await FolderRepository(s).create(user_id=uid, name="Desk")
        desk_id = desk.id

    created = await client.post(
        "/v1/boards", json={"title": "t", "folder_id": desk_id}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert "folder_id" not in body
    board_id = body["id"]

    async with session_factory() as s:
        board = await s.get(Board, board_id)
        assert board is not None
        assert board.folder_id is None
        await s.execute(
            update(Board).where(Board.id == board_id).values(folder_id=desk_id)
        )
        await s.commit()

    bound = await client.post(f"/v1/boards/{board_id}/conversation")
    assert bound.status_code == 200, bound.text
    conv_id = bound.json()["conversation_id"]

    async with session_factory() as s:
        conv = await s.get(Conversation, conv_id)
        assert conv is not None
        assert conv.folder_id is None
