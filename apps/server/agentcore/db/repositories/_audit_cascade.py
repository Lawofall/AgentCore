"""Cascade-delete helpers for ``agent_audit_events`` on message truncate.

Conversation hard-delete drops the rows via ``fk_agent_audit_events_conversation_id``.
Regenerate / single-message delete still drops the turn's rows here, because
``turn_id`` is not a foreign key to ``messages``.
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import AgentAuditEvent, Message


async def delete_audit_after(
    session: AsyncSession, conversation_id: str, *, after_created_at: datetime
) -> None:
    await session.execute(
        delete(AgentAuditEvent).where(
            AgentAuditEvent.turn_id.in_(
                select(Message.id).where(
                    Message.conversation_id == conversation_id,
                    Message.created_at > after_created_at,
                )
            )
        )
    )


async def delete_audit_for_message(
    session: AsyncSession, conversation_id: str, message_id: str
) -> None:
    await session.execute(
        delete(AgentAuditEvent).where(
            AgentAuditEvent.turn_id == message_id,
            AgentAuditEvent.conversation_id == conversation_id,
        )
    )
