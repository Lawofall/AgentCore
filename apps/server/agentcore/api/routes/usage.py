"""Cost & usage observability endpoints (单回合工资单 + 对话累计 + 账户仪表盘).

组团时单回合花销按 Agent/角色拆开（工资单）；账户仪表盘给窗口总额 / 额度 / 趋势 /
按模型构成；活动列表按对话一行。

All reads are scoped to the authenticated user via ``cost_events.user_id`` /
``cost_calls.user_id``, so a non-owner can never read another user's spend
(IDOR-safe) — the message / conversation endpoints additionally 404 or return
empty rather than leaking existence. The ledger (``cost_calls`` +
``cost_events``) is the truth source for real money spent (不变量 #1);
``Message.usage`` is only a display snapshot.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from agentcore.api.cost_view import cost_breakdown, estimated_cost_breakdown, usage_breakdown
from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_cost_event_repo,
    get_user_repo,
)
from agentcore.api.routes.seats import seat_status_for
from agentcore.api.schemas import (
    AgentCostLine,
    ConversationCost,
    DailyCost,
    ModelCostLine,
    QuotaStatus,
    SearchQuotaStatus,
    TurnCost,
    UsageActivityLine,
    UsageActivityPage,
    UsageActivityWindow,
    UsageSummary,
    UsageWindow,
)
from agentcore.billing.seat_service import ensure_current
from agentcore.config import settings
from agentcore.conversation.quota import QuotaLimits
from agentcore.core.errors import NotFoundError
from agentcore.db.models import CostEvent
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    UserRepository,
)
from agentcore.llm.pricing import CURRENCY_CNY
from agentcore.tools.builtin.web.search_quota import search_quota_snapshot

router = APIRouter(tags=["usage"])

# Account dashboard trend window: the last N UTC days (incl today) shown as a
# spend sparkline. Fixed length so the series is zero-filled and stable.
_TREND_DAYS = 30
_ACTIVITY_PAGE_MAX = 50


def _sum_rows(rows: list[CostEvent]) -> tuple[dict, dict, dict, int]:
    """Roll up a turn's payroll rows into (usage, cost, estimated_cost, rounds).

    Summed in Python from the rows already fetched for the payroll (no second
    query). Display ``cost.total`` is billed + BYOK nominal (same CNY card).
    ``estimated_cost`` stays the BYOK slice（不扣额度）. This helper does not
    feed ``enforce_quota``.
    """
    usage = {"input": 0, "output": 0, "reasoning": 0, "cache_hit": 0, "cache_miss": 0}
    cost = {"input": 0, "cached": 0, "output": 0, "total": 0, "pricing_source": "curated"}
    estimated = {
        "input": 0,
        "cached": 0,
        "output": 0,
        "total": 0,
        "pricing_source": "estimated",
    }
    rounds = 0
    for row in rows:
        for key in usage:
            usage[key] += int((row.tokens or {}).get(key, 0))
        row_cost = row.cost or {}
        billed_nano = int(row.cost_total_nano or 0)
        estimated_nano = int(getattr(row, "cost_estimated_nano", 0) or 0)
        row_currency = str(row.currency or row_cost.get("currency") or CURRENCY_CNY)
        display = billed_nano + estimated_nano
        if display:
            for key in ("input", "cached", "output"):
                cost[key] += int(row_cost.get(key, 0))
            cost["total"] += display
            cost.setdefault("currency", row_currency)
            if row_cost.get("pricing_source"):
                cost["pricing_source"] = str(row_cost["pricing_source"])
        if estimated_nano:
            for key in ("input", "cached", "output"):
                estimated[key] += int(row_cost.get(key, 0))
            estimated["total"] += estimated_nano
            estimated.setdefault("currency", row_currency)
            if row_cost.get("pricing_source"):
                estimated["pricing_source"] = str(row_cost["pricing_source"])
        rounds += int(row.rounds or 0)
    return usage, cost, estimated, rounds


@router.get("/messages/{message_id}/cost", response_model=TurnCost)
async def get_message_cost(
    message_id: str,
    user: AuthUser,
    repo: CostEventRepository = Depends(get_cost_event_repo),
) -> TurnCost:
    """The team payroll for one assistant turn (per-Agent cost + the turn total).

    Rebuilt from the ledger by ``message_id`` (scoped to the caller), so a past
    turn's payroll replays on reload. An unknown / non-owned message yields zeros
    + an empty roster rather than 404 — it never leaks whether the id exists.
    """
    rows = list(await repo.list_for_message(message_id, user_id=user.user_id))
    agents = []
    for row in rows:
        row_cost = row.cost or {}
        billed = int(row.cost_total_nano or 0)
        estimated_nano = int(getattr(row, "cost_estimated_nano", 0) or 0)
        # Currency lives on the ledger's scalar column, not in the JSONB body
        # (``split_cost`` keeps the body to money keys + sources), so stamp it on
        # both breakdowns here. BYOK uses the same CNY card as billed.
        row_currency = str(row.currency or row_cost.get("currency") or CURRENCY_CNY)
        agents.append(
            AgentCostLine(
                run_id=row.run_id,
                agent_id=row.agent_id,
                role=row.persona or row.role,
                model=row.model,
                usage=usage_breakdown(row.tokens or {}),
                cost=cost_breakdown(
                    {
                        **({} if estimated_nano and not billed else row_cost),
                        "total": billed,
                        "input": int(row_cost.get("input", 0)) if billed else 0,
                        "cached": int(row_cost.get("cached", 0)) if billed else 0,
                        "output": int(row_cost.get("output", 0)) if billed else 0,
                        "currency": row_currency if billed else CURRENCY_CNY,
                        "pricing_source": str(row_cost.get("pricing_source") or "curated"),
                    }
                ),
                estimated_cost=estimated_cost_breakdown(
                    estimated_nano=estimated_nano,
                    cost={**row_cost, "currency": row_currency} if estimated_nano else None,
                ),
                duration_ms=int(row.duration_ms or 0),
            )
        )
    usage, cost, estimated, rounds = _sum_rows(rows)
    return TurnCost(
        message_id=message_id,
        usage=usage_breakdown(usage),
        cost=cost_breakdown(cost),
        estimated_cost=estimated_cost_breakdown(cost=estimated),
        rounds=rounds,
        agents=agents,
    )


@router.get("/conversations/{conversation_id}/cost", response_model=ConversationCost)
async def get_conversation_cost(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    repo: CostEventRepository = Depends(get_cost_event_repo),
) -> ConversationCost:
    """A conversation's cumulative spend (对话累计 — the conversation-header chip).

    404 for a non-owner, consistent with the other conversation reads.
    """
    conv = await conv_repo.get_by_id(conversation_id, user_id=user.user_id)
    if not conv:
        raise NotFoundError("对话不存在")
    agg = await repo.aggregate_for_conversation(conversation_id, user_id=user.user_id)
    return ConversationCost(
        conversation_id=conversation_id,
        usage=usage_breakdown(agg["usage"]),
        cost=cost_breakdown(agg["cost"]),
        estimated_cost=estimated_cost_breakdown(cost=agg.get("estimated_cost") or {}),
        turns=agg["turns"],
    )


@router.get("/usage/summary", response_model=UsageSummary)
async def get_usage_summary(
    user: AuthUser,
    repo: CostEventRepository = Depends(get_cost_event_repo),
    user_repo: UserRepository = Depends(get_user_repo),
) -> UsageSummary:
    """Account dashboard: today's tokens/cost, the month's cost, the 30-day
    daily-cost trend, this month's by-model mix, platform search uses, and quota.

    Windows are bounded at the current UTC day / month start (MVP — a per-user
    timezone is a later refinement). ``recent_daily_cost`` is the last
    ``_TREND_DAYS`` UTC days (zero-filled, oldest-first) for the sparkline.
    Money is nano-CNY; ``CostBreakdown.cny_total`` is already yuan (no FX).
    Per-role split lives on the turn payroll
    (``GET /messages/{id}/cost``), not this monthly account view.
    Conversation-level rows live on ``GET /usage/activity``.

    This is the account total, so it also carries spend that belongs to no
    conversation at all (AI 改写 — ``role=assist`` ledger rows).
    ``requests`` counts assistant turns only, so those rows raise 花销 without
    raising 请求数.

    ``quota`` mirrors what ``enforce_quota`` will actually apply to this user:
    per-user override columns first, else global ``quota_*`` — so the meters
    never show a cap the gate wouldn't enforce.
    """
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)

    today = await repo.aggregate_for_window(user_id=user.user_id, since=day_start)
    month = await repo.aggregate_for_window(user_id=user.user_id, since=month_start)
    month_by_model = await repo.aggregate_by_model_for_window(
        user_id=user.user_id, since=month_start
    )

    # 近 30 日趋势: zero-fill the daily map into a fixed, oldest-first series ending
    # today, so the sparkline is a stable length even for sparse spend.
    trend_start = day_start - timedelta(days=_TREND_DAYS - 1)
    daily = await repo.aggregate_daily_for_window(user_id=user.user_id, since=trend_start)
    recent_daily_cost = []
    for i in range(_TREND_DAYS):
        iso = (trend_start + timedelta(days=i)).date().isoformat()
        recent_daily_cost.append(DailyCost(date=iso, cost_total=daily.get(iso, 0)))

    search_snap = await search_quota_snapshot(user.user_id, now=now)

    account = await user_repo.get_by_id(user.user_id)
    if account is not None:
        account = await ensure_current(user_repo, account, now=now)

    # Mirror the gate's limit resolution (per-user override → global quota_*).
    limits = (
        QuotaLimits.for_user(account)
        if account is not None
        else QuotaLimits.from_settings()
    )

    return UsageSummary(
        today=UsageWindow(
            usage=usage_breakdown(today["usage"]),
            cost=cost_breakdown(today["cost"]),
            estimated_cost=estimated_cost_breakdown(cost=today.get("estimated_cost") or {}),
            requests=today["turns"],
        ),
        month=UsageWindow(
            usage=usage_breakdown(month["usage"]),
            cost=cost_breakdown(month["cost"]),
            estimated_cost=estimated_cost_breakdown(cost=month.get("estimated_cost") or {}),
            requests=month["turns"],
        ),
        recent_daily_cost=recent_daily_cost,
        quota=QuotaStatus(
            daily_tokens=limits.daily_tokens,
            monthly_cost_nano=limits.monthly_cost_nano,
            daily_cost_nano=limits.daily_cost_nano,
        ),
        search=SearchQuotaStatus(
            daily_used=search_snap.daily_used,
            daily_limit=search_snap.daily_limit,
            monthly_used=search_snap.monthly_used,
            monthly_limit=search_snap.monthly_limit,
        ),
        month_by_model=[
            ModelCostLine(
                model=row["model"],
                calls=int(row["calls"]),
                tokens_total=int(row["tokens_total"]),
                cost_total=int(row["cost_total"]),
                cost_estimated_total=int(row.get("cost_estimated_total", 0) or 0),
            )
            for row in month_by_model
        ],
        billing_mode=settings.billing_mode,
        seat=seat_status_for(account if account is not None else user, now=now),
    )


def _activity_since(window: UsageActivityWindow, now: datetime) -> datetime:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "today":
        return day_start
    if window == "7d":
        return day_start - timedelta(days=6)
    return day_start.replace(day=1)


@router.get("/usage/activity", response_model=UsageActivityPage)
async def get_usage_activity(
    user: AuthUser,
    window: UsageActivityWindow = Query("month"),
    model: str | None = Query(None, max_length=50),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=_ACTIVITY_PAGE_MAX),
    repo: CostEventRepository = Depends(get_cost_event_repo),
) -> UsageActivityPage:
    """Recent spend grouped by conversation (用量活动表).

    One row per conversation in the window; account-level AI 改写 is one extra
    row with a null ``conversation_id``. ``model`` keeps rows that used that
    billed model id and slices tokens/cost to it. Ordered by last spend.
    """
    now = datetime.now(UTC)
    model_id = (model or "").strip() or None
    agg = await repo.aggregate_activity_for_window(
        user_id=user.user_id,
        since=_activity_since(window, now),
        model=model_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return UsageActivityPage(
        data=[
            UsageActivityLine(
                conversation_id=row["conversation_id"],
                kind=row["kind"],
                title=row["title"],
                deleted=bool(row["deleted"]),
                models=list(row["models"]),
                usage=usage_breakdown(row["usage"]),
                cost=cost_breakdown(row["cost"]),
                estimated_cost=estimated_cost_breakdown(cost=row.get("estimated_cost") or {}),
                last_at=row["last_at"],
                turns=int(row["turns"]),
            )
            for row in agg["items"]
        ],
        total=int(agg["total"]),
        usage=usage_breakdown(agg["usage"]),
        cost=cost_breakdown(agg["cost"]),
        estimated_cost=estimated_cost_breakdown(cost=agg.get("estimated_cost") or {}),
        page=page,
        page_size=page_size,
        window=window,
        model=model_id,
    )
