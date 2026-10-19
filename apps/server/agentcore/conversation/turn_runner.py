"""Run the chat pipeline and persist the turn (shared by send / regenerate / resume)."""

import asyncio
import contextlib
import time

from agentcore.config import settings
from agentcore.conversation.common import preview
from agentcore.conversation.turn_persistence import (
    close_user_stop_turn,
    create_assistant_placeholder,
    persist_placeholder_abort,
    persist_turn_result,
    salvage_incomplete_turn,
)
from agentcore.core.log_context import get_log_value, log_context, new_trace_id
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.resolve import LLMCredentials
from agentcore.runtime.events import EventSink
from agentcore.runtime.leases import (
    acquire_turn_lease,
    lease_heartbeat_loop,
    orphan_turn_lease,
    release_turn_lease,
)
from agentcore.runtime.pipeline import run_chat_pipeline
from agentcore.runtime.session_persistence import load_run_session, save_run_session
from agentcore.runtime.suspension.persistence import delete_paused_turn, save_paused_turn
from agentcore.runtime.turn.complete_log import log_chat_turn_complete
from agentcore.runtime.turn.latency import bind_turn_latency, reset_turn_latency
from agentcore.runtime.turn.runs import turn_runs
from agentcore.runtime.turn.snapshot import ConversationSnapshot
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)


def session_callbacks(conversation_id: str):
    """The 留人 跨进程落盘 write-through saver + roster-miss loader, or ``(None, None)``.

    The raw saver awaits DB I/O; ``run_chat_pipeline`` / ``resume_chat_pipeline`` wrap
    it in :class:`~agentcore.runtime.session_persistence.SessionRosterWriter` so the
    hot path only schedules, and turn-end flush drains pending writes (as-built: 成本配额 §三).
    """
    if not settings.session_roster_persist_enabled:
        return None, None

    async def _persist_session(session) -> None:
        await save_run_session(conversation_id, session)

    return _persist_session, load_run_session


def suspension_callbacks():
    """The 结构化挂起 2b persist-before-wait / drop-after-resolve closures."""
    if not settings.structured_suspension_persist_enabled:
        return None, None
    return save_paused_turn, delete_paused_turn


