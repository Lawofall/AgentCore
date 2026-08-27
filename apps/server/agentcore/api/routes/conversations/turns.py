"""Turn re-execution and durable resume: regenerate / list paused / resume (结构化挂起)."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_db,
)
from agentcore.api.schemas import (
    PausedTurnSummary,
    PendingInteractionSummary,
    RegenerateMessageRequest,
    ResumeTurnRequest,
    TurnRecoveryResponse,
)
from agentcore.api.sse import (
    release_request_db_before_sse,
    sse_attach_response,
    sse_response,
    sse_resume_deferred_response,
    sse_resume_settled_response,
)
from agentcore.conversation.rate_limit import enforce_user_message_rate_limit
from agentcore.conversation.service import continue_chat, regenerate_chat, resume_chat
from agentcore.core.errors import NotFoundError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import ConversationRepository, TurnJournalRepository
from agentcore.fulfill.origin import current_origin_device
from agentcore.runtime.checkpoints import CheckpointResponse
from agentcore.runtime.events import (
    EventSink,
    publish_conversation_signal,
    resume_deferred,
    resume_settled,
)
from agentcore.runtime.interaction_orphan import orphan_live_turn_hot_pending
from agentcore.runtime.journal.pending_interactions import fold_pending_interactions
from agentcore.runtime.settlement import prewrite_cold_resume_settlement
from agentcore.runtime.suspension import (
    TurnSuspension,
    suspension_summary_fields,
)
from agentcore.runtime.suspension.consumed import classify_resume_miss
from agentcore.runtime.suspension.persistence import (
    claim_paused_turn,
    list_paused_turns,
    load_paused_turn,
    paused_turn_exists,
)
from agentcore.runtime.turn.runs import ResumeDeferredWaiter, turn_runs

from ._helpers import (
    _preflight_owned_chat_turn,
    _require_owned_conversation,
    emit_preflight_warnings,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _paused_summary(f: TurnSuspension) -> PausedTurnSummary:
    """Project one persisted suspension frame to its wire summary (shared by the
    paused-list + recovery endpoints). Kind-specific slots come from the shared
    :func:`~agentcore.runtime.suspension.suspension_summary_fields` codec registry
    (same source as the sidecar ``paused_summary``)."""
    return PausedTurnSummary(
        message_id=f.message_id,
        kind=f.kind,
        checkpoint_id=f.checkpoint_id,
        user_message=f.user_message,
        **suspension_summary_fields(f),
    )


async def _pending_interaction_summaries(
    conversation_id: str,
) -> list[PendingInteractionSummary]:
    """Journal-fold pending hot-path interactions for recovery (D5).

    Also merges still-open registry hot cards (approval / delegation /
    user escalation) so a journal empty-window / race cannot drop an
    answerable in-process Future from ``GET …/recovery``.
    """
    run = turn_runs.get(conversation_id)
    turn_ids: list[str] = []
    live_message_id = ""
    if run is not None:
        mid = getattr(run.sink, "_message_id", None) or getattr(run.sink, "message_id", None)
        if mid:
            live_message_id = str(mid)
            turn_ids.append(live_message_id)

    out: list[PendingInteractionSummary] = []
    seen: set[str] = set()
    async with async_session_factory() as db:
        if not turn_ids:
            # 会话时间序（max created_at），非 in-turn seq — 长回合不得挤掉旧卡。
            turn_ids = await TurnJournalRepository(db).list_recent_turn_ids(
                conversation_id, limit=40
            )

        for turn_id in turn_ids:
            entries = await TurnJournalRepository(db).load(turn_id)
            for pending in fold_pending_interactions(entries, message_id=turn_id):
                if pending.id in seen:
                    continue
                seen.add(pending.id)
                out.append(
                    PendingInteractionSummary(
                        kind=pending.kind,  # type: ignore[arg-type]
                        id=pending.id,
                        message_id=pending.message_id,
                        payload=pending.payload,
                    )
                )

    from agentcore.runtime.events.hot_interaction_reattach import registry_hot_pending

    for pending in registry_hot_pending(conversation_id, message_id=live_message_id):
        if pending.id in seen:
            continue
        seen.add(pending.id)
        out.append(
            PendingInteractionSummary(
                kind=pending.kind,  # type: ignore[arg-type]
                id=pending.id,
                message_id=pending.message_id,
                payload=pending.payload,
            )
        )
    return out


@router.post("/{conversation_id}/messages/{message_id}/regenerate")
async def regenerate_message(
    conversation_id: str,
    message_id: str,
    body: RegenerateMessageRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
):
    """Re-run a turn from an existing user message via SSE.

    Serves both "regenerate" (no body content — reuse the stored user text) and
    "edit & resend" (``content`` set — edit the user message first). The target
    ``message_id`` must be a user message; the superseded assistant reply and any
    later turns are dropped before re-running. Like ``send_message``, the pipeline
    runs as a detached task tracked in the ``TurnRunRegistry`` and the SSE only
    attaches (执行与请求解耦 C1 · slice 1a): a disconnect lets it finish + persist,
    an explicit 停止 goes through ``POST .../stop``. A re-run is a fresh turn, so it
    passes the same gates (rate limit → ownership → BYOK/quota billing gate) as
    ``send_message``.
    """
    await enforce_user_message_rate_limit(user.user_id)

    preflight = await _preflight_owned_chat_turn(conversation_id, user, session)
    await release_request_db_before_sse(session)

    # 触发点④：regenerate 前 orphan 热路 pending（活 turn 的 message_id，非目标用户消息）
    # 停止事实先成立（同 stop 端点）：orphan 途中被取消的 pending 会让宿主提前 unwind。
    turn_runs.mark_user_stop(conversation_id)
    await orphan_live_turn_hot_pending(conversation_id)
    await turn_runs.stop_and_drain(conversation_id)

    sink = EventSink()
    emit_preflight_warnings(sink, preflight)

    task = asyncio.create_task(
        regenerate_chat(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user.user_id,
            sink=sink,
            edited_content=body.content,
            edited_attachments=body.attachments,
            edited_agent_mentions=body.agent_mentions,
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
        )
    )
    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user.user_id
    )

    return sse_response(sink, detach_on_disconnect=True)


@router.get("/{conversation_id}/recovery", response_model=TurnRecoveryResponse)
async def get_conversation_recovery(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """One-shot recovery snapshot for a conversation reopen (recovery 统一, 对称 §8.2).

    Folds reopen probes into a single owner-gated read: live_running / paused /
    pending_interactions (journal fold of hot-path cards).
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    run = turn_runs.get(conversation_id)
    live_running = run is not None and not run.task.done()
    frames = await list_paused_turns(conversation_id)
    pending = await _pending_interaction_summaries(conversation_id)
    return TurnRecoveryResponse(
        live_running=live_running,
        paused=[_paused_summary(f) for f in frames],
        pending_interactions=pending,
    )


