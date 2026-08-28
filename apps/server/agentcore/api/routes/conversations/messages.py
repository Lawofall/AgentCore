"""Conversation message routes: list / delete / send / stop / attach / local-turn.

Every route requires an authenticated user and is scoped to that user's own
conversations (IDOR-safe). Sending runs the turn as a detached task tracked in the
``TurnRunRegistry`` so a client disconnect no longer kills it (执行与请求解耦 C1).
"""

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import (
    AuthUser,
    get_conversation_repo,
    get_db,
    get_memory_update_repo,
    get_message_repo,
    get_turn_journal_repo,
)
from agentcore.api.schemas import (
    AbortLocalTurnRequest,
    AbortLocalTurnResponse,
    AgentMention,
    BeginLocalTurnRequest,
    BeginLocalTurnResponse,
    LocalTurnHeartbeatRequest,
    LocalTurnHeartbeatResponse,
    LocalTurnJournalRequest,
    LocalTurnStreamSegmentsRequest,
    MemoryUpdateView,
    MessageAttachment,
    MessageDetail,
    MessageListResponse,
    QueuedTurnItem,
    QueuedTurnListResponse,
    RecordTurnRequest,
    RecordTurnResponse,
    RunsPayload,
    SendMessageRequest,
    SetMessageFeedbackRequest,
    StatusResponse,
    StopTurnResponse,
)
from agentcore.api.schemas.messages import TurnCollabMetrics, parse_team_batch
from agentcore.api.sse import (
    parse_last_event_id,
    release_request_db_before_sse,
    sse_attach_response,
    sse_conversation_response,
    sse_queued_response,
    sse_response,
)
from agentcore.conversation.rate_limit import enforce_user_message_rate_limit
from agentcore.conversation.service import (
    abort_local_turn,
    append_local_turn_journal,
    begin_local_turn,
    heartbeat_local_turn,
    record_local_turn,
    stream_chat,
    upsert_local_turn_stream_segments,
)
from agentcore.conversation.store import get_conversation_store
from agentcore.conversation.store.overlay import (
    overlay_message_fields,
    overlay_runs_with_segments,
)
from agentcore.core.errors import NotFoundError
from agentcore.db.repositories import (
    ConversationRepository,
    MemoryUpdateRepository,
    MessageRepository,
    TurnJournalRepository,
)
from agentcore.fulfill.origin import current_origin_device
from agentcore.llm.resolve import resolve_user_llm_credentials
from agentcore.runtime.events import EventSink
from agentcore.runtime.journal import runs_from_entries_cached, slim_runs_payload
from agentcore.runtime.journal.entries import _PROCESS_PREFIX
from agentcore.runtime.journal.team_batch import team_batch_from_entries
from agentcore.runtime.turn.runs import turn_runs

