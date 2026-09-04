"""Interaction resolution: settle a paused approval / escalation / delegation /
stage_card."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_conversation_repo, get_db
from agentcore.api.schemas import (
    ResolveInteractionRequest,
    ResolveStageCardInteraction,
    StatusResponse,
    interaction_result_from_body,
)
from agentcore.core.errors import AuthorizationError, NotFoundError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import ConversationRepository, TurnJournalRepository
from agentcore.runtime.events import (
    approval_resolved,
    escalation_resolved,
)
from agentcore.runtime.interaction import HOT_KINDS, InteractionKind, default_interaction_registry
from agentcore.runtime.interaction_orphan import emit_orphan_fact
from agentcore.runtime.journal.pending_interactions import fold_pending_interactions
from agentcore.runtime.settlement import already_settled_in_writer, prewrite_settlement
from agentcore.runtime.turn.runs import turn_runs

from ._helpers import _require_conversation_write

logger = get_logger(__name__)
router = APIRouter(prefix="/conversations", tags=["conversations"])


def _settlement_event_for_resolve(
    body: ResolveInteractionRequest,
    interaction_id: str,
    pending_payload: dict | None,
):
    """Build the same ``*_resolved`` SSE the awaiter would emit (D8 同形)."""
    payload = pending_payload or {}
    if body.kind == InteractionKind.APPROVAL.value:
        return approval_resolved(
            approval_id=interaction_id,
            tool_call_id=str(payload.get("tool_call_id") or interaction_id),
            decision=body.decision.value
            if hasattr(body.decision, "value")
            else str(body.decision),
        )
    if body.kind == InteractionKind.ESCALATION.value:
        use_assumption = bool(getattr(body, "use_assumption", False))
        answer = "" if use_assumption else str(getattr(body, "answer", "") or "")
        status = "assumed" if use_assumption else "resolved"
        return escalation_resolved(
            str(payload.get("run_id") or ""),
            str(payload.get("agent_id") or ""),
            escalation_id=interaction_id,
            status=status,
            answer=answer,
            arbitrated_by="user",
        )
    return None


async def _journal_pending_for_id(
    conversation_id: str, interaction_id: str, kind: str
) -> tuple[str | None, dict | None]:
    """Find a journal-fold pending match; returns (turn_id, payload) or (None, None)."""
    run = turn_runs.get(conversation_id)
    candidate_ids: list[str] = []
    if run is not None:
        mid = getattr(run.sink, "_message_id", None)
        if mid:
            candidate_ids.append(str(mid))

    async with async_session_factory() as db:
        if not candidate_ids:
            candidate_ids = await TurnJournalRepository(db).list_recent_turn_ids(
                conversation_id, limit=40
            )

        for turn_id in candidate_ids:
            entries = await TurnJournalRepository(db).load(turn_id)
            for pending in fold_pending_interactions(entries, message_id=turn_id):
                if pending.id == interaction_id and pending.kind == kind:
                    return turn_id, pending.payload
    return None, None


async def _resolve_stage_card(
    *,
    conversation_id: str,
    interaction_id: str,
    body: ResolveStageCardInteraction,
    user: AuthUser,
    session: AsyncSession,
    x_client_platform: str | None,
):
    """Leftover 推进卡 is not a debate entry — 410."""
    from agentcore.runtime.kickoff.retired import refuse_stage_card_resolve

    _ = (conversation_id, interaction_id, body, user, session, x_client_platform)
    refuse_stage_card_resolve()


@router.post("/{conversation_id}/interactions/{interaction_id}")
async def resolve_interaction(
    conversation_id: str,
    interaction_id: str,
    user: AuthUser,
    body: ResolveInteractionRequest = Body(discriminator="kind"),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    session: AsyncSession = Depends(get_db),
    x_client_platform: Annotated[str | None, Header(alias="X-Client-Platform")] = None,
):
    """Settle any paused hot-path interaction over the unified bridge (§8.2).

    ``stage_card``：leftover 推进卡 resolve 为 410；开辩须用户在对话里点名。
    其它 kind（approval / delegation / client_tool / escalation）：Settlement 预写 (D8)
    后 settle Future；journal 有 required、无 Future → 410。
    Cold-path ``ask_user`` / ``plan_review`` 不在此 endpoint。
    Leftover ``team_preview`` resume is 410 on ``POST …/resume``.
    """
    access = await _require_conversation_write(conversation_id, user.user_id, session)
    if access.is_member_turn and body.kind == "client_tool":
        raise AuthorizationError("不能代结桌主的本机审批")

    if isinstance(body, ResolveStageCardInteraction):
        return await _resolve_stage_card(
            conversation_id=conversation_id,
            interaction_id=interaction_id,
            body=body,
            user=user,
            session=session,
            x_client_platform=x_client_platform,
        )

    result = interaction_result_from_body(body)
    registry = default_interaction_registry()
    pending = registry.get(interaction_id)

    if (
        pending is not None
        and pending.conversation_id == conversation_id
        and pending.kind == body.kind
    ):
        if (
            pending.kind.value == "escalation"
            and (pending.payload or {}).get("awaiting") == "ceo"
        ):
            raise NotFoundError("该升级正由主管仲裁，请等待")

        event = _settlement_event_for_resolve(body, interaction_id, pending.payload)
        if event is not None:
            if already_settled_in_writer(event):
                return StatusResponse(status="already_processed")
            try:
                await prewrite_settlement(event)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "interaction.settlement_prewrite_failed",
                    interaction_id=interaction_id,
                    error=str(e),
                )
                raise HTTPException(
                    status_code=500,
                    detail={"code": "settlement_write_failed"},
                ) from e

        if not registry.resolve(interaction_id, result, conversation_id=conversation_id):
            return StatusResponse(status="already_processed")
        return StatusResponse()

    if body.kind in HOT_KINDS:
        turn_id, _payload = await _journal_pending_for_id(
            conversation_id, interaction_id, body.kind
        )
        if turn_id is not None:
            await emit_orphan_fact(
                interaction_id=interaction_id,
                kind=body.kind,
                turn_id=turn_id,
                conversation_id=conversation_id,
                prefer_direct=True,
            )
            raise HTTPException(
                status_code=410,
                detail={"code": "interaction_orphaned"},
            )

    raise NotFoundError("交互请求不存在或已处理")
