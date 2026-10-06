"""Assemblies CRUD (工具箱·装配).

``/v1/users/me/assemblies`` — list, create, update, delete, set the star.
The model columns live on the assembly. A conversation stores only ``assembly_id``.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import (
    CreateLlmModelProfileRequest,
    LlmModelProfileListResponse,
    LlmModelProfileView,
    SetDefaultModelProfileRequest,
    StatusResponse,
    UpdateLlmModelProfileRequest,
)
from agentcore.core.logging import get_logger
from agentcore.db.repositories import UserRepository
from agentcore.llm.model_profiles import (
    LlmModelProfileService,
    ModelProfileView,
    ProfileSlot,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/users/me/assemblies", tags=["assemblies"])


def get_profile_service(session: AsyncSession = Depends(get_db)) -> LlmModelProfileService:
    return LlmModelProfileService(session)


def _slot(slot: object) -> dict | None:
    if slot is None:
        return None
    return {
        "origin": slot.origin,  # type: ignore[attr-defined]
        "provider_id": slot.provider_id,  # type: ignore[attr-defined]
        "model": slot.model,  # type: ignore[attr-defined]
    }


def _to_response(view: ModelProfileView) -> LlmModelProfileView:
    return LlmModelProfileView(
        id=view.id,
        name=view.name,
        kind=view.kind,
        is_default=view.is_default,
        recipe=view.recipe,  # type: ignore[arg-type]
        main=_slot(view.main),  # type: ignore[arg-type]
        worker=_slot(view.worker),  # type: ignore[arg-type]
        background=_slot(view.background),  # type: ignore[arg-type]
        vision=_slot(view.vision),  # type: ignore[arg-type]
        reasoning_effort=view.reasoning_effort,
        context_budget=view.context_budget,
        enabled_mcp_server_ids=list(view.enabled_mcp_server_ids),
        omit_factory_catalog=view.omit_factory_catalog,
        warnings=list(view.warnings),
    )


@router.get("", response_model=LlmModelProfileListResponse)
async def list_model_profiles(
    user: AuthUser,
    service: LlmModelProfileService = Depends(get_profile_service),
    session: AsyncSession = Depends(get_db),
):
    views = await service.list_profiles(user.user_id)
    u = await UserRepository(session).get_by_id(user.user_id)
    default_id = getattr(u, "default_assembly_id", None) if u else None
    return LlmModelProfileListResponse(
        data=[_to_response(v) for v in views],
        default_assembly_id=default_id,
    )


@router.post("", response_model=LlmModelProfileView, status_code=201)
async def create_model_profile(
    body: CreateLlmModelProfileRequest,
    user: AuthUser,
    service: LlmModelProfileService = Depends(get_profile_service),
):
    view = await service.create_profile(
        user.user_id,
        name=body.name,
        set_as_default=body.set_as_default,
    )
    logger.info(
        "llm_model_profile.created",
        user_id=user.user_id,
        profile_id=view.id,
        name=view.name,
    )
    return _to_response(view)


@router.put("/default", response_model=LlmModelProfileView)
async def set_default_model_profile(
    body: SetDefaultModelProfileRequest,
    user: AuthUser,
    service: LlmModelProfileService = Depends(get_profile_service),
):
    return _to_response(await service.set_default(user.user_id, body.profile_id))


@router.get("/{profile_id}", response_model=LlmModelProfileView)
async def get_model_profile(
    profile_id: str,
    user: AuthUser,
    service: LlmModelProfileService = Depends(get_profile_service),
):
    return _to_response(await service.get_profile(user.user_id, profile_id))


@router.patch("/{profile_id}", response_model=LlmModelProfileView)
async def update_model_profile(
    profile_id: str,
    body: UpdateLlmModelProfileRequest,
    user: AuthUser,
    service: LlmModelProfileService = Depends(get_profile_service),
):
    fields = set(body.model_fields_set)

    def _as_slot(raw: object) -> ProfileSlot | None:
        if raw is None:
            return None
        return ProfileSlot(
            origin=raw.origin,  # type: ignore[attr-defined]
            model=raw.model,  # type: ignore[attr-defined]
            provider_id=raw.provider_id,  # type: ignore[attr-defined]
        )

    return _to_response(
        await service.update_profile(
            user.user_id,
            profile_id,
            name=body.name,
            enabled_mcp_server_ids=body.enabled_mcp_server_ids,
            omit_factory_catalog=body.omit_factory_catalog,
            main=_as_slot(body.main) if "main" in fields else None,
            worker=_as_slot(body.worker) if "worker" in fields else None,
            background=_as_slot(body.background) if "background" in fields else None,
            vision=_as_slot(body.vision) if "vision" in fields else None,
            reasoning_effort=body.reasoning_effort,
            context_budget=body.context_budget,
            fields_set=fields,
        )
    )


@router.delete("/{profile_id}", response_model=StatusResponse)
async def delete_model_profile(
    profile_id: str,
    user: AuthUser,
    service: LlmModelProfileService = Depends(get_profile_service),
):
    await service.delete_profile(user.user_id, profile_id)
    return StatusResponse()