from ._helpers import (
    _preflight_owned_chat_turn,
    _require_owned_conversation,
    emit_preflight_warnings,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _project_message_detail(
    m,
    *,
    journal_rows: list | None,
    segments: list,
    slim: bool,
) -> MessageDetail:
    """Fold + overlay one row into ``MessageDetail``. List sets ``slim`` to drop bulky events."""
    detail = MessageDetail.model_validate(m)
    usage = m.usage or {}
    runs = runs_from_entries_cached(m.id, journal_rows)
    runs = overlay_runs_with_segments(runs, segments, usage=usage)
    if slim and runs is not None:
        runs = slim_runs_payload(runs)
    if runs is not None:
        detail.runs = RunsPayload.model_validate(runs)
    # Empty-face redesign: lift usage.error → runs.error when journal omitted it,
    # so REST reload paints the same face as live (usage JSON is the durable home).
    usage_err = usage.get("error")
    if isinstance(usage_err, dict) and (
        usage_err.get("message") or usage_err.get("code")
    ):
        from agentcore.api.schemas.messages import RunError

        lifted = RunError(
            code=str(usage_err.get("code") or "LLM_ERROR").strip() or "LLM_ERROR",
            message=str(usage_err.get("message") or "").strip()
            or "本轮未能完成，请重试。",
        )
        if detail.runs is None:
            detail.runs = RunsPayload(error=lifted)
        elif detail.runs.error is None:
            detail.runs = detail.runs.model_copy(update={"error": lifted})
    # 回合轮次 (Tier 2 重载): rounds shares the row's usage column but has no own
    # attribute, so project it on read (usage itself is normalized by the schema
    # validator). Drives the bubble's「N 轮」caption alongside usage.
    rounds = usage.get("rounds")
    if rounds is not None:
        detail.rounds = rounds
    duration_ms = usage.get("duration_ms")
    if duration_ms is not None:
        detail.duration_ms = int(duration_ms)
    # Assistant-row lifecycle (usage.status) — overlay criterion for stream_state.
    status = usage.get("status")
    if status is not None:
        detail.status = status
    # Cold-path pause latch (usage.paused): write keeps status=running; lift so clients
    # hydrate as paused rather than streaming.
    if usage.get("paused"):
        detail.paused = True
    # System provenance (usage.origin) — e.g. execution_harvest synthetic user rows.
    origin = usage.get("origin")
    if isinstance(origin, str) and origin.strip():
        detail.origin = origin.strip()
    # 曾中断恢复 (usage.recovered): crash redrive finished this turn in place.
    if usage.get("recovered"):
        detail.recovered = True
    # In-flight overlay: fill content / reasoning from turn_stream_state when running.
    # When journal already has process_content, skip captain:content → messages.content
    # (deliverable_only: narration lives on the process lane, not the content column).
    if segments:
        from agentcore.runtime.events.attach_replay import journal_is_structured

        rows = journal_rows or []
        skip_cap_content = any(
            (e.get("kind") or "") == f"{_PROCESS_PREFIX}content" for e in rows
        ) or journal_is_structured(rows)
        content, reasoning = overlay_message_fields(
            content=detail.content,
            reasoning_content=detail.reasoning_content,
            segments=segments,
            usage=usage,
            skip_captain_content=skip_cap_content,
        )
        detail.content = content or ""
        detail.reasoning_content = reasoning
    collab = usage.get("collab")
    if collab is not None:
        detail.collab = TurnCollabMetrics.model_validate(collab)
    if m.role == "assistant":
        detail.team_batch = parse_team_batch(team_batch_from_entries(journal_rows or []))
    raw_outcome = usage.get("outcome")
    if raw_outcome in ("ok", "partial", "paused", "error"):
        detail.outcome = raw_outcome
    return detail


async def _persist_delivered_interjection_attachments(
    *,
    conversation_id: str,
    user_id: str,
    attachments: list[dict],
    sink: EventSink,
) -> list[dict]:
    """Persist mid-flight interjection attachments into the conversation workspace.

    Same ``persist_attachments`` path as a normal turn. Returns enriched dicts
    (``workspace_path`` set, inline ``text`` retained) for stash / later drain.
    """
    if not attachments:
        return []

    from agentcore.conversation.common import resolve_local_binding
    from agentcore.conversation.turn_backend import build_turn_backend
    from agentcore.db import async_session_factory
    from agentcore.workspace.attachments import persist_attachments

    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
        if not conv:
            return attachments
        folder_id = conv.folder_id
        local_binding = await resolve_local_binding(session, conv)

    backend = await build_turn_backend(
        user_id=user_id,
        conversation_id=conversation_id,
        folder_id=folder_id,
        sink=sink,
        local_binding=local_binding,
    )
    return await persist_attachments(backend, attachments)


@router.get("/{conversation_id}/messages", response_model=MessageListResponse)
async def list_messages(
    conversation_id: str,
    user: AuthUser,
    limit: int = Query(100, ge=1, le=200),
    before: datetime | None = Query(None),
    after: datetime | None = Query(None),
    around: str | None = Query(None),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    repo: MessageRepository = Depends(get_message_repo),
    journal_repo: TurnJournalRepository = Depends(get_turn_journal_repo),
    mem_update_repo: MemoryUpdateRepository = Depends(get_memory_update_repo),
):
    """A window of a conversation's messages (cursor-windowed, chronological).

    Four mutually-exclusive modes (checked in this order):

    - ``around={message_id}``: a window centered on a message — the search-hit jump
      (load-around B). 404 if the message isn't in this conversation.
    - ``before={iso}``: the page strictly older than the cursor (scroll up).
    - ``after={iso}``: the page strictly newer than the cursor (scroll down).
    - none: the latest window (conversation open).

    Assistant ``runs.events`` on this list may be slimmed (``events_complete=false``);
    fetch ``GET …/messages/{message_id}`` for the full journal. ``has_more_before`` /
    ``has_more_after`` drive infinite scroll; a one-sided query computes only the flag
    for the direction it moves in (an ``around`` window computes both). ``total`` is
    the conversation's full message count.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    total = await repo.count_by_conversation(conversation_id)

    has_more_before = False
    has_more_after = False
    if around is not None:
        window = await repo.window_around(
            conversation_id, message_id=around, before=limit, after=limit
        )
        if window is None:
            raise NotFoundError("消息不存在")
        messages, has_more_before, has_more_after = window
    elif before is not None:
        messages, has_more_before = await repo.list_before(
            conversation_id, before=before, limit=limit
        )
    elif after is not None:
        messages, has_more_after = await repo.list_after(conversation_id, after=after, limit=limit)
    else:
        messages, has_more_before = await repo.list_latest(conversation_id, limit=limit)

    # Project each assistant message's replay payload (runs) from the唯一事实源
    # turn_journal (§8.3) — it is no longer stored on the message row. One batched
    # query over the page's message ids (no N+1); turns with no facts stay runs=None.
    # The per-row fold is memoized by (message_id, journal version) so re-opening /
    # reloading a window doesn't re-project unchanged turns (项目审计-成本性能专项 PERF-003).
    journal_map = await journal_repo.load_map([m.id for m in messages])
    # Batch-load in-flight stream segments for overlay (P1 · §3.3).
    stream_map = await get_conversation_store().list_stream_segments_map(
        turn_ids=[m.id for m in messages]
    )
    details: list[MessageDetail] = [
        _project_message_detail(
            m,
            journal_rows=journal_map.get(m.id),
            segments=stream_map.get(m.id) or [],
            slim=True,
        )
        for m in messages
    ]

    # 记忆更新对话内可见 (§1.6): the conversation-tail「记忆已更新」cards. They sit AFTER
    # the last message, so they belong only to the LATEST window (no before/after/around) —
    # scroll-up / search-hit pages skip the read entirely.
    memory_updates: list[MemoryUpdateView] = []
    if around is None and before is None and after is None:
        memory_updates = [
            MemoryUpdateView.model_validate(row)
            for row in await mem_update_repo.list_for_conversation(conversation_id)
        ]

    return MessageListResponse(
        data=details,
        total=total,
        has_more_before=has_more_before,
        has_more_after=has_more_after,
        memory_updates=memory_updates,
    )


@router.get("/{conversation_id}/messages/{message_id}", response_model=MessageDetail)
async def get_message(
    conversation_id: str,
    message_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    repo: MessageRepository = Depends(get_message_repo),
    journal_repo: TurnJournalRepository = Depends(get_turn_journal_repo),
):
    """One message with the full turn replay payload (冷 GET 降载).

    The conversation list may slim ``runs.events``; this owner-scoped GET returns the
    same ``MessageDetail`` projection **without** dropping display events, so the
    team graph / turn-detail page can replay exactly. 404 when the conversation is
    not owned or the message is not in it (same IDOR posture as delete).
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    message = await repo.get_by_id(message_id, conversation_id=conversation_id)
    if message is None:
        raise NotFoundError("消息不存在")
    journal_map = await journal_repo.load_map([message.id])
    stream_map = await get_conversation_store().list_stream_segments_map(
        turn_ids=[message.id]
    )
    return _project_message_detail(
        message,
        journal_rows=journal_map.get(message.id),
        segments=stream_map.get(message.id) or [],
        slim=False,
    )


@router.delete("/{conversation_id}/messages/{message_id}", response_model=StatusResponse)
async def delete_message(
    conversation_id: str,
    message_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    repo: MessageRepository = Depends(get_message_repo),
):
    """Delete a single message (单条消息删除).

    Owner-scoped: proving ownership of the conversation first, then deleting only
    within it, means a guessed ``message_id`` from another user's chat can't be
    removed (404 on a foreign/absent conversation; no-op-then-404 on an absent
    message). Append-only ``cost_events`` are intentionally preserved — deleting a
    message never rewrites real spend (不变量 #1).
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    deleted = await repo.delete_by_id(message_id, conversation_id=conversation_id)
    if not deleted:
        raise NotFoundError("消息不存在")
    return StatusResponse()


@router.patch("/{conversation_id}/messages/{message_id}/feedback", response_model=StatusResponse)
async def set_message_feedback(
    conversation_id: str,
    message_id: str,
    body: SetMessageFeedbackRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    repo: MessageRepository = Depends(get_message_repo),
):
    """Set / clear the user's 点赞/点踩 on an assistant reply (回复反馈).

    Owner-scoped like delete (prove conversation ownership first, then update only within
    it, so a guessed cross-user ``message_id`` can't be rated — IDOR-safe). ``feedback`` is
    ``"up"`` / ``"down"`` to rate, or ``null`` to clear the rating (toggling the same side
    off). 404 when the message isn't in this conversation.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    updated = await repo.set_feedback(
        message_id, conversation_id=conversation_id, feedback=body.feedback
    )
    if not updated:
        raise NotFoundError("消息不存在")
    return StatusResponse()


@router.post(
    "/{conversation_id}/messages",
    # response_class 压掉 FastAPI 默认 200 application/json 空 schema——本端点恒返回
    # SSE（发送即有流），200 契约只声明 text/event-stream。
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "SSE stream (发送即有流): idle turn / FIFO queue wait+续流 / "
                "coordination interjection ack"
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        409: {"description": "Hot-path pending interaction blocks new messages"},
    },
)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    x_client_platform: Annotated[str | None, Header(alias="X-Client-Platform")] = None,
):
    """Send a user message and get a streaming AI response via SSE.

    执行与请求解耦 (C1 · slice 1a): the pipeline runs as a *detached* task tracked in
    the ``TurnRunRegistry`` (keyed by conversation), and the SSE stream only attaches
    to it (``detach_on_disconnect=True``). A client disconnect therefore no longer
    kills the turn (案例 1: 7-min 断连即丢交付) — it finishes + persists in the
    background; an explicit 停止 routes through ``POST .../stop`` instead.

    发送即有流 (D9): this endpoint **always** returns SSE (never 202 JSON).

    ``delivery`` 必填（``steer`` | ``queue``；缺 → 422）：

    - **空闲** → 开跑并流式推送整个回合（客户端仍带 ``delivery=steer``）。
    - **协调活跃 + steer** → ``user_interjection``（短流确认）；CEO 可智能升格排队。
    - **协调活跃 + queue** → **强制** FIFO（绕过插话），立即 ``turn_queued``。
    - **经典 in-flight + queue** → FIFO ``turn_queued``，drain 后同连接续流。
    - **经典 in-flight + steer** → 挂到 live turn 进程内 pending（DURABLE
      ``user_interjection(received)``；步顶注入后再发 ``injected``）；
      无 accepting 窗口 / 回合已收口 → 回落 FIFO（``turn_queued.degraded_from=steer``）。
    - **热路 pending**（approval / escalation / …）仍 409。

    Gated before the stream starts (成本配额与计费.md §一) so a refused turn gets a
    clean error instead of a half-opened SSE: per-user rate limit first (sheds a
    flooding account before any resource DB work), then ownership, then the
    BYOK/quota billing gate (BYOK mode requires the user's own key; platform mode
    enforces quota). The resolved BYOK credentials thread through the whole turn.

    Request-scoped DB session for preflight only — explicitly closed before the SSE
    stream opens so a long-lived stream never holds a pooled connection (fixes
    GC-termination warnings on abrupt teardown).
    """
    await enforce_user_message_rate_limit(user.user_id)

    # 提问确认交互统一 D9：热路挂起中同对话发新消息 → 409（regenerate/retry 不拦）
    from agentcore.runtime.turn.delivery import (
        DeliveryBlockedError,
        deliver_in_flight,
        raise_if_delivery_blocked,
    )

    try:
        raise_if_delivery_blocked(conversation_id)
    except DeliveryBlockedError as blocked:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={
                "code": blocked.code,
                "pending_kinds": blocked.pending_kinds,
            },
        ) from blocked

    # 批 B 失效修订：发消息不再立即 orphan；回合收尾若未调 debate / 未起 MLR 才落事实。

    needs_tools = body.requires_tools
    preflight = await _preflight_owned_chat_turn(
        conversation_id, user, session, needs_tools=needs_tools
    )
    await release_request_db_before_sse(session)

    # In-flight turn → process-local delivery kernel (sidecar RPC shares this).
    # Idle open-turn stays below (HTTP ``stream_chat`` only).
    existing = turn_runs.get(conversation_id)
    if existing is not None and not existing.task.done():
        from agentcore.runtime.events import user_interjection

        delivered = await deliver_in_flight(
            conversation_id=conversation_id,
            content=body.content,
            delivery=body.delivery,
            user_id=user.user_id,
            attachments=[a.model_dump() for a in body.attachments],
            agent_mentions=[m.model_dump() for m in body.agent_mentions],
            requires_tools=needs_tools,
            x_client_platform=x_client_platform,
            origin_device_id=current_origin_device(),
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
            persist_attachments_fn=_persist_delivered_interjection_attachments,
            wait_for_start=True,
        )
        if delivered is not None and delivered.status == "received":
            # Sender's POST: short SSE confirm — history/SSE only, do NOT re-journal
            # (live sink already emitted once; same interjection_id must not double-write).
            confirm = EventSink()
            confirm.emit_sse_only(
                user_interjection(
                    interjection_id=delivered.interjection_id or "",
                    execution_id=delivered.execution_id or "",
                    content=body.content,
                    status="received",
                    attachments=delivered.attachments_meta or None,
                    agent_mentions=delivered.agent_mentions or None,
                )
            )
            confirm.close(reason=delivered.confirm_reason)
            return sse_response(confirm, detach_on_disconnect=True)
        if delivered is not None:
            assert delivered.started is not None
            return sse_queued_response(
                conversation_id=conversation_id,
                queue_id=delivered.queue_id or "",
                position=delivered.position or 1,
                queue_depth=delivered.queue_depth or 1,
                started=delivered.started,
                degraded_from=delivered.degraded_from,
            )

    sink = EventSink()
    emit_preflight_warnings(sink, preflight)

    task = asyncio.create_task(
        stream_chat(
            conversation_id=conversation_id,
            user_message=body.content,
            user_id=user.user_id,
            sink=sink,
            attachments=[a.model_dump() for a in body.attachments],
            agent_mentions=[m.model_dump() for m in body.agent_mentions],
            llm_credentials=preflight.credentials,
            llm_supports_tools=preflight.supports_tools,
            x_client_platform=x_client_platform,
        )
    )
    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user.user_id
    )

    return sse_response(sink, detach_on_disconnect=True)


