"""Sidecar turn execution: run, resume, event pump + outbox finalize."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from typing import Any

from agentcore.conversation.common import preview
from agentcore.conversation.store.usage_settle import usage_settle_finalize_kwargs
from agentcore.conversation.zero_output_rollback import (
    maybe_discard_zero_output_outbox,
    result_from_unstarted_close,
)
from agentcore.core.errors import ClientTooOldError, InferenceTokenExpiredError
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.llm.resolve import resolve_turn_model
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, FinishReason, error_event, message_end
from agentcore.runtime.journal import (
    KIND_TURN_END,
    attach_journal_error_type,
    runs_from_entries,
)
from agentcore.runtime.journal.pending_interactions import (
    append_unrecorded_hot_orphan_facts,
)
from agentcore.runtime.suspension import TurnSuspension
from agentcore.runtime.turn.complete_log import log_chat_turn_complete
from agentcore.runtime.turn.interrupt import (
    finish_reason_for,
    normalize_interrupt_reason,
)
from agentcore.runtime.turn.latency import (
    ActiveMeter,
    bind_active_meter,
    bind_turn_latency,
    reset_active_meter,
    reset_turn_latency,
    stamp_turn_wall,
)
from agentcore.sidecar import protocol
from agentcore.sidecar.server_pkg.result import trim_result

logger = get_logger(__name__)

_TRACE_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")


def schedule_with_context_budget(raw: object, coro: Any) -> asyncio.Task:
    """Start ``coro`` with this turn's context budget copied into the task.

    ``create_task`` copies the current context. Resetting the parent afterwards
    does not clear the task's copy.
    """
    from agentcore.llm.context_budget import (
        bind_context_budget,
        coerce_context_budget,
        reset_context_budget,
    )

    token = bind_context_budget(coerce_context_budget(raw))
    try:
        return asyncio.create_task(coro)
    finally:
        reset_context_budget(token)


def parse_client_turn_ids(params: dict[str, Any]) -> tuple[str, str, str] | None:
    """Desktop-minted ``(user_message_id, message_id, trace_id)``.

    ``trace_id`` must be 32-hex (``uuid4().hex``). Hyphenated UUIDs and
    ``new_id()`` leftovers are rejected — sidecar must not mint these.
    """
    umid = str(params.get("userMessageId") or "").strip()
    message_id = str(params.get("messageId") or "").strip()
    trace_id = str(params.get("traceId") or "").strip()
    if not umid or not message_id or not _TRACE_HEX32.fullmatch(trace_id):
        return None
    return umid, message_id, trace_id


def resolve_resume_user_message_id(
    client_id: str = "",
    frame_id: str | None = None,
) -> str:
    """Outbox / finalize key for a sidecar resume.

    Prefer the client-pinned user bubble, then the pause-frame id. When both
    are missing, mint a real UUID — same as ``startTurn``. Never derive
    ``resume-{turn_id}``: that 43-char token is not a ``messages.id`` UUID and
    cloud ``_finalize_local`` ``get_by_id`` raises (整请求 500).
    """
    umid = str(client_id or "").strip() or str(frame_id or "").strip()
    return umid or new_id()


def structured_missing_inference_error() -> dict[str, str]:
    """``{code, message}`` for sidecar turns that lack inference credentials.

    Same shape local finalize already persists into ``usage.error`` / ``turn_end.error``.
    """
    err = InferenceTokenExpiredError()
    return {"code": str(err.code), "message": err.message}


def missing_inference_turn_result(message_id: str) -> dict[str, Any]:
    """Synthetic failed-turn result when startTurn/resume has no inference creds."""
    structured = structured_missing_inference_error()
    return {
        "message_id": message_id,
        "content": "",
        "error": structured,
        "error_code": structured["code"],
        "finish_reason": FinishReason.ERROR,
        "journal_entries": [
            {
                "kind": KIND_TURN_END,
                "payload": {
                    "finish_reason": FinishReason.ERROR.value,
                    "error": structured,
                },
                "ts": None,
            }
        ],
    }


def normalize_folder_id_param(raw: Any) -> str | None:
    """RPC ``folderId`` → ``str | None`` (blank / null = bare chat). Never invent a project."""
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


def normalize_local_subpath_param(raw: Any) -> str:
    """RPC ``localSubpath`` → stripped str (null/blank = root itself)."""
    if raw is None:
        return ""
    return str(raw).strip()


def rpc_agent_mentions(params: dict[str, Any]) -> list[dict[str, Any]]:
    """startTurn ``agentMentions`` / ``agent_mentions`` → sanitized ``{agent_id, role}``."""
    from agentcore.core.mentions import to_stored_agent_mentions

    raw = params.get("agentMentions")
    if raw is None:
        raw = params.get("agent_mentions")
    return to_stored_agent_mentions(raw if isinstance(raw, list) else None)


def rpc_attachments(params: dict[str, Any]) -> list[dict[str, Any]]:
    """startTurn / deliver-drain ``attachments`` → dicts (desktop already settled paths)."""
    raw = params.get("attachments")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def rpc_table_selection(params: dict[str, Any]) -> list[str]:
    """startTurn / deliverMessage ``tableSelection`` → unique row ids (cap 40)."""
    from agentcore.table.context import sanitize_table_selection

    raw = params.get("tableSelection")
    if raw is None:
        raw = params.get("table_selection")
    if not isinstance(raw, list):
        return []
    return sanitize_table_selection([str(item) for item in raw])


def register_current_turn_run(
    *, conversation_id: str, sink: EventSink, user_id: str
) -> None:
    """Occupy ``turn_runs`` with the running sidecar task (same slot as cloud in-flight)."""
    task = asyncio.current_task()
    if task is None or not conversation_id:
        return
    from agentcore.runtime.turn.runs import turn_runs

    turn_runs.register(
        conversation_id=conversation_id, task=task, sink=sink, user_id=user_id
    )


def resolve_rpc_folder_binding(
    params: dict[str, Any],
) -> tuple[bool, str | None, str]:
    """Prefer RPC ``localRootId`` (+ optional ``localSubpath``); absent key ⇒ not injected.

    Same presence rule as ``folderId``: key present (including explicit null / \"\")
    means the desktop stamped a bind — do not open local PG for Folder.local_*.
    Returns ``(injected, local_root_id, local_subpath)``.
    """
    if "localRootId" not in params:
        return False, None, ""
    root = normalize_folder_id_param(params.get("localRootId"))
    subpath = (
        normalize_local_subpath_param(params.get("localSubpath"))
        if "localSubpath" in params
        else ""
    )
    return True, root, subpath


def apply_rpc_folder_binding_to_suspension(
    suspension: TurnSuspension, params: dict[str, Any]
) -> None:
    """Overlay resume RPC project scope / bind onto the claimed frame when re-sent.

    ``folderId`` key present (including explicit null) → overwrite ``suspension.folder_id``;
    key absent → keep frame value (old desktop). Same presence rule for ``localRootId``.
    """
    if "folderId" in params:
        suspension.folder_id = normalize_folder_id_param(params.get("folderId"))
    injected, root, subpath = resolve_rpc_folder_binding(params)
    if not injected:
        return
    suspension.folder_binding_injected = True
    suspension.folder_local_root_id = root
    suspension.folder_local_subpath = subpath


class FolderIdRequiredError(ValueError):
    """startTurn omitted the ``folderId`` key. Not a conversation lookup."""


def resolve_start_turn_folder_id(params: dict[str, Any]) -> str | None:
    """Require RPC ``folderId`` (null/blank = bare). Missing key is not a lookup."""
    if "folderId" not in params:
        raise FolderIdRequiredError(ClientTooOldError().message)
    return normalize_folder_id_param(params.get("folderId"))


def _finish_str(result: dict[str, Any]) -> str | None:
    finish = result.get("finish_reason")
    if finish is None:
        return None
    return finish.value if hasattr(finish, "value") else str(finish)


async def _remember_pause_meter(store: Any, message_id: str, result: dict[str, Any] | None) -> None:
    """Keep this message's active meter on the local pause file.

    Cloud resume reads ``messages.usage``. The sidecar has no message DB, so
    the next local resume continues from this stamp.
    """
    if store is None or result is None or _finish_str(result) != "paused":
        return
    await store.stamp_active_meter(message_id, ActiveMeter.from_usage(result).as_dict())


def _inference_search_creds(creds: Any):
    """Map turn ``LLMCredentials`` → leaf ``InferenceSearchCredentials`` (no llm import in web)."""
    from agentcore.tools.builtin.web.cloud_fallback import InferenceSearchCredentials

    if creds is None:
        return None
    return InferenceSearchCredentials(
        api_key=creds.api_key,
        base_url=creds.base_url,
        extra_headers=creds.extra_headers,
    )


def _salvage_interrupt_reason() -> str:
    """Map the sidecar cancel stamp onto interrupt ownership.

    Only an explicit ``user_stop`` stamp is a user stop. Other cancels are
    ``unknown`` — not ``lease_expired`` (the sweeper owns that name).
    """
    from agentcore.sidecar.server_pkg.cancel_mark import cancel_reason_from_task

    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    raw = cancel_reason_from_task(task)
    if raw == "user_stop":
        return "user_stop"
    return "unknown"


def _salvage_finish_reason() -> FinishReason:
    return finish_reason_for(normalize_interrupt_reason(_salvage_interrupt_reason()))


def _rpc_error_for_salvage_finish(
    finish: FinishReason,
) -> tuple[int, str, dict[str, Any] | None]:
    """JSON-RPC receipt follows stop-reason ownership, not CancelledError itself.

    Witnessed ``user_stop`` (finish cancelled) keeps ``TURN_CANCELLED``. Any other
    salvage finish is ``TURN_INTERRUPTED`` — never guessed as ``process_kill``.
    """
    if finish is FinishReason.CANCELLED:
        return protocol.TURN_CANCELLED, "turn cancelled", None
    return (
        protocol.TURN_INTERRUPTED,
        "turn interrupted",
        {"finish_reason": finish.value},
    )


def _emit_user_stop_message_end(sink: EventSink) -> None:
    """Live stop confirmation for the UI (``stopping`` → ``stopped``).

    Must run before ``sink.close()`` so the event pump still drains it. JSON-RPC
    ``TURN_CANCELLED`` alone is not enough — the renderer confirms on ``message_end``.
    Idempotent if ``turn_runs`` already emitted on ``mark_user_stop`` / ``stop``.
    """
    if sink._closed or sink._stream_finish_reason is not None:
        return
    with contextlib.suppress(Exception):
        sink.emit(message_end(FinishReason.CANCELLED))


def _emit_cancel_end_if_cancelling(sink: EventSink) -> None:
    """Emit terminal ``message_end`` when this task is unwinding from cancel."""
    task = asyncio.current_task()
    if task is None or not task.cancelling():
        return
    if sink._closed or sink._stream_finish_reason is not None:
        return
    with contextlib.suppress(Exception):
        sink.emit(message_end(_salvage_finish_reason()))


def _emit_hot_orphans_if_cancelling(sink: EventSink, conversation_id: str) -> None:
    """Emit ``interaction_orphaned`` for leftover hot cards on cancel unwind.

    Live UI also orphans via ``message_end`` → renderer ``clearInteractionPrompts``.
    These facts still need to land in the sink journal so salvage write-back does
    not leave a clickable ghost after reload. Registry Futures are not resolved
    here — the cancelling task already interrupts the awaiter.
    """
    task = asyncio.current_task()
    if task is None or not task.cancelling():
        return
    if sink._closed:
        return
    cid = (conversation_id or "").strip()
    if not cid:
        return
    from agentcore.runtime.events import interaction_orphaned
    from agentcore.runtime.interaction import (
        default_interaction_registry,
        is_hot_user_pending_kind,
    )

    registry = default_interaction_registry()
    for req in list(registry.list_pending(cid)):
        if not is_hot_user_pending_kind(req.kind.value, req.payload):
            continue
        with contextlib.suppress(Exception):
            sink.emit(
                interaction_orphaned(interaction_id=req.id, kind=req.kind.value)
            )


def _ensure_cancelled_turn_end(
    journal: list[dict[str, Any]] | None,
    finish_reason: str | FinishReason | None = None,
) -> list[dict[str, Any]]:
    """Sidecar salvage closer: ``turn_end`` plus leftover hot-card orphan facts.

    Cloud persist already appends ``turn_end`` in the interrupt path; local outbox
    salvage does not go through ``CloudStore.finalize``, so resume/startTurn cancel
    must close the journal here. Default cancelled keeps historical call sites.
    Leftover hot cards get ``interaction_orphaned`` in the same salvage payload so
    list GET / hydrate do not paint a clickable ghost.
    """
    finish = (
        finish_reason.value
        if isinstance(finish_reason, FinishReason)
        else (str(finish_reason).strip() if finish_reason else FinishReason.CANCELLED.value)
    )
    entries = list(journal or [])
    if not any((e.get("kind") or e.get("type") or "") == KIND_TURN_END for e in entries):
        seqs = [e.get("seq") for e in entries if isinstance(e.get("seq"), int)]
        next_seq = (max(seqs) + 1) if seqs else len(entries)
        entries.append(
            {
                "kind": KIND_TURN_END,
                "payload": {"finish_reason": finish},
                "ts": None,
                "seq": next_seq,
            }
        )
    return append_unrecorded_hot_orphan_facts(entries)


class TurnExecutionMixin:
    def _log_turn_cancelled(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        message_id: str | None,
        trace_id: str,
        content_chars: int,
        journal_entries: int,
        salvaged: bool,
    ) -> None:
        """Fingerprint CancelledError salvage (RPC stamp vs process/internal cancel)."""
        from agentcore.sidecar.server_pkg.cancel_mark import cancel_reason_from_task

        task = asyncio.current_task()
        logger.info(
            "sidecar.turn_cancelled",
            turn_id=turn_id,
            conversation_id=conversation_id or None,
            message_id=message_id,
            trace_id=trace_id or None,
            reason=cancel_reason_from_task(task),
            salvaged=salvaged,
            content_chars=content_chars,
            journal_entries=journal_entries,
        )

    async def _run_turn(self, request_id: Any, turn_id: str, params: dict[str, Any]) -> None:
        """Run one turn on the local engine; stream events; reply when done."""
        assert self._root is not None  # guarded by _on_start_turn
        conversation_id = str(params.get("conversationId") or turn_id)
        user_message = str(params.get("userMessage") or "")
        parsed_ids = parse_client_turn_ids(params)
        if parsed_ids is None:
            try:
                await self._reply_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "startTurn requires userMessageId, messageId, and 32-hex traceId",
                )
            finally:
                self._unregister_turn(turn_id)
            return
        user_message_id, message_id, trace_id = parsed_ids
        try:
            folder_id = resolve_start_turn_folder_id(params)
        except FolderIdRequiredError as exc:
            try:
                await self._reply_error(
                    request_id, protocol.INVALID_PARAMS, str(exc)
                )
            finally:
                self._unregister_turn(turn_id)
            return
        self._resolve_fifo_desktop_start(message_id)
        if str(params.get("queueId") or "").strip():
            self._mark_queue_turn(
                turn_id,
                user_message_id=user_message_id,
                message_id=message_id,
                trace_id=trace_id,
            )
        agent_mentions = rpc_agent_mentions(params)
        attachments = rpc_attachments(params)
        table_selection = rpc_table_selection(params)
        queue_id = str(params.get("queueId") or "").strip()
        # Occupy turn_runs before history fetch so deliverMessage matches startTurn.
        sink = EventSink()
        register_current_turn_run(
            conversation_id=conversation_id, sink=sink, user_id=self._user_id
        )
        from agentcore.conversation.history import drop_trailing_user_turn
        from agentcore.sidecar.chat_history import (
            ChatContextUnavailableError,
            resolve_sidecar_turn_history,
        )

        raw_history = params.get("history")
        desktop_confirmed = isinstance(raw_history, list)
        try:
            history = await resolve_sidecar_turn_history(
                conversation_id,
                creds=self._account_creds,
                fallback=raw_history if desktop_confirmed else None,
                # Desktop cookie window is the same endpoint; do not fetch twice.
                prefer_cloud=not desktop_confirmed,
            )
            # Cloud load may include this turn's user; desktop fetch may precede insert.
            history = drop_trailing_user_turn(
                history, only_if_content=user_message
            )
        except ChatContextUnavailableError as exc:
            logger.warning(
                "chat_context.sidecar_unavailable",
                conversation_id=conversation_id,
                turn_id=turn_id,
                error=exc.message,
            )
            try:
                await self._reply_error(
                    request_id, protocol.INTERNAL_ERROR, exc.message
                )
            finally:
                self._unregister_turn(turn_id)
            return
        self.stamp_turn_history(conversation_id, history)
        # Desktop mints the triple; sidecar must not new_id() assistant / trace.

        turn_creds = self._creds_for(conversation_id, trace_id, message_id)
        if queue_id:
            from agentcore.runtime.events import turn_queue_started
            from agentcore.runtime.turn.queue import turn_queue

            sink.emit(
                turn_queue_started(
                    queue_id=queue_id,
                    conversation_id=conversation_id,
                    remaining_depth=turn_queue.depth(conversation_id),
                    content=user_message,
                    user_message_id=user_message_id,
                    attachments=attachments or None,
                    agent_mentions=agent_mentions or None,
                )
            )
        backend = self._make_backend(external_mounts=params.get("externalMounts"))
        self._register_live_backend(conversation_id, backend)
        saver, deleter = self._suspension_hooks()
        session_saver, session_loader = self._session_hooks(conversation_id)
        outbox = self._outbox_store
        if outbox is not None:
            outbox.bind_turn(
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                user_message=user_message,
                message_id=message_id,
                trace_id=trace_id,
                agent_mentions=agent_mentions or None,
                attachments=attachments or None,
            )
            await outbox.begin_turn(
                conversation_id=conversation_id,
                message_id=message_id,
                trace_id=trace_id,
            )
            sink.bind_content_checkpoint(
                conversation_id=conversation_id,
                message_id=message_id,
            )
        # baseline / pipeline sit inside try so begin_turn OPEN cannot stick
        # when later steps fail.
        pump: asyncio.Task[None] | None = None
        result: dict[str, Any] | None = None
        try:
            # No inference JWT (probe-spawned sidecar / mint omitted) → fail before
            # prepare/build_turn_router with structured INFERENCE_TOKEN_EXPIRED so
            # outbox → local finalize can land usage.error (no English internal leak).
            if turn_creds is None:
                result = missing_inference_turn_result(message_id)
                structured = result["error"]
                assert isinstance(structured, dict)
                logger.warning(
                    "sidecar.inference_credentials_missing",
                    op="startTurn",
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                )
                pump = asyncio.create_task(
                    self._pump(turn_id, sink, conversation_id=conversation_id)
                )
                try:
                    sink.emit(
                        error_event(
                            str(structured["code"]),
                            str(structured["message"]),
                        )
                    )
                    sink.emit(message_end(FinishReason.ERROR))
                finally:
                    sink.close(reason="sidecar_missing_inference")
                await pump
                if outbox is not None:
                    await self._outbox_finalize(
                        outbox,
                        conversation_id=conversation_id,
                        user_message=user_message,
                        user_message_id=user_message_id,
                        trace_id=trace_id,
                        result=result,
                        user_created_this_send=True,
                    )
                await self._reply(
                    request_id,
                    trim_result(
                        turn_id,
                        result,
                        model=resolve_turn_model(None),
                    ),
                )
                return

            # Same for Folder local bind (explore workspace_key): desktop stamps
            # localRootId/localSubpath so assemble never HARD-fails on PG-down.
            binding_injected, folder_local_root_id, folder_local_subpath = (
                resolve_rpc_folder_binding(params)
            )
            self.stamp_folder_scope(
                conversation_id,
                folder_id=folder_id,
                binding_injected=binding_injected,
                local_root_id=folder_local_root_id,
                local_subpath=folder_local_subpath or "",
            )

            pump = asyncio.create_task(
                self._pump(turn_id, sink, conversation_id=conversation_id)
            )
            started = time.monotonic()
            _, latency_token = bind_turn_latency(started)
            try:
                # Bind the turn's trace_id here (the cloud binds it in stream_chat; the engine
                # itself doesn't) so the engine's message_start carries it and the live bubble
                # joins the same trace as the proxy logs + write-back (打通气泡↔日志, live ==
                # reload). Task-local + auto-restored; copied into delegated worker tasks.
                # cost_role=captain: stream.py only notes TTFT on the captain first stream.
                with log_context(
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    user_id=self._user_id,
                    message_id=message_id,
                    agent_id="CEO",
                    cost_role="captain",
                    persona="CEO",
                ):
                    # Align with cloud turn_runner chat.turn_start; via ≠ location.
                    logger.info(
                        "chat.turn_start",
                        chars=len(user_message or ""),
                        preview=preview(user_message),
                        history=len(history) if isinstance(history, list) else 0,
                        location=backend.location,
                        via="sidecar",
                        message_id=message_id,
                    )
                    from agentcore.account.credentials import account_credentials_scope
                    from agentcore.folders.credentials import folders_credentials_scope
                    from agentcore.sidecar import server as sidecar_server
                    from agentcore.tools.builtin.web.cloud_fallback import (
                        inference_search_credentials_scope,
                    )
                    from agentcore.workspace.cloud_credentials import (
                        workspaces_credentials_scope,
                    )

                    # Sidecar is spawned only by the desktop Electron host. Pass
                    # platform=desktop so prepare builds DesktopClientChannel and
                    # MCP/Host discover over the existing ClientTool fulfill path
                    # (docs/02-架构/工作区.md · Host / 本机回填). Never infer
                    # desktop_online from location=local.
                    # Bind inference JWT for web_search cloud fallback when local
                    # SearXNG is unreachable (ContextVar; reset after turn).
                    # Bind folders narrow ticket for roster / desk-binding cloud HTTP.
                    # Bind account narrow ticket for conversation-log search/read.
                    # Bind workspaces narrow ticket for unbound cloud-desk file HTTP.
                    with (
                        inference_search_credentials_scope(
                            _inference_search_creds(turn_creds)
                        ),
                        folders_credentials_scope(self._folders_creds),
                        account_credentials_scope(self._account_creds),
                        workspaces_credentials_scope(self._workspaces_creds),
                    ):
                        result = await sidecar_server.run_chat_pipeline(
                            conversation_id=conversation_id,
                            user_message=user_message,
                            history=list(history),
                            sink=sink,
                            user_id=self._user_id,
                            backend=backend,
                            folder_id=folder_id,
                            folder_binding_injected=binding_injected,
                            folder_local_root_id=folder_local_root_id,
                            folder_local_subpath=folder_local_subpath,
                            approvals_enabled=self._approvals_enabled,
                            permission_axes=self.permission_axes_for(conversation_id),
                            llm_credentials=turn_creds,
                            session_saver=session_saver,
                            session_loader=session_loader,
                            suspension_saver=saver,
                            suspension_deleter=deleter,
                            message_id=message_id,
                            x_client_platform="desktop",
                            agent_mentions=agent_mentions or None,
                            attachments=attachments or None,
                            table_selection=table_selection or None,
                        )
                        stamp_turn_wall(result)
                        await _remember_pause_meter(self._paused_store, message_id, result)
                        # Duration / Phase-0 close before the detached-drive hold —
                        # same wall-clock as cloud turn_runner (harvest wait is not TTFT).
                        log_chat_turn_complete(
                            result,
                            duration_ms=int((time.monotonic() - started) * 1000),
                            llm_credentials=turn_creds,
                        )
                        # Pillar D1: keep sink open while a detached background drive is
                        # still live so run_completed / execution_completed reach the UI
                        # and outbox READY is not sealed mid-DURABLE append. Cancel /
                        # exception skip this await and still close below.
                        from agentcore.runtime.coordination import await_live_detached_drive
                        from agentcore.runtime.pipeline.finalize import (
                            refresh_result_journal_from_host,
                        )

                        await await_live_detached_drive(conversation_id)
                        refresh_result_journal_from_host(result, sink=sink)
            finally:
                # Cancel confirmation while the probe is still bound so live
                # message_end carries the same whole-turn clock as persist.
                _emit_cancel_end_if_cancelling(sink)
                _emit_hot_orphans_if_cancelling(sink, conversation_id)
                reset_turn_latency(latency_token)
                # The pipeline no longer closes the sink (its owner does); the sidecar owns
                # this one, so close it on EVERY path — success or crash — or the pump would
                # await the None sentinel forever.
                sink.close(reason="sidecar_turn_finally")
            await pump  # sink closed above → all events flushed
            assert result is not None
            # finalize / READY only after close AND after any detached drive settled
            # (await above), so post-detach DURABLE journal appends are not dropped
            # by the outbox READY gate.
            if outbox is not None:
                await self._outbox_finalize(
                    outbox,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    user_message_id=user_message_id,
                    trace_id=trace_id,
                    result=result,
                    user_created_this_send=True,
                )
            # Surface the model this turn actually ran on (cloud-proxy / account model).
            await self._reply(
                request_id,
                trim_result(turn_id, result, model=resolve_turn_model(turn_creds)),
            )
        except asyncio.CancelledError:
            finish = _salvage_finish_reason()
            journal = _ensure_cancelled_turn_end(
                list(sink.execution_journal() or []),
                finish,
            )
            content = sink.streamed_content() or ""
            discarded = False
            if outbox is not None:
                discarded = await self._outbox_salvage_or_discard_this_send(
                    outbox,
                    conversation_id=conversation_id,
                    user_message_id=user_message_id,
                    message_id=message_id,
                    trace_id=trace_id,
                    sink=sink,
                    finish_reason=finish,
                    journal=journal,
                    content=content,
                    interrupt_reason=_salvage_interrupt_reason(),
                )
            self._log_turn_cancelled(
                turn_id=turn_id,
                conversation_id=conversation_id,
                message_id=message_id,
                trace_id=trace_id,
                content_chars=len(content),
                journal_entries=len(journal),
                salvaged=outbox is not None and not discarded,
            )
            # Reply first: a hung event pump must not delay the cancel RPC.
            code, message, data = _rpc_error_for_salvage_finish(finish)
            self._reply_error_soon(request_id, code, message, data=data)
            if pump is not None:
                with contextlib.suppress(Exception):
                    await pump
            else:
                with contextlib.suppress(Exception):
                    sink.close(reason="sidecar_turn_cancelled")
            raise
        except Exception as e:
            if outbox is not None:
                await self._outbox_salvage_or_discard_this_send(
                    outbox,
                    conversation_id=conversation_id,
                    user_message_id=user_message_id,
                    message_id=message_id,
                    trace_id=trace_id,
                    sink=sink,
                    finish_reason=FinishReason.ERROR,
                    journal=_ensure_cancelled_turn_end(
                        list(sink.execution_journal() or []),
                        FinishReason.ERROR,
                    ),
                    content=sink.streamed_content() or "",
                    error=str(e),
                )
            if pump is not None:
                with contextlib.suppress(Exception):
                    await pump
            else:
                with contextlib.suppress(Exception):
                    sink.close(reason="sidecar_turn_failed")
            logger.error("sidecar.turn_failed", turn_id=turn_id, error=str(e), exc_info=True)
            await self._reply_error(request_id, protocol.INTERNAL_ERROR, str(e))
        finally:
            closed = ""
            if result is not None:
                closed = str(result.get("content") or "")
            if not closed:
                closed = sink.streamed_content() or ""
            self._stamp_closed_turn(conversation_id, user_message, closed)
            if outbox is not None:
                outbox.clear_turn(message_id)
            self._unregister_live_backend(conversation_id, backend)
            self._unregister_turn(turn_id)

    async def _outbox_salvage_or_discard_this_send(
        self,
        outbox: Any,
        *,
        conversation_id: str,
        user_message_id: str,
        message_id: str,
        trace_id: str,
        sink: EventSink,
        finish_reason: object,
        journal: list[dict[str, Any]],
        content: str,
        interrupt_reason: str | None = None,
        error: str | None = None,
    ) -> bool:
        """This-send startTurn only: empty fail drops the outbox; otherwise salvage.

        Resume / continue keep calling ``outbox.salvage`` directly — those paths
        must not discard (``user_created_this_send=False``).
        """
        discarded = await maybe_discard_zero_output_outbox(
            outbox,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            result=result_from_unstarted_close(
                sink=sink,
                message_id=message_id,
                finish_reason=finish_reason,
            ),
            user_created_this_send=True,
        )
        if discarded:
            return True
        await outbox.salvage(
            journal=journal,
            content=content,
            conversation_id=conversation_id,
            trace_id=trace_id,
            message_id=message_id,
            interrupt_reason=interrupt_reason,
            error=error,
        )
        return False

    async def _outbox_finalize(
        self,
        outbox: Any,
        *,
        conversation_id: str,
        user_message: str,
        user_message_id: str,
        trace_id: str,
        result: dict[str, Any],
        origin: str | None = None,
        execution_id: str | None = None,
        harvest_kind: str | None = None,
        user_created_this_send: bool = False,
    ) -> None:
        """Seal the outbox record as ready for main-process writeback."""
        if await maybe_discard_zero_output_outbox(
            outbox,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            result=result,
            user_created_this_send=user_created_this_send,
        ):
            return
        journal_entries = result.get("journal_entries")
        error_type = result.get("error_type")
        if isinstance(error_type, str) and error_type.strip():
            journal_entries = attach_journal_error_type(
                journal_entries if isinstance(journal_entries, list) else None,
                error_type,
            )
        runs = runs_from_entries(journal_entries) if journal_entries else None
        finish = _finish_str(result)
        content = result.get("content") or ""
        # Empty cancelled must not write a blank product face: keep finish_reason
        # on runs so desktop syntheticErrorForEmptyFailure can paint the card.
        # (Do not expand into closing_posture / server 收口.)
        if finish == "cancelled" and not str(content).strip():
            if runs is None:
                runs = {"events": [], "finish_reason": "cancelled"}
            elif isinstance(runs, dict) and not runs.get("finish_reason"):
                runs = {**runs, "finish_reason": "cancelled"}
        await outbox.finalize(
            mode="local",
            conversation_id=conversation_id,
            user_message=user_message,
            user_message_id=user_message_id,
            assistant_content=content,
            assistant_reasoning=result.get("reasoning_content"),
            citations=result.get("citations") or [],
            evidence_ledger=result.get("evidence_ledger") or [],
            runs=runs,
            # Complete result journal replaces progressive mid-run map when present.
            journal_entries=journal_entries if isinstance(journal_entries, list) else None,
            message_id=result.get("message_id"),
            trace_id=trace_id,
            finish_reason=finish,
            origin=origin,
            execution_id=execution_id,
            harvest_kind=harvest_kind,
            **usage_settle_finalize_kwargs(result),
        )

    async def _outbox_resume_writeback(
        self,
        outbox: Any,
        *,
        conversation_id: str,
        message_id: str,
        trace_id: str,
    ) -> None:
        """No-op: resume journal rides desktop OPEN checkpoint POST.

        Do not expand ``persist_sidecar_journal_best_effort`` as the live path.
        Harvest / READY still settle via final local-turns drain.
        """
        del outbox, conversation_id, message_id, trace_id

    async def _run_resume(
        self,
        request_id: Any,
        suspension: TurnSuspension,
        decision: CheckpointDecision,
        note: str,
        selected: list[str],
        trace_id: str = "",
        user_message_id: str = "",
        external_mounts: list | dict | None = None,
        *,
        settlement_prewritten: bool = False,
        reply_ids: list[Any] | None = None,
    ) -> None:
        """Rebuild + finish a durably-paused turn; stream events; reply when done.

        D1: settlement is prewritten to the local outbox journal **before** the
        pipeline; on success the claimed frame is consumed immediately
        (:meth:`confirm_claim`). Pipeline failure after that does **not** restore
        the frame (decision card stays settled; user continues via a new message).

        ``settlement_prewritten``: deferred busy path already durable-wrote settlement
        before waiting for the slot — skip a second prewrite and confirm immediately.

        ``reply_ids``: deferred same-id joiners share this live list so every RPC
        receives the same final result/error (wire contract unchanged).
        """
        assert self._root is not None  # guarded by _on_resume
        turn_id = suspension.message_id
        conversation_id = suspension.conversation_id
        user_message = suspension.user_message or ""
        self.stamp_turn_history(conversation_id, suspension.history)
        # Prefer the client-pinned user bubble id; else the frame; else mint UUID.
        umid = resolve_resume_user_message_id(
            user_message_id,
            getattr(suspension, "user_message_id", None),
        )
        decision_value = decision.value if hasattr(decision, "value") else str(decision)
        # Resolved once so the pipeline runs on it AND the reply surfaces the same model.
        resume_creds = self._creds_for(conversation_id, trace_id, turn_id)
        if resume_creds is None:
            # Belt-and-suspenders: handler should have refused before claim. Roll back
            # so the pause card stays retryable after a remint.
            structured = structured_missing_inference_error()
            logger.warning(
                "sidecar.inference_credentials_missing",
                op="resume",
                turn_id=turn_id,
                conversation_id=conversation_id,
            )
            if self._paused_store is not None and not settlement_prewritten:
                await self._paused_store.rollback_claim(turn_id)
            await self._send_to_request_ids(
                reply_ids,
                request_id,
                lambda rid: protocol.make_error(
                    rid,
                    protocol.RESUME_RETRYABLE,
                    structured["message"],
                    data=structured,
                ),
            )
            self._unregister_turn(turn_id)
            return

        sink = EventSink()
        register_current_turn_run(
            conversation_id=conversation_id, sink=sink, user_id=self._user_id
        )
        backend = self._make_backend(external_mounts=external_mounts)
        self._register_live_backend(conversation_id, backend)
        saver, deleter = self._suspension_hooks()
        session_saver, session_loader = self._session_hooks(conversation_id)
        outbox = self._outbox_store
        settlement_durable = False
        if outbox is not None:
            outbox.bind_turn(
                conversation_id=conversation_id,
                user_message_id=umid,
                user_message=user_message,
                message_id=turn_id,
                trace_id=trace_id,
            )
            await outbox.reopen_for_resume(
                turn_id=turn_id,
                user_message_id=umid,
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
            await outbox.begin_turn(
                conversation_id=conversation_id,
                message_id=turn_id,
                trace_id=trace_id,
            )
            sink.bind_content_checkpoint(
                conversation_id=conversation_id,
                message_id=turn_id,
            )

        # D1: prewrite settlement → confirm_claim before any pipeline work.
        # Deferred busy path may have already prewritten before the slot wait.
        if settlement_prewritten and outbox is not None:
            if self._paused_store is not None:
                await self._paused_store.confirm_claim(turn_id)
            settlement_durable = True
        elif outbox is not None:
            try:
                from agentcore.sidecar.settlement_prewrite import (
                    prewrite_sidecar_resume_settlement,
                )

                await prewrite_sidecar_resume_settlement(
                    outbox,
                    suspension,
                    decision=decision_value,
                    note=note,
                    selected=selected,
                    user_message_id=umid,
                    trace_id=trace_id,
                )
            except Exception as e:
                err_msg = f"settlement prewrite failed: {e}"
                if self._paused_store is not None:
                    await self._paused_store.rollback_claim(turn_id)
                if outbox is not None:
                    outbox.clear_turn(turn_id)
                self._unregister_live_backend(conversation_id, backend)
                self._unregister_turn(turn_id)
                logger.warning(
                    "sidecar.resume_settlement_prewrite_failed",
                    turn_id=turn_id,
                    error=str(e),
                )
                await self._send_to_request_ids(
                    reply_ids,
                    request_id,
                    lambda rid: protocol.make_error(
                        rid,
                        protocol.RESUME_RETRYABLE,
                        err_msg,
                    ),
                )
                return
            if self._paused_store is not None:
                await self._paused_store.confirm_claim(turn_id)
            settlement_durable = True
        else:
            # No outbox ⇒ cannot durable-prewrite; keep legacy confirm-on-success.
            settlement_durable = False

        if outbox is not None and settlement_durable:
            await self._outbox_resume_writeback(
                outbox,
                conversation_id=conversation_id,
                message_id=turn_id,
                trace_id=trace_id,
            )

        pump = asyncio.create_task(
            self._pump(turn_id, sink, conversation_id=conversation_id)
        )
        result: dict[str, Any] | None = None
        started = time.monotonic()
        meter = ActiveMeter.from_usage(suspension.active_meter)
        _, latency_token = bind_turn_latency(
            started,
            carried_duration_ms=meter.duration_ms,
            carried_generation_ms=meter.generation_ms,
            first_stream_done=meter.first_stream_done,
        )
        meter_token = bind_active_meter(meter)
        try:
            try:
                # Bind this continuation's trace_id (same rationale as _run_turn) so the
                # resumed reply's message_start + local logs join its proxy logs + write-back.
                with log_context(
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    user_id=self._user_id,
                ):
                    from agentcore.account.credentials import account_credentials_scope
                    from agentcore.folders.credentials import folders_credentials_scope
                    from agentcore.sidecar import server as sidecar_server
                    from agentcore.tools.builtin.web.cloud_fallback import (
                        inference_search_credentials_scope,
                    )
                    from agentcore.workspace.cloud_credentials import (
                        workspaces_credentials_scope,
                    )

                    with (
                        inference_search_credentials_scope(
                            _inference_search_creds(resume_creds)
                        ),
                        folders_credentials_scope(self._folders_creds),
                        account_credentials_scope(self._account_creds),
                        workspaces_credentials_scope(self._workspaces_creds),
                    ):
                        result = await sidecar_server.resume_chat_pipeline(
                            suspension=suspension,
                            decision=decision,
                            note=note,
                            selected=selected,
                            sink=sink,
                            backend=backend,
                            # Sidecar has no message DB: prior-turn history rides in the
                            # local frame (rehydrated at claim); resume splices it ahead
                            # of the journal-folded rounds (Phase 2 ⑤).
                            history=suspension.history,
                            llm_credentials=resume_creds,
                            session_saver=session_saver,
                            session_loader=session_loader,
                            suspension_saver=saver,
                            suspension_deleter=deleter,
                            permission_axes=self.permission_axes_for(conversation_id),
                            # Same desktop channel as fresh turns — omit ⇒ resume drops MCP/Host.
                            x_client_platform="desktop",
                        )
                        stamp_turn_wall(result)
                        await _remember_pause_meter(self._paused_store, turn_id, result)
                        # Same D1 hold as _run_turn: delay close while detached drive lives.
                        from agentcore.runtime.coordination import await_live_detached_drive
                        from agentcore.runtime.pipeline.finalize import (
                            refresh_result_journal_from_host,
                        )

                        await await_live_detached_drive(conversation_id)
                        refresh_result_journal_from_host(result, sink=sink)
            finally:
                _emit_cancel_end_if_cancelling(sink)
                _emit_hot_orphans_if_cancelling(sink, conversation_id)
                reset_turn_latency(latency_token)
                reset_active_meter(meter_token)
                # The pipeline no longer closes the sink (its owner does); the sidecar owns
                # this one, so close it on EVERY path — success or crash — or the pump would
                # await the None sentinel forever.
                sink.close(reason="sidecar_resume_finally")
            await pump  # sink closed above → all events flushed
            assert result is not None
            # finalize / READY only after close AND after any detached drive settled.
            if outbox is not None:
                await self._outbox_finalize(
                    outbox,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    user_message_id=umid,
                    trace_id=trace_id,
                    result=result,
                )
            # Same model signal as a start turn (see _run_turn): the resumed reply reports
            # the model it actually ran on so the badge stays honest across a resume.
            model = resolve_turn_model(resume_creds)
            await self._send_to_request_ids(
                reply_ids,
                request_id,
                lambda rid: protocol.make_result(
                    rid, trim_result(turn_id, result, model=model)
                ),
            )
        except asyncio.CancelledError:
            # Settlement already durable ⇒ do not restore the decision card.
            if not settlement_durable and self._paused_store is not None:
                await self._paused_store.rollback_claim(turn_id)
            # G8: streamed_content is live-only; join hang-frame pre_pause.
            # Journal: merge hang-frame process_* with live (symmetric to content).
            from agentcore.conversation.turn_persistence import (
                compose_salvage_content,
                compose_salvage_journal,
            )

            finish = _salvage_finish_reason()
            journal = _ensure_cancelled_turn_end(
                compose_salvage_journal(
                    sink.execution_journal() or [],
                    suspension.journal_entries,
                ),
                finish,
            )
            content = compose_salvage_content(
                sink.streamed_content() or "",
                suspension.journal_entries,
            )
            if outbox is not None:
                await outbox.salvage(
                    journal=journal,
                    content=content,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    message_id=turn_id,
                    interrupt_reason=_salvage_interrupt_reason(),
                )
            self._log_turn_cancelled(
                turn_id=turn_id,
                conversation_id=conversation_id,
                message_id=turn_id,
                trace_id=trace_id,
                content_chars=len(content or ""),
                journal_entries=len(journal or []),
                salvaged=outbox is not None,
            )
            # Reply first: a hung event pump must not delay the cancel RPC.
            code, message, data = _rpc_error_for_salvage_finish(finish)
            self._send_soon_to_request_ids(
                reply_ids,
                request_id,
                lambda rid: protocol.make_error(rid, code, message, data=data),
            )
            with contextlib.suppress(Exception):
                await pump
            raise
        except Exception as e:
            if not settlement_durable and self._paused_store is not None:
                await self._paused_store.rollback_claim(turn_id)
            if outbox is not None:
                from agentcore.conversation.turn_persistence import (
                    compose_salvage_content,
                    compose_salvage_journal,
                )

                await outbox.salvage(
                    journal=_ensure_cancelled_turn_end(
                        compose_salvage_journal(
                            sink.execution_journal() or [],
                            suspension.journal_entries,
                        ),
                        FinishReason.ERROR,
                    ),
                    content=compose_salvage_content(
                        sink.streamed_content() or "",
                        suspension.journal_entries,
                    ),
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    message_id=turn_id,
                    error=str(e),
                )
            with contextlib.suppress(Exception):
                await pump
            err_msg = str(e)
            logger.error("sidecar.resume_failed", turn_id=turn_id, error=err_msg, exc_info=True)
            # After settlement, failure does not restore the frame for retry.
            err_code = protocol.INTERNAL_ERROR if settlement_durable else protocol.RESUME_RETRYABLE
            await self._send_to_request_ids(
                reply_ids,
                request_id,
                lambda rid: protocol.make_error(rid, err_code, err_msg),
            )
        else:
            if not settlement_durable and self._paused_store is not None:
                await self._paused_store.confirm_claim(turn_id)
        finally:
            closed = ""
            if result is not None:
                closed = str(result.get("content") or "")
            if not closed:
                closed = sink.streamed_content() or ""
            self._stamp_closed_turn(conversation_id, user_message, closed)
            if outbox is not None:
                outbox.clear_turn(turn_id)
            self._unregister_live_backend(conversation_id, backend)
            self._unregister_turn(turn_id)

    async def _pump(
        self, turn_id: str, sink: EventSink, *, conversation_id: str = ""
    ) -> None:
        """Drain the turn's EventSink, emitting each event as a notification.

        Mirrors the SSE layer's ``_event_generator`` consumer: pull until the sink
        is closed (``None``), forwarding every event verbatim. ``StrEnum`` values in
        the payload (``EventType`` / ``FinishReason``) serialize as plain strings.

        ``conversationId`` rides with ``turnId`` so harvest (no desktop-minted
        startTurn slot) can still address the live conversation. Desktop may
        ignore the extra field on user-started turns.

        FIFO drain marks the turn in ``_queue_turns``; those notifications carry
        ``origin=queue`` plus the client id triple so desktop registers a live
        user turn (not harvest ephemeral).
        """
        cid = (conversation_id or self._turn_conversations.get(turn_id) or "").strip()
        queue_meta = self._queue_turns.get(turn_id)
        while True:
            event = await sink.get()
            if event is None:
                return
            params: dict[str, Any] = {
                "turnId": turn_id,
                "conversationId": cid,
                "event": {
                    "type": event.type.value,
                    "timestamp": event.timestamp,
                    "payload": event.payload,
                },
            }
            if queue_meta:
                params["origin"] = "queue"
                params.update(queue_meta)
            await self._send(protocol.make_notification("turn/event", params))
