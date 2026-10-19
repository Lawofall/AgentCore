"""Parent delete for IM rows: cascade children, null pointers."""

from uuid import uuid4

from agentcore.db.models import (
    Chat,
    ChatMember,
    ChatMessage,
)


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
