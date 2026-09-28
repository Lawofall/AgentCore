"""Cloud web search for the on-machine sidecar.

Platform SearXNG, CleverSee, and keyed own SearXNG run here so the desktop
never holds a search key and the platform count cannot be skipped. A keyless
own SearXNG is called from the sidecar and does not use this route.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from agentcore.api.routes.inference.token import inference_user
from agentcore.conversation.inference_rate_limit import enforce_inference_proxy_rate_limit
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import QuotaExceededError
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.core.net import EgressError, describe_net_error
from agentcore.db.models import User
from agentcore.llm.credentials import (
    INFERENCE_CONVERSATION_HEADER,
    INFERENCE_MESSAGE_HEADER,
    INFERENCE_TRACE_HEADER,
)
from agentcore.tools.builtin.web.search_backend import DEFAULT_MAX_RESULTS, get_search_backend
from agentcore.tools.builtin.web.search_choice import (
    SearchRouteView,
    load_search_target,
    route_from_target,
)
from agentcore.tools.builtin.web.search_dispatch import dispatch_search
from agentcore.tools.builtin.web.search_quota import (
    admit_platform_search,
    note_platform_search,
)

logger = get_logger(__name__)

router = APIRouter()

# Sidecar HTTP contract ceiling (the built-in tool always requests 8).
_MAX_RESULTS_CAP = 12


class InferenceWebSearchRequest(BaseModel):
    """Sidecar cloud-search body — query required; result count / language optional."""

    query: str = Field(..., min_length=1)
    max_results: int | None = Field(default=None, ge=1, le=_MAX_RESULTS_CAP)
    language: str | None = Field(default=None, max_length=32)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be empty")
        return stripped

    @field_validator("language")
    @classmethod
    def _strip_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class InferenceWebSearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str


class InferenceWebSearchResponse(BaseModel):
    results: list[InferenceWebSearchResultItem]
    source: Literal["cloud"] = "cloud"


@router.post(
    "/inference/web_search",
    response_model=InferenceWebSearchResponse,
    summary="Sidecar cloud web search",
)
async def inference_web_search(
    body: InferenceWebSearchRequest,
    request: Request,
    user: User = Depends(inference_user),
) -> InferenceWebSearchResponse | JSONResponse:
    """Run server-side web search for a sidecar turn (account index; no client keys)."""
    conversation_id = request.headers.get(INFERENCE_CONVERSATION_HEADER) or None
    message_id = request.headers.get(INFERENCE_MESSAGE_HEADER) or None
    trace_id = request.headers.get(INFERENCE_TRACE_HEADER) or None
    max_results = body.max_results if body.max_results is not None else DEFAULT_MAX_RESULTS

    with log_context(
        trace_id=trace_id,
        conversation_id=conversation_id,
        user_id=user.user_id,
    ):
        # Same outer rate fence as chat/completions (once per sidecar turn via message_id).
        await enforce_inference_proxy_rate_limit(user.user_id, message_id=message_id)

        try:
            target = await load_search_target(user.user_id)
            if target.kind == "platform":
                await admit_platform_search(user.user_id)
                backend = get_search_backend()
                hits = await backend.search(
                    body.query,
                    max_results=max_results,
                    language=body.language,
                )
                await note_platform_search(user.user_id)
            else:
                dispatched = await dispatch_search(
                    target,
                    body.query,
                    max_results=max_results,
                    on_phase=None,
                    language=body.language,
                    user_id=user.user_id,
                )
                hits = dispatched.results
            protocol = target.protocol
        except QuotaExceededError:
            raise
        except EgressError as exc:
            logger.warning(
                "inference.web_search_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "code": ErrorCode.INTERNAL_ERROR,
                        "message": str(exc) or "云端搜索暂时不可用",
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface clean 502; never leak traceback
            logger.warning(
                "inference.web_search_failed",
                error=describe_net_error(exc),
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "code": ErrorCode.INTERNAL_ERROR,
                        "message": "云端搜索暂时不可用",
                    }
                },
            )

        logger.info(
            "inference.web_search",
            result_count=len(hits),
            max_results=max_results,
            query_chars=len(body.query),
            engine=protocol,
        )
        return InferenceWebSearchResponse(
            results=[
                InferenceWebSearchResultItem(
                    title=hit.title,
                    url=hit.url,
                    snippet=hit.snippet,
                )
                for hit in hits
            ],
            source="cloud",
        )


@router.get("/inference/search_route", response_model=SearchRouteView)
async def inference_search_route(user: User = Depends(inference_user)) -> SearchRouteView:
    """Sidecar-visible search route (inference JWT). Never includes an API key."""
    return route_from_target(await load_search_target(user.user_id))
