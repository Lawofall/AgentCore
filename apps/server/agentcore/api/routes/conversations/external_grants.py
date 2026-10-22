"""Conversation-scoped directory grants (readonly | organize | attach_rw).

Separate from workspace binding — grants add a working root (session cwd) or
extra folders for file tools within one conversation; they never replace the
bound workspace ``folder_id``. Persisted in Postgres for the conversation
lifetime (not the API process).
"""

from fastapi import APIRouter, Depends, Query

from agentcore.api.dependencies import AuthUser, get_conversation_repo
from agentcore.api.schemas import (
    ExternalGrantItem,
    ExternalGrantListResponse,
    ExternalGrantResponse,
    GrantExternalReadonlyRequest,
    StatusResponse,
)
from agentcore.conversation.common import resolve_local_binding
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.db.repositories import ConversationRepository
from agentcore.fulfill.declare import declare_receipt_root
from agentcore.workspace import grant_store
from agentcore.workspace.working_root import model_facing_namespace

from ._helpers import _get_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _item(m) -> ExternalGrantItem:
    return ExternalGrantItem(
        alias=m.alias,
        root_id=m.root_id,
        label=m.label,
        namespace=model_facing_namespace(m),
        mode=m.mode,
        working_root=bool(m.working_root),
    )


@router.get(
    "/{conversation_id}/workspace/external-grants",
    response_model=ExternalGrantListResponse,
)
async def list_external_grants(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    return ExternalGrantListResponse(
        data=[_item(m) for m in await grant_store.list_grants(conversation_id)]
    )


@router.post(
    "/{conversation_id}/workspace/external-grants",
    response_model=ExternalGrantResponse,
    status_code=201,
)
async def grant_external_folder(
    conversation_id: str,
    body: GrantExternalReadonlyRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Register a conversation external mount (readonly, organize, or attach_rw).

    Called after desktop mint (silent read-only host-path mount or user-confirmed
    organize / attach_rw grant). Body carries ``root_id`` / label / mode only — never
    absolute paths. ``attach_rw`` is local-traditional only.

    The response doubles as the device's declaration for this root: the caller's
    ``X-Client-Device`` is bound onto its live fulfill session *and* stored on the
    grant (``fulfill/declare.py``), so the first file op of the resuming turn has
    a machine to route to. Scratch conversations promote the first grant to the
    working root (session cwd); ``folder_id`` does not change.
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    device_id = declare_receipt_root(user.user_id, body.root_id)
    if body.mode == "attach_rw" and not device_id:
        binding = await resolve_local_binding(conv_repo._session, conv)
        if binding is None:
            raise ValidationError("附加可写根需要桌面客户端")
    scratch = conv.folder_id is None
    mount = await grant_store.add_grant(
        conversation_id,
        root_id=body.root_id,
        label=body.label,
        alias_hint=body.alias_hint,
        mode=body.mode,
        device_id=device_id,
        working_root=scratch,
    )
    return ExternalGrantResponse(grant=_item(mount))


@router.delete(
    "/{conversation_id}/workspace/external-grants",
    response_model=StatusResponse,
)
async def revoke_external_grants(
    conversation_id: str,
    user: AuthUser,
    alias: str | None = Query(None),
    root_id: str | None = Query(None),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Revoke one grant (by alias or root_id) or all grants for the conversation."""
    await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    if alias is None and root_id is None:
        await grant_store.clear_conversation(conversation_id)
        return StatusResponse()
    ok = await grant_store.revoke_grant(conversation_id, alias=alias, root_id=root_id)
    if not ok:
        raise NotFoundError("授权不存在或已撤销")
    return StatusResponse()
