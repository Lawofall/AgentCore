"""Issue / redeem / expire paid seats. Caller owns the session commit."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.redemption import generate_code, hash_code, normalize_code, prefix_of
from agentcore.billing.seats import (
    SKUS,
    PaidPlan,
    SeatChange,
    quota_overrides,
    receipt_body,
    redeem,
    reminder_body,
    snapshot_from_user,
    tick,
)
from agentcore.core.errors import ConflictError, NotFoundError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.db.models import User
from agentcore.db.models.seats import RedemptionCode
from agentcore.db.repositories import RedemptionCodeRepository, UserRepository
from agentcore.mail.sender import send_text

logger = get_logger(__name__)

_MAX_BATCH = 200
_MAX_ISSUE_ATTEMPTS = 8


def _paid(sku: str) -> PaidPlan:
    if sku not in SKUS:
        raise ValidationError("档位无效")
    return sku  # type: ignore[return-value]


async def _apply(
    users: UserRepository,
    user_id: str,
    change: SeatChange,
    *,
    commit: bool,
) -> User | None:
    quota = quota_overrides(change.plan)
    return await users.apply_seat(
        user_id,
        plan=change.plan,
        until=change.until,
        pending_plan=change.pending_plan,
        reminded_until=change.reminded_until,
        monthly_cost_cny=quota["monthly_cost_cny"],  # type: ignore[arg-type]
        daily_cost_cny=quota["daily_cost_cny"],  # type: ignore[arg-type]
        search_daily=quota["search_daily"],  # type: ignore[arg-type]
        search_monthly=quota["search_monthly"],  # type: ignore[arg-type]
        commit=commit,
    )


async def ensure_current(users: UserRepository, user: User, *, now: datetime) -> User:
    """Apply a due pending downgrade or post-grace expiry. Idempotent."""
    change = tick(snapshot_from_user(user), now)
    if change.kind not in {"apply_pending", "expire"}:
        return user
    updated = await _apply(users, user.user_id, change, commit=True)
    logger.info(
        "seat.tick",
        user_id=user.user_id,
        kind=change.kind,
        plan=change.plan,
    )
    return updated or user


async def redeem_code(
    session: AsyncSession,
    *,
    user: User,
    raw_code: str,
    now: datetime | None = None,
) -> SeatChange:
    now = now or datetime.now(UTC)
    users = UserRepository(session)
    codes = RedemptionCodeRepository(session)
    user = await ensure_current(users, user, now=now)

    normalized = normalize_code(raw_code)
    if not normalized:
        raise ValidationError("请填写兑换码")
    row = await codes.get_by_hash_for_update(hash_code(normalized))
    if row is None or row.status == "void":
        raise NotFoundError("兑换码无效")
    if row.status == "redeemed":
        raise ConflictError("兑换码已使用")

    sku = _paid(row.sku)
    change = redeem(snapshot_from_user(user), sku, now)
    await _apply(users, user.user_id, change, commit=False)
    await codes.mark_redeemed(row.id, user_id=user.user_id, at=now, commit=False)
    await session.commit()
    logger.info(
        "seat.redeemed",
        user_id=user.user_id,
        sku=sku,
        kind=change.kind,
        code_id=row.id,
    )
    await _mail_receipt(user, change, opened_at=now)
    return change


async def issue_batch(
    session: AsyncSession,
    *,
    sku: str,
    count: int,
    created_by: str,
) -> tuple[str, list[str]]:
    paid = _paid(sku)
    if count < 1 or count > _MAX_BATCH:
        raise ValidationError(f"一次最多生成 {_MAX_BATCH} 个兑换码")
    codes = RedemptionCodeRepository(session)
    batch_id = new_id()
    plaintext: list[str] = []
    rows: list[RedemptionCode] = []
    seen: set[str] = set()
    attempts = 0
    while len(rows) < count:
        attempts += 1
        if attempts > count * _MAX_ISSUE_ATTEMPTS:
            raise ValidationError("生成兑换码失败，请重试")
        raw = generate_code(paid)
        digest = hash_code(raw)
        if digest in seen:
            continue
        seen.add(digest)
        plaintext.append(raw)
        rows.append(
            RedemptionCode(
                id=new_id(),
                code_hash=digest,
                code_prefix=prefix_of(raw),
                sku=paid,
                status="unused",
                batch_id=batch_id,
                created_by=created_by,
            )
        )
    await codes.add_many(rows, commit=True)
    logger.info(
        "seat.codes_issued",
        sku=paid,
        count=len(rows),
        batch_id=batch_id,
        actor_id=created_by,
    )
    return batch_id, plaintext


async def void_code(session: AsyncSession, *, code_id: str, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    codes = RedemptionCodeRepository(session)
    row = await codes.get_by_id(code_id)
    if row is None:
        raise NotFoundError("兑换码不存在")
    if row.status != "unused":
        raise ConflictError("只能作废未使用的兑换码")
    updated = await codes.mark_void(code_id, at=now, commit=True)
    if updated == 0:
        raise ConflictError("只能作废未使用的兑换码")
    logger.info("seat.code_voided", code_id=code_id, sku=row.sku)


async def run_seat_sweep(*, now: datetime | None = None) -> dict[str, int]:
    """Expire / apply pending / remind. Best-effort; one failed user does not abort."""
    from agentcore.db.base import async_session_factory

    now = now or datetime.now(UTC)
    from agentcore.billing.seats import REMIND

    stats = {"expired": 0, "pending": 0, "reminded": 0}
    async with async_session_factory() as session:
        users = UserRepository(session)
        due = await users.list_seated(until_before=now)
        remindable = await users.list_remindable(
            remind_from=now + REMIND, now=now
        )
        # `list_seated` also catches rows still in grace; tick() no-ops those.
        seen: set[str] = set()
        for user in (*due, *remindable):
            if user.user_id in seen:
                continue
            seen.add(user.user_id)
            change = tick(snapshot_from_user(user), now)
            try:
                if change.kind in {"apply_pending", "expire"}:
                    await _apply(users, user.user_id, change, commit=True)
                    stats["pending" if change.kind == "apply_pending" else "expired"] += 1
                    logger.info(
                        "seat.tick",
                        user_id=user.user_id,
                        kind=change.kind,
                        plan=change.plan,
                    )
                elif change.kind == "remind":
                    await users.apply_seat(
                        user.user_id,
                        plan=user.seat_plan,
                        until=user.seat_until,
                        pending_plan=user.seat_pending_plan,
                        reminded_until=change.reminded_until,
                        monthly_cost_cny=user.quota_monthly_cost_cny,
                        daily_cost_cny=user.quota_daily_cost_cny,
                        search_daily=user.quota_search_daily,
                        search_monthly=user.quota_search_monthly,
                        commit=True,
                    )
                    await _mail_reminder(user)
                    stats["reminded"] += 1
                    logger.info("seat.reminded", user_id=user.user_id)
            except Exception as exc:  # noqa: BLE001 — one account must not kill the sweep
                logger.warning(
                    "seat.sweep_user_failed",
                    user_id=user.user_id,
                    error=str(exc),
                )
    return stats


async def _mail_receipt(user: User, change: SeatChange, *, opened_at: datetime) -> None:
    to = (user.email or "").strip()
    if not to:
        return
    subject, body = receipt_body(change, opened_at=opened_at)
    try:
        await send_text(to=to, subject=subject, body=body, purpose="seat_receipt")
    except Exception as exc:  # noqa: BLE001 — redeem already committed
        logger.warning("seat.mail_failed", purpose="seat_receipt", error=str(exc))


async def _mail_reminder(user: User) -> None:
    to = (user.email or "").strip()
    if not to:
        return
    subject, body = reminder_body(snapshot_from_user(user))
    try:
        await send_text(to=to, subject=subject, body=body, purpose="seat_reminder")
    except Exception as exc:  # noqa: BLE001
        logger.warning("seat.mail_failed", purpose="seat_reminder", error=str(exc))
