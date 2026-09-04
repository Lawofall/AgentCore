"""User-initiated worker redirect during an active delegate batch, plus the terminal-outcome
accept収口 (跑一半改方向 Step 4)."""

from typing import Any

from fastapi import APIRouter, Depends

from agentcore.api.dependencies import (
    AuthUser,
    get_agent_audit_repo,
    get_conversation_repo,
)
from agentcore.api.schemas import (
    AcceptRunOutcomeRequest,
    AcceptRunOutcomeResponse,
    SubmitRunRedirectRequest,
    SubmitRunRedirectResponse,
)
from agentcore.core.logging import get_logger
from agentcore.db.repositories import AgentAuditEventRepository, ConversationRepository
from agentcore.runtime.runs.intervene import accept_run_redirect

from ._helpers import _require_conversation_write

router = APIRouter(prefix="/conversations", tags=["conversations"])

logger = get_logger(__name__)


@router.post("/{conversation_id}/run-redirect", response_model=SubmitRunRedirectResponse)
async def submit_run_redirect(
    conversation_id: str,
    body: SubmitRunRedirectRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Queue a mid-flight redirect for one worker in the current delegate batch.

    The CEO is blocked inside ``delegate`` — this endpoint is the user直控 channel
    (WaveScheduler cancels the run, then hot ``continue_run`` or cold ``_redir``接手).

    The response says whether the engine actually took it (``accepted``): a run the
    live plan can't reach never enters the queue, and never reports success.
    """
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    ack = accept_run_redirect(
        execution_id=body.execution_id,
        run_id=body.run_id,
        feedback=body.feedback,
        conversation_id=conversation_id,
    )
    if ack.accepted:
        logger.info(
            "run_redirect.queued",
            conversation_id=conversation_id,
            execution_id=body.execution_id,
            run_id=body.run_id,
            queued=ack.queued,
        )
    else:
        logger.info(
            "run_redirect.unreachable",
            conversation_id=conversation_id,
            execution_id=body.execution_id,
            run_id=body.run_id,
            reason=ack.reason,
        )
    return SubmitRunRedirectResponse(
        queued=ack.queued,
        accepted=ack.accepted,
        reason=ack.reason,
        detail=ack.detail,
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/accept-outcome",
    response_model=AcceptRunOutcomeResponse,
)
async def accept_run_outcome(
    conversation_id: str,
    message_id: str,
    body: AcceptRunOutcomeRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    audit_repo: AgentAuditEventRepository = Depends(get_agent_audit_repo),
) -> AcceptRunOutcomeResponse:
    """Record the user's explicit accept of a run's terminal outcome (跑一半改方向 Step 4).

    Closes the loop for two audit-surfaced dead ends in a delegated turn: a
    ``deterministic_failure`` (retry徒劳) or a ``redirect_ignored`` (a「立即改此人」steer that could
    not apply mid-run). Replaces the old frontend-only ``clearExecution`` with a durable
    「用户主动接受此结果」row on the SAME append-only audit trail the run detail already reads — no
    new table, no new SSE event. Owner-scoped (对话归属校验防 IDOR) and idempotent per (turn, run):
    a second accept for the same run is a no-op (``recorded=false``).
    """
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    rows = await audit_repo.list_for_turn(conversation_id=conversation_id, turn_id=message_id)
    # Idempotent per (turn, run): a repeated accept (double-click / retry) must not append twice.
    if any(r.action == "run.outcome_accepted" and r.run_id == body.run_id for r in rows):
        return AcceptRunOutcomeResponse(recorded=False)
    # Sort after the turn's existing audit events; inherit their trace_id for a joined timeline.
    seq = max((r.seq for r in rows), default=-1) + 1
    trace_id = next((r.trace_id for r in rows if r.trace_id), None)
    detail: dict[str, Any] = {"decision": "accepted", "reason": body.reason}
    if body.note:
        detail["note"] = body.note[:200]
    await audit_repo.append(
        user_id=user.user_id,
        conversation_id=conversation_id,
        turn_id=message_id,
        trace_id=trace_id,
        seq=seq,
        category="state",
        action="run.outcome_accepted",
        actor_kind="system",
        outcome="ok",
        execution_id=body.execution_id,
        run_id=body.run_id,
        detail=detail,
    )
    logger.info(
        "run_outcome.accepted",
        conversation_id=conversation_id,
        message_id=message_id,
        run_id=body.run_id,
        reason=body.reason,
    )
    return AcceptRunOutcomeResponse(recorded=True)
