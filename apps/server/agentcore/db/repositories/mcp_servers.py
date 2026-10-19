"""Account remote MCP Server rows."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models.mcp import UserMcpOauthPending, UserMcpServer
from agentcore.db.repositories._base import commit_or_flush


class UserMcpServerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: str) -> Sequence[UserMcpServer]:
        result = await self._session.execute(
            select(UserMcpServer)
            .where(UserMcpServer.user_id == user_id)
            .order_by(UserMcpServer.created_at.asc(), UserMcpServer.id.asc())
        )
        return result.scalars().all()

    async def get(self, server_id: str, *, user_id: str) -> UserMcpServer | None:
        result = await self._session.execute(
            select(UserMcpServer).where(
                UserMcpServer.id == server_id,
                UserMcpServer.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        url: str,
        auth_kind: str,
        token_enc: bytes | None,
        commit: bool = True,
    ) -> UserMcpServer:
        row = UserMcpServer(
            id=new_id(),
            user_id=user_id,
            name=name,
            url=url,
            auth_kind=auth_kind,
            token_enc=token_enc,
            status="unchecked",
        )
        self._session.add(row)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def delete(self, server_id: str, *, user_id: str, commit: bool = True) -> bool:
        result = await self._session.execute(
            delete(UserMcpServer).where(
                UserMcpServer.id == server_id,
                UserMcpServer.user_id == user_id,
            )
        )
        await commit_or_flush(self._session, commit=commit)
        return (cast("CursorResult[Any]", result).rowcount or 0) > 0

    async def delete_all_for_user(self, user_id: str, *, commit: bool = True) -> None:
        await self._session.execute(
            delete(UserMcpOauthPending).where(UserMcpOauthPending.user_id == user_id)
        )
        await self._session.execute(
            delete(UserMcpServer).where(UserMcpServer.user_id == user_id)
        )
        await commit_or_flush(self._session, commit=commit)


class UserMcpOauthPendingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, row: UserMcpOauthPending, *, commit: bool = True) -> None:
        self._session.add(row)
        await commit_or_flush(self._session, commit=commit)

    async def take(self, state: str) -> UserMcpOauthPending | None:
        result = await self._session.execute(
            select(UserMcpOauthPending).where(UserMcpOauthPending.state == state)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        await self._session.delete(row)
        await self._session.commit()
        return row

    async def purge_expired(self) -> None:
        now = datetime.now(UTC)
        await self._session.execute(
            delete(UserMcpOauthPending).where(UserMcpOauthPending.expires_at <= now)
        )
        await self._session.commit()
