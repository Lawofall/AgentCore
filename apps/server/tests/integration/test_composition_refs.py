"""Parent delete for table and IM rows: cascade children, null pointers."""

from uuid import uuid4

from agentcore.db.models import (
    Chat,
    ChatMember,
    ChatMessage,
    Conversation,
    Table,
    TableRow,
    TableView,
)


async def test_table_children_follow_the_grid_and_pointers_null(session_factory):
    uid = str(uuid4())
    conversation_id = str(uuid4())
    table_id = str(uuid4())
    view_id = str(uuid4())
    row_id = str(uuid4())
    async with session_factory() as session:
        session.add(Conversation(id=conversation_id, user_id=uid, title="bound"))
        session.add(
            Table(id=table_id, user_id=uid, title="grid", conversation_id=conversation_id)
        )
        session.add(TableView(id=view_id, table_id=table_id, name="主视图"))
        await session.commit()
    async with session_factory() as session:
        table = await session.get(Table, table_id)
        assert table is not None
        table.active_view_id = view_id
        session.add(TableRow(id=row_id, table_id=table_id, cells={}, position=1))
        await session.commit()

    async with session_factory() as session:
        view = await session.get(TableView, view_id)
        assert view is not None
        await session.delete(view)
        await session.commit()
    async with session_factory() as session:
        table = await session.get(Table, table_id)
        assert table is not None
        assert table.active_view_id is None
        assert await session.get(TableRow, row_id) is not None

    async with session_factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        await session.delete(conversation)
        await session.commit()
    async with session_factory() as session:
        table = await session.get(Table, table_id)
        assert table is not None
        assert table.conversation_id is None
        await session.delete(table)
        await session.commit()
    async with session_factory() as session:
        assert await session.get(Table, table_id) is None
        assert await session.get(TableRow, row_id) is None


async def test_chat_children_follow_the_chat_and_pointers_null(session_factory):
    user_id = str(uuid4())
    chat_id = str(uuid4())
    target_id = str(uuid4())
    reply_id = str(uuid4())
    async with session_factory() as session:
        session.add(Chat(id=chat_id, type="group", title="g"))
        session.add(ChatMessage(id=target_id, chat_id=chat_id, content="a"))
        await session.commit()
    async with session_factory() as session:
        session.add(
            ChatMessage(
                id=reply_id,
                chat_id=chat_id,
                content="b",
                reply_to_message_id=target_id,
            )
        )
        session.add(
            ChatMember(chat_id=chat_id, user_id=user_id, last_read_message_id=target_id)
        )
        await session.commit()

    async with session_factory() as session:
        target = await session.get(ChatMessage, target_id)
        assert target is not None
        await session.delete(target)
        await session.commit()
    async with session_factory() as session:
        reply = await session.get(ChatMessage, reply_id)
        member = await session.get(ChatMember, (chat_id, user_id))
        assert reply is not None and reply.reply_to_message_id is None
        assert member is not None and member.last_read_message_id is None
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        await session.delete(chat)
        await session.commit()
    async with session_factory() as session:
        assert await session.get(ChatMessage, reply_id) is None
        assert await session.get(ChatMember, (chat_id, user_id)) is None
        assert await session.get(Chat, chat_id) is None
