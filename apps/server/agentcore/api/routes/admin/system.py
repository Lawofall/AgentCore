"""Read-only deployment status snapshot (系统状态)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentcore.api.dependencies import AdminUser, get_user_repo
from agentcore.api.routes.system import app_version
from agentcore.api.schemas import AdminSystemStatus, QuotaStatus
from agentcore.config import settings
from agentcore.db.base import database_ready
from agentcore.db.repositories import UserRepository
from agentcore.llm.pricing import NANO_PER_CNY

router = APIRouter(tags=["admin"])


@router.get("/system", response_model=AdminSystemStatus)
async def system_status(
    admin: AdminUser,
    users: UserRepository = Depends(get_user_repo),
) -> AdminSystemStatus:
    """系统状态 (read-only): billing mode + global quota defaults (config),
    database reachability, build provenance, and account tallies.

    A deployment sanity-check — nothing here is editable from the console (config is
    env + redeploy). Reuses the same DB probe as ``/readyz`` and the same version as
    ``/version`` so the panel never drifts from the real signals.
    """
    counts = await users.count_overview()
    db_ok = await database_ready()
    return AdminSystemStatus(
        billing_mode=settings.billing_mode,
        quota=QuotaStatus(
            daily_tokens=settings.quota_daily_tokens,
            monthly_cost_nano=int(settings.quota_monthly_cost_cny * NANO_PER_CNY),
            daily_cost_nano=int(settings.quota_daily_cost_cny * NANO_PER_CNY),
        ),
        database_ok=db_ok,
        version=app_version(),
        git_sha=settings.git_sha,
        built_at=settings.built_at,
        users_total=counts["total"],
        users_active=counts["active"],
        admins=counts["admins"],
    )
