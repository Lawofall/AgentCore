"""Durable turn-queue rows. Caller owns the session and commits."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models.turn_queue import TurnQueueItem
from agentcore.db.repositories._base import commit_or_flush


class TurnQueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        queue_id: str,
        conversation_id: str,
        user_id: str,
        position: int,
        content: str,
        attachments: list[Any],
        agent_mentions: list[Any],
        requires_tools: bool,
        x_client_platform: str | None,
        origin_device_id: str | None,
        interjection_id: str | None,
        user_message_id: str | None,
        message_id: str | None,
        trace_id: str | None,
        engine: str,
        local_root_id: str | None,
        commit: bool = True,
    ) -> None:
        row = await self._session.get(TurnQueueItem, queue_id)
        if row is None:
            self._session.add(
                TurnQueueItem(
                    queue_id=queue_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    position=position,
                    content=content,
                    attachments=list(attachments),
                    agent_mentions=list(agent_mentions),
                    requires_tools=requires_tools,
                    x_client_platform=x_client_platform,
                    origin_device_id=origin_device_id,
                    interjection_id=interjection_id,
                    user_message_id=user_message_id,
                    message_id=message_id,
                    trace_id=trace_id,
                    engine=engine,
                    local_root_id=local_root_id,
                )
            )
        else:
            row.conversation_id = conversation_id
            row.user_id = user_id
            row.position = position
            row.content = content
            row.attachments = list(attachments)
            row.agent_mentions = list(agent_mentions)
            row.requires_tools = requires_tools
            row.x_client_platform = x_client_platform
            row.origin_device_id = origin_device_id
            row.interjection_id = interjection_id
            row.user_message_id = user_message_id
            row.message_id = message_id
            row.trace_id = trace_id
            row.engine = engine
            row.local_root_id = local_root_id
        await commit_or_flush(self._session, commit=commit)

    async def update_payload(
        self,
        queue_id: str,
        *,
        content: str,
        attachments: list[Any],
        agent_mentions: list[Any],
        commit: bool = True,
    ) -> None:
        await self._session.execute(
            update(TurnQueueItem)
            .where(TurnQueueItem.queue_id == queue_id)
            .values(
                content=content,
                attachments=list(attachments),
                agent_mentions=list(agent_mentions),
            )
        )
        await commit_or_flush(self._session, commit=commit)

    async def rewrite_order(
        self,
        conversation_id: str,
        queue_ids: list[str],
        *,
        commit: bool = True,
    ) -> None:
        for position, queue_id in enumerate(queue_ids, start=1):
            await self._session.execute(
                update(TurnQueueItem)
                .where(
                    TurnQueueItem.queue_id == queue_id,
                    TurnQueueItem.conversation_id == conversation_id,
                )
                .values(position=position)
            )
        await commit_or_flush(self._session, commit=commit)

    async def delete(self, queue_id: str, *, commit: bool = True) -> int:
        result = await self._session.execute(
            delete(TurnQueueItem).where(TurnQueueItem.queue_id == queue_id)
        )
        await commit_or_flush(self._session, commit=commit)
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def delete_conversation(self, conversation_id: str, *, commit: bool = True) -> None:
        await self._session.execute(
            delete(TurnQueueItem).where(TurnQueueItem.conversation_id == conversation_id)
        )
        await commit_or_flush(self._session, commit=commit)

    async def load(
        self,
        *,
        engine: str,
        user_id: str,
        local_root_id: str | None,
    ) -> list[TurnQueueItem]:
        stmt = select(TurnQueueItem).where(TurnQueueItem.engine == engine)
        if user_id:
            stmt = stmt.where(TurnQueueItem.user_id == user_id)
        if local_root_id is not None:
            stmt = stmt.where(TurnQueueItem.local_root_id == local_root_id)
        stmt = stmt.order_by(
            TurnQueueItem.conversation_id,
            TurnQueueItem.position,
            TurnQueueItem.created_at,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
