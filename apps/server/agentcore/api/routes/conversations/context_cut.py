"""User context cut: preview, commit, undo. Same conversation, transcript stays."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentcore.api.dependencies import AuthUser, get_conversation_repo
from agentcore.api.schemas.conversations import (
    ContextCutCommitRequest,
    ContextCutPreviewRequest,
    ContextCutPreviewResponse,
    ConversationSummary,
    conversation_summary_from_orm,
)
from agentcore.conversation.context_cut import (
    commit_context_cut,
    preview_context_cut,
    undo_context_cut,
)
from agentcore.db.repositories import ConversationRepository

from ._helpers import _require_conversation_write

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post(
    "/{conversation_id}/context-cut/preview",
    response_model=ContextCutPreviewResponse,
)
async def preview_context_cut_route(
    conversation_id: str,
    body: ContextCutPreviewRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
) -> ContextCutPreviewResponse:
    """Summarize everything still in the window before this message. Does not write."""
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    preview = await preview_context_cut(
        conv_repo._session, conversation_id, body.message_id
    )
    return ContextCutPreviewResponse(
        summary=preview.summary,
        keep_message_id=preview.keep_message_id,
        fold_through=preview.fold_through,
        fold_digest=preview.fold_digest,
        folded_count=preview.folded_count,
    )


@router.post("/{conversation_id}/context-cut", response_model=ConversationSummary)
async def commit_context_cut_route(
    conversation_id: str,
    body: ContextCutCommitRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
) -> ConversationSummary:
    """Store the prose the user confirmed. Does not summarize again."""
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    conv = await commit_context_cut(
        conv_repo._session,
        conversation_id,
        message_id=body.message_id,
        fold_digest_value=body.fold_digest,
        summary=body.summary,
    )
    return conversation_summary_from_orm(conv)


@router.post("/{conversation_id}/context-cut/undo", response_model=ConversationSummary)
async def undo_context_cut_route(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
) -> ConversationSummary:
    """Restore the compaction state from immediately before the latest cut."""
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    conv = await undo_context_cut(conv_repo._session, conversation_id)
    return conversation_summary_from_orm(conv)
