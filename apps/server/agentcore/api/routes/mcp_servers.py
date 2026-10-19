"""Account-level remote MCP Servers (Streamable HTTP + OAuth)."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import (
    CreateMcpServerRequest,
    McpOAuthStartRequest,
    McpOAuthStartResponse,
    McpServerListResponse,
    McpServerTestResponse,
    McpServerView,
    StatusResponse,
    UpdateMcpServerRequest,
)
from agentcore.core.errors import AgentCoreError
from agentcore.mcp.http import McpHttpError
from agentcore.mcp.servers import McpServerService, safe_return_origin
from agentcore.mcp.servers import McpServerView as ServiceView

router = APIRouter(prefix="/users/me/mcp-servers", tags=["mcp-servers"])


def get_mcp_server_service(
    session: AsyncSession = Depends(get_db),
) -> McpServerService:
    return McpServerService(session)


def _to_response(view: ServiceView) -> McpServerView:
    return McpServerView(
        id=view.id,
        name=view.name,
        url=view.url,
        auth_kind=view.auth_kind,
        status=view.status,
        has_token=view.has_token,
        last_error=view.last_error,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get("", response_model=McpServerListResponse)
async def list_mcp_servers(
    user: AuthUser,
    service: McpServerService = Depends(get_mcp_server_service),
):
    rows = await service.list_servers(user.user_id)
    return McpServerListResponse(data=[_to_response(row) for row in rows], total=len(rows))


@router.post("", response_model=McpServerView, status_code=201)
async def create_mcp_server(
    body: CreateMcpServerRequest,
    user: AuthUser,
    service: McpServerService = Depends(get_mcp_server_service),
):
    return _to_response(
        await service.create(
            user.user_id, name=body.name, url=body.url, bearer=body.bearer
        )
    )


@router.patch("/{server_id}", response_model=McpServerView)
async def update_mcp_server(
    server_id: str,
    body: UpdateMcpServerRequest,
    user: AuthUser,
    service: McpServerService = Depends(get_mcp_server_service),
):
    return _to_response(
        await service.update(
            user.user_id,
            server_id,
            name=body.name,
            url=body.url,
            bearer=body.bearer,
            fields_set=set(body.model_fields_set),
        )
    )


@router.delete("/{server_id}", response_model=StatusResponse)
async def delete_mcp_server(
    server_id: str,
    user: AuthUser,
    service: McpServerService = Depends(get_mcp_server_service),
):
    await service.delete(user.user_id, server_id)
    return StatusResponse()


@router.post("/{server_id}/test", response_model=McpServerTestResponse)
async def test_mcp_server(
    server_id: str,
    user: AuthUser,
    service: McpServerService = Depends(get_mcp_server_service),
):
    view, tools = await service.test(user.user_id, server_id)
    return McpServerTestResponse(
        id=view.id,
        status=view.status,
        tools=tools,
        last_error=view.last_error,
    )


@router.post("/{server_id}/oauth/start", response_model=McpOAuthStartResponse)
async def start_mcp_oauth(
    server_id: str,
    request: Request,
    body: McpOAuthStartRequest,
    user: AuthUser,
    service: McpServerService = Depends(get_mcp_server_service),
):
    fallback = str(request.base_url).rstrip("/")
    redirect_uri = fallback + "/v1/users/me/mcp-servers/oauth/callback"
    origin = safe_return_origin(body.return_origin, fallback=fallback)
    authorization_url = await service.start_oauth(
        user.user_id,
        server_id,
        redirect_uri=redirect_uri,
        return_origin=origin,
    )
    return McpOAuthStartResponse(authorization_url=authorization_url)


@router.get("/oauth/callback")
async def mcp_oauth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    session: AsyncSession = Depends(get_db),
):
    """Browser redirect from the authorization server — no login cookie required."""
    service = McpServerService(session)
    fallback = str(request.base_url).rstrip("/")
    if error or not code.strip() or not state.strip():
        return RedirectResponse(f"{fallback}/toolbox#plugs", status_code=302)
    try:
        return_origin, _server_id = await service.finish_oauth(
            code=code.strip(), state=state.strip()
        )
    except (AgentCoreError, McpHttpError):
        return RedirectResponse(f"{fallback}/toolbox#plugs", status_code=302)
    target = safe_return_origin(return_origin, fallback=fallback)
    return RedirectResponse(f"{target}/toolbox#plugs", status_code=302)
