"""Sidecar turn execution: run, resume, event pump + outbox finalize."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from agentcore.conversation.common import preview
from agentcore.conversation.zero_output_rollback import maybe_discard_zero_output_outbox
from agentcore.core.errors import InferenceTokenExpiredError
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.llm.resolve import resolve_turn_model
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, FinishReason, error_event, message_end
from agentcore.runtime.journal import KIND_TURN_END, runs_from_entries
from agentcore.runtime.suspension import TurnSuspension
from agentcore.sidecar import protocol
from agentcore.sidecar.server_pkg.result import trim_result

logger = get_logger(__name__)


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
    from agentcore.conversation.mentions import to_stored_agent_mentions

    raw = params.get("agentMentions")
    if raw is None:
        raw = params.get("agent_mentions")
    return to_stored_agent_mentions(raw if isinstance(raw, list) else None)


def rpc_ask_id(params: dict[str, Any]) -> str | None:
    """startTurn ``askId`` / ``ask_id`` → inbound return-path slot (or None)."""
    from agentcore.conversation.ask_reply import normalize_ask_id

    raw = params.get("askId")
    if raw is None:
        raw = params.get("ask_id")
    return normalize_ask_id(raw)


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


async def load_conversation_folder_id(conversation_id: str) -> str | None:
    """Same shape as cloud ``conversation/turns.py``: ``conv.folder_id`` via unscoped get.

    Missing conversation → ``None`` (bare/global). Resume inherits via
    ``suspension.folder_id`` written on the start-turn pause path.

    DB unreachable → ``DatabaseUnavailableError`` (honest fail). Never invent a
    cached ``folder_id`` or silently continue as bare chat (wrong project scope).
    """
    from agentcore.db.base import async_session_factory
    from agentcore.db.errors import reraise_as_database_unavailable
    from agentcore.db.repositories import ConversationRepository

    try:
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
            if not conv:
                return None
            raw = getattr(conv, "folder_id", None)
            return normalize_folder_id_param(raw)
    except Exception as e:
        reraise_as_database_unavailable(e)
        raise


async def resolve_start_turn_folder_id(
    params: dict[str, Any], conversation_id: str
) -> str | None:
    """Prefer RPC ``params.folderId``; only hit local PG when the key is absent (old desktop).

    Key present (including explicit ``null`` / ``""``) → normalize, no DB.
    Key missing → ``load_conversation_folder_id`` (compat); connect refuse stays honest fail.
    """
    if "folderId" in params:
        return normalize_folder_id_param(params.get("folderId"))
    return await load_conversation_folder_id(conversation_id)


def _finish_str(result: dict[str, Any]) -> str | None:
    finish = result.get("finish_reason")
    if finish is None:
        return None
    return finish.value if hasattr(finish, "value") else str(finish)


async def _settle_ask_replies_if_committed(
    *,
    conversation_id: str,
    result: dict[str, Any] | None,
    sink: EventSink,
    user_message: str,
    ask_id: str | None = None,
    user_created_this_send: bool = True,
) -> None:
    """Settle hanging asks after this sidecar send stuck (before sink.close)."""
    from agentcore.conversation.question_resolve import (
        is_abort_finish_reason,
        note_ask_replies_for_committed_send,
    )
    from agentcore.conversation.zero_output_rollback import (
        should_delete_zero_output_send_result,
    )

    if result is None:
        return
    if should_delete_zero_output_send_result(
        result, user_created_this_send=user_created_this_send
    ):
        return
    if is_abort_finish_reason(_finish_str(result)):
        return
    journal = result.get("journal_entries")
    await note_ask_replies_for_committed_send(
        conversation_id=conversation_id,
        sink=sink,
        ask_id=ask_id,
        answer=user_message,
        journal=journal if isinstance(journal, list) else None,
    )


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
    """Map the sidecar cancel stamp onto interrupt-body silence vs honesty.

    Only an explicit ``user_stop`` stamp stays silent when nothing streamed.
    Process / unspecified / abort cancels owe the user a sentence.
    """
    from agentcore.sidecar.server_pkg.cancel_mark import cancel_reason_from_task

    raw = cancel_reason_from_task(asyncio.current_task())
    if raw == "user_stop":
        return "user_stop"
    return "lease_expired"


def _emit_user_stop_message_end(sink: EventSink) -> None:
    """Live stop confirmation for the UI (honest ``stopping`` → ``stopped``).

    Must run before ``sink.close()`` so the event pump still drains it. JSON-RPC
    ``TURN_CANCELLED`` alone is not enough — the renderer confirms on ``message_end``.
    """
    if sink._closed:
        return
    with contextlib.suppress(Exception):
        sink.emit(message_end(FinishReason.CANCELLED))


def _emit_cancel_end_if_cancelling(sink: EventSink) -> None:
    """Emit terminal ``message_end`` when this task is unwinding from cancel."""
    task = asyncio.current_task()
    if task is None or not task.cancelling():
        return
    _emit_user_stop_message_end(sink)


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
        except ChatContextUnavailableError as exc:
            logger.warning(
                "chat_context.sidecar_unavailable",
                conversation_id=conversation_id,
                turn_id=turn_id,
                error=exc.message,
            )
            try:
                await self._send(
                    protocol.make_error(request_id, protocol.INTERNAL_ERROR, exc.message)
                )
            finally:
                self._unregister_turn(turn_id)
            return
        self.stamp_turn_history(conversation_id, history)
        agent_mentions = rpc_agent_mentions(params)
        ask_id = rpc_ask_id(params)
        # The desktop mints one trace_id per local turn and threads it here + into the
        # write-back, so this turn's proxied LLM calls and its persisted reply share it.
        trace_id = str(params.get("traceId") or "")
        # Optimistic user bubble id — outbox idempotency anchor (as-built: 双模式工作区 §10.3).
        user_message_id = str(params.get("userMessageId") or "").strip() or new_id()
        # mint assistant message_id up front (cloud turn_runner posture) so begin_turn /
        # content checkpoints / journal share one id before the pipeline runs.
        message_id = new_id()

        turn_creds = self._creds_for(conversation_id, trace_id, message_id)

        sink = EventSink()
        backend = self._make_backend(external_mounts=params.get("externalMounts"))
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
        # folder_id / baseline / pipeline sit inside try so begin_turn OPEN cannot
        # stick forever when DB is down on the legacy fallback path (方案一 · 诚实失败).
        pump: asyncio.Task[None] | None = None
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
                    await _settle_ask_replies_if_committed(
                        conversation_id=conversation_id,
                        result=result,
                        sink=sink,
                        user_message=user_message,
                        ask_id=ask_id,
                    )
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
                await self._send(
                    protocol.make_result(
                        request_id,
                        trim_result(
                            turn_id,
                            result,
                            model=resolve_turn_model(None),
                        ),
                    )
                )
                return

            # Prefer params.folderId (desktop inject); key absent → DB load (old desktop).
            # Explicit null/"" = bare chat — do not open local PG just to learn that.
            folder_id = await resolve_start_turn_folder_id(params, conversation_id)
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

            # A1+ local：message_id mint + begin_turn 后、pipeline 前打本机基线（resume 不重打）。
            from agentcore.workspace.turn_baseline import maybe_capture_turn_baseline

            await maybe_capture_turn_baseline(
                user_id=self._user_id,
                folder_id=folder_id,
                conversation_id=conversation_id,
                message_id=message_id,
                backend=backend,
                workspace_root=self._root,
            )
            pump = asyncio.create_task(
                self._pump(turn_id, sink, conversation_id=conversation_id)
            )
            try:
                # Bind the turn's trace_id here (the cloud binds it in stream_chat; the engine
                # itself doesn't) so the engine's message_start carries it and the live bubble
                # joins the same trace as the proxy logs + write-back (打通气泡↔日志, live ==
                # reload). Task-local + auto-restored; copied into delegated worker tasks.
                with log_context(
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    user_id=self._user_id,
                    message_id=message_id,
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

                    # Sidecar is spawned only by the desktop Electron host. Pass
                    # platform=desktop so prepare builds DesktopClientChannel and
                    # MCP/Host discover over the existing ClientTool fulfill path
                    # (docs/02-架构/双模式工作区.md · Host / 本机回填). Never infer
                    # desktop_online from location=local.
                    # Bind inference JWT for web_search cloud fallback when local
                    # SearXNG is unreachable (ContextVar; reset after turn).
                    # Bind folders narrow ticket for roster / desk-binding cloud HTTP.
                    # Bind account narrow ticket for conversation-log search/read.
                    with (
                        inference_search_credentials_scope(
                            _inference_search_creds(turn_creds)
                        ),
                        folders_credentials_scope(self._folders_creds),
                        account_credentials_scope(self._account_creds),
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
                            ask_id=ask_id,
                        )
                        # Pillar D1: keep sink open while a detached background drive is
                        # still live so run_completed / execution_completed reach the UI
                        # and outbox READY is not sealed mid-DURABLE append. Cancel /
                        # exception skip this await and still close below.
                        from agentcore.runtime.coordination import await_live_detached_drive

                        await await_live_detached_drive(conversation_id)
                        await _settle_ask_replies_if_committed(
                            conversation_id=conversation_id,
                            result=result,
                            sink=sink,
                            user_message=user_message,
                            ask_id=ask_id,
                        )
            finally:
                # Cancel path: emit confirmation *before* close so the pump still
                # delivers ``message_end(cancelled)`` (TURN_CANCELLED alone is not enough).
                _emit_cancel_end_if_cancelling(sink)
                # The pipeline no longer closes the sink (its owner does); the sidecar owns
                # this one, so close it on EVERY path — success or crash — or the pump would
                # await the None sentinel forever.
                sink.close(reason="sidecar_turn_finally")
            await pump  # sink closed above → all events flushed
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
            await self._send(
                protocol.make_result(
                    request_id,
                    trim_result(turn_id, result, model=resolve_turn_model(turn_creds)),
                )
            )
        except asyncio.CancelledError:
            journal = list(sink.execution_journal() or [])
            content = sink.streamed_content() or ""
            if outbox is not None:
                await outbox.salvage(
                    journal=journal,
                    content=content,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    message_id=message_id,
                    interrupt_reason=_salvage_interrupt_reason(),
                )
            self._log_turn_cancelled(
                turn_id=turn_id,
                conversation_id=conversation_id,
                message_id=message_id,
                trace_id=trace_id,
                content_chars=len(content),
                journal_entries=len(journal),
                salvaged=outbox is not None,
            )
            if pump is not None:
                with contextlib.suppress(Exception):
                    await pump
            else:
                with contextlib.suppress(Exception):
                    sink.close(reason="sidecar_turn_cancelled")
            # Reply on an independent task: this one is unwinding from cancellation.
            self._send_soon(
                protocol.make_error(request_id, protocol.TURN_CANCELLED, "turn cancelled")
            )
            raise
        except Exception as e:
            if outbox is not None:
                await outbox.salvage(
                    journal=list(sink.execution_journal() or []),
                    content=sink.streamed_content() or "",
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    message_id=message_id,
                    interrupt_reason="lease_expired",
                )
            if pump is not None:
                with contextlib.suppress(Exception):
                    await pump
            else:
                with contextlib.suppress(Exception):
                    sink.close(reason="sidecar_turn_failed")
            logger.error("sidecar.turn_failed", turn_id=turn_id, error=str(e), exc_info=True)
            await self._send(protocol.make_error(request_id, protocol.INTERNAL_ERROR, str(e)))
        finally:
            if outbox is not None:
                outbox.clear_turn(message_id)
            self._unregister_turn(turn_id)

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
            runs=runs,
            # Complete result journal replaces progressive mid-run map when present.
            journal_entries=journal_entries if isinstance(journal_entries, list) else None,
            message_id=result.get("message_id"),
            input_tokens=int(result.get("input_tokens", 0) or 0),
            output_tokens=int(result.get("output_tokens", 0) or 0),
            reasoning_tokens=int(result.get("reasoning_tokens", 0) or 0),
            cache_hit_tokens=int(result.get("cache_hit_tokens", 0) or 0),
            cache_miss_tokens=int(result.get("cache_miss_tokens", 0) or 0),
            rounds=int(result.get("rounds", 0) or 0),
            trace_id=trace_id,
            finish_reason=finish,
            origin=origin,
            execution_id=execution_id,
            harvest_kind=harvest_kind,
        )

    async def _outbox_resume_writeback(
        self,
        outbox: Any,
        *,
        conversation_id: str,
        message_id: str,
        trace_id: str,
    ) -> None:
        """Best-effort PG replace of the current outbox journal. Must not raise.

        Symmetric with pause ``_outbox_finalize`` → local-turns persist: after
        settlement is durable, land hang-frame + ``*_resolved`` so a hard refresh
        does not keep the pause hang-frame. Failure must not block 开工.
        """
        from agentcore.conversation.store.outbox import journal_entries_from_map
        from agentcore.runtime.journal.persist import persist_sidecar_journal_best_effort

        try:
            record = outbox.find_record_by_message_id(message_id)
            entries = (
                journal_entries_from_map(record.get("journal")) if record else None
            )
            await persist_sidecar_journal_best_effort(
                message_id=message_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                entries=entries,
            )
        except Exception as e:  # noqa: BLE001 — resume start must not wait on PG
            logger.warning(
                "journal.persist_failed",
                message_id=message_id,
                error=str(e),
            )

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
        excluded_run_ids: list[str] | None = None,
        write_capability_overrides: list[dict[str, str]] | None = None,
        model_overrides: dict[str, dict[str, str]] | None = None,
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

        ``excluded_run_ids`` / ``write_capability_overrides`` / ``model_overrides``
        mirror cloud POST resume (开工组队有限否决 + 人盖模型) through settlement
        prewrite → resume pipeline.
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
        excluded = list(excluded_run_ids or [])
        overrides = list(write_capability_overrides or [])
        models = dict(model_overrides or {})
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
        backend = self._make_backend(external_mounts=external_mounts)
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
                    excluded_run_ids=excluded,
                    write_capability_overrides=overrides,
                    model_overrides=models,
                )
            except Exception as e:
                err_msg = f"settlement prewrite failed: {e}"
                if self._paused_store is not None:
                    await self._paused_store.rollback_claim(turn_id)
                if outbox is not None:
                    outbox.clear_turn(turn_id)
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

                    with (
                        inference_search_credentials_scope(
                            _inference_search_creds(resume_creds)
                        ),
                        folders_credentials_scope(self._folders_creds),
                        account_credentials_scope(self._account_creds),
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
                            excluded_run_ids=excluded,
                            write_capability_overrides=overrides,
                            model_overrides=models,
                        )
                        # Same D1 hold as _run_turn: delay close while detached drive lives.
                        from agentcore.runtime.coordination import await_live_detached_drive

                        await await_live_detached_drive(conversation_id)
                        await _settle_ask_replies_if_committed(
                            conversation_id=conversation_id,
                            result=result,
                            sink=sink,
                            user_message=user_message,
                            user_created_this_send=False,
                        )
            finally:
                _emit_cancel_end_if_cancelling(sink)
                # The pipeline no longer closes the sink (its owner does); the sidecar owns
                # this one, so close it on EVERY path — success or crash — or the pump would
                # await the None sentinel forever.
                sink.close(reason="sidecar_resume_finally")
            await pump  # sink closed above → all events flushed
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

            journal = compose_salvage_journal(
                sink.execution_journal() or [],
                suspension.journal_entries,
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
            with contextlib.suppress(Exception):
                await pump
            self._send_soon_to_request_ids(
                reply_ids,
                request_id,
                lambda rid: protocol.make_error(
                    rid, protocol.TURN_CANCELLED, "turn cancelled"
                ),
            )
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
                    journal=compose_salvage_journal(
                        sink.execution_journal() or [],
                        suspension.journal_entries,
                    ),
                    content=compose_salvage_content(
                        sink.streamed_content() or "",
                        suspension.journal_entries,
                    ),
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    message_id=turn_id,
                    interrupt_reason="lease_expired",
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
            if outbox is not None:
                outbox.clear_turn(turn_id)
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
        """
        cid = (conversation_id or self._turn_conversations.get(turn_id) or "").strip()
        while True:
            event = await sink.get()
            if event is None:
                return
            await self._send(
                protocol.make_notification(
                    "turn/event",
                    {
                        "turnId": turn_id,
                        "conversationId": cid,
                        "event": {
                            "type": event.type.value,
                            "timestamp": event.timestamp,
                            "payload": event.payload,
                        },
                    },
                )
            )