async def _resume_of_consumed_frame(conversation_id: str, message_id: str):
    """Answer a「继续」whose paused frame is already gone (幂等成功 or an honest 404).

    Consuming the frame is what a successful continuation DOES, so a second submit of
    the same cold card — double-click, the other device holding the same card, a retry
    after the SSE dropped — must not be told the turn never existed. The conclusion the
    frame's consumer stamped decides (see ``suspension.consumed``): a settled card ⇒
    200 + SSE that leads with ``resume_settled`` carrying THAT decision — the winner's,
    not this request's — and, when the continuation is running on this server, goes on
    to stream it (the idle-conversation twin of the deferred 幂等 join, which only ever
    covered the busy window).

    No conclusion ⇒ the frame died without anyone continuing it, and the two causes need
    different copy: an abandoned pause pruned by the 7-day sweep is retryable by
    re-asking, a regenerated turn is simply not there any more.
    """
    miss = await classify_resume_miss(conversation_id=conversation_id, message_id=message_id)
    if miss.kind == "expired":
        raise NotFoundError("这张卡已超过保留期被清理（挂起最多保留 7 天），请重新提问")
    if miss.kind == "regenerated":
        raise NotFoundError("该回合已被重新生成或删除，这张卡不再有效")

    # Attaching the live sink is a transport shortcut — this worker happens to hold the
    # continuation, so the caller can watch it on this same connection. It is NOT what
    # ``turn_status`` means: that is the turn's own state (the assistant row), and a
    # continuation running on another worker must not read as finished just because
    # this process's registry has never heard of it.
    run = turn_runs.get(conversation_id)
    live_sink = (
        run.sink
        if run is not None and not run.task.done() and run.sink.message_id == message_id
        else None
    )
    logger.info(
        "resume.already_settled",
        conversation_id=conversation_id,
        message_id=message_id,
        kind=miss.card_kind,
        decision=miss.decision,
        settled_by=miss.settled_by,
        turn_status=miss.turn_status,
        joined_live=live_sink is not None,
    )
    return sse_resume_settled_response(
        resume_settled(
            message_id=message_id,
            conversation_id=conversation_id,
            kind=miss.card_kind,  # type: ignore[arg-type]
            checkpoint_id=miss.checkpoint_id,
            decision=miss.decision,
            decided_at=miss.decided_at,
            turn_status=miss.turn_status,
        ),
        sink=live_sink,
    )


