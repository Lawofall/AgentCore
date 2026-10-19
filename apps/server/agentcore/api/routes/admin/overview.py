"""Admin console landing dashboard (控制台概览)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends

from agentcore.api.cost_view import cost_breakdown
from agentcore.api.dependencies import (
    AdminUser,
    get_cost_event_repo,
    get_turn_metrics_repo,
    get_user_repo,
)
from agentcore.api.routes.admin._shared import _TREND_DAYS, _health_window
from agentcore.api.schemas import (
    AdminOverview,
    DailyCost,
    DailyTurns,
    TurnMetricLine,
)
from agentcore.config import settings
from agentcore.db.base import database_ready
from agentcore.db.repositories import (
    CostEventRepository,
    TurnMetricsRepository,
    UserRepository,
)
from agentcore.messaging.hub import default_chat_hub

router = APIRouter(tags=["admin"])

# 概览首页「近期错误」feed length — a short glance on the dashboard (the full feed
# lives on the 观测 page).
_OVERVIEW_ERRORS = 5


@router.get("/overview", response_model=AdminOverview)
async def overview(
    admin: AdminUser,
    users: UserRepository = Depends(get_user_repo),
    cost_repo: CostEventRepository = Depends(get_cost_event_repo),
    metrics_repo: TurnMetricsRepository = Depends(get_turn_metrics_repo),
) -> AdminOverview:
    """控制台概览 (landing dashboard): today's platform pulse (active users + turn
    health + cost), account tallies, live online accounts, the 7-day cost / turn
    trends, deployment health, and a short recent-errors feed.

    A curated one-call home view that *reuses the same aggregates* as the 用量 /
    观测 / 系统 surfaces (so the headline numbers never drift from the drill-down
    pages) plus one extra metric — distinct active users today. Each error row
    carries its ``conversation_id`` to drill into 会话复盘.
    """
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=_TREND_DAYS - 1)

    today_health = await metrics_repo.aggregate_health_for_window(since=day_start)
    active_users_today = await metrics_repo.count_distinct_users_for_window(since=day_start)
    today_cost = await cost_repo.aggregate_for_window(since=day_start)

    # 近 7 日成本趋势 (zero-filled, oldest-first ending today).
    daily_cost = await cost_repo.aggregate_daily_for_window(since=week_start)
    recent_daily_cost = []
    for i in range(_TREND_DAYS):
        iso = (week_start + timedelta(days=i)).date().isoformat()
        recent_daily_cost.append(DailyCost(date=iso, cost_total=daily_cost.get(iso, 0)))

    # 近 7 日回合趋势 (zero-filled, oldest-first ending today).
    daily_turns = await metrics_repo.aggregate_daily_for_window(since=week_start)
    recent_daily_turns = []
    for i in range(_TREND_DAYS):
        iso = (week_start + timedelta(days=i)).date().isoformat()
        point = daily_turns.get(iso) or {}
        recent_daily_turns.append(
            DailyTurns(
                date=iso,
                turns=int(point.get("turns", 0)),
                errors=int(point.get("errors", 0)),
            )
        )

    counts = await users.count_overview()
    db_ok = await database_ready()
    errors = await metrics_repo.list_recent_errors(limit=_OVERVIEW_ERRORS)

    return AdminOverview(
        active_users_today=active_users_today,
        today=_health_window(today_health),
        cost_today=cost_breakdown(today_cost["cost"]),
        users_total=counts["total"],
        users_active=counts["active"],
        admins=counts["admins"],
        users_online=default_chat_hub().online_user_count(),
        recent_daily_cost=recent_daily_cost,
        recent_daily_turns=recent_daily_turns,
        database_ok=db_ok,
        recent_errors=[TurnMetricLine.model_validate(r) for r in errors],
        billing_mode=settings.billing_mode,
    )
