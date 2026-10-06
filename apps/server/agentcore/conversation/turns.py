"""Cloud SSE turn entry points: send, regenerate, durable resume."""

import asyncio
import contextlib
import time

from agentcore.config import settings
from agentcore.conversation.common import (
    preview,
    resolve_folder_local_binding,
    resolve_local_binding,
    resolve_permission_axes,
    resolve_profile_set,
    resolve_turn_file_workspace,
    schedule_title_generation,
)
from agentcore.conversation.compaction import compact_before_turn
from agentcore.conversation.history import drop_trailing_user_turn, load_chat_context
from agentcore.conversation.midflight_persist import load_or_create_turn_user_message
from agentcore.conversation.turn_backend import build_turn_backend
from agentcore.conversation.turn_persistence import (
    close_user_stop_turn,
    persist_turn_result,
)
from agentcore.conversation.turn_runner import (
    run_and_persist,
    session_callbacks,
    suspension_callbacks,
)
from agentcore.conversation.turn_stats import turn_worker_stats
from agentcore.conversation.zero_output_rollback import (
    maybe_delete_zero_output_send,
    result_from_unstarted_close,
)
from agentcore.core.error_codes import ErrorCode
from agentcore.core.inline_body import plain_text
from agentcore.core.log_context import log_context, new_trace_id
from agentcore.core.logging import get_logger
from agentcore.core.mentions import to_stored_agent_mentions
from agentcore.core.types import new_id
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import (
    ConversationRepository,
    MessageRepository,
    TableRepository,
)
from agentcore.llm.resolve import LLMCredentials, resolve_turn_model
from agentcore.runtime.checkpoints import CheckpointResponse
from agentcore.runtime.error_fields import error_fields_for
from agentcore.runtime.events import EventSink, FinishReason, error_event, message_end, turn_saved
from agentcore.runtime.leases import (
    acquire_turn_lease,
    lease_heartbeat_loop,
    orphan_turn_lease,
    release_turn_lease,
)
from agentcore.runtime.pipeline import resume_chat_pipeline
from agentcore.runtime.pipeline.continue_ceo import continue_ceo_pipeline
from agentcore.runtime.suspension import TurnSuspension
from agentcore.runtime.suspension.persistence import restore_paused_turn
from agentcore.runtime.turn.ceo_continue import (
    release_ceo_continue_claim,
    restore_ceo_continue_lock,
)
from agentcore.runtime.turn.runs import turn_runs
from agentcore.workspace.attachments import persist_attachments

logger = get_logger(__name__)


async def _arm_tool_switches(session: object, conv: object):
    """Publish this conversation's assembly onto the turn."""
    from agentcore.assembly.bind import arm_conversation_assembly

    return await arm_conversation_assembly(session, conv)  # type: ignore[arg-type]


def _disarm_tool_switches(token: object) -> None:
    from agentcore.assembly.bind import disarm_conversation_assembly

    disarm_conversation_assembly(token)


