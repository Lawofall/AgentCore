"""Interaction resolution: settle a paused approval / escalation / delegation /
stage_card."""

from __future__ import annotations

import asyncio
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
from agentcore.api.sse import release_request_db_before_sse, sse_response
from agentcore.conversation.rate_limit import enforce_user_message_rate_limit
from agentcore.core.errors import NotFoundError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import ConversationRepository, TurnJournalRepository
from agentcore.runtime.events import (
    EventSink,
    approval_resolved,
    escalation_resolved,
)
from agentcore.runtime.interaction import HOT_KINDS, InteractionKind, default_interaction_registry
from agentcore.runtime.interaction_orphan import emit_orphan_fact
from agentcore.runtime.journal.pending_interactions import fold_pending_interactions
from agentcore.runtime.settlement import already_settled_in_writer, prewrite_settlement
from agentcore.runtime.turn.runs import turn_runs

from ._helpers import (
    _preflight_owned_chat_turn,
    _require_owned_conversation,
    emit_preflight_warnings,
)

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
    """Validate stage_card, then stream follow-up.

    ``start_debate``：``debate.started``（真正开跑）后才 resolved；
    仅启动失败保持 pending 可重试，开跑后中途失败不回 pending。
    ``research_first``：决议即留痕 resolved，再回灌 CEO。
    """
    from agentcore.conversation.stage_card_resolve import (
        load_stage_card_pending,
        orphan_sibling_stage_cards,
        prewrite_stage_card_resolved,
        run_stage_card_research_first,
        run_stage_card_start_debate,
        validate_start_debate_card,
    )

    await enforce_user_message_rate_limit(user.user_id)
    found = await load_stage_card_pending(conversation_id, interaction_id)
    if found is None:
        raise NotFoundError("推进卡不存在或已处理")
    host_turn_id, payload = found

    motion_override = body.motion_override
    note = body.note or ""
    card_for_debate = dict(payload)

    if body.decision == "start_debate":
        merged, err = validate_start_debate_card(payload, motion_override)
        if err:
            raise HTTPException(
                status_code=422,
                detail={"code": "motion_invalid", "message": err},
            )
        assert merged is not None
        card_for_debate = merged
        card_for_debate["stage_card_id"] = interaction_id

    if not await turn_runs.drain(conversation_id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "turn_in_progress",
                "message": "会话有正在进行的回合，先等它结束或显式停止",
            },
        )

    preflight = await _preflight_owned_chat_turn(conversation_id, user, session)
    await release_request_db_before_sse(session)

    sink = EventSink()
    emit_preflight_warnings(sink, preflight)

    if body.decision == "start_debate":
        # debate.started 才 resolved — 不在开辩前预写。
        coro = run_stage_card_start_debate(
            conversation_id=conversation_id,
            user_id=user.user_id,
            sink=sink,
            card=card_for_debate,
            note=note,
            host_turn_id=host_turn_id,
            stage_card_id=interaction_id,
            motion_override=motion_override,
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
            x_client_platform=x_client_platform,
        )
    else:
        try:
            await prewrite_stage_card_resolved(
                turn_id=host_turn_id,
                conversation_id=conversation_id,
                stage_card_id=interaction_id,
                decision=body.decision,
                note=note,
                motion_override=None,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "stage_card.settlement_prewrite_failed",
                interaction_id=interaction_id,
                error=str(e),
            )
            raise HTTPException(
                status_code=500,
                detail={"code": "settlement_write_failed"},
            ) from e
        await orphan_sibling_stage_cards(
            conversation_id,
            keep_id=interaction_id,
            sink=sink,
            reason="superseded",
        )
        coro = run_stage_card_research_first(
            conversation_id=conversation_id,
            user_id=user.user_id,
            sink=sink,
            card=dict(payload),
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
            x_client_platform=x_client_platform,
        )
    task = asyncio.create_task(coro)
    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user.user_id
    )
    return sse_response(sink, detach_on_disconnect=True)


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

    ``stage_card``：跨回合耐久卡 → 校验后起新回合 SSE（机制直起辩论或回灌调研）。
    其它 kind（approval / delegation / client_tool / escalation）：Settlement 预写 (D8)
    后 settle Future；journal 有 required、无 Future → 410。
    Cold-path ``ask_user`` / ``plan_review`` / ``team_preview`` 不在此 endpoint。
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)

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
