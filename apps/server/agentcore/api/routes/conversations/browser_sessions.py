"""Browser session list/create/close/navigate — multi ``session_id`` (M0 / M2).

Owner-only. Conversation-level thin wrap: the registry is the single in-memory map
(``session_id`` primary key); these endpoints expose list / create / close / navigate
without a second Registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import (
    BrowserSessionCreateRequest,
    BrowserSessionListResponse,
    BrowserSessionNavigateRequest,
    BrowserSessionNavPatch,
    BrowserSessionView,
    StatusResponse,
)
from agentcore.config import settings
from agentcore.conversation.common import resolve_turn_file_workspace
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.db.models import Conversation
from agentcore.db.repositories import ConversationRepository
from agentcore.folders.placement import resolve_folder_placement
from agentcore.runtime.browser import default_browser_session_registry
from agentcore.tools.sandbox.browser.protocol import BrowserCommand, BrowserSessionRequest
from agentcore.workspace.locate import workspace_root_path

from ._helpers import _get_owned_conversation, _require_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _conversation_workspace_root_str(
    conv: Conversation, user_id: str, session: AsyncSession
) -> str | None:
    """Same physical root as file tools. Do not mkdir for opening a browser."""
    ws_folder_id, _ = resolve_turn_file_workspace(
        birth_folder_id=conv.folder_id,
        auto_desk_folder_id=getattr(conv, "auto_desk_folder_id", None),
    )
    placement = await resolve_folder_placement(ws_folder_id, session=session)
    try:
        root = workspace_root_path(
            user_id=user_id,
            folder_rel_path=placement.rel_path,
            conversation_id=conv.id,
        )
    except ValueError:
        return None
    if not root.is_dir():
        return None
    return str(root)


def _view_from_info(info) -> BrowserSessionView:
    return BrowserSessionView(
        session_id=info.session_id,
        conversation_id=info.conversation_id,
        host_kind=info.host_kind,
        run_id=info.run_id,
        control=info.control,
        created_at=info.created_at,
        last_used=info.last_used,
        url=getattr(info, "url", None),
        title=getattr(info, "title", None),
    )


@router.get("/{conversation_id}/browser/sessions", response_model=BrowserSessionListResponse)
async def list_browser_sessions(
    conversation_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
) -> BrowserSessionListResponse:
    """List live browser sessions for the conversation (owner-only)."""
    await _require_owned_conversation(
        conversation_id, user.user_id, ConversationRepository(session)
    )
    reg = default_browser_session_registry()
    infos = reg.list_by_conversation(conversation_id)
    active = reg.resolve_session_id(conversation_id)
    return BrowserSessionListResponse(
        data=[_view_from_info(i) for i in infos],
        active_session_id=active,
    )


@router.post(
    "/{conversation_id}/browser/sessions",
    response_model=BrowserSessionView,
    status_code=201,
)
async def create_browser_session(
    conversation_id: str,
    body: BrowserSessionCreateRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
) -> BrowserSessionView:
    """Create a new browser session tab (owner-only)."""
    conv = await _get_owned_conversation(
        conversation_id, user.user_id, ConversationRepository(session)
    )
    workspace_root = await _conversation_workspace_root_str(conv, user.user_id, session)
    if body.host_kind == "sandbox" and not workspace_root:
        raise ValidationError("云端浏览器需要已挂载的工作区盘（禁止无盘 jail）。")
    reg = default_browser_session_registry()
    request = BrowserSessionRequest(
        conversation_id=conversation_id,
        workspace_root=workspace_root,
        viewport_width=int(settings.browser_keyframe_width),
        jpeg_quality=int(settings.browser_keyframe_jpeg_quality),
        host_kind=body.host_kind,
    )
    _browser, _kf, session_id = await reg.create(
        request,
        host_kind=body.host_kind,
        activate=body.activate,
    )
    infos = [i for i in reg.list_by_conversation(conversation_id) if i.session_id == session_id]
    return _view_from_info(infos[0])


@router.delete(
    "/{conversation_id}/browser/sessions/{session_id}",
    response_model=StatusResponse,
)
async def close_browser_session(
    conversation_id: str,
    session_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
) -> StatusResponse:
    """Close one browser session by id (owner-only)."""
    await _require_owned_conversation(
        conversation_id, user.user_id, ConversationRepository(session)
    )
    reg = default_browser_session_registry()
    if reg.conversation_of(session_id) != conversation_id:
        raise NotFoundError("浏览器会话不存在")
    await reg.close_session(session_id)
    return StatusResponse()


@router.patch(
    "/{conversation_id}/browser/sessions/{session_id}",
    response_model=BrowserSessionView,
)
async def patch_browser_session_nav(
    conversation_id: str,
    session_id: str,
    body: BrowserSessionNavPatch,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
) -> BrowserSessionView:
    """L7 最小：回写 url/title（用户地址栏 / Bridge 导航后）。"""
    await _require_owned_conversation(
        conversation_id, user.user_id, ConversationRepository(session)
    )
    reg = default_browser_session_registry()
    if reg.conversation_of(session_id) != conversation_id:
        raise NotFoundError("浏览器会话不存在")
    if not reg.update_nav(session_id, url=body.url, title=body.title):
        raise NotFoundError("浏览器会话不存在")
    infos = [i for i in reg.list_by_conversation(conversation_id) if i.session_id == session_id]
    return _view_from_info(infos[0])


@router.post(
    "/{conversation_id}/browser/sessions/{session_id}/navigate",
    response_model=BrowserSessionView,
)
async def navigate_browser_session(
    conversation_id: str,
    session_id: str,
    body: BrowserSessionNavigateRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
) -> BrowserSessionView:
    """Owner address-bar navigate: registry ``send(BrowserCommand navigate)``.

    Works for sandbox (Web) and local host_kind sessions alike.
    """
    await _require_owned_conversation(
        conversation_id, user.user_id, ConversationRepository(session)
    )
    reg = default_browser_session_registry()
    if reg.conversation_of(session_id) != conversation_id:
        raise NotFoundError("浏览器会话不存在")
    browser = reg.get(session_id)
    if browser is None:
        raise NotFoundError("浏览器会话不存在")
    result = await browser.send(BrowserCommand(action="navigate", args={"url": body.url}))
    if not result.ok:
        raise ValidationError(result.error or "浏览器导航失败")
    final_url = (result.data or {}).get("final_url")
    title = (result.data or {}).get("title")
    reg.update_nav(
        session_id,
        url=str(final_url) if final_url is not None else body.url,
        title=str(title) if title is not None else None,
    )
    infos = [i for i in reg.list_by_conversation(conversation_id) if i.session_id == session_id]
    if not infos:
        raise NotFoundError("浏览器会话不存在")
    return _view_from_info(infos[0])