async def stream_chat(
    *,
    conversation_id: str,
    user_message: str,
    user_id: str,
    sink: EventSink,
    attachments: list[dict] | None = None,
    llm_credentials: LLMCredentials | None = None,
    llm_supports_tools: bool | None = None,
    x_client_platform: str | None = None,
    agent_mentions: list[dict] | None = None,
    table_selection: list[str] | None = None,
    existing_user_message_id: str | None = None,
) -> None:
    """Main entry: persist user message, run pipeline, persist assistant reply.

    ``existing_user_message_id``: mid-flight persist already wrote the user row
    (queue / steer). Drain reuses it instead of inserting a second bubble.
    """
    backend = None
    switch_token = None
    try:
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if not conv:
                sink.emit(error_event(ErrorCode.NOT_FOUND, "Conversation not found"))
                sink.emit(message_end(FinishReason.ERROR))
                return
            switch_token = await _arm_tool_switches(session, conv)
            folder_id = conv.folder_id
            auto_desk_raw = getattr(conv, "auto_desk_folder_id", None)
            ws_folder_id, _auto_desk_folder_id = resolve_turn_file_workspace(
                birth_folder_id=folder_id,
                auto_desk_folder_id=auto_desk_raw if isinstance(auto_desk_raw, str) else None,
            )
            if ws_folder_id and not folder_id:
                local_binding = await resolve_folder_local_binding(session, ws_folder_id)
            else:
                local_binding = await resolve_local_binding(session, conv)
            profile_set = await resolve_profile_set(session, conv, user_id)
            permission_axes = await resolve_permission_axes(session, conversation_id)

            # If this conversation is a table's dedicated thread, hand its table id
            # to the pipeline so ``<表格>`` facts can render.
            table = await TableRepository(session).get_by_conversation_id(
                conversation_id, user_id=user_id
            )
            table_id = table.id if table else None

        backend = await build_turn_backend(
            user_id=user_id,
            conversation_id=conversation_id,
            folder_id=ws_folder_id,
            sink=sink,
            local_binding=local_binding,
        )

        # A′: no whole-turn workspace_lock — write/snapshot sinks hold the key.
        # Compact stays outside the lock (conversation DB, not workspace disk).
        # 不得静默等锁：pause 不握 folder 锁；写路径短等经 workspace_lock_wait SSE。
        resident_attachments = await persist_attachments(
            backend, attachments, sitting_folder_id=ws_folder_id
        )

        await compact_before_turn(
            conversation_id,
            model_id=resolve_turn_model(llm_credentials),
        )

        async with async_session_factory() as session:
            user_msg = await load_or_create_turn_user_message(
                session,
                conversation_id=conversation_id,
                user_message=user_message,
                existing_user_message_id=existing_user_message_id,
                attachments=resident_attachments,
                agent_mentions=agent_mentions,
            )
            history = await load_chat_context(session, conversation_id)

        sink.emit(turn_saved(user_message_id=user_msg.id))

        # One trace_id for the whole send turn (pipeline + parallel early title).
        turn_trace_id = new_trace_id()
        # Cloud early title: fire-and-forget in parallel with the turn (user message
        # only). Skip when the conversation already has a title (manual rename).
        if not (conv.title and str(conv.title).strip()):
            schedule_title_generation(
                conversation_id=conversation_id,
                user_id=user_id,
                user_message=plain_text(user_message),
                sink=sink,
                trace_id=turn_trace_id,
            )

        with log_context(
            trace_id=turn_trace_id,
            conversation_id=conversation_id,
            user_id=user_id,
        ):
            try:
                turn_result = await run_and_persist(
                    conversation_id=conversation_id,
                    user_message=user_message,
                    user_id=user_id,
                    folder_id=folder_id,
                    sink=sink,
                    history=drop_trailing_user_turn(history),
                    attachments=resident_attachments,
                    backend=backend,
                    llm_credentials=llm_credentials,
                    profile_set=profile_set,
                    permission_axes=permission_axes,
                    table_id=table_id,
                    table_selection=table_selection,
                    llm_supports_tools=llm_supports_tools,
                    x_client_platform=x_client_platform,
                    agent_mentions=agent_mentions,
                )
            except asyncio.CancelledError:
                await maybe_delete_zero_output_send(
                    conversation_id=conversation_id,
                    user_message_id=user_msg.id,
                    result=result_from_unstarted_close(sink=sink),
                    user_created_this_send=True,
                )
                raise
            await maybe_delete_zero_output_send(
                conversation_id=conversation_id,
                user_message_id=user_msg.id,
                result=turn_result,
                user_created_this_send=True,
            )
            # Pillar D1: delay sink.close while a detached coordination drive is live
            # (symmetric with sidecar _run_turn). Exception / cancel skip this.
            from agentcore.runtime.coordination import await_live_detached_drive

            await await_live_detached_drive(conversation_id)

    except Exception as e:
        logger.error("chat.stream_error", error=str(e), exc_info=True)
        if not sink._closed:
            code, message, err_ctx = error_fields_for(
                e,
                fallback_code=ErrorCode.STREAM_ERROR,
                fallback_message="服务出错了，请稍后重试。",
            )
            sink.emit(error_event(code, message, context=err_ctx))
            sink.emit(message_end(FinishReason.ERROR))
    finally:
        _disarm_tool_switches(switch_token)
        if not sink._closed:
            sink.close(reason="turn_finally")