@router.post("/{conversation_id}/messages/{message_id}/resume")
async def resume_message(
    conversation_id: str,
    message_id: str,
    body: ResumeTurnRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    x_client_platform: Annotated[str | None, Header(alias="X-Client-Platform")] = None,
):
    """Continue a durably-paused turn via SSE (结构化挂起 2b ``POST .../resume``).

    The turn paused at a plan_review / ask_user checkpoint and lost its
    live stream (disconnect / restart); only its persisted frame survived.

    Settlement 预写 (D8)：① peek frame → ② busy 则 deferred（预写后 ``resume_deferred``，
    槽空再 claim）/ idle 则立即 claim → ③ ``*_resolved`` 落库成功 → ④ claim → ⑤ resume
    pipeline。settlement 写失败 ⇒ 5xx、不 claim、frame 保留可重试。settlement 落库后
    pipeline 取消/失败 ⇒ interrupted_after_decision（D1：不复活决策卡）。

    同 ``message_id`` 重复提交一律**幂等成功**，不再 404：忙槽窗口内 join 已 park 的
    waiter（跳过第二次预写，两条 SSE 共享同一次续跑）；帧已被那次续跑消费（peek 扑空 /
    claim 竞争落败）则走 :func:`_resume_of_consumed_frame` —— 200 + ``resume_settled``
    事实帧（带的是**赢家**的决策：claim 赢家把结论与删帧写在同一事务里），本机在跑就同
    连接续流。谁也没消费掉这张卡（没有结论行）才是真失效（404，文案区分「超保留期清理」
    与「回合已重新生成」）。

    ``body.selected`` carries the user's ask_user picks (ignored for plan_review).
    Gated like ``send_message`` (it spends tokens): rate limit → ownership → BYOK/quota
    — all BEFORE settlement/claim, so a refused turn keeps its resumable frame.
    """
    await enforce_user_message_rate_limit(user.user_id)

    preflight = await _preflight_owned_chat_turn(conversation_id, user, session)
    await release_request_db_before_sse(session)

    peeked = await load_paused_turn(message_id, conversation_id=conversation_id)
    if peeked is None:
        # Peek swallows read faults into ``None`` too — same recheck as the claim path
        # below: only a frame that is *really* gone licenses the「已被处理」judgement.
        # Without it, one transient DB hiccup reads as「已被重新生成」and the client
        # drops a card whose frame is still on disk and still resumable.
        if await paused_turn_exists(message_id, conversation_id=conversation_id):
            logger.warning(
                "resume.claim_unresolved",
                conversation_id=conversation_id,
                message_id=message_id,
                phase="peek",
            )
            raise HTTPException(status_code=500, detail={"code": "resume_claim_failed"})
        # Nothing prewritten yet on this request, so the journal's settlement (if any)
        # is somebody else's — a clean「已被处理」judgement.
        return await _resume_of_consumed_frame(conversation_id, message_id)

    # D9: paused 不占锁，会话可另开新回合。Resume 不得 cancel 在跑回合——busy 时收下
    # 决策（deferred），槽空后再 claim + 同连接续跑（否决 409 丢意图）。
    busy_reason = turn_runs.busy_reason_for_resume(conversation_id, message_id)
    # 同 message_id 重复提交 = 幂等 join（D1 后 settlement 已锁，改口不再收）：只把这条
    # SSE 挂到已 park 的 waiter 上，不重复预写。
    joining_deferred = (
        busy_reason is not None
        and turn_runs.resume_deferred_message_id(conversation_id) == message_id
    )

    decision = body.decision.value if hasattr(body.decision, "value") else str(body.decision)
    if not joining_deferred:
        try:
            await prewrite_cold_resume_settlement(
                peeked,
                decision=decision,
                note=body.note or "",
                selected=list(body.selected or []),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "resume.settlement_prewrite_failed",
                message_id=message_id,
                error=str(e),
            )
            raise HTTPException(
                status_code=500,
                detail={"code": "settlement_write_failed"},
            ) from e

    checkpoint_response = CheckpointResponse(
        decision=body.decision,
        note=body.note,
        selected=body.selected,
    )

    if busy_reason is not None:
        started: asyncio.Future = asyncio.get_running_loop().create_future()
        waiter = ResumeDeferredWaiter(
            conversation_id=conversation_id,
            message_id=message_id,
            busy_reason=busy_reason,
            checkpoint_response=checkpoint_response,
            user_id=user.user_id,
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
            x_client_platform=x_client_platform,
            origin_device_id=current_origin_device(),
            preflight_warnings=list(preflight.warnings),
            started=started,
        )
        if turn_runs.register_resume_deferred(waiter) is waiter:
            # 「放行已记下」is conversation state, not this connection's: the other端 has
            # the same cold card on screen and would otherwise keep offering a button that
            # is already spent (云对话多端同权 B2 · 验收 5). This SSE emits its own copy, so
            # the signal lane covers everyone else — a 幂等 join (same card re-submitted)
            # changes nothing for them and stays silent.
            publish_conversation_signal(
                conversation_id,
                resume_deferred(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    busy_reason=busy_reason,
                ),
            )
        return sse_resume_deferred_response(
            message_id=message_id,
            conversation_id=conversation_id,
            busy_reason=busy_reason,
            started=started,
        )

    suspension = await claim_paused_turn(
        message_id,
        conversation_id=conversation_id,
        decision=decision,
        settled_by=current_origin_device() or "",
    )
    if suspension is None:
        # The frame being really gone is the evidence that somebody else consumed it:
        # claim also answers ``None`` on a DB fault (it swallows), and reporting that as
        # 已处理 would fake a continuation nobody started.
        if await paused_turn_exists(message_id, conversation_id=conversation_id):
            logger.warning(
                "resume.claim_unresolved",
                conversation_id=conversation_id,
                message_id=message_id,
            )
            raise HTTPException(status_code=500, detail={"code": "resume_claim_failed"})
        return await _resume_of_consumed_frame(conversation_id, message_id)

    sink = EventSink(message_id=message_id)
    emit_preflight_warnings(sink, preflight)
    task = asyncio.create_task(
        resume_chat(
            suspension=suspension,
            response=checkpoint_response,
            sink=sink,
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
            x_client_platform=x_client_platform,
        )
    )
    # 执行与请求解耦 (C1 · slice 1a): track the resumed run so a disconnect lets it
    # finish + persist and 停止 routes through POST .../stop, same as a fresh send.
    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user.user_id
    )
    return sse_response(sink, detach_on_disconnect=True)


