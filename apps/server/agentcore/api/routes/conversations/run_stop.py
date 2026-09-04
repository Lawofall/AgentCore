"""User-initiated per-worker stop during an active delegate batch (只停这项工作)."""

from fastapi import APIRouter, Depends

from agentcore.api.dependencies import AuthUser, get_conversation_repo
from agentcore.api.schemas import SubmitRunStopRequest, SubmitRunStopResponse
from agentcore.db.repositories import ConversationRepository
from agentcore.runtime.runs.intervene import accept_run_stop

from ._helpers import _require_conversation_write

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("/{conversation_id}/run-stop", response_model=SubmitRunStopResponse)
async def submit_run_stop(
    conversation_id: str,
    body: SubmitRunStopRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Queue a mid-flight stop for one or all workers in the current delegate batch.

    The CEO is blocked inside ``delegate`` — this endpoint is the user直控 channel
    (same posture as ``run-redirect``). Unlike redirect, stop never triggers hot
    revision or cold ``_redir``; WaveScheduler cancels / withdraws targets so drive
    converges and the CEO keeps the turn.

    Not fire-and-forget: the response says whether the engine actually took it
    (``accepted``) — 够不着的 run 上不入队，也不拿整条执行的排队计数冒充成功。
    """
    await _require_conversation_write(conversation_id, user.user_id, conv_repo._session)
    ack = accept_run_stop(
        execution_id=body.execution_id,
        run_id=body.run_id,
        conversation_id=conversation_id,
    )
    return SubmitRunStopResponse(
        queued=ack.queued,
        accepted=ack.accepted,
        reason=ack.reason,
        detail=ack.detail,
    )
