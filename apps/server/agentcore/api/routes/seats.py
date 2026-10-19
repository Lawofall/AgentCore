"""User-facing paid-seat redeem (设置 · 订阅)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db, get_user_repo
from agentcore.api.schemas.seats import RedeemSeatRequest, SeatStatus
from agentcore.billing.seat_service import redeem_code
from agentcore.billing.seats import published_plan, snapshot_from_user
from agentcore.config import settings
from agentcore.db.repositories import UserRepository

router = APIRouter(prefix="/seats", tags=["seats"])


def seat_status_for(user, *, now) -> SeatStatus:
    snap = snapshot_from_user(user)
    return SeatStatus(
        plan=published_plan(snap, now),
        until=snap.until if published_plan(snap, now) != "basic" else None,
        pending_plan=snap.pending_plan if published_plan(snap, now) != "basic" else None,
        store_url=settings.seat_store_url.strip(),
    )


@router.post("/redeem", response_model=SeatStatus)
async def redeem_seat(
    body: RedeemSeatRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    users: UserRepository = Depends(get_user_repo),
) -> SeatStatus:
    """Consume one 链动小铺 card secret and open / extend the matching seat."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    await redeem_code(session, user=user, raw_code=body.code, now=now)
    fresh = await users.get_by_id(user.user_id)
    account = fresh or user
    return seat_status_for(account, now=now)