async def run_and_persist(
    *,
    conversation_id: str,
    user_message: str,
    user_id: str,
    conversation: ConversationSnapshot,
    sink: EventSink,
    history: list[dict],
    attachments: list[dict] | None,
    backend: WorkspaceBackend,
    llm_credentials: LLMCredentials | None,
    profile_set: ProfileSet | None = None,
    llm_supports_tools: bool | None = None,
    x_client_platform: str | None = None,
    agent_mentions: list[dict] | None = None,
    continue_message_id: str | None = None,
    inherited_journal_entries: list[dict] | None = None,
) -> dict | None:
    """Run the pipeline, then persist the assistant reply (+ derived title / stage_card).

    Returns the pipeline result dict (including ``message_id`` for this turn) on a
    normal completion, or a failed result when the presence gate aborts after the
    placeholder exists (row is stamped ``failed`` so hydrate does not keep spinning).
    ``None`` only if the turn never produced a result (cancel / early abort paths
    that raise instead).

    ``conversation`` is the session snapshot (permission / 统筹 / folder).

    ``continue_message_id`` runs this turn ON an existing assistant row instead of a
    freshly minted one (崩溃重驱恢复收口 · D5 归属原回合): the placeholder insert is
    skipped (the row is already there, still ``running``) and finalize's merging
    upsert writes the result back into that bubble. Pair it with
    ``inherited_journal_entries`` — that turn's existing facts — so the journal
    continues rather than restarting at seq 0.
    """
    session_saver, session_loader = session_callbacks(conversation_id)
    suspension_saver, suspension_deleter = suspension_callbacks()

    folder_id = conversation.folder_id
    continuing = bool(continue_message_id)
    message_id = continue_message_id or new_id()
    # attempt_id = this run of the turn (resume mints a fresh one); ≠ journal turn_id
    # (journal/audit turn_id ≡ message_id). Log context key is attempt_id; turn_metrics
    # still stores the value under its DB column ``turn_id``.
    attempt_id = new_id()
    trace_id = get_log_value("trace_id") or new_trace_id()
    started = time.monotonic()
    # Phase-0 latency probe: anchor = user-message handling start (monotonic).
    latency_probe, latency_token = bind_turn_latency(started)
    lease_stop: asyncio.Event | None = None
    heartbeat_task: asyncio.Task | None = None
    outcome: dict | None = None
    try:
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
            path_reason = get_log_value("stream_path_reason")
            logger.info(
                "chat.turn_start",
                chars=len(user_message or ""),
                preview=preview(user_message),
                history=len(history),
                attachments=len(attachments or []),
                location=backend.location,
                via="cloud",
                message_id=message_id,
                continuing=continuing,
                **({"stream_path_reason": path_reason} if path_reason else {}),
            )
            if not continuing:
                await create_assistant_placeholder(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    trace_id=trace_id,
                )
            sink.bind_content_checkpoint(
                conversation_id=conversation_id,
                message_id=message_id,
            )
            # Presence gate: local desktop-channel turns need a workspace fulfiller
            # before any channel IO (baseline / prepare). Millisecond honest abort.
            from agentcore.runtime.pipeline.errors import (
                backend_uses_local_channel,
                bind_prepare_local_io_deadline,
                prepare_local_io_deadline_bound,
                raise_if_local_workspace_fulfiller_absent,
                reset_prepare_local_io_deadline,
            )

            try:
                raise_if_local_workspace_fulfiller_absent(
                    user_id=user_id, backend=backend
                )
            except Exception as e:
                # Placeholder already exists. Persist failed so a later open /
                # follow does not treat the row as still running.
                return await persist_placeholder_abort(
                    exc=e,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    folder_id=folder_id,
                    backend=backend,
                    sink=sink,
                    user_message=user_message,
                    llm_credentials=llm_credentials,
                    trace_id=trace_id,
                    turn_id=attempt_id,
                    message_id=message_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            # One prepare-phase local IO wall clock for prepare's channel probes.
            # First file-mutating tool captures the turn baseline unbudgeted in
            # execute (not here — greetings must not zip). Pipeline execution
            # (tools, cross-desk delegate re-probes) stays unbudgeted, per
            # 工作区.md §7.7.
            budget_token = None
            if not prepare_local_io_deadline_bound() and backend_uses_local_channel(backend):
                budget_token = bind_prepare_local_io_deadline()
            try:
                # Dev-only demo tape: divert before the real pipeline when this conversation
                # is bound under DEMO_TAPE_REPLAY_ENABLED. Optional — ImportError must not
                # block live turns (e.g. partial deploy missing tape_frame_meta).
                tape_result = None
                try:
                    from agentcore.demo_tape.hooks import run_tape_turn_if_bound
                except ImportError as e:
                    logger.warning("demo_tape.import_failed", error=str(e), phase="turn")
                else:
                    try:
                        tape_result = await run_tape_turn_if_bound(
                            conversation_id=conversation_id,
                            sink=sink,
                            message_id=message_id,
                            user_id=user_id,
                            user_message=user_message,
                            folder_id=folder_id,
                            trace_id=trace_id,
                        )
                    except asyncio.CancelledError:
                        # Same 收口 as the real pipeline below: a mid-replay disconnect / shutdown
                        # must not leave a zombie RUNNING assistant row — salvage the streamed part.
                        if turn_runs.is_clean_cancel(conversation_id):
                            await close_user_stop_turn(
                                sink=sink,
                                conversation_id=conversation_id,
                                trace_id=trace_id,
                                message_id=message_id,
                            )
                        else:
                            salvage_incomplete_turn(
                                sink=sink,
                                conversation_id=conversation_id,
                                trace_id=trace_id,
                                message_id=message_id,
                            )
                        raise
                if tape_result is not None:
                    duration_ms = int((time.monotonic() - started) * 1000)
                    logger.info(
                        "demo_tape.turn_complete",
                        finish_reason=getattr(
                            tape_result.get("finish_reason"),
                            "value",
                            tape_result.get("finish_reason"),
                        ),
                        duration_ms=duration_ms,
                        message_id=message_id,
                    )
                    await persist_turn_result(
                        result=tape_result,
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
                        kind="turn",
                    )
                    tape_result["message_id"] = message_id
                    return tape_result

                if settings.turn_lease_enabled:
                    owner_id = await acquire_turn_lease(
                        message_id=message_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        phase="running",
                        meta={"trace_id": trace_id, "folder_id": folder_id},
                    )
                    lease_stop = asyncio.Event()
                    heartbeat_task = asyncio.create_task(
                        lease_heartbeat_loop(
                            message_id,
                            owner_id=owner_id,
                            interval_seconds=settings.turn_lease_heartbeat_seconds,
                            stop=lease_stop,
                        )
                    )
                # Process cancel must leave the lease for sweeper reclaim (not delete it).
                release_lease_clean = True
                try:
                    try:
                        result = await run_chat_pipeline(
                            conversation_id=conversation_id,
                            user_message=user_message,
                            history=history,
                            sink=sink,
                            user_id=user_id,
                            backend=backend,
                            conversation=conversation,
                            attachments=attachments,
                            llm_credentials=llm_credentials,
                            profile_set=profile_set,
                            session_saver=session_saver,
                            session_loader=session_loader,
                            suspension_saver=suspension_saver,
                            suspension_deleter=suspension_deleter,
                            llm_supports_tools=llm_supports_tools,
                            message_id=message_id,
                            x_client_platform=x_client_platform,
                            agent_mentions=agent_mentions,
                            inherited_journal_entries=inherited_journal_entries,
                        )
                    except asyncio.CancelledError:
                        # Hard cancel / lifespan → terminal incomplete + release.
                        # True hard kill (no lifespan salvage) = orphan for sweeper.
                        if turn_runs.is_clean_cancel(conversation_id):
                            closed = await close_user_stop_turn(
                                sink=sink,
                                conversation_id=conversation_id,
                                trace_id=trace_id,
                                message_id=message_id,
                            )
                            release_lease_clean = bool(closed)
                        else:
                            release_lease_clean = False
                        raise
                    duration_ms = int((time.monotonic() - started) * 1000)
                    log_chat_turn_complete(
                        result,
                        duration_ms=duration_ms,
                        llm_credentials=llm_credentials,
                    )

                    # Persist INSIDE the trace scope so the post-turn tail (cost.recorded,
                    # obs.turn_spans, turn-metrics/snapshot/title warnings) inherits this turn's
                    # trace_id / attempt_id from the log context — otherwise those lines fire after
                    # the scope closes and lose the single 全链路 join key (conversation-logs.mdc).
                    # persist-then-D1-await, same as continue_chat (await lives at stream_chat).
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
                        kind="turn",
                    )
                    if isinstance(result, dict):
                        result["message_id"] = message_id
                        outcome = result
                    else:
                        outcome = {"message_id": message_id}
                finally:
                    if lease_stop is not None:
                        lease_stop.set()
                    if heartbeat_task is not None:
                        heartbeat_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat_task
                    # Normal terminal / pause / user-stop / lifespan shutdown: clear lease.
                    # True hard-kill CancelledError: orphan (shielded) so sweeper can recover.
                    if settings.turn_lease_enabled:
                        if release_lease_clean:
                            await release_turn_lease(message_id)
                        else:
                            with contextlib.suppress(asyncio.TimeoutError, Exception):
                                await asyncio.wait_for(
                                    asyncio.shield(orphan_turn_lease(message_id)),
                                    timeout=2.0,
                                )
            finally:
                if budget_token is not None:
                    reset_prepare_local_io_deadline(budget_token)
    finally:
        reset_turn_latency(latency_token)
    return outcome
