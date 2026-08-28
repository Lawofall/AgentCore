"""W3 conversation-scoped external directory grants (readonly | organize | attach_rw).

Separate from workspace binding — grants add ``external/<alias>/`` mounts for
file tools within one conversation; they never replace the bound workspace root.
Persisted in Postgres for the conversation lifetime (not the API process).
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
from agentcore.workspace.external_mounts import external_ns

from ._helpers import _get_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _item(m) -> ExternalGrantItem:
    return ExternalGrantItem(
        alias=m.alias,
        root_id=m.root_id,
        label=m.label,
        namespace=external_ns(m.alias),
        mode=m.mode,
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

    Called after desktop mint (silent ``external_mount_readonly`` or user-confirmed
    organize / attach_rw grant). Body carries ``root_id`` / label / mode only — never
    absolute paths. ``attach_rw`` is local-traditional only.

    The response doubles as the device's declaration for this root: the caller's
    ``X-Client-Device`` is bound onto its live fulfill session *and* stored on the
    grant (``fulfill/declare.py``), so the very first ``external/<alias>/`` op of
    the resuming turn has a machine to route to.
    """
    conv = await _get_owned_conversation(conversation_id, user.user_id, conv_repo)
    if body.mode == "attach_rw":
        binding = await resolve_local_binding(conv_repo._session, conv)
        if binding is None:
            raise ValidationError("附加可写根仅本机传统对话可用")
    device_id = declare_receipt_root(user.user_id, body.root_id)
    mount = await grant_store.add_grant(
        conversation_id,
        root_id=body.root_id,
        label=body.label,
        alias_hint=body.alias_hint,
        mode=body.mode,
        device_id=device_id,
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