@router.get(
    "/{conversation_id}/queued-turns",
    response_model=QueuedTurnListResponse,
)
async def list_queued_turns(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """List the conversation's process-local FIFO queued turns (条权威仍是 GET / 快照).

    Owner-gated like send / cancel. Returns the current in-memory snapshot in FIFO
    order (``position`` 1-based). EPHEMERAL ``turn_queued`` / ``turn_queue_cancelled``
    remain change signals only; ``turn_queue_started`` is the timeline entrance
    frame (content on the frame). Restart empties the queue (no durable queue).
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    from agentcore.runtime.turn.delivery import list_queued_items

    pending = list_queued_items(conversation_id)
    return QueuedTurnListResponse(
        items=[
            QueuedTurnItem(
                queue_id=item.queue_id,
                content=item.content,
                position=idx,
                interjection_id=item.interjection_id,
                attachments=[MessageAttachment.model_validate(a) for a in item.attachments],
                agent_mentions=[AgentMention.model_validate(m) for m in item.agent_mentions],
            )
            for idx, item in enumerate(pending, start=1)
        ]
    )


@router.post(
    "/{conversation_id}/queued-turns/{queue_id}/cancel",
    response_model=StatusResponse,
)
async def cancel_queued_turn(
    conversation_id: str,
    queue_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Cancel one FIFO queued turn before drain (同对话再发 · 按项取消).

    Owner-gated like send. Removes the entry by ``queue_id``; already started / unknown
    → 404. Stop does **not** clear the queue — cancel is the only per-item withdraw.

    On success emits EPHEMERAL ``turn_queue_cancelled`` on the live turn sink AND signals
    it to every端 following the conversation (云对话多端同权 B2 · 验收 5). The signal lane
    is what makes the withdraw visible when there is no live run at all — the queue
    outlives its host turn (drain not yet armed / deferred owns the next slot), and until
    now cancelling in that window told the other端 nothing.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    from agentcore.runtime.turn.delivery import cancel_queued_item

    item = cancel_queued_item(conversation_id, queue_id)
    if item is None:
        raise NotFoundError("排队项不存在或已开始")
    return StatusResponse()


@router.post("/{conversation_id}/stop", response_model=StopTurnResponse)
async def stop_message(
    conversation_id: str,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Explicitly cancel the conversation's in-flight turn (hard ``user_stop``).

    Cascade-cancels the detached run + live coordination workers and closes with
    ``cancelled``. Disconnect still ≠ cancel. Owner-gated; idempotent when nothing
    is running. Does **not** clear FIFO queued turns — cancel those via
    ``POST …/queued-turns/{queue_id}/cancel``.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    from agentcore.runtime.events.client_tool_reattach import cancel_pending_client_tools
    from agentcore.runtime.interaction_orphan import orphan_live_turn_hot_pending

    # 用户主动停止先成立，再发任何取消：the orphan pass below awaits the DB between
    # pending cards and cancels their Futures, so the turn can already unwind inside
    # it — with the flag unset that unwind orphans the lease (气泡「中断」+ sweeper
    # 重驱) instead of closing as 已停止.
    turn_runs.mark_user_stop(conversation_id)
    await orphan_live_turn_hot_pending(conversation_id)
    # Before the task cancel below: the awaiter's ``finally`` discards these
    # entries, and an already-dispatched op (host_shell…) would otherwise run to
    # completion on the user's machine with nobody left to receive it.
    cancel_pending_client_tools(conversation_id)

    stopped = turn_runs.stop(conversation_id)
    if not stopped:
        from agentcore.runtime.coordination.session import (
            cancel_coordination_on_user_stop,
        )

        stopped = cancel_coordination_on_user_stop(conversation_id)
    return StopTurnResponse(stopped=stopped)


@router.get("/{conversation_id}/stream")
async def attach_stream(
    conversation_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    follow: bool = Query(
        False,
        description=(
            "对话级长订阅（云对话多端同权 B2）：空闲不返回 204，保持连接送心跳，"
            "此后每个新回合（发送 / 队列 drain / 冷 resume 唤醒 / stage_card）自动续播。"
            "缺省 false = 回合级 attach（旧客户端语义：无 live run → 204，回合收口即断流）。"
        ),
    ),
):
    """Re-attach to the conversation's in-flight turn and 续看 it live (C1 · slice 1b).

    Since a disconnect no longer cancels a turn (slice 1a — it runs detached + persists
    in the background), a client that dropped (network blip) or reopened the app can
    rejoin the live run here: the SSE replays the transcript so far (coalesced — one
    content / reasoning block, the team graph, finished tool calls) then tails new
    events, all in the SAME event shape as the original stream, so the client folds it
    through one dispatch path.

    With ``Last-Event-ID`` (P3): journal-backed durable replay + stream_state synthetic
    deltas, then live tail. Without the header (same-process fast path): the sink's own
    in-memory history.

    A non-empty catch-up段 opens with a ``message_start``, and that frame
    states which kind of段 follows (nothing to replay = no head at all: a
    reset order with no body behind it would clear local state nothing
    brings back):

    - ``full_replay: true`` — RESET the local streaming state held for that
      ``message_id`` (content / reasoning / process timeline); the段 is the
      turn's whole story. Always the case without the header, and the
      fallback whenever the cursor cannot be trusted (no cursor / it belongs
      to another turn / turn already settled / it names no fact this turn
      ever stamped).
    - no ``full_replay`` — an INCREMENTAL段: keep what you hold for this
      turn and fold the段 onto it. Only the facts after ``Last-Event-ID``
      are shipped, so structural pairs may look「不完整」(a ``tool_use_end``
      whose start was pre-cursor) — that is correct, the client has the前文.

    Clients must act on the flag instead of comparing the id against the bubble on
    screen: guessing wrong folds the body twice. A live first frame (and any plain
    same-id re-stamp) omits it and keeps meaning「同回合重开」.

    **Cursor contract**: ``Last-Event-ID`` is the last SSE ``id:`` this client folded,
    echoed back verbatim. That id reads ``<turn_id>:<seq>`` — journal seq is numbered per
    turn from 0, so the turn it belongs to travels with it and a cursor kept per
    CONVERSATION (both clients do) cannot be mistaken for one from the turn now being
    replayed. The增量段 is offered only for a cursor naming this turn; one naming another
    turn, and a bare ``<seq>`` from a client that predates the format, both get the full
    journal replay. A client that discards its local turn state (clear-then-fold rejoin,
    fresh bubble) should still send ``0`` / omit the header rather than an id it no longer
    has the prefix for. Text deltas carry no ``id:``, so the cursor lags into the middle
    of a text block; the段 therefore marks whole-block frames with ``replace`` (see the
    ``content_delta`` / ``run_output_delta`` payloads).

    ``follow=true`` (对话级订阅) makes the subscription track the **conversation**: an
    idle conversation holds the connection (heartbeats) instead of 204ing, and每个新回合
    is replayed + tailed on the same stream. That is what lets a second device parked on
    an idle conversation see the turn another device just started — with回合级 attach it
    took a 204 and then had no way to learn anything ever happened.

    Without it (default), returns ``204 No Content`` when no run is live for the
    conversation (already finished / never started / suspended at a checkpoint) — the
    client then falls back to the persisted transcript (reload) / durable resume, and
    the stream ends when that one turn does. Kept as the default because a客户端 that
    also opens the POST turn stream would otherwise fold the next turn twice; opting in
    is the客户端's statement that this is its ONE connection for the conversation.

    Either way a pure observer: dropping this stream only unsubscribes it (never
    cancels, and never touches the other端); an explicit 停止 still goes through
    ``POST .../stop``. Owner-gated.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    cursor = parse_last_event_id(last_event_id)
    if follow:
        # Ownership check is done; the stream only observes in-memory sinks.
        await release_request_db_before_sse(session)
        return sse_conversation_response(conversation_id, cursor=cursor)
    run = turn_runs.get(conversation_id)
    if run is None or run.task.done():
        return Response(status_code=204)
    await release_request_db_before_sse(session)
    return sse_attach_response(run.sink, cursor=cursor)


@router.post("/{conversation_id}/local-turns", response_model=RecordTurnResponse)
async def record_local_turn_endpoint(
    conversation_id: str,
    body: RecordTurnRequest,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Persist a turn that ran on the user's machine via the sidecar (双模式工作区 §一.1).

    The local engine produced the reply on the user's box (no server SSE turn ran),
    so the desktop reports the finished turn here to land it in durable history.
    Owner-scoped (404 for a non-owner). Spend is NOT recorded here — a sidecar turn's
    LLM calls are metered authoritatively at the cloud inference proxy (``/v1/inference``,
    Slice 4a); this endpoint persists content only.

    Unlike ``send_message`` there is NO pre-turn billing gate — the turn already
    happened on the user's machine; this only RECORDS its content. The write-back is
    idempotent so the desktop can safely retry a flaky POST: messages dedupe on the
    client-minted ``user_message_id``, so a retry after a committed-but-lost response
    never duplicates the turn. The title is generated best-effort on the user's resolved
    BYOK key (None → platform fallback).
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    # Best-effort credentials for the title pass — unlike send_message's preflight we
    # never REFUSE here (the turn is already done; recording must not be blockable).
    credentials = await resolve_user_llm_credentials(session, user.user_id)
    result = await record_local_turn(
        conversation_id=conversation_id,
        user_id=user.user_id,
        user_message=body.user_message,
        assistant_content=body.content,
        assistant_reasoning=body.reasoning_content,
        citations=[c.model_dump() for c in body.citations] or None,
        evidence_ledger=[e.model_dump() for e in body.evidence_ledger] or None,
        runs=body.runs.model_dump() if body.runs else None,
        journal=body.journal,
        tool_failures=[f.model_dump() for f in body.tool_failures],
        user_message_id=body.user_message_id,
        message_id=body.message_id,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        reasoning_tokens=body.reasoning_tokens,
        cache_hit_tokens=body.cache_hit_tokens,
        cache_miss_tokens=body.cache_miss_tokens,
        rounds=body.rounds,
        trace_id=body.trace_id,
        finish_reason=body.finish_reason,
        llm_credentials=credentials,
        origin=body.origin,
        execution_id=body.execution_id,
        harvest_kind=body.harvest_kind,
        agent_mentions=[m.model_dump() for m in body.agent_mentions] or None,
    )
    return RecordTurnResponse(**result)


@router.post("/{conversation_id}/local-turns/begin", response_model=BeginLocalTurnResponse)
async def begin_local_turn_endpoint(
    conversation_id: str,
    body: BeginLocalTurnRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Pin the user row + a running assistant placeholder (投影同寿命).

    Same request is idempotent on ``user_message_id`` and assistant ``message_id``.
    Does not start a cloud SSE turn, mint a title, compact, or go through
    ``POST /messages``. Owner Bearer, same as ``POST …/local-turns``.
    """
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    result = await begin_local_turn(
        conversation_id=conversation_id,
        user_id=user.user_id,
        user_message=body.user_message,
        user_message_id=body.user_message_id,
        message_id=body.message_id,
        trace_id=body.trace_id,
        agent_mentions=(
            None
            if body.agent_mentions is None
            else [m.model_dump() for m in body.agent_mentions]
        ),
        regenerate=body.regenerate,
        attachments=(
            [a.model_dump(mode="json") for a in body.attachments]
            if body.attachments is not None
            else None
        ),
    )
    return BeginLocalTurnResponse(**result)


@router.post(
    "/{conversation_id}/local-turns/heartbeat",
    response_model=LocalTurnHeartbeatResponse,
)
async def heartbeat_local_turn_endpoint(
    conversation_id: str,
    body: LocalTurnHeartbeatRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Refresh the sidecar occupy lease. Owner Bearer; never steals a cloud lease."""
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    ok = await heartbeat_local_turn(
        conversation_id=conversation_id,
        message_id=body.message_id,
    )
    return LocalTurnHeartbeatResponse(ok=ok)


@router.post("/{conversation_id}/local-turns/journal", response_model=StatusResponse)
async def append_local_turn_journal_endpoint(
    conversation_id: str,
    body: LocalTurnJournalRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Append journal facts with required ``seq`` (replace=False). Failures are 4xx/5xx."""
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    await append_local_turn_journal(
        conversation_id=conversation_id,
        user_id=user.user_id,
        message_id=body.message_id,
        trace_id=body.trace_id,
        entries=[(fact.seq, fact.entry) for fact in body.entries],
    )
    return StatusResponse()


@router.post(
    "/{conversation_id}/local-turns/stream-segments",
    response_model=StatusResponse,
)
async def upsert_local_turn_stream_segments_endpoint(
    conversation_id: str,
    body: LocalTurnStreamSegmentsRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """UPSERT in-flight stream snapshots. Does not rewrite ``messages.content``."""
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    await upsert_local_turn_stream_segments(
        conversation_id=conversation_id,
        user_id=user.user_id,
        message_id=body.message_id,
        segments=[(s.channel, s.text, s.generation) for s in body.segments],
    )
    return StatusResponse()


@router.post("/{conversation_id}/local-turns/abort", response_model=AbortLocalTurnResponse)
async def abort_local_turn_endpoint(
    conversation_id: str,
    body: AbortLocalTurnRequest,
    user: AuthUser,
    conv_repo: ConversationRepository = Depends(get_conversation_repo),
):
    """Delete a still-running assistant + paired user (startup failure). Settled = no-op."""
    await _require_owned_conversation(conversation_id, user.user_id, conv_repo)
    result = await abort_local_turn(
        conversation_id=conversation_id,
        user_id=user.user_id,
        user_message_id=body.user_message_id,
        message_id=body.message_id,
    )
    return AbortLocalTurnResponse(**result)
