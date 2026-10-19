"""Redemption-code inventory (admin issue / void + redeem lock)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models.seats import RedemptionCode
from agentcore.db.repositories._base import commit_or_flush


class RedemptionCodeRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_hash_for_update(self, code_hash: str) -> RedemptionCode | None:
        result = await self._session.execute(
            select(RedemptionCode)
            .where(RedemptionCode.code_hash == code_hash)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, code_id: str) -> RedemptionCode | None:
        result = await self._session.execute(
            select(RedemptionCode).where(RedemptionCode.id == code_id)
        )
        return result.scalar_one_or_none()

    async def add_many(
        self, rows: Sequence[RedemptionCode], *, commit: bool = True
    ) -> None:
        self._session.add_all(list(rows))
        await commit_or_flush(self._session, commit=commit)

    async def list_all(
        self,
        *,
        sku: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[RedemptionCode], int]:
        filters = []
        if sku:
            filters.append(RedemptionCode.sku == sku)
        if status:
            filters.append(RedemptionCode.status == status)
        count_stmt = select(func.count()).select_from(RedemptionCode)
        stmt = select(RedemptionCode)
        if filters:
            count_stmt = count_stmt.where(*filters)
            stmt = stmt.where(*filters)
        total = int((await self._session.execute(count_stmt)).scalar_one())
        result = await self._session.execute(
            stmt.order_by(RedemptionCode.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all(), total

    async def unused_counts(self) -> dict[str, int]:
        result = await self._session.execute(
            select(RedemptionCode.sku, func.count())
            .where(RedemptionCode.status == "unused")
            .group_by(RedemptionCode.sku)
        )
        counts = {"starter": 0, "plus": 0, "flagship": 0}
        for sku, n in result.all():
            counts[str(sku)] = int(n)
        return counts

    async def mark_redeemed(
        self,
        code_id: str,
        *,
        user_id: str,
        at: datetime,
        commit: bool = True,
    ) -> None:
        await self._session.execute(
            update(RedemptionCode)
            .where(RedemptionCode.id == code_id)
            .values(status="redeemed", redeemed_by=user_id, redeemed_at=at)
        )
        await commit_or_flush(self._session, commit=commit)

    async def mark_void(
        self,
        code_id: str,
        *,
        at: datetime,
        commit: bool = True,
    ) -> int:
        result = await self._session.execute(
            update(RedemptionCode)
            .where(RedemptionCode.id == code_id, RedemptionCode.status == "unused")
            .values(status="void", voided_at=at)
        )
        await commit_or_flush(self._session, commit=commit)
        return int(cast(CursorResult[Any], result).rowcount or 0)
