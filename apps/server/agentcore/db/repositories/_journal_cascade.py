"""Cascade-delete helpers for ``turn_journal`` rows.

Conversation hard-delete drops these rows via ``fk_turn_journal_conversation_id``.
Regenerate / single-message delete still drops them here: ``turn_id`` is not a
foreign key to ``messages`` (a paused turn writes rows before the message exists).

``turn_stream_state`` has no ``conversation_id`` either; message delete paths
call ``_stream_state_cascade`` in the same transaction, before the messages go.

None of these commit; the calling repository commits the surrounding unit of work.
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import Message, TurnJournalRow


async def delete_journal_after(
    session: AsyncSession, conversation_id: str, *, after_created_at: datetime
) -> None:
    """Drop journal rows of a conversation's messages created strictly after a point.

    The message-side of regenerate / edit-and-resend (drops the superseded tail);
    the ``turn_id`` subquery is resolved before the messages themselves are deleted.
    """
    await session.execute(
        delete(TurnJournalRow).where(
            TurnJournalRow.turn_id.in_(
                select(Message.id).where(
                    Message.conversation_id == conversation_id,
                    Message.created_at > after_created_at,
                )
            )
        )
    )


async def delete_journal_for_message(
    session: AsyncSession, conversation_id: str, message_id: str
) -> None:
    """Drop one message's journal rows (单条消息删除), conversation-scoped so a
    cross-tenant id touches nothing."""
    await session.execute(
        delete(TurnJournalRow).where(
            TurnJournalRow.turn_id == message_id,
            TurnJournalRow.conversation_id == conversation_id,
        )
    )
