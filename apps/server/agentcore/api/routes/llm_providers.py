"""BYOK LLM provider management (设置·模型配置 · 多服务商列表).

A user's list of OpenAI-compatible providers: list (+ deployment caps), add, edit,
remove, and connectivity-test each. Account default combination is under
``/users/me/assemblies``.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import (
    CreateLlmProviderRequest,
    LlmProvidersResponse,
    LlmProviderView,
    StatusResponse,
    UpdateLlmProviderRequest,
)
from agentcore.core.logging import get_logger
from agentcore.llm.provider_service import LlmProviderService, LlmProvidersView
from agentcore.llm.provider_service import LlmProviderView as ServiceProviderView

logger = get_logger(__name__)

router = APIRouter(prefix="/users/me/llm-providers", tags=["llm-providers"])


def get_llm_provider_service(session: AsyncSession = Depends(get_db)) -> LlmProviderService:
    return LlmProviderService(session)


def _provider_to_response(view: ServiceProviderView) -> LlmProviderView:
    return LlmProviderView(
        id=view.id,
        label=view.label,
        base_url=view.base_url,
        status=view.status,
        masked_key=view.masked_key,
        supports_tools=view.supports_tools,
        message=view.message,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _collection_to_response(view: LlmProvidersView) -> LlmProvidersResponse:
    return LlmProvidersResponse(
        providers=[_provider_to_response(p) for p in view.providers],
        default_assembly_id=view.default_assembly_id,
        billing_mode=view.billing_mode,
        platform_available=view.platform_available,
        platform_model=view.platform_model,
    )


@router.get("", response_model=LlmProvidersResponse)
async def list_llm_providers(
    user: AuthUser,
    service: LlmProviderService = Depends(get_llm_provider_service),
):
    """The user's BYOK providers + deployment capabilities."""
    return _collection_to_response(await service.list_providers(user.user_id))


@router.post("", response_model=LlmProviderView, status_code=201)
async def create_llm_provider(
    body: CreateLlmProviderRequest,
    user: AuthUser,
    service: LlmProviderService = Depends(get_llm_provider_service),
):
    """Add an OpenAI-compatible provider (key encrypted at rest; status 'unchecked')."""
    view = await service.create_provider(
        user.user_id,
        label=body.label,
        api_key=body.api_key,
        base_url=body.base_url,
    )
    logger.info(
        "llm_provider.created",
        user_id=user.user_id,
        provider_id=view.id,
        base_url=view.base_url,
    )
    return _provider_to_response(view)


@router.patch("/{provider_id}", response_model=LlmProviderView)
async def update_llm_provider(
    provider_id: str,
    body: UpdateLlmProviderRequest,
    user: AuthUser,
    service: LlmProviderService = Depends(get_llm_provider_service),
):
    """Update a provider (endpoint / label; key optional to keep)."""
    fields_set = set(body.model_fields_set)
    view = await service.update_provider(
        user.user_id,
        provider_id,
        label=body.label,
        api_key=body.api_key,
        base_url=body.base_url,
        fields_set=fields_set,
    )
    if "api_key" in fields_set and (body.api_key or "").strip():
        # Key material never logged — only the fact a BYOK key was rotated.
        logger.info(
            "llm_provider.key_updated",
            user_id=user.user_id,
            provider_id=provider_id,
        )
    return _provider_to_response(view)


@router.delete("/{provider_id}", response_model=StatusResponse)
async def delete_llm_provider(
    provider_id: str,
    user: AuthUser,
    service: LlmProviderService = Depends(get_llm_provider_service),
):
    """Remove a provider (profile slots referencing it fall back cleanly)."""
    await service.delete_provider(user.user_id, provider_id)
    logger.info(
        "llm_provider.deleted",
        user_id=user.user_id,
        provider_id=provider_id,
    )
    return StatusResponse()


@router.post("/{provider_id}/test", response_model=LlmProviderView)
async def test_llm_provider(
    provider_id: str,
    user: AuthUser,
    service: LlmProviderService = Depends(get_llm_provider_service),
):
    """Probe one provider's endpoint and persist 'active' / 'error' + supports_tools."""
    view = await service.test_provider(user.user_id, provider_id)
    logger.info(
        "llm_provider.tested",
        user_id=user.user_id,
        provider_id=provider_id,
        status=view.status,
        supports_tools=view.supports_tools,
        has_message=bool(view.message),
    )
    return _provider_to_response(view)
