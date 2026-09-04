"""Cloud user-preview ticket mint (安全 · 五、第二刀).

``POST /v1/preview/token`` exchanges the caller's access session for a short
enter URL. The API never proxies guest HTTP; sandboxd owns that origin.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_conversation_repo, get_db
from agentcore.api.routes.conversations._helpers import _get_owned_conversation
from agentcore.api.routes.conversations.files import _workspace_coords
from agentcore.config import settings
from agentcore.core.errors import (
    AgentCoreError,
    ConflictError,
    PreviewUnavailableError,
)
from agentcore.db.repositories import ConversationRepository
from agentcore.security.tokens import create_preview_token
from agentcore.tools.sandbox import desk_process as desk_process_mod
from agentcore.tools.sandbox.desk_process import (
    PREVIEW_PORT_NOT_READY,
    PROCESS_NOT_REGISTERED,
    PROCESS_NOT_RUNNING,
    DeskProcessError,
)
from agentcore.workspace.locate import build_server_workspace

router = APIRouter(prefix="/preview", tags=["preview"])

_NOT_READY = "该进程未在运行或没有可预览的 HTTP 端口"


class PreviewTokenRequest(BaseModel):
    """Mint body: which conversation process, and which listen port when several."""

    conversation_id: str = Field(min_length=1)
    process_id: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)


class PreviewTokenResponse(BaseModel):
    """Enter URL for the preview origin. The ticket is in the query, not a field."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://preview.example/enter",
                "expires_in_sec": 900,
                "port": 5173,
            }
        }
    )

    url: str
    expires_in_sec: int
    port: int


def _http_ports(found: object) -> tuple[int, ...]:
    raw = getattr(found, "http_ports", ()) or ()
    return tuple(int(p) for p in raw)


def _ports_conflict(ports: tuple[int, ...]) -> ConflictError:
    listed = "、".join(str(p) for p in ports)
    return ConflictError(f"请指定预览端口（{listed}）")


def _resolve_preview_port(found: object, requested: int | None) -> int:
    """Pick the guest HTTP port. Never guess among several."""
    if found is None or getattr(found, "status", None) != "running":
        raise ConflictError(_NOT_READY)
    ports = _http_ports(found)
    if not ports:
        raise ConflictError(_NOT_READY)
    if requested is not None:
        if requested not in ports:
            raise _ports_conflict(ports)
        return requested
    if len(ports) == 1:
        return ports[0]
    raise _ports_conflict(ports)


_ENSURE_CONFLICT_CODES = frozenset(
    {
        PROCESS_NOT_REGISTERED,
        PROCESS_NOT_RUNNING,
        PREVIEW_PORT_NOT_READY,
        "VALIDATION_ERROR",
    }
)


def _workspace_path(workspace: object) -> str:
    root = getattr(workspace, "root", None)
    if root is None:
        raise PreviewUnavailableError()
    return str(Path(root).resolve())


async def _ensure_cloud_preview(
    workspace: object, conversation_id: str, process_id: str, port: int
) -> None:
    try:
        await desk_process_mod.ensure_cloud_preview(
            _workspace_path(workspace), conversation_id, process_id, port
        )
    except DeskProcessError as exc:
        if exc.code in _ENSURE_CONFLICT_CODES:
            raise ConflictError(str(exc)) from exc
        raise PreviewUnavailableError() from exc
    except AgentCoreError:
        raise
    except Exception as exc:
        raise PreviewUnavailableError() from exc


def _preview_public_base() -> str:
    base = (settings.preview_public_base_url or "").strip().rstrip("/")
    if not base:
        raise PreviewUnavailableError()
    return base


@router.post("/token", response_model=PreviewTokenResponse)
async def mint_preview_token(
    body: PreviewTokenRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
) -> PreviewTokenResponse:
    """Exchange the access session for a short cloud-preview enter URL."""
    conv = await _get_owned_conversation(body.conversation_id, user.user_id, conv_repo)
    workspace = build_server_workspace(
        **await _workspace_coords(user.user_id, conv, session),
    )
    found = desk_process_mod.lookup_cloud_preview(body.conversation_id, body.process_id)
    port = _resolve_preview_port(found, body.port)
    base = _preview_public_base()
    await _ensure_cloud_preview(workspace, body.conversation_id, body.process_id, port)
    token = create_preview_token(
        user.user_id,
        conversation_id=body.conversation_id,
        process_id=body.process_id,
        port=port,
    )
    return PreviewTokenResponse(
        url=f"{base}/enter?t={token}",
        expires_in_sec=settings.preview_token_expire_minutes * 60,
        port=port,
    )
