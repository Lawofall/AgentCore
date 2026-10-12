"""Owner-scoped model transcript for one assistant message."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_message_repo,
    get_turn_journal_repo,
    get_turn_metrics_repo,
)
from agentcore.api.schemas.trace_view import TurnTraceResponse
from agentcore.core.errors import NotFoundError
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnJournalRepository,
    TurnMetricsRepository,
)
from agentcore.observability.query.trace_cells import build_turn_trace

from ._helpers import _require_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get(
    "/{conversation_id}/messages/{message_id}/trace",
    response_model=TurnTraceResponse,
)
async def get_turn_trace(
    conversation_id: str,
    message_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
    metrics_repo: TurnMetricsRepository = Depends(get_turn_metrics_repo),
    journal_repo: TurnJournalRepository = Depends(get_turn_journal_repo),
) -> TurnTraceResponse:
    """One owned assistant turn as the facts the model read and wrote.

    Display events stay in the journal for the chat and are not listed.
    The page does not read dev JSONL. A missing journal is a gap sentence.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    message = await msg_repo.get_by_id(message_id, conversation_id=conversation_id)
    if message is None or message.role != "assistant":
        raise NotFoundError("回合不存在")

    trace_id = (message.trace_id or "").strip() or None
    usage = message.usage if isinstance(message.usage, dict) else {}
    duration_ms = usage.get("duration_ms")
    mode: str | None = None
    if trace_id:
        metrics = await metrics_repo.latest_for_conversation_trace(
            conversation_id=conversation_id,
            trace_id=trace_id,
        )
        if metrics is not None:
            mode = metrics.mode
            if not isinstance(duration_ms, int) or duration_ms <= 0:
                duration_ms = metrics.duration_ms

    journal = await journal_repo.load_owned(message_id, conversation_id)
    payload = build_turn_trace(
        trace_id=trace_id,
        mode=mode,
        duration_ms=duration_ms if isinstance(duration_ms, int) else None,
        journal=journal,
    )
    return TurnTraceResponse.model_validate(payload)
