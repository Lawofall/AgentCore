"""Account web-search providers (设置 · 通用).

Platform SearXNG is the default (selected id null). Own CleverSee keys and own
SearXNG URLs are rows here. The model still sees one ``web_search``.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import StatusResponse
from agentcore.core.logging import get_logger
from agentcore.tools.builtin.web.provider_service import (
    SearchProviderService,
    SearchProvidersView,
    SearchProviderView,
)
from agentcore.tools.builtin.web.search_quota import SearchQuotaSnapshot

logger = get_logger(__name__)

router = APIRouter(prefix="/users/me/search-providers", tags=["search-providers"])


def get_search_provider_service(
    session: AsyncSession = Depends(get_db),
) -> SearchProviderService:
    return SearchProviderService(session)


class SearchQuotaView(BaseModel):
    daily_used: int
    daily_limit: int
    monthly_used: int
    monthly_limit: int


class SearchProviderResponse(BaseModel):
    id: str
    label: str
    protocol: Literal["searxng", "cleversee"]
    base_url: str
    status: Literal["unchecked", "active", "error"]
    masked_key: str | None = None
    message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SearchProvidersResponse(BaseModel):
    selected_provider_id: str | None
    quota: SearchQuotaView
    providers: list[SearchProviderResponse]


class CreateSearchProviderRequest(BaseModel):
    protocol: Literal["searxng", "cleversee"]
    label: str = ""
    base_url: str = ""
    api_key: str = ""


class UpdateSearchProviderRequest(BaseModel):
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class SearchProviderSelectionRequest(BaseModel):
    provider_id: str | None = Field(default=None)


def _quota(snapshot: SearchQuotaSnapshot) -> SearchQuotaView:
    return SearchQuotaView(
        daily_used=snapshot.daily_used,
        daily_limit=snapshot.daily_limit,
        monthly_used=snapshot.monthly_used,
        monthly_limit=snapshot.monthly_limit,
    )


def _provider(view: SearchProviderView) -> SearchProviderResponse:
    return SearchProviderResponse(
        id=view.id,
        label=view.label,
        protocol=view.protocol,  # type: ignore[arg-type]
        base_url=view.base_url,
        status=view.status,  # type: ignore[arg-type]
        masked_key=view.masked_key,
        message=view.message,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _collection(view: SearchProvidersView) -> SearchProvidersResponse:
    return SearchProvidersResponse(
        selected_provider_id=view.selected_provider_id,
        quota=_quota(view.quota),
        providers=[_provider(row) for row in view.providers],
    )


@router.get("", response_model=SearchProvidersResponse)
async def list_search_providers(
    user: AuthUser,
    service: SearchProviderService = Depends(get_search_provider_service),
) -> SearchProvidersResponse:
    return _collection(await service.list_providers(user.user_id))


@router.post("", response_model=SearchProviderResponse, status_code=201)
async def create_search_provider(
    body: CreateSearchProviderRequest,
    user: AuthUser,
    service: SearchProviderService = Depends(get_search_provider_service),
) -> SearchProviderResponse:
    view = await service.create_provider(
        user.user_id,
        protocol=body.protocol,
        label=body.label,
        base_url=body.base_url,
        api_key=body.api_key,
    )
    logger.info(
        "search_provider.created",
        user_id=user.user_id,
        provider_id=view.id,
        protocol=view.protocol,
    )
    return _provider(view)


@router.put("/selection", response_model=SearchProvidersResponse)
async def select_search_provider(
    body: SearchProviderSelectionRequest,
    user: AuthUser,
    service: SearchProviderService = Depends(get_search_provider_service),
) -> SearchProvidersResponse:
    await service.select_provider(user.user_id, body.provider_id)
    logger.info(
        "search_provider.selected",
        user_id=user.user_id,
        provider_id=body.provider_id or "",
    )
    return _collection(await service.list_providers(user.user_id))


@router.patch("/{provider_id}", response_model=SearchProviderResponse)
async def update_search_provider(
    provider_id: str,
    body: UpdateSearchProviderRequest,
    user: AuthUser,
    service: SearchProviderService = Depends(get_search_provider_service),
) -> SearchProviderResponse:
    view = await service.update_provider(
        user.user_id,
        provider_id,
        label=body.label,
        base_url=body.base_url,
        api_key=body.api_key,
        fields_set=set(body.model_fields_set),
    )
    return _provider(view)


@router.delete("/{provider_id}", response_model=StatusResponse)
async def delete_search_provider(
    provider_id: str,
    user: AuthUser,
    service: SearchProviderService = Depends(get_search_provider_service),
) -> StatusResponse:
    await service.delete_provider(user.user_id, provider_id)
    logger.info(
        "search_provider.deleted",
        user_id=user.user_id,
        provider_id=provider_id,
    )
    return StatusResponse()


@router.post("/{provider_id}/test", response_model=SearchProviderResponse)
async def test_search_provider(
    provider_id: str,
    user: AuthUser,
    service: SearchProviderService = Depends(get_search_provider_service),
) -> SearchProviderResponse:
    view = await service.test_provider(user.user_id, provider_id)
    logger.info(
        "search_provider.tested",
        user_id=user.user_id,
        provider_id=provider_id,
        status=view.status,
    )
    return _provider(view)
