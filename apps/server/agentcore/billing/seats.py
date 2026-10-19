"""Published paid seats (基础版 / 高级版 / 旗舰版) — SKU numbers + redeem / expiry math.

Keep in sync with desktop ``subscriptionPlans.ts``. Authority →
成本配额与计费.md §〇·六. One code = one SKU × 30 days. Not a wallet.
``basic`` is the free default (NULL ``seat_plan``), not a paid SKU.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

PaidPlan = Literal["starter", "plus", "flagship"]
PublishedPlan = Literal["basic", "starter", "plus", "flagship"]
SeatKind = Literal[
    "activate",
    "renew",
    "upgrade",
    "defer_downgrade",
    "apply_pending",
    "expire",
    "remind",
    "none",
]

PERIOD = timedelta(days=30)
GRACE = timedelta(days=3)
REMIND = timedelta(days=3)
# China has no DST; receipts show the Asia/Shanghai calendar day.
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

PLAN_RANK: dict[str, int] = {"starter": 1, "plus": 2, "flagship": 3}


@dataclass(frozen=True)
class SeatSku:
    id: PaidPlan
    name: str
    price_yuan: float
    monthly_quota_yuan: float
    daily_quota_yuan: float
    search_daily: int
    search_monthly: int
    code_prefix: str


SKUS: dict[PaidPlan, SeatSku] = {
    "starter": SeatSku(
        id="starter",
        name="基础版",
        price_yuan=19.9,
        monthly_quota_yuan=20.0,
        daily_quota_yuan=5.0,
        search_daily=80,
        search_monthly=800,
        code_prefix="ACS",
    ),
    "plus": SeatSku(
        id="plus",
        name="高级版",
        price_yuan=49.9,
        monthly_quota_yuan=55.0,
        daily_quota_yuan=8.0,
        search_daily=150,
        search_monthly=1500,
        code_prefix="ACP",
    ),
    "flagship": SeatSku(
        id="flagship",
        name="旗舰版",
        price_yuan=89.0,
        monthly_quota_yuan=100.0,
        daily_quota_yuan=10.0,
        search_daily=300,
        search_monthly=3000,
        code_prefix="ACF",
    ),
}


@dataclass(frozen=True)
class SeatSnapshot:
    plan: PaidPlan | None
    until: datetime | None
    pending_plan: PaidPlan | None
    reminded_until: datetime | None


@dataclass(frozen=True)
class SeatChange:
    plan: PaidPlan | None
    until: datetime | None
    pending_plan: PaidPlan | None
    reminded_until: datetime | None
    kind: SeatKind


def plan_name(plan: str | None) -> str:
    if plan in SKUS:
        return SKUS[plan].name
    return "免费版"


def quota_overrides(plan: PaidPlan | None) -> dict[str, float | int | None]:
    """Per-user quota columns for this SKU. ``None`` plan = inherit globals."""
    if plan is None:
        return {
            "monthly_cost_cny": None,
            "daily_cost_cny": None,
            "search_daily": None,
            "search_monthly": None,
        }
    sku = SKUS[plan]
    return {
        "monthly_cost_cny": sku.monthly_quota_yuan,
        "daily_cost_cny": sku.daily_quota_yuan,
        "search_daily": sku.search_daily,
        "search_monthly": sku.search_monthly,
    }


def _paid_through(snap: SeatSnapshot, now: datetime) -> bool:
    """True while the current period or its 3-day grace still holds."""
    return bool(snap.plan and snap.until and now < snap.until + GRACE)


def redeem(snap: SeatSnapshot, sku: PaidPlan, now: datetime) -> SeatChange:
    """Apply one unused code of ``sku`` against the current seat.

    Same SKU while still paid-through → +30 from the existing ``until``.
    Higher SKU → switch now, 30 days from ``now``.
    Lower SKU → consume the code, take effect when the current ``until`` hits.
    After grace (or no seat) → 30 days from ``now``.
    """
    new_until = now + PERIOD
    if not _paid_through(snap, now) or snap.plan is None or snap.until is None:
        return SeatChange(
            plan=sku,
            until=new_until,
            pending_plan=None,
            reminded_until=None,
            kind="activate",
        )

    current_rank = PLAN_RANK[snap.plan]
    incoming = PLAN_RANK[sku]
    if incoming > current_rank:
        return SeatChange(
            plan=sku,
            until=new_until,
            pending_plan=None,
            reminded_until=None,
            kind="upgrade",
        )
    if incoming == current_rank:
        return SeatChange(
            plan=snap.plan,
            until=snap.until + PERIOD,
            pending_plan=None,
            reminded_until=None,
            kind="renew",
        )
    return SeatChange(
        plan=snap.plan,
        until=snap.until,
        pending_plan=sku,
        reminded_until=snap.reminded_until,
        kind="defer_downgrade",
    )


def tick(snap: SeatSnapshot, now: datetime) -> SeatChange:
    """Advance one seat: apply a due downgrade, expire after grace, or remind.

    Pending downgrade takes effect at ``until`` (no grace on the old plan).
    With no pending, the 3-day grace keeps the current plan, then reverts.
    """
    if snap.plan is None or snap.until is None:
        return SeatChange(
            plan=None,
            until=None,
            pending_plan=None,
            reminded_until=None,
            kind="none",
        )

    if snap.pending_plan and now >= snap.until:
        return SeatChange(
            plan=snap.pending_plan,
            until=snap.until + PERIOD,
            pending_plan=None,
            reminded_until=None,
            kind="apply_pending",
        )

    if now >= snap.until + GRACE:
        return SeatChange(
            plan=None,
            until=None,
            pending_plan=None,
            reminded_until=None,
            kind="expire",
        )

    remind_from = snap.until - REMIND
    already = snap.reminded_until is not None and snap.reminded_until == snap.until
    if now >= remind_from and now < snap.until and not already:
        return SeatChange(
            plan=snap.plan,
            until=snap.until,
            pending_plan=snap.pending_plan,
            reminded_until=snap.until,
            kind="remind",
        )

    return SeatChange(
        plan=snap.plan,
        until=snap.until,
        pending_plan=snap.pending_plan,
        reminded_until=snap.reminded_until,
        kind="none",
    )


def snapshot_from_user(user: object) -> SeatSnapshot:
    plan = getattr(user, "seat_plan", None)
    pending = getattr(user, "seat_pending_plan", None)
    paid: PaidPlan | None = plan if plan in SKUS else None
    pending_paid: PaidPlan | None = pending if pending in SKUS else None
    return SeatSnapshot(
        plan=paid,
        until=getattr(user, "seat_until", None),
        pending_plan=pending_paid,
        reminded_until=getattr(user, "seat_reminded_until", None),
    )


def published_plan(snap: SeatSnapshot, now: datetime) -> PublishedPlan:
    """User-facing seat for the subscription card (grace still counts as paid)."""
    if snap.plan and snap.until and now < snap.until + GRACE:
        return snap.plan
    return "basic"


def date_label(dt: datetime) -> str:
    """Calendar day in Asia/Shanghai for receipts (not UTC clock copy)."""
    local = dt.astimezone(SHANGHAI)
    return f"{local.year}年{local.month}月{local.day}日"


def receipt_body(change: SeatChange, *, opened_at: datetime) -> tuple[str, str]:
    """Subject + body for the login-inbox receipt. Caller skips if no email."""
    name = plan_name(change.plan)
    if change.kind == "defer_downgrade":
        pending = plan_name(change.pending_plan)
        until = date_label(change.until) if change.until else ""
        return (
            f"已兑换{pending}",
            f"已兑换{pending}。当前{name}用至{until}，其后自动改为{pending} 30 天。",
        )
    until = date_label(change.until) if change.until else ""
    opened = date_label(opened_at)
    if change.kind == "renew":
        return (
            f"已续费{name}",
            f"已续费{name}。席位延至{until}。到期前续费请再购买同一档兑换码。",
        )
    return (
        f"已开通{name}",
        f"已开通{name}。设置 · 用量里的上限已生效。席位从{opened}起 30 天，"
        f"至{until}。到期前续费请再购买同一档兑换码。",
    )


def reminder_body(snap: SeatSnapshot) -> tuple[str, str]:
    name = plan_name(snap.plan)
    until = date_label(snap.until) if snap.until else ""
    if snap.pending_plan:
        pending = plan_name(snap.pending_plan)
        return (
            f"{name}将于{until}到期",
            f"{name}将于{until}到期，其后改为{pending} 30 天。",
        )
    return (
        f"{name}将于{until}到期",
        f"席位将于{until}到期，续费后从该日起再 30 天。请到设置 · 订阅购买同一档兑换码。",
    )
