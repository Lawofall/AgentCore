"""Account web-search connection (设置 · 通用).

Platform SearXNG is the default. 开析 and SearXNG are one slot each. Connect
probes and, only on success, saves the credential and selects that engine.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.core.logging import get_logger
from agentcore.tools.builtin.web.provider_service import (
    SearchConnectionService,
    SearchConnectionView,
    SearchSlotView,
)
from agentcore.tools.builtin.web.search_choice import CLEVERSEE, SEARXNG
from agentcore.tools.builtin.web.search_quota import SearchQuotaSnapshot

logger = get_logger(__name__)

router = APIRouter(prefix="/users/me/search-connection", tags=["search-connection"])


def get_search_connection_service(
    session: AsyncSession = Depends(get_db),
) -> SearchConnectionService:
    return SearchConnectionService(session)


class SearchQuotaView(BaseModel):
    daily_used: int
    daily_limit: int
    monthly_used: int
    monthly_limit: int


class SearchSlotResponse(BaseModel):
    status: Literal["connected", "needs_reconnect"]
    base_url: str
    masked_key: str | None = None
    message: str | None = None


class SearchConnectionResponse(BaseModel):
    selected: Literal["platform", "cleversee", "searxng"]
    quota: SearchQuotaView
    cleversee: SearchSlotResponse | None = None
    searxng: SearchSlotResponse | None = None


class SearchSelectionRequest(BaseModel):
    engine: Literal["platform", "cleversee", "searxng"]


class ConnectCleverSeeRequest(BaseModel):
    api_key: str = ""


class ConnectSearxngRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""


def _quota(snapshot: SearchQuotaSnapshot) -> SearchQuotaView:
    return SearchQuotaView(
        daily_used=snapshot.daily_used,
        daily_limit=snapshot.daily_limit,
        monthly_used=snapshot.monthly_used,
        monthly_limit=snapshot.monthly_limit,
    )


def _slot(view: SearchSlotView | None) -> SearchSlotResponse | None:
    if view is None:
        return None
    return SearchSlotResponse(
        status=view.status,  # type: ignore[arg-type]
        base_url=view.base_url,
        masked_key=view.masked_key,
        message=view.message,
    )


def _connection(view: SearchConnectionView) -> SearchConnectionResponse:
    return SearchConnectionResponse(
        selected=view.selected,  # type: ignore[arg-type]
        quota=_quota(view.quota),
        cleversee=_slot(view.cleversee),
        searxng=_slot(view.searxng),
    )


@router.get("", response_model=SearchConnectionResponse)
async def get_search_connection(
    user: AuthUser,
    service: SearchConnectionService = Depends(get_search_connection_service),
) -> SearchConnectionResponse:
    return _connection(await service.get_view(user.user_id))


@router.put("/selection", response_model=SearchConnectionResponse)
async def select_search_connection(
    body: SearchSelectionRequest,
    user: AuthUser,
    service: SearchConnectionService = Depends(get_search_connection_service),
) -> SearchConnectionResponse:
    view = await service.select(user.user_id, body.engine)
    logger.info(
        "search_connection.selected",
        user_id=user.user_id,
        engine=body.engine,
    )
    return _connection(view)


@router.post("/cleversee", response_model=SearchConnectionResponse)
async def connect_cleversee(
    body: ConnectCleverSeeRequest,
    user: AuthUser,
    service: SearchConnectionService = Depends(get_search_connection_service),
) -> SearchConnectionResponse:
    view = await service.connect_cleversee(user.user_id, body.api_key)
    logger.info("search_connection.connected", user_id=user.user_id, engine=CLEVERSEE)
    return _connection(view)


@router.post("/searxng", response_model=SearchConnectionResponse)
async def connect_searxng(
    body: ConnectSearxngRequest,
    user: AuthUser,
    service: SearchConnectionService = Depends(get_search_connection_service),
) -> SearchConnectionResponse:
    view = await service.connect_searxng(user.user_id, body.base_url, body.api_key)
    logger.info("search_connection.connected", user_id=user.user_id, engine=SEARXNG)
    return _connection(view)


@router.delete("/cleversee", response_model=SearchConnectionResponse)
async def disconnect_cleversee(
    user: AuthUser,
    service: SearchConnectionService = Depends(get_search_connection_service),
) -> SearchConnectionResponse:
    view = await service.disconnect(user.user_id, CLEVERSEE)
    logger.info("search_connection.disconnected", user_id=user.user_id, engine=CLEVERSEE)
    return _connection(view)


@router.delete("/searxng", response_model=SearchConnectionResponse)
async def disconnect_searxng(
    user: AuthUser,
    service: SearchConnectionService = Depends(get_search_connection_service),
) -> SearchConnectionResponse:
    view = await service.disconnect(user.user_id, SEARXNG)
    logger.info("search_connection.disconnected", user_id=user.user_id, engine=SEARXNG)
    return _connection(view)