async def regenerate_chat(
    *,
    conversation_id: str,
    message_id: str,
    user_id: str,
    sink: EventSink,
    edited_content: str | None = None,
    edited_attachments: list | None = None,
    edited_agent_mentions: list | None = None,
    llm_credentials: LLMCredentials | None = None,
    llm_supports_tools: bool | None = None,
) -> None:
    """Re-run a turn from an existing user message (regenerate / edit-and-resend)."""
    backend = None
    switch_token = None
    try:
        # Align with stream_chat: resolve bindings + truncate in session 1, compact
        # outside, load history in session 2. Never expire_all then touch ORM attrs
        # (MissingGreenlet → chat.regenerate_error 「服务出错了」).
        async with async_session_factory() as session:
            conv_repo = ConversationRepository(session)
            msg_repo = MessageRepository(session)

            conv = await conv_repo.get_by_id_unscoped(conversation_id)
            if not conv:
                logger.warning(
                    "chat.regenerate_rejected",
                    conversation_id=conversation_id,
                    message_id=message_id,
                    user_id=user_id,
                    reason="conversation_not_found",
                )
                sink.emit(error_event(ErrorCode.NOT_FOUND, "Conversation not found"))
                sink.emit(message_end(FinishReason.ERROR))
                return
            switch_token = await _arm_tool_switches(session, conv)

            target = await msg_repo.get_by_id(message_id, conversation_id=conversation_id)
            if not target or target.role != "user":
                # 前端曾用错 id（非用户消息 / 已不存在）时只走 SSE，旧版不落库——苏大大样本难追。
                logger.warning(
                    "chat.regenerate_rejected",
                    conversation_id=conversation_id,
                    message_id=message_id,
                    user_id=user_id,
                    reason="missing" if target is None else "not_user",
                    found_role=None if target is None else target.role,
                )
                sink.emit(error_event(ErrorCode.INVALID, "Can only regenerate from a user message"))
                sink.emit(message_end(FinishReason.ERROR))
                return

            # Capture scalars before commit (expire_on_commit) while attrs are hot.
            user_message = edited_content if edited_content is not None else (target.content or "")
            if edited_agent_mentions is not None:
                stored_mentions = to_stored_agent_mentions(
                    [
                        item.model_dump() if hasattr(item, "model_dump") else item
                        for item in edited_agent_mentions
                    ]
                )
            else:
                stored_mentions = to_stored_agent_mentions(
                    list(getattr(target, "agent_mentions", None) or [])
                )
            stored_attachments = None
            if edited_attachments is not None:
                stored_attachments = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in edited_attachments
                ]
            target_created_at = target.created_at
            folder_id = conv.folder_id
            auto_desk_raw = getattr(conv, "auto_desk_folder_id", None)
            ws_folder_id, _auto_desk_folder_id = resolve_turn_file_workspace(
                birth_folder_id=folder_id,
                auto_desk_folder_id=auto_desk_raw if isinstance(auto_desk_raw, str) else None,
            )
            if ws_folder_id and not folder_id:
                local_binding = await resolve_folder_local_binding(session, ws_folder_id)
            else:
                local_binding = await resolve_local_binding(session, conv)
            profile_set = await resolve_profile_set(session, conv, user_id)
            permission_axes = await resolve_permission_axes(session, conversation_id)
            table = await TableRepository(session).get_by_conversation_id(
                conversation_id, user_id=user_id
            )
            table_id = table.id if table else None

            if (
                edited_content is not None
                or stored_attachments is not None
                or edited_agent_mentions is not None
            ):
                await msg_repo.update_content(
                    message_id,
                    edited_content if edited_content is not None else None,
                    attachments=stored_attachments,
                    agent_mentions=(stored_mentions if edited_agent_mentions is not None else None),
                    commit=False,
                )

            await msg_repo.delete_after(
                conversation_id, after_created_at=target_created_at, commit=False
            )
            await session.commit()

        await compact_before_turn(
            conversation_id,
            model_id=resolve_turn_model(llm_credentials),
        )

        async with async_session_factory() as session:
            history = await load_chat_context(session, conversation_id)

        backend = await build_turn_backend(
            user_id=user_id,
            conversation_id=conversation_id,
            folder_id=ws_folder_id,
            sink=sink,
            local_binding=local_binding,
        )

        # A′: no whole-turn workspace_lock — write/snapshot sinks hold the key.
        # 不得静默等锁：pause 不握 folder 锁；写路径短等经 workspace_lock_wait SSE。
        await run_and_persist(
            conversation_id=conversation_id,
            user_message=user_message,
            user_id=user_id,
            folder_id=folder_id,
            sink=sink,
            history=drop_trailing_user_turn(history),
            attachments=None,
            backend=backend,
            llm_credentials=llm_credentials,
            profile_set=profile_set,
            permission_axes=permission_axes,
            table_id=table_id,
            llm_supports_tools=llm_supports_tools,
            agent_mentions=stored_mentions or None,
        )
        from agentcore.runtime.coordination import await_live_detached_drive

        await await_live_detached_drive(conversation_id)

    except Exception as e:
        logger.error(
            "chat.regenerate_error",
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        if not sink._closed:
            code, message, err_ctx = error_fields_for(
                e,
                fallback_code=ErrorCode.STREAM_ERROR,
                fallback_message="服务出错了，请稍后重试。",
            )
            sink.emit(error_event(code, message, context=err_ctx))
            sink.emit(message_end(FinishReason.ERROR))
    finally:
        _disarm_tool_switches(switch_token)
        if not sink._closed:
            sink.close(reason="regenerate_finally")


async def resume_chat(
    *,
    suspension: TurnSuspension,
    response: CheckpointResponse,
    sink: EventSink,
    llm_credentials: LLMCredentials | None = None,
    llm_supports_tools: bool | None = None,
    x_client_platform: str | None = None,
) -> None:
    """Continue a leftover plan_review / live ask_user pause (结构化挂起 2b resume).

    The route prewrote the ``*_resolved`` settlement AND claimed (DELETE) the
    ``paused_turns`` row before dispatching here, so settlement is durable on entry.
    Per D1 (sidecar parity) a durable settlement is never rolled back: cancel / failure
    after this point projects as interrupted_after_decision, NOT a frame restore —
    restoring would resurrect the already-authorized decision card (e.g. leftover team_preview
    reappearing after 停止 lands mid-continuation).
    """
    conversation_id = suspension.conversation_id
    user_id = suspension.user_id
    # D1 (sidecar parity): the cloud /resume route prewrites the ``*_resolved`` settlement
    # AND claims the frame BEFORE dispatching here, so settlement is durable on entry. A
    # durable settlement is never rolled back — cancel / failure after this point is
    # interrupted_after_decision, not a frame restore (restoring would resurrect the
    # already-authorized card, e.g. leftover team_preview reappearing after 停止 mid-run).
    settlement_durable = True
    backend = None
    switch_token = None
    latency_token = None
    meter_token = None
    try:
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if not conv:
                sink.emit(error_event(ErrorCode.NOT_FOUND, "Conversation not found"))
                sink.emit(message_end(FinishReason.ERROR))
                return
            switch_token = await _arm_tool_switches(session, conv)
            folder_id = conv.folder_id
            auto_desk_raw = getattr(conv, "auto_desk_folder_id", None)
            ws_folder_id, _auto_desk_folder_id = resolve_turn_file_workspace(
                birth_folder_id=folder_id,
                auto_desk_folder_id=auto_desk_raw if isinstance(auto_desk_raw, str) else None,
            )
            if ws_folder_id and not folder_id:
                local_binding = await resolve_folder_local_binding(session, ws_folder_id)
            else:
                local_binding = await resolve_local_binding(session, conv)
            profile_set = await resolve_profile_set(session, conv, user_id)
            # Conversation permission mode (not frozen into the frame): a mid-pause
            # switch applies to the resumed continuation.
            permission_axes = await resolve_permission_axes(session, conversation_id)

        await compact_before_turn(
            conversation_id,
            model_id=resolve_turn_model(llm_credentials),
        )

        async with async_session_factory() as session:
            history = await load_chat_context(session, conversation_id)
            table = await TableRepository(session).get_by_conversation_id(
                conversation_id, user_id=user_id
            )
            table_id = table.id if table else None

        backend = await build_turn_backend(
            user_id=user_id,
            conversation_id=conversation_id,
            folder_id=ws_folder_id,
            sink=sink,
            local_binding=local_binding,
        )
        session_saver, session_loader = session_callbacks(conversation_id)
        suspension_saver, suspension_deleter = suspension_callbacks()

        # A′: no whole-turn workspace_lock — write/snapshot sinks hold the key.
        # 不得静默等锁：pause 不握 folder 锁；写路径短等经 workspace_lock_wait SSE。
        trace_id = suspension.trace_id or new_trace_id()
        # Fresh attempt_id on every resume (same message_id / journal turn_id).
        attempt_id = new_id()
        started = time.monotonic()
        from agentcore.runtime.turn.latency import bind_active_meter, bind_turn_latency

        meter = await _load_active_meter(conversation_id, suspension.message_id)
        _, latency_token = bind_turn_latency(
            started,
            carried_duration_ms=meter.duration_ms,
            carried_generation_ms=meter.generation_ms,
            first_stream_done=meter.first_stream_done,
        )
        meter_token = bind_active_meter(meter)
        with log_context(
            trace_id=trace_id,
            conversation_id=conversation_id,
            user_id=user_id,
            attempt_id=attempt_id,
            message_id=suspension.message_id,
            agent_id="CEO",
            cost_role="captain",
            persona="CEO",
        ):
            logger.info(
                "chat.resume_start",
                message_id=suspension.message_id,
                kind=suspension.kind.value,
                decision=response.decision.value,
                seeded=len(getattr(suspension, "completed", {})),
            )
            sink.bind_content_checkpoint(
                conversation_id=conversation_id,
                message_id=suspension.message_id,
            )
            lease_stop: asyncio.Event | None = None
            heartbeat_task: asyncio.Task | None = None
            if settings.turn_lease_enabled:
                owner_id = await acquire_turn_lease(
                    message_id=suspension.message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    phase="resuming",
                    meta={"trace_id": trace_id, "kind": suspension.kind.value},
                )
                lease_stop = asyncio.Event()
                heartbeat_task = asyncio.create_task(
                    lease_heartbeat_loop(
                        suspension.message_id,
                        owner_id=owner_id,
                        interval_seconds=settings.turn_lease_heartbeat_seconds,
                        stop=lease_stop,
                        phase="resuming",
                    )
                )
            # Process cancel must leave the lease for sweeper reclaim (not delete it).
            release_lease_clean = True
            try:
                try:
                    try:
                        from agentcore.demo_tape.hooks import run_tape_resume_if_marked
                    except ImportError as e:
                        logger.warning("demo_tape.import_failed", error=str(e), phase="resume")
                        tape_result = None
                    else:
                        tape_result = await run_tape_resume_if_marked(
                            suspension=suspension,
                            response=response,
                            sink=sink,
                            folder_id=folder_id,
                            trace_id=trace_id,
                        )
                    if tape_result is not None:
                        result = tape_result
                    else:
                        result = await resume_chat_pipeline(
                            suspension=suspension,
                            decision=response.decision,
                            note=response.note,
                            selected=response.selected,
                            sink=sink,
                            backend=backend,
                            history=drop_trailing_user_turn(history),
                            table_id=table_id,
                            llm_credentials=llm_credentials,
                            profile_set=profile_set,
                            session_saver=session_saver,
                            session_loader=session_loader,
                            suspension_saver=suspension_saver,
                            suspension_deleter=suspension_deleter,
                            llm_supports_tools=llm_supports_tools,
                            permission_axes=permission_axes,
                            x_client_platform=x_client_platform,
                        )
                except asyncio.CancelledError:
                    # Hard cancel / lifespan / hard kill.
                    if turn_runs.is_clean_cancel(conversation_id):
                        closed = await close_user_stop_turn(
                            sink=sink,
                            conversation_id=conversation_id,
                            trace_id=trace_id,
                            message_id=suspension.message_id,
                            journal_entries=suspension.journal_entries,
                        )
                        release_lease_clean = bool(closed)
                    else:
                        release_lease_clean = False
                    raise
                finish = result.get("finish_reason")
                finish_value = getattr(finish, "value", finish)
                duration_ms = _product_duration_ms(result, started)
                delegated, workers = turn_worker_stats(result)
                # 协作质量 (学·度量 §2.5): publish the same four counters the
                # fresh path logs at chat.turn_complete and both paths persist
                # to turn_metrics — without them the resumed turn's authority
                # never reaches the log and the 双轨对账 misreads the paused
                # snapshot as final. Terminal STOP resumes carry no collab
                # (no CEO round ran) and stay field-less → 不可对账, not drift.
                collab = result.get("collab")
                collab_fields = (
                    {
                        "boundary_yields": collab.get("boundary_yields", 0),
                        "scope_signals": collab.get("scope_signals", 0),
                        "escalations": collab.get("escalations", 0),
                        "revises": collab.get("revises", 0),
                    }
                    if collab is not None
                    else {}
                )
                resume_outcome = result.get("outcome")
                logger.info(
                    "chat.resume_complete",
                    finish_reason=finish_value,
                    **(
                        {"outcome": resume_outcome}
                        if resume_outcome in ("ok", "partial", "paused", "error")
                        else {}
                    ),
                    rounds=result.get("rounds", 0),
                    reply_chars=len(result.get("content") or ""),
                    reply_preview=preview(result.get("content") or ""),
                    delegated=delegated,
                    workers=workers,
                    duration_ms=duration_ms,
                    error=result.get("error"),
                    **collab_fields,
                )
                # Persist INSIDE the trace scope (same as run_and_persist) so the
                # resumed turn's tail (cost.recorded / obs.turn_spans / metrics)
                # inherits trace_id / attempt_id instead of losing the join key.
                # persist-then-D1-await, same as continue_chat.
                await persist_turn_result(
                    result=result,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    folder_id=folder_id,
                    backend=backend,
                    sink=sink,
                    user_message=suspension.user_message,
                    llm_credentials=llm_credentials,
                    trace_id=trace_id,
                    turn_id=attempt_id,
                    duration_ms=duration_ms,
                    kind="resume",
                )
            finally:
                if lease_stop is not None:
                    lease_stop.set()
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task
                if settings.turn_lease_enabled:
                    if release_lease_clean:
                        await release_turn_lease(suspension.message_id)
                    else:
                        with contextlib.suppress(asyncio.TimeoutError, Exception):
                            await asyncio.wait_for(
                                asyncio.shield(orphan_turn_lease(suspension.message_id)),
                                timeout=2.0,
                            )

        # Same D1 hold as stream_chat / sidecar resume: delay close while detached drive lives.
        from agentcore.runtime.coordination import await_live_detached_drive

        await await_live_detached_drive(conversation_id)

    except Exception as e:
        logger.error("chat.resume_error", error=str(e), exc_info=True)
        if not sink._closed:
            code, message, err_ctx = error_fields_for(
                e,
                fallback_code=ErrorCode.STREAM_ERROR,
                fallback_message="服务出错了，请稍后重试。",
            )
            sink.emit(error_event(code, message, context=err_ctx))
            sink.emit(message_end(FinishReason.ERROR))
    finally:
        _disarm_tool_switches(switch_token)
        from agentcore.runtime.turn.latency import reset_active_meter, reset_turn_latency

        if latency_token is not None:
            reset_turn_latency(latency_token)
        if meter_token is not None:
            reset_active_meter(meter_token)
        # Pre-settlement failures would re-upsert the frame for retry; the cloud route
        # guarantees a durable settlement before dispatch (D1), so this stays dormant —
        # a post-decision cancel/error is interrupted_after_decision, never a frame revive.
        if not settlement_durable:
            await restore_paused_turn(suspension)
        if not sink._closed:
            sink.close(reason="resume_finally")


def _turn_started_fields(entries: list[dict]) -> tuple[str, str]:
    """``(user_message, system_prompt)`` from the journal ``turn_started`` fact."""
    for entry in entries:
        if (entry.get("kind") or "") != "turn_started":
            continue
        payload = entry.get("payload") or {}
        return (
            str(payload.get("user_message") or ""),
            str(payload.get("system_prompt") or ""),
        )
    return "", ""


def _product_duration_ms(result: dict, started: float) -> int:
    """Active time already stamped for this message, else the bound probe.

    Resume used to publish ``monotonic - started`` for this segment only and
    leave the previous segment's decode window on the row. The footer and the
    usage speed then described different slices. The probe carries the earlier
    active time, and settle writes that sum onto ``result``.
    """
    from agentcore.runtime.turn.latency import turn_wall_ms

    stamped = result.get("duration_ms")
    if isinstance(stamped, int) and not isinstance(stamped, bool) and stamped > 0:
        return stamped
    wall = turn_wall_ms()
    if wall is not None and wall > 0:
        return wall
    return max(int((time.monotonic() - started) * 1000), 0)


async def _load_active_meter(conversation_id: str, message_id: str):
    """Pause snapshot on the assistant row. Empty when the row cannot be read."""
    from agentcore.runtime.turn.latency import ActiveMeter

    try:
        async with async_session_factory() as session:
            msg = await MessageRepository(session).get_by_id(
                message_id, conversation_id=conversation_id
            )
        if asyncio.iscoroutine(msg):
            msg.close()
            msg = None
        usage = msg.usage if msg is not None and isinstance(msg.usage, dict) else None
    except Exception as e:
        logger.warning(
            "chat.active_meter_load_failed",
            conversation_id=conversation_id,
            message_id=message_id,
            error=str(e),
        )
        return ActiveMeter()
    return ActiveMeter.from_usage(usage)


def _captain_run_id_from_journal(entries: list[dict]) -> str:
    for entry in entries:
        if (entry.get("kind") or "") != "run_started":
            continue
        payload = entry.get("payload") or {}
        if payload.get("kind") == "captain":
            run_id = payload.get("run_id")
            if isinstance(run_id, str) and run_id:
                return run_id
    return ""


async def continue_chat(
    *,
    conversation_id: str,
    message_id: str,
    user_id: str,
    sink: EventSink,
    llm_credentials: LLMCredentials | None = None,
    llm_supports_tools: bool | None = None,
) -> None:
    """Continue a cloud CEO turn paused on exhausted rate limit (no checkpoint card)."""
    from agentcore.db.repositories import TurnJournalRepository
    from agentcore.runtime.turn.ceo_continue import CEO_CONTINUE_KIND

    backend = None
    switch_token = None
    lock_user_id = user_id
    restore_lock = True
    latency_token = None
    meter_token = None
    try:
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if not conv:
                sink.emit(error_event(ErrorCode.NOT_FOUND, "Conversation not found"))
                sink.emit(message_end(FinishReason.ERROR))
                return
            switch_token = await _arm_tool_switches(session, conv)
            folder_id = conv.folder_id
            auto_desk_raw = getattr(conv, "auto_desk_folder_id", None)
            ws_folder_id, _auto_desk_folder_id = resolve_turn_file_workspace(
                birth_folder_id=folder_id,
                auto_desk_folder_id=auto_desk_raw if isinstance(auto_desk_raw, str) else None,
            )
            if ws_folder_id and not folder_id:
                local_binding = await resolve_folder_local_binding(session, ws_folder_id)
            else:
                local_binding = await resolve_local_binding(session, conv)
            profile_set = await resolve_profile_set(session, conv, user_id)
            permission_axes = await resolve_permission_axes(session, conversation_id)
            entries = await TurnJournalRepository(session).load(message_id)

        if not entries:
            sink.emit(error_event(ErrorCode.NOT_FOUND, "无法继续：执行日志不存在。"))
            sink.emit(message_end(FinishReason.ERROR))
            return

        user_message, _ceo_chat_prompt = _turn_started_fields(entries)
        captain_run_id = _captain_run_id_from_journal(entries)

        await compact_before_turn(
            conversation_id,
            model_id=resolve_turn_model(llm_credentials),
        )

        async with async_session_factory() as session:
            history = await load_chat_context(session, conversation_id)
            table = await TableRepository(session).get_by_conversation_id(
                conversation_id, user_id=user_id
            )
            table_id = table.id if table else None

        backend = await build_turn_backend(
            user_id=user_id,
            conversation_id=conversation_id,
            folder_id=ws_folder_id,
            sink=sink,
            local_binding=local_binding,
        )
        session_saver, session_loader = session_callbacks(conversation_id)
        suspension_saver, suspension_deleter = suspension_callbacks()

        trace_id = new_trace_id()
        attempt_id = new_id()
        started = time.monotonic()
        from agentcore.runtime.turn.latency import bind_active_meter, bind_turn_latency

        meter = await _load_active_meter(conversation_id, message_id)
        _, latency_token = bind_turn_latency(
            started,
            carried_duration_ms=meter.duration_ms,
            carried_generation_ms=meter.generation_ms,
            first_stream_done=meter.first_stream_done,
        )
        meter_token = bind_active_meter(meter)
        with log_context(
            trace_id=trace_id,
            conversation_id=conversation_id,
            user_id=user_id,
            attempt_id=attempt_id,
            message_id=message_id,
            agent_id="CEO",
            cost_role="captain",
            persona="CEO",
        ):
            logger.info("chat.continue_start", message_id=message_id)
            sink.bind_content_checkpoint(
                conversation_id=conversation_id,
                message_id=message_id,
            )
            lease_stop: asyncio.Event | None = None
            heartbeat_task: asyncio.Task | None = None
            if settings.turn_lease_enabled:
                owner_id = await acquire_turn_lease(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    phase="resuming",
                    meta={"trace_id": trace_id, "kind": CEO_CONTINUE_KIND},
                )
                lease_stop = asyncio.Event()
                heartbeat_task = asyncio.create_task(
                    lease_heartbeat_loop(
                        message_id,
                        owner_id=owner_id,
                        interval_seconds=settings.turn_lease_heartbeat_seconds,
                        stop=lease_stop,
                        phase="resuming",
                    )
                )
            release_lease_clean = True
            try:
                try:
                    result = await continue_ceo_pipeline(
                        conversation_id=conversation_id,
                        message_id=message_id,
                        user_id=user_id,
                        user_message=user_message,
                        journal_entries=list(entries),
                        captain_run_id=captain_run_id,
                        sink=sink,
                        backend=backend,
                        history=drop_trailing_user_turn(history) if history else None,
                        table_id=table_id,
                        folder_id=ws_folder_id,
                        llm_credentials=llm_credentials,
                        profile_set=profile_set,
                        session_saver=session_saver,
                        session_loader=session_loader,
                        suspension_saver=suspension_saver,
                        suspension_deleter=suspension_deleter,
                        llm_supports_tools=llm_supports_tools,
                        permission_axes=permission_axes,
                        trace_id=trace_id,
                    )
                except asyncio.CancelledError:
                    if turn_runs.is_clean_cancel(conversation_id):
                        closed = await close_user_stop_turn(
                            sink=sink,
                            conversation_id=conversation_id,
                            trace_id=trace_id,
                            message_id=message_id,
                            journal_entries=list(entries),
                        )
                        release_lease_clean = bool(closed)
                    else:
                        release_lease_clean = False
                    raise
                finish = result.get("finish_reason")
                finish_value = getattr(finish, "value", finish)
                duration_ms = _product_duration_ms(result, started)
                delegated, workers = turn_worker_stats(result)
                collab = result.get("collab")
                collab_fields = (
                    {
                        "boundary_yields": collab.get("boundary_yields", 0),
                        "scope_signals": collab.get("scope_signals", 0),
                        "escalations": collab.get("escalations", 0),
                        "revises": collab.get("revises", 0),
                    }
                    if collab is not None
                    else {}
                )
                continue_outcome = result.get("outcome")
                logger.info(
                    "chat.continue_complete",
                    finish_reason=finish_value,
                    **(
                        {"outcome": continue_outcome}
                        if continue_outcome in ("ok", "partial", "paused", "error")
                        else {}
                    ),
                    rounds=result.get("rounds", 0),
                    reply_chars=len(result.get("content") or ""),
                    reply_preview=preview(result.get("content") or ""),
                    delegated=delegated,
                    workers=workers,
                    duration_ms=duration_ms,
                    error=result.get("error"),
                    **collab_fields,
                )
                await persist_turn_result(
                    result=result,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    folder_id=folder_id,
                    backend=backend,
                    sink=sink,
                    user_message=user_message,
                    llm_credentials=llm_credentials,
                    trace_id=trace_id,
                    turn_id=attempt_id,
                    duration_ms=duration_ms,
                    kind="resume",
                )
                restore_lock = False
            finally:
                if lease_stop is not None:
                    lease_stop.set()
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task
                if settings.turn_lease_enabled:
                    if release_lease_clean:
                        await release_turn_lease(message_id)
                    else:
                        with contextlib.suppress(asyncio.TimeoutError, Exception):
                            await asyncio.wait_for(
                                asyncio.shield(orphan_turn_lease(message_id)),
                                timeout=2.0,
                            )

        from agentcore.runtime.coordination import await_live_detached_drive

        await await_live_detached_drive(conversation_id)

    except Exception as e:
        logger.error("chat.continue_error", error=str(e), exc_info=True)
        if not sink._closed:
            code, message, err_ctx = error_fields_for(
                e,
                fallback_code=ErrorCode.STREAM_ERROR,
                fallback_message="服务出错了，请稍后重试。",
            )
            sink.emit(error_event(code, message, context=err_ctx))
            sink.emit(message_end(FinishReason.ERROR))
    finally:
        _disarm_tool_switches(switch_token)
        from agentcore.runtime.turn.latency import reset_active_meter, reset_turn_latency

        if latency_token is not None:
            reset_turn_latency(latency_token)
        if meter_token is not None:
            reset_active_meter(meter_token)
        if restore_lock:
            await restore_ceo_continue_lock(
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=lock_user_id,
            )
        else:
            await release_ceo_continue_claim(message_id, conversation_id=conversation_id)
        if not sink._closed:
            sink.close(reason="continue_finally")
