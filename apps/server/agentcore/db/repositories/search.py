"""Account search providers and the platform search-count ledger."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models import PlatformSearchUse, UserSearchProvider
from agentcore.db.repositories._base import _UNSET, commit_or_flush


class UserSearchProviderRepository:
    """Owner-scoped rows. Encryption stays in the service."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_for_user(self, user_id: str) -> Sequence[UserSearchProvider]:
        result = await self._session.execute(
            select(UserSearchProvider)
            .where(UserSearchProvider.user_id == user_id)
            .order_by(UserSearchProvider.created_at.asc(), UserSearchProvider.id.asc())
        )
        return result.scalars().all()

    async def count_for_user(self, user_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(UserSearchProvider)
            .where(UserSearchProvider.user_id == user_id)
        )
        return int(result.scalar_one() or 0)

    async def get(self, provider_id: str, *, user_id: str) -> UserSearchProvider | None:
        result = await self._session.execute(
            select(UserSearchProvider).where(
                UserSearchProvider.id == provider_id,
                UserSearchProvider.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: str,
        label: str,
        protocol: str,
        base_url: str,
        api_key_enc: bytes | None,
        commit: bool = True,
    ) -> UserSearchProvider:
        row = UserSearchProvider(
            id=new_id(),
            user_id=user_id,
            label=label,
            protocol=protocol,
            base_url=base_url,
            api_key_enc=api_key_enc,
            status="unchecked",
        )
        self._session.add(row)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def update(
        self,
        provider_id: str,
        *,
        user_id: str,
        label: str | object = _UNSET,
        base_url: str | object = _UNSET,
        api_key_enc: bytes | None | object = _UNSET,
        clear_key: bool = False,
        commit: bool = True,
    ) -> UserSearchProvider | None:
        row = await self.get(provider_id, user_id=user_id)
        if row is None:
            return None
        reset_status = False
        if label is not _UNSET:
            row.label = str(label or "").strip()
        if base_url is not _UNSET:
            row.base_url = str(base_url).strip().rstrip("/")
            reset_status = True
        if clear_key:
            row.api_key_enc = None
            reset_status = True
        elif api_key_enc is not _UNSET:
            row.api_key_enc = api_key_enc  # type: ignore[assignment]
            reset_status = True
        if reset_status:
            row.status = "unchecked"
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(row)
        return row

    async def set_status(
        self,
        provider_id: str,
        *,
        user_id: str,
        status: str,
        commit: bool = True,
    ) -> None:
        await self._session.execute(
            update(UserSearchProvider)
            .where(
                UserSearchProvider.id == provider_id,
                UserSearchProvider.user_id == user_id,
            )
            .values(status=status)
        )
        await commit_or_flush(self._session, commit=commit)

    async def delete(self, provider_id: str, *, user_id: str, commit: bool = True) -> bool:
        row = await self.get(provider_id, user_id=user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await commit_or_flush(self._session, commit=commit)
        return True

    async def delete_all_for_user(self, user_id: str, *, commit: bool = True) -> None:
        rows = await self.list_for_user(user_id)
        for row in rows:
            await self._session.delete(row)
        await commit_or_flush(self._session, commit=commit)


class PlatformSearchUseRepository:
    """Append-only count of platform SearXNG requests that were sent."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def count_since(self, user_id: str, since: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(PlatformSearchUse)
            .where(
                PlatformSearchUse.user_id == user_id,
                PlatformSearchUse.created_at >= since,
            )
        )
        return int(result.scalar_one() or 0)

    async def record(self, user_id: str, *, commit: bool = True) -> None:
        self._session.add(PlatformSearchUse(id=new_id(), user_id=user_id))
        await commit_or_flush(self._session, commit=commit)
