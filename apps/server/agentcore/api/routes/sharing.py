"""Public share routes: conversation manage API + ``GET /shared/{token}`` dispatch.

Two routers:

- ``router`` (auth, mounted under ``/v1/conversations``) — the owner creates, lists,
  and revokes public links for their own conversation (404 for a non-owner, IDOR-safe).
  Creating a share FREEZES a content-only transcript snapshot (所见即所享): later edits
  never leak, no future turns are exposed.
- ``public_router`` (NO auth, mounted at the root) — ``GET /shared/{token}`` renders a
  frozen conversation *or* 文档 snapshot as a self-contained HTML page. The token is
  the share row id (uuid4, unguessable); a revoked / unknown / malformed token
  renders a 404 page without leaking whether the id ever existed. 文档 mint/list/revoke
  live on ``/v1/docs/{id}/shares`` (desk ``can_write``).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_conversation_share_repo,
    get_doc_share_repo,
    get_message_repo,
)
from agentcore.api.schemas import (
    CreateShareRequest,
    ShareListResponse,
    ShareSummary,
    StatusResponse,
)
from agentcore.conversation.sharing import build_share_snapshot, render_share_html
from agentcore.core.errors import NotFoundError
from agentcore.db.models import ConversationShare
from agentcore.db.repositories import (
    ConversationRepository,
    ConversationShareRepository,
    DocShareRepository,
    MessageRepository,
)
from agentcore.doc.share import render_doc_share_html

router = APIRouter(prefix="/conversations", tags=["sharing"])
public_router = APIRouter(tags=["shared"])


def _share_summary(share: ConversationShare) -> ShareSummary:
    # ``url`` is the relative public path; the client prepends the API origin (same
    # convention as UserResponse.avatar_url), so the backend stays host-agnostic.
    return ShareSummary(
        id=share.id,
        url=f"/shared/{share.id}",
        title=share.title,
        created_at=share.created_at,
        expires_at=share.expires_at,
    )


def _expires_at_from_request(body: CreateShareRequest | None) -> datetime | None:
    """Map create-body TTL to an absolute expiry (``None`` = never)."""
    days = 30 if body is None else body.expires_in_days
    if days is None:
        return None
    return datetime.now(UTC) + timedelta(days=days)


async def _require_owned(conversation_id: str, user_id: str, repo: ConversationRepository) -> None:
    if await repo.get_by_id(conversation_id, user_id=user_id) is None:
        raise NotFoundError("对话不存在")


@router.post("/{conversation_id}/shares", response_model=ShareSummary, status_code=201)
async def create_share(
    conversation_id: str,
    user: AuthUser,
    body: CreateShareRequest | None = None,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
    share_repo: ConversationShareRepository = Depends(get_conversation_share_repo),
):
    """Create a public read-only link for a conversation (分享对话).

    Owner-scoped (404 otherwise). Freezes a content-only snapshot of the transcript
    *now* — the public page renders this copy, so a later edit / delete to the live
    messages never changes a shared link, and no future turns are exposed. Each call
    mints a fresh snapshot + token; revoke (or delete the conversation) to kill a link.
    """
    conv = await conv_repo.get_by_id(conversation_id, user_id=user.user_id)
    if conv is None:
        raise NotFoundError("对话不存在")
    messages = await msg_repo.list_all_for_conversation(conversation_id)
    snapshot = build_share_snapshot(messages)
    share = await share_repo.create(
        conversation_id=conversation_id,
        user_id=user.user_id,
        title=(conv.title or "").strip(),
        snapshot=snapshot,
        expires_at=_expires_at_from_request(body),
    )
    return _share_summary(share)


@router.get("/{conversation_id}/shares", response_model=ShareListResponse)
async def list_shares(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    share_repo: ConversationShareRepository = Depends(get_conversation_share_repo),
):
    """List a conversation's live share links (owner-scoped) — the manage view."""
    await _require_owned(conversation_id, user.user_id, conv_repo)
    shares = await share_repo.list_active_for_conversation(conversation_id, user_id=user.user_id)
    data = [_share_summary(s) for s in shares]
    return ShareListResponse(data=data, total=len(data))


@router.delete("/{conversation_id}/shares/{share_id}", response_model=StatusResponse)
async def revoke_share(
    conversation_id: str,
    share_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    share_repo: ConversationShareRepository = Depends(get_conversation_share_repo),
):
    """Revoke a share link (撤销分享). The link 404s immediately after. 404 if the
    conversation isn't owned or the share is unknown / already revoked."""
    await _require_owned(conversation_id, user.user_id, conv_repo)
    revoked = await share_repo.revoke(
        share_id, conversation_id=conversation_id, user_id=user.user_id
    )
    if not revoked:
        raise NotFoundError("分享不存在")
    return StatusResponse()


def _not_found_page() -> str:
    """Minimal public 404 page for a revoked / unknown share token."""
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>分享不存在</title>\n"
        "<style>body{margin:0;min-height:100vh;display:flex;align-items:center;"
        "justify-content:center;background:#f7f7f8;color:#6b7280;font:16px/1.6 "
        '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;}'
        "</style>\n</head>\n<body>\n"
        "<div>该分享链接不存在或已被撤销。</div>\n"
        "</body>\n</html>\n"
    )


@public_router.get("/shared/{token}", response_class=HTMLResponse)
async def view_shared(
    token: str,
    share_repo: ConversationShareRepository = Depends(get_conversation_share_repo),
    doc_share_repo: DocShareRepository = Depends(get_doc_share_repo),
):
    """Public, read-only view of a frozen share snapshot (no auth).

    Conversation and 文档 shares share the ``/shared/<id>`` URL. A revoked /
    unknown / malformed token returns the same 404 page (never an existence leak).
    """
    try:
        UUID(token)
    except ValueError:
        return HTMLResponse(_not_found_page(), status_code=404)
    share = await share_repo.get_active(token)
    if share is not None:
        page = render_share_html(
            title=share.title, snapshot=share.snapshot, created_at=share.created_at
        )
        return HTMLResponse(page)
    doc_share = await doc_share_repo.get_active(token)
    if doc_share is None:
        return HTMLResponse(_not_found_page(), status_code=404)
    page = render_doc_share_html(
        title=doc_share.title,
        snapshot=doc_share.snapshot,
        created_at=doc_share.created_at,
    )
    return HTMLResponse(page)
