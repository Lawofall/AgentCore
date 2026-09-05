"""Browser input injection — owner-only, live session (no takeover mark).

``POST …/browser/input`` {events, session_id?} injects CDP Input events on a live
session. 409 only when there is no live session or injection fails.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import BrowserInputRequest, BrowserInputResponse
from agentcore.config import settings
from agentcore.core.errors import ConflictError, ValidationError
from agentcore.db.repositories import ConversationRepository
from agentcore.runtime.browser import default_browser_session_registry
from agentcore.tools.sandbox.browser.protocol import (
    BrowserCommand,
    BrowserDriverCrashedError,
)

from ._helpers import _require_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/{conversation_id}/browser/input", response_model=BrowserInputResponse)
async def submit_browser_input(
    conversation_id: str,
    body: BrowserInputRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
) -> BrowserInputResponse:
    """Inject a batch of input events (owner-only; 409 if no live session / inject fails)."""
    await _require_owned_conversation(
        conversation_id, user.user_id, ConversationRepository(session)
    )
    sid = body.session_id
    max_events = int(settings.browser_input_max_events)
    if len(body.events) > max_events:
        raise ValidationError(f"单次输入事件过多（上限 {max_events} 条），请分批发送")

    reg = default_browser_session_registry()
    browser = reg.peek(conversation_id, session_id=sid)
    if browser is None:
        raise ConflictError("无活浏览器会话，无法注入输入")

    # Only counts/kinds are observable — event CONTENT (key/text, possibly a password) is
    # never logged (D17).
    events = [ev.model_dump(exclude_none=True) for ev in body.events]
    try:
        result = await browser.send(BrowserCommand(action="input", args={"events": events}))
    except BrowserDriverCrashedError:
        resolved = reg.resolve_session_id(conversation_id, session_id=sid)
        if resolved:
            await reg.close_session(resolved)
        else:
            await reg.close(conversation_id)
        raise ConflictError("浏览器会话已失效，无法注入输入") from None
    if not result.ok:
        raise ConflictError("浏览器输入注入失败，请重试")
    return BrowserInputResponse(injected=int(result.data.get("injected") or 0))
