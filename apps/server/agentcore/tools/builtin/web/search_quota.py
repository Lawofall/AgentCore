"""Platform web_search count quota.

Orthogonal to the ¥ / token caps. Only a request that actually leaves for the
platform SearXNG counts. Own CleverSee and own SearXNG do not. Cache hits and
rejected queries never reach this module.

``0`` on a window means that window is unlimited. ``is_unlimited`` skips both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agentcore.config import settings
from agentcore.core.errors import QuotaExceededError, utc_moment_iso
from agentcore.core.logging import get_logger

logger = get_logger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RESET_HINT = "额度重置后可继续"
_OWN_EXIT = "或在设置 · 通用改接自己的搜索"


@dataclass(frozen=True)
class SearchQuotaLimits:
    daily: int
    monthly: int

    @classmethod
    def for_user(cls, user: object | None) -> SearchQuotaLimits:
        if user is not None and getattr(user, "is_unlimited", False):
            return cls(0, 0)
        daily = getattr(user, "quota_search_daily", None) if user is not None else None
        monthly = getattr(user, "quota_search_monthly", None) if user is not None else None
        return cls(
            daily if daily is not None else settings.quota_search_daily,
            monthly if monthly is not None else settings.quota_search_monthly,
        )

    @property
    def all_unlimited(self) -> bool:
        return self.daily <= 0 and self.monthly <= 0


@dataclass(frozen=True)
class SearchQuotaSnapshot:
    daily_used: int
    daily_limit: int
    monthly_used: int
    monthly_limit: int
    daily_reset_at: str
    monthly_reset_at: str


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(day_start: datetime) -> datetime:
    return day_start.replace(day=1)


def _next_day_reset(day_start: datetime) -> str:
    return utc_moment_iso(day_start + timedelta(days=1))


def _next_month_reset(month_start: datetime) -> str:
    if month_start.month == 12:
        return utc_moment_iso(month_start.replace(year=month_start.year + 1, month=1))
    return utc_moment_iso(month_start.replace(month=month_start.month + 1))


def search_quota_block(
    limits: SearchQuotaLimits,
    *,
    daily_used: int,
    monthly_used: int,
    now: datetime,
) -> QuotaExceededError | None:
    """Return the refusal when a window is exhausted. Unlimited windows are skipped."""
    if limits.all_unlimited:
        return None
    day_start = _day_start(now)
    if limits.daily > 0 and daily_used >= limits.daily:
        return QuotaExceededError(
            f"已达今日平台搜索次数（{daily_used} / {limits.daily}），"
            f"{_RESET_HINT}；{_OWN_EXIT}。",
            dimension="search_daily",
            used=daily_used,
            limit=limits.daily,
            reset_at=_next_day_reset(day_start),
        )
    if limits.monthly > 0 and monthly_used >= limits.monthly:
        month_start = _month_start(day_start)
        return QuotaExceededError(
            f"本月平台搜索次数已用完（{monthly_used} / {limits.monthly}），"
            f"{_RESET_HINT}；{_OWN_EXIT}。",
            dimension="search_monthly",
            used=monthly_used,
            limit=limits.monthly,
            reset_at=_next_month_reset(month_start),
        )
    return None


def _account_id(user_id: str) -> bool:
    return bool(_UUID_RE.fullmatch(user_id or ""))


async def _load_user(session, user_id: str):
    from agentcore.db.repositories import UserRepository

    return await UserRepository(session).get_by_id(user_id)


async def search_quota_snapshot(
    user_id: str, *, now: datetime | None = None
) -> SearchQuotaSnapshot:
    """Used / limit for the settings page. Non-accounts report the global caps at 0 used."""
    now = now or datetime.now(UTC)
    day_start = _day_start(now)
    month_start = _month_start(day_start)
    if not _account_id(user_id):
        limits = SearchQuotaLimits.for_user(None)
        return SearchQuotaSnapshot(
            0,
            limits.daily,
            0,
            limits.monthly,
            _next_day_reset(day_start),
            _next_month_reset(month_start),
        )
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.search import PlatformSearchUseRepository

    async with async_session_factory() as session:
        user = await _load_user(session, user_id)
        limits = SearchQuotaLimits.for_user(user)
        repo = PlatformSearchUseRepository(session)
        daily_used = await repo.count_since(user_id, day_start)
        monthly_used = (
            daily_used
            if month_start == day_start
            else await repo.count_since(user_id, month_start)
        )
    return SearchQuotaSnapshot(
        daily_used,
        limits.daily,
        monthly_used,
        limits.monthly,
        _next_day_reset(day_start),
        _next_month_reset(month_start),
    )


async def admit_platform_search(user_id: str, *, now: datetime | None = None) -> None:
    """Raise when this account cannot send another platform search."""
    if not _account_id(user_id):
        return
    now = now or datetime.now(UTC)
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.search import PlatformSearchUseRepository

    async with async_session_factory() as session:
        user = await _load_user(session, user_id)
        limits = SearchQuotaLimits.for_user(user)
        if limits.all_unlimited:
            return
        day_start = _day_start(now)
        repo = PlatformSearchUseRepository(session)
        daily_used = await repo.count_since(user_id, day_start) if limits.daily > 0 else 0
        monthly_used = 0
        if limits.monthly > 0:
            month_start = _month_start(day_start)
            if limits.daily > 0 and month_start == day_start:
                monthly_used = daily_used
            else:
                monthly_used = await repo.count_since(user_id, month_start)
        block = search_quota_block(
            limits, daily_used=daily_used, monthly_used=monthly_used, now=now
        )
    if block is not None:
        logger.info(
            "search.quota_exceeded",
            dimension=block.dimension,
            used=block.used,
            limit=block.limit,
        )
        raise block


async def note_platform_search(user_id: str) -> None:
    """Record one platform search that returned (including an empty SERP)."""
    if not _account_id(user_id):
        return
    from agentcore.db.base import async_session_factory
    from agentcore.db.repositories.search import PlatformSearchUseRepository

    try:
        async with async_session_factory() as session:
            await PlatformSearchUseRepository(session).record(user_id)
    except Exception:
        logger.warning("search.quota_record_failed", exc_info=True)
