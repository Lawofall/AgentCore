"""Admin card-secret inventory for 链动小铺 auto-delivery."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.admin.audit import record_admin_audit
from agentcore.api.dependencies import AdminUser, get_db
from agentcore.api.schemas.seats import (
    CreateRedemptionCodesRequest,
    CreateRedemptionCodesResponse,
    RedemptionCodeLine,
    RedemptionCodeListResponse,
)
from agentcore.billing.seat_service import issue_batch, void_code
from agentcore.core.errors import NotFoundError
from agentcore.db.models.seats import RedemptionCode
from agentcore.db.repositories import RedemptionCodeRepository

router = APIRouter()


def _line(row: RedemptionCode) -> RedemptionCodeLine:
    return RedemptionCodeLine(
        id=row.id,
        sku=row.sku,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        prefix=row.code_prefix,
        batch_id=row.batch_id,
        created_at=row.created_at,
        redeemed_at=row.redeemed_at,
        redeemed_by=row.redeemed_by,
    )


@router.get("/redemption-codes", response_model=RedemptionCodeListResponse)
async def list_redemption_codes(
    _admin: AdminUser,
    sku: Literal["starter", "plus", "flagship"] | None = Query(None),
    status: Literal["unused", "redeemed", "void"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> RedemptionCodeListResponse:
    repo = RedemptionCodeRepository(session)
    rows, total = await repo.list_all(sku=sku, status=status, limit=limit, offset=offset)
    unused = await repo.unused_counts()
    return RedemptionCodeListResponse(
        data=[_line(r) for r in rows],
        total=total,
        unused_starter=unused["starter"],
        unused_plus=unused["plus"],
        unused_flagship=unused["flagship"],
    )


@router.post(
    "/redemption-codes",
    response_model=CreateRedemptionCodesResponse,
    status_code=201,
)
async def create_redemption_codes(
    body: CreateRedemptionCodesRequest,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
) -> CreateRedemptionCodesResponse:
    batch_id, codes = await issue_batch(
        session, sku=body.sku, count=body.count, created_by=admin.user_id
    )
    await record_admin_audit(
        session,
        actor_id=admin.user_id,
        action="redemption_code.issue",
        target_type="redemption_batch",
        target_id=batch_id,
        detail={"sku": body.sku, "count": body.count},
    )
    return CreateRedemptionCodesResponse(batch_id=batch_id, sku=body.sku, codes=codes)


@router.post("/redemption-codes/{code_id}/void", status_code=204)
async def void_redemption_code(
    code_id: str,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
) -> None:
    row_repo = RedemptionCodeRepository(session)
    existing = await row_repo.get_by_id(code_id)
    if existing is None:
        raise NotFoundError("兑换码不存在")
    await void_code(session, code_id=code_id, now=datetime.now(UTC))
    await record_admin_audit(
        session,
        actor_id=admin.user_id,
        action="redemption_code.void",
        target_type="redemption_code",
        target_id=code_id,
        detail={"sku": existing.sku},
    )
