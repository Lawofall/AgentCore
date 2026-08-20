"""Admin CRUD for the platform LLM credential pool."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.admin.audit import record_admin_audit
from agentcore.api.dependencies import AdminUser, get_db
from agentcore.api.schemas import (
    CreatePlatformCredentialRequest,
    PlatformCredentialListResponse,
    PlatformCredentialView,
    StatusResponse,
    ToolSurfaceLimits,
    UpdatePlatformCredentialRequest,
)
from agentcore.config import settings
from agentcore.llm.platform_credential_service import (
    PlatformCredentialService,
)
from agentcore.llm.platform_credential_service import (
    PlatformCredentialView as ServiceView,
)
from agentcore.llm.platform_pool import pick_enabled_platform_pool_member
from agentcore.llm.platform_pool_scheduler import (
    account_runtime_for_admin,
    clear_account_runtime_state,
)
from agentcore.llm.tool_surface import tool_surface_limits_as_dict

router = APIRouter(tags=["admin"])


def get_platform_credential_service(
    session: AsyncSession = Depends(get_db),
) -> PlatformCredentialService:
    return PlatformCredentialService(session)


def _view(row: ServiceView) -> PlatformCredentialView:
    runtime = account_runtime_for_admin(row.id)
    limits = row.tool_surface_limits
    return PlatformCredentialView(
        id=row.id,
        label=row.label,
        base_url=row.base_url,
        subscription_day=row.subscription_day,
        enabled=row.enabled,
        masked_key=row.masked_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        status=runtime.status,
        recovery_at=runtime.recovery_at,
        limit_name=runtime.limit_name,
        tool_surface_limits=ToolSurfaceLimits(
            max_tools=limits.max_tools,
            max_properties_total=limits.max_properties_total,
            max_properties_per_tool=limits.max_properties_per_tool,
        ),
    )


def _fallback() -> Literal["pool", "env", "none"]:
    if pick_enabled_platform_pool_member() is not None:
        return "pool"
    if settings.platform_api_key.strip():
        return "env"
    return "none"


def _audit_detail(view: ServiceView) -> dict[str, object]:
    return {
        "label": view.label,
        "base_url": view.base_url,
        "subscription_day": view.subscription_day,
        "enabled": view.enabled,
        "tool_surface_limits": tool_surface_limits_as_dict(view.tool_surface_limits),
    }


@router.get("/platform-credentials", response_model=PlatformCredentialListResponse)
async def list_platform_credentials(
    _admin: AdminUser,
    service: PlatformCredentialService = Depends(get_platform_credential_service),
) -> PlatformCredentialListResponse:
    """List platform-pool members. Ciphertext only; ``masked_key`` is last-4."""
    rows = await service.list_credentials()
    return PlatformCredentialListResponse(
        data=[_view(r) for r in rows],
        fallback=_fallback(),
    )


@router.post(
    "/platform-credentials",
    response_model=PlatformCredentialView,
    status_code=201,
)
async def create_platform_credential(
    body: CreatePlatformCredentialRequest,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
    service: PlatformCredentialService = Depends(get_platform_credential_service),
) -> PlatformCredentialView:
    """Add a pool member. Key is encrypted at rest and never returned."""
    view = await service.create_credential(
        label=body.label,
        api_key=body.api_key,
        base_url=body.base_url,
        subscription_day=body.subscription_day,
        enabled=body.enabled,
        tool_surface_limits=(
            body.tool_surface_limits.model_dump() if body.tool_surface_limits is not None else None
        ),
    )
    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="platform_credential.create",
        target_type="platform_credential",
        target_id=view.id,
        detail=_audit_detail(view),
    )
    return _view(view)


@router.patch("/platform-credentials/{credential_id}", response_model=PlatformCredentialView)
async def update_platform_credential(
    credential_id: str,
    body: UpdatePlatformCredentialRequest,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
    service: PlatformCredentialService = Depends(get_platform_credential_service),
) -> PlatformCredentialView:
    """Patch a member. Omit ``api_key`` to keep the stored ciphertext."""
    view = await service.update_credential(
        credential_id,
        fields_set=set(body.model_fields_set),
        label=body.label,
        api_key=body.api_key,
        base_url=body.base_url,
        subscription_day=body.subscription_day,
        enabled=body.enabled,
        tool_surface_limits=(
            body.tool_surface_limits.model_dump() if body.tool_surface_limits is not None else None
        ),
    )
    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="platform_credential.update",
        target_type="platform_credential",
        target_id=view.id,
        detail=_audit_detail(view),
    )
    return _view(view)


@router.delete("/platform-credentials/{credential_id}", response_model=StatusResponse)
async def delete_platform_credential(
    credential_id: str,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
    service: PlatformCredentialService = Depends(get_platform_credential_service),
) -> StatusResponse:
    """Remove a pool member. Does not touch ``cost_calls`` history."""
    await service.delete_credential(credential_id)
    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="platform_credential.delete",
        target_type="platform_credential",
        target_id=credential_id,
    )
    return StatusResponse(status="ok")


@router.post(
    "/platform-credentials/{credential_id}/clear-runtime",
    response_model=PlatformCredentialView,
)
async def clear_platform_credential_runtime(
    credential_id: str,
    admin: AdminUser,
    db: AsyncSession = Depends(get_db),
    service: PlatformCredentialService = Depends(get_platform_credential_service),
) -> PlatformCredentialView:
    """Drop cooling / exhausted / blocked flags so the member is schedulable again."""
    view = await service.get_credential(credential_id)
    prior = account_runtime_for_admin(credential_id)
    clear_account_runtime_state(credential_id)
    await record_admin_audit(
        db,
        actor_id=admin.user_id,
        action="platform_credential.clear_runtime",
        target_type="platform_credential",
        target_id=view.id,
        detail={**_audit_detail(view), "cleared_status": prior.status},
    )
    return _view(view)
