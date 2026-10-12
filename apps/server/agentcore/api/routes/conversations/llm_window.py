"""Owner-scoped diagnostic LLM window for one run within a turn."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_db,
    get_message_repo,
    get_turn_journal_repo,
)
from agentcore.api.schemas.llm_window import RunLlmWindowResponse
from agentcore.conversation.history import load_history_for_turn
from agentcore.core.errors import NotFoundError
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnJournalRepository,
)
from agentcore.runtime.journal.window_wire import history_len_from_journal, project_run_llm_window

from ._helpers import _require_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get(
    "/{conversation_id}/messages/{message_id}/runs/{run_id}/llm-window",
    response_model=RunLlmWindowResponse,
)
async def get_run_llm_window(
    conversation_id: str,
    message_id: str,
    run_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    msg_repo: MessageRepository = Depends(get_message_repo),
    journal_repo: TurnJournalRepository = Depends(get_turn_journal_repo),
    round_idx: int | None = Query(default=None),
) -> RunLlmWindowResponse:
    """Fold one run's LLM input window from turn_journal (owner-scoped, diagnostic).

    ``round_idx`` selects the request that call received, including the compact
    watermark already stored before it. Omitted, the end-state transcript is
    returned with compact left off.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)

    turn = await msg_repo.get_by_id(message_id, conversation_id=conversation_id)
    if turn is None or turn.role != "assistant":
        raise NotFoundError("回合不存在")

    entries = await journal_repo.load(message_id)
    history_len = history_len_from_journal(entries)

    history: list[dict] | None = None
    if history_len > 0:
        older, _ = await msg_repo.list_before(
            conversation_id, before=turn.created_at, limit=100
        )
        user_msg = None
        for msg in reversed(older):
            if msg.role == "user":
                user_msg = msg
                break
        if user_msg is not None:
            history = await load_history_for_turn(
                session,
                conversation_id,
                before_user_created_at=user_msg.created_at,
                history_len=history_len,
            )

    return project_run_llm_window(
        entries,
        run_id=run_id,
        history=history,
        round_idx=round_idx,
        history_len=history_len,
    )