@router.post("/{conversation_id}/messages/{message_id}/continue")
async def continue_message(
    conversation_id: str,
    message_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
):
    """Continue a cloud CEO turn paused on exhausted rate limit (no checkpoint card)."""
    from agentcore.runtime.turn.ceo_continue import claim_ceo_continue_lock

    await enforce_user_message_rate_limit(user.user_id)
    preflight = await _preflight_owned_chat_turn(conversation_id, user, session)
    await release_request_db_before_sse(session)

    run = turn_runs.get(conversation_id)
    if run is not None and not run.task.done():
        mid = getattr(run.sink, "message_id", None) or getattr(
            run.sink, "_message_id", None
        )
        if str(mid or "") == message_id:
            return sse_attach_response(run.sink)

    claimed = await claim_ceo_continue_lock(
        message_id, conversation_id=conversation_id, user_id=user.user_id
    )
    if claimed is None:
        run = turn_runs.get(conversation_id)
        if run is not None and not run.task.done():
            mid = getattr(run.sink, "message_id", None) or getattr(
                run.sink, "_message_id", None
            )
            if str(mid or "") == message_id:
                return sse_attach_response(run.sink)
        raise HTTPException(status_code=404, detail={"code": "continue_not_available"})

    sink = EventSink(message_id=message_id)
    emit_preflight_warnings(sink, preflight)
    task = asyncio.create_task(
        continue_chat(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user.user_id,
            sink=sink,
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
        )
    )
    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user.user_id
    )
    return sse_response(sink, detach_on_disconnect=True)
