"""Paid-seat redeem + admin card-secret inventory schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agentcore.billing.seats import PaidPlan, PublishedPlan

RedemptionStatus = Literal["unused", "redeemed", "void"]


class SeatStatus(BaseModel):
    """Current published seat on the account (设置 · 订阅 / 用量)."""

    plan: PublishedPlan
    until: datetime | None = None
    pending_plan: PaidPlan | None = None
    store_url: str = ""


class RedeemSeatRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


class CreateRedemptionCodesRequest(BaseModel):
    sku: PaidPlan
    count: int = Field(..., ge=1, le=200)


class CreateRedemptionCodesResponse(BaseModel):
    """Plaintext codes are returned once. They are not stored."""

    batch_id: str
    sku: PaidPlan
    codes: list[str]


class RedemptionCodeLine(BaseModel):
    id: str
    sku: PaidPlan
    status: RedemptionStatus
    prefix: str
    batch_id: str
    created_at: datetime
    redeemed_at: datetime | None = None
    redeemed_by: str | None = None


class RedemptionCodeListResponse(BaseModel):
    data: list[RedemptionCodeLine]
    total: int
    unused_starter: int
    unused_plus: int
    unused_flagship: int
