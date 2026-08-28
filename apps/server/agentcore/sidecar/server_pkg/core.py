"""SidecarServer core: transport dispatch and outbound I/O."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from agentcore.account.credentials import AccountCredentials
from agentcore.conversation.store.outbox import OutboxStore
from agentcore.core.logging import get_logger
from agentcore.core.types import DEFAULT_PERMISSION_AXES, PermissionAxes
from agentcore.folders.credentials import FoldersCredentials
from agentcore.llm.credentials import (
    INFERENCE_CONVERSATION_HEADER,
    INFERENCE_TRACE_HEADER,
    LLMCredentials,
)
from agentcore.runtime.suspension import TurnSuspension
from agentcore.sidecar import protocol
from agentcore.sidecar.fulfill_bridge import SidecarFulfillBridge
from agentcore.sidecar.paused_store import LocalPausedTurnStore
from agentcore.sidecar.run_session_store import LocalRunSessionStore
from agentcore.sidecar.server_pkg.delivery import DeliveryMixin
from agentcore.sidecar.server_pkg.handlers import HandlerMixin
from agentcore.sidecar.server_pkg.turns import TurnExecutionMixin
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

logger = get_logger(__name__)

_active_sidecar: SidecarServer | None = None
# True after this process installed a sidecar. Shutdown may clear the pointer
# while harvest still needs to know "do not fall into local PG".
_sidecar_process: bool = False


@dataclass(frozen=True, slots=True)
class SidecarFolderScope:
    """startTurn / resume folder bind, readable by harvest without opening local PG."""

    folder_id: str | None
    binding_injected: bool
    local_root_id: str | None
    local_subpath: str


ResumeBusyReason = Literal["wrap_up", "live_turn"]


@dataclass
class SidecarResumeDeferredWaiter:
    """Cold resume parked until the conversation slot frees (对齐云端 ResumeDeferredWaiter).

    Same ``message_id`` re-submits append to ``reply_ids`` (idempotent join).
    A different ``message_id`` supersedes (prior ``slot_free`` cancelled).
    """

    conversation_id: str
    message_id: str
    busy_reason: ResumeBusyReason
    slot_free: asyncio.Future[None] = field(repr=False)
    # RPC ids that must receive the same final resume reply (primary + same-id joins).
    reply_ids: list[Any] = field(default_factory=list)


class SidecarServer(HandlerMixin, DeliveryMixin, TurnExecutionMixin):
    """Routes inbound JSON-RPC lines to the engine and streams events back out."""

    def __init__(self, write_line: Callable[[str], Awaitable[None]]) -> None:
        self._write_line = write_line
        # Set by ``initialize``; until then every turn-bearing method is refused.
        self._initialized = False
        self._user_id = ""
        self._root: Path | None = None
        self._creds: LLMCredentials | None = None
        # Folders narrow-ticket creds (``{baseUrl, apiKey}``); refreshed per turn.
        self._folders_creds: FoldersCredentials | None = None
        # Account narrow-ticket creds for conversation-log tools; refreshed per turn.
        self._account_creds: AccountCredentials | None = None
        self._approvals_enabled = True
        # Initialize-seeded default. Per-turn refresh writes ``_permission_axes_by_conv``
        # so harvest reads this conversation, not the process-level last write.
        self._permission_axes: PermissionAxes = DEFAULT_PERMISSION_AXES
        self._permission_axes_by_conv: dict[str, PermissionAxes] = {}
        # The local durable-pause store (§8.6 paused-turn port, local impl), set from
        # ``initialize``'s ``dataDir``. ``None`` ⇒ no data dir ⇒ pauses stay in-memory.
        self._paused_store: LocalPausedTurnStore | None = None
        # Local durable 留人 roster (cloud ``run_sessions`` counterpart). ``None`` ⇒
        # memory-only; continuation must not claim「落盘未命中」.
        self._run_session_store: LocalRunSessionStore | None = None
        # Progressive outbox (as-built: 双模式工作区 §10.3): sibling of paused under dataDir.
        # ``None`` ⇒ no data dir ⇒ no local outbox (dev without durable write-back).
        self._outbox_store: OutboxStore | None = None
        # startTurn / resume folder bind, keyed by conversation_id (harvest reads
        # this instead of ``resolve_local_binding(db)``).
        self._folder_scope: dict[str, SidecarFolderScope] = {}
        # Recent chat history from startTurn / resume, same lifetime as folder_scope.
        self._turn_history: dict[str, list[Any]] = {}
        # turn_id → running task, so ``cancel`` can reach an in-flight turn. A resume
        # registers under its message_id, so a cancel during resume reaches it too.
        self._turns: dict[str, asyncio.Task[None]] = {}
        # turn_id → conversation_id (cancel cascades coordination without FE always
        # repeating conversationId; cleared with ``_turns``).
        self._turn_conversations: dict[str, str] = {}
        # FIFO-started user turns: envelope ``origin=queue`` + client ids for desktop.
        self._queue_turns: dict[str, dict[str, str]] = {}
        # message_id → drain waiter. Desktop ``startTurn`` (occupy first) unblocks.
        self._fifo_desktop_start: dict[str, asyncio.Future[None]] = {}
        # turn_id → monotonic deadline. Cancel of an unknown turnId (desktop is
        # still awaiting in-flight warm before startTurn RPC) so startTurn refuses.
        self._cancel_tombstones: dict[str, float] = {}
        # Cold resume × live: at most one deferred waiter per conversation.
        # Same message_id joins; a different message_id supersedes (last click wins).
        self._resume_deferred: dict[str, SidecarResumeDeferredWaiter] = {}
        # Fire-and-forget sends spawned during cancellation; kept referenced so
        # they are not garbage-collected before they flush.
        self._pending_sends: set[asyncio.Task[None]] = set()
        # This process's CLIENT_TOOL 履约方 on the in-process fulfill hub. Built on
        # ``initialize`` (needs a running loop + the account id); without it every
        # host / mcp / notify / board / terminal op would fail「无履约方」.
        self._fulfill_bridge: SidecarFulfillBridge | None = None
        # Flipped by ``shutdown`` so the process loop can exit cleanly.
        self.shutdown_requested = asyncio.Event()

    def _register_turn(
        self, turn_id: str, task: asyncio.Task[None], *, conversation_id: str
    ) -> None:
        self._turns[turn_id] = task
        cid = (conversation_id or "").strip()
        if cid:
            self._turn_conversations[turn_id] = cid

    def _mark_queue_turn(
        self,
        turn_id: str,
        *,
        user_message_id: str,
        message_id: str,
        trace_id: str,
    ) -> None:
        self._queue_turns[turn_id] = {
            "userMessageId": user_message_id,
            "messageId": message_id,
            "traceId": trace_id,
        }

    def _resolve_fifo_desktop_start(
        self, message_id: str, exc: BaseException | None = None
    ) -> None:
        fut = self._fifo_desktop_start.pop(message_id, None)
        if fut is None or fut.done():
            return
        if exc is None:
            fut.set_result(None)
        else:
            fut.set_exception(exc)

    def _unregister_turn(self, turn_id: str) -> None:
        cid = self._turn_conversations.get(turn_id, "")
        self._turns.pop(turn_id, None)
        self._turn_conversations.pop(turn_id, None)
        self._queue_turns.pop(turn_id, None)
        if cid:
            self._wake_resume_deferred_if_idle(cid)

    def live_turn_task(self, conversation_id: str) -> asyncio.Task[None] | None:
        """Live ``_turns`` task holding ``conversation_id``, if any."""
        cid = (conversation_id or "").strip()
        if not cid:
            return None
        for tid, task in self._turns.items():
            if task.done():
                continue
            if self._turn_conversations.get(tid) == cid:
                return task
        return None

    def folder_scope_for(self, conversation_id: str) -> SidecarFolderScope | None:
        cid = (conversation_id or "").strip()
        if not cid:
            return None
        return self._folder_scope.get(cid)

    def apply_folder_scope(self, session: Any) -> None:
        """Copy stamped folder bind onto a live ``CoordinationSession``."""
        scope = self.folder_scope_for(getattr(session, "conversation_id", "") or "")
        if scope is None:
            return
        if not getattr(session, "birth_desk_id", None) and scope.folder_id:
            session.birth_desk_id = scope.folder_id
        session.folder_binding_injected = scope.binding_injected
        session.folder_local_root_id = scope.local_root_id
        session.folder_local_subpath = scope.local_subpath

    def stamp_folder_scope(
        self,
        conversation_id: str,
        *,
        folder_id: str | None,
        binding_injected: bool,
        local_root_id: str | None,
        local_subpath: str,
    ) -> SidecarFolderScope | None:
        """Remember startTurn / resume folder bind for harvest (no local PG).

        Returns the previous scope so resume veto can roll it back.
        """
        cid = (conversation_id or "").strip()
        if not cid:
            return None
        previous = self._folder_scope.get(cid)
        self._folder_scope[cid] = SidecarFolderScope(
            folder_id=folder_id,
            binding_injected=binding_injected,
            local_root_id=local_root_id,
            local_subpath=local_subpath or "",
        )
        from agentcore.runtime.coordination.session import (
            active_coordination_for_conversation,
        )

        session = active_coordination_for_conversation(cid)
        if session is not None:
            self.apply_folder_scope(session)
        return previous

    def restore_folder_scope(
        self, conversation_id: str, previous: SidecarFolderScope | None
    ) -> None:
        """Undo :meth:`stamp_folder_scope` (resume veto)."""
        cid = (conversation_id or "").strip()
        if not cid:
            return
        if previous is None:
            self._folder_scope.pop(cid, None)
            return
        self._folder_scope[cid] = previous
        from agentcore.runtime.coordination.session import (
            active_coordination_for_conversation,
        )

        session = active_coordination_for_conversation(cid)
        if session is not None:
            self.apply_folder_scope(session)

    def stamp_turn_history(self, conversation_id: str, history: Any) -> None:
        """Remember startTurn / resume history for harvest (same lifetime as folder)."""
        cid = (conversation_id or "").strip()
        if not cid:
            return
        self._turn_history[cid] = list(history) if isinstance(history, list) else []

    def history_for(self, conversation_id: str) -> list[Any]:
        cid = (conversation_id or "").strip()
        if not cid:
            return []
        return list(self._turn_history.get(cid) or [])

    def _stamp_closed_turn(
        self, conversation_id: str, user_message: str, assistant_content: str
    ) -> None:
        """Append the just-closed turn onto the process stamp window (queue drain history)."""
        prior = self.stamped_history(conversation_id)
        rows = list(prior) if prior is not None else []
        if user_message:
            last = rows[-1] if rows else None
            if not (
                isinstance(last, dict)
                and last.get("role") == "user"
                and last.get("content") == user_message
            ):
                rows.append({"role": "user", "content": user_message})
        if assistant_content:
            rows.append({"role": "assistant", "content": assistant_content})
        self.stamp_turn_history(conversation_id, rows)

    def stamped_history(self, conversation_id: str) -> list[Any] | None:
        """Prior-turn window if startTurn/resume stamped one; ``None`` if never set."""
        cid = (conversation_id or "").strip()
        if not cid or cid not in self._turn_history:
            return None
        return list(self._turn_history[cid])

    def permission_axes_for(self, conversation_id: str) -> PermissionAxes:
        """Per-conversation axes, else the initialize default (never another conv's last)."""
        cid = (conversation_id or "").strip()
        if cid:
            bag = self._permission_axes_by_conv.get(cid)
            if bag is not None:
                return bag
        return self._permission_axes

    def busy_reason_for_resume(
        self, conversation_id: str, message_id: str
    ) -> ResumeBusyReason | None:
        """``wrap_up`` / ``live_turn`` when a live task holds the conversation slot."""
        cid = (conversation_id or "").strip()
        if not cid:
            return None
        for tid, task in self._turns.items():
            if task.done():
                continue
            if self._turn_conversations.get(tid) != cid:
                continue
            return "wrap_up" if tid == message_id else "live_turn"
        return None

    def register_resume_deferred(
        self, waiter: SidecarResumeDeferredWaiter
    ) -> SidecarResumeDeferredWaiter:
        """Park a cold resume until the slot frees.

        Same ``message_id`` → join into the existing waiter (no cancel, no second park).
        Different ``message_id`` → last click wins (prior ``slot_free`` cancelled).
        Returns the waiter that owns the slot (existing on join, else ``waiter``).
        """
        prior = self._resume_deferred.get(waiter.conversation_id)
        if (
            prior is not None
            and prior is not waiter
            and prior.message_id == waiter.message_id
        ):
            for rid in waiter.reply_ids:
                if rid not in prior.reply_ids:
                    prior.reply_ids.append(rid)
            logger.info(
                "resume.deferred_joined",
                conversation_id=waiter.conversation_id,
                message_id=waiter.message_id,
                busy_reason=prior.busy_reason,
                reply_count=len(prior.reply_ids),
            )
            return prior

        prior = self._resume_deferred.pop(waiter.conversation_id, None)
        if prior is not None and prior is not waiter and not prior.slot_free.done():
            prior.slot_free.cancel()
        self._resume_deferred[waiter.conversation_id] = waiter
        logger.info(
            "resume.deferred",
            conversation_id=waiter.conversation_id,
            message_id=waiter.message_id,
            busy_reason=waiter.busy_reason,
        )
        if self.busy_reason_for_resume(waiter.conversation_id, waiter.message_id) is None:
            taken = self._resume_deferred.pop(waiter.conversation_id, None)
            if taken is waiter and not taken.slot_free.done():
                taken.slot_free.set_result(None)
        return waiter

    def _wake_resume_deferred_if_idle(self, conversation_id: str) -> None:
        waiter = self._resume_deferred.get(conversation_id)
        if waiter is None:
            return
        if self.busy_reason_for_resume(conversation_id, waiter.message_id) is not None:
            return
        taken = self._resume_deferred.pop(conversation_id, None)
        if taken is None or taken.slot_free.done():
            return
        taken.slot_free.set_result(None)
        logger.info(
            "resume.deferred_started",
            conversation_id=taken.conversation_id,
            message_id=taken.message_id,
            busy_reason=taken.busy_reason,
        )

    async def handle_line(self, line: str) -> None:
        """Parse and dispatch one inbound line. Never raises (loop-safe)."""
        line = line.strip()
        if not line:
            return
        try:
            message = protocol.decode_line(line)
        except protocol.ProtocolError as e:
            await self._send(protocol.make_error(None, protocol.PARSE_ERROR, str(e)))
            return

        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(method, str):
            # A response/notification we did not expect, or a malformed request.
            if request_id is not None:
                await self._send(
                    protocol.make_error(request_id, protocol.INVALID_REQUEST, "missing method")
                )
            return

        try:
            await self._dispatch(request_id, method, params)
        except Exception as e:  # a dispatch bug must not kill the read loop
            logger.error("sidecar.dispatch_failed", method=method, error=str(e), exc_info=True)
            if request_id is not None:
                await self._send(protocol.make_error(request_id, protocol.INTERNAL_ERROR, str(e)))

    async def _dispatch(self, request_id: Any, method: str, params: dict[str, Any]) -> None:
        if method == "initialize":
            await self._on_initialize(request_id, params)
            if self._initialized:
                set_active_sidecar(self)
        elif method == "startTurn":
            await self._on_start_turn(request_id, params)
        elif method == "respond":
            await self._on_respond(request_id, params)
        elif method == "resume":
            await self._on_resume(request_id, params)
        elif method == "listPaused":
            await self._on_list_paused(request_id, params)
        elif method == "cancel":
            await self._on_cancel(request_id, params)
        elif method == "deliverMessage":
            await self._on_deliver_message(request_id, params)
        elif method == "cancelQueuedTurn":
            await self._on_cancel_queued_turn(request_id, params)
        elif method == "listQueuedTurns":
            await self._on_list_queued_turns(request_id, params)
        elif method == "runRedirect":
            await self._on_run_redirect(request_id, params)
        elif method == "runStop":
            await self._on_run_stop(request_id, params)
        elif method == "debateSteer":
            await self._on_debate_steer(request_id, params)
        elif method == "turnFilesDiff":
            await self._on_turn_files_diff(request_id, params)
        elif method == "listBrowserSessions":
            await self._on_list_browser_sessions(request_id, params)
        elif method == "restoreTurnBaseline":
            await self._on_restore_turn_baseline(request_id, params)
        elif method == "createWorkspaceVersion":
            await self._on_create_workspace_version(request_id, params)
        elif method == "restoreWorkspaceVersion":
            await self._on_restore_workspace_version(request_id, params)
        elif method == "warmCodeIndex":
            await self._on_warm_code_index(request_id, params)
        elif method == "warmMcpDiscover":
            await self._on_warm_mcp_discover(request_id, params)
        elif method == "warmAccountRulesMemory":
            await self._on_warm_account_rules_memory(request_id, params)
        elif method == "shutdown":
            from agentcore.demo_tape.recorder import uninstall_recorder

            uninstall_recorder()
            self._close_fulfiller()
            live = any(not t.done() for t in self._turns.values())
            if get_active_sidecar() is self and not live:
                set_active_sidecar(None)
                from agentcore.runtime.turn.queue import reset_queue_starter

                reset_queue_starter()
            elif live:
                logger.info(
                    "sidecar.shutdown_kept_active",
                    live_turns=sum(1 for t in self._turns.values() if not t.done()),
                )
            self.shutdown_requested.set()
            await self._reply(request_id, {"ok": True})
        else:
            await self._send(
                protocol.make_error(
                    request_id, protocol.METHOD_NOT_FOUND, f"unknown method: {method}"
                )
            )

    def _bind_fulfiller(self) -> None:
        """Point this process's fulfill-hub session at the current account id.

        Called from ``initialize`` and from every per-turn ``userId`` refresh: the
        hub selects a fulfiller by ``user_id``, so a probe-spawned sidecar that
        initialized as the ``local`` alias must re-register once the real account
        arrives — otherwise the channels look for a fulfiller nobody holds.
        """
        if self._fulfill_bridge is None:
            self._fulfill_bridge = SidecarFulfillBridge(self._send)
        self._fulfill_bridge.bind_user(self._user_id)

    def _declare_fulfill_root(self, params: dict[str, Any]) -> None:
        """Declare this turn's ``localRootId`` on the fulfiller session.

        Root-scoped ``workspace`` frames (a worker desk bound to the turn's local
        root) are only routed to a session that declares the root — the same rule
        the cloud desktop satisfies by registering the root with the API process
        (``agentcore.fulfill.declare``). The sidecar's own file ops never take
        this path (direct ``Path`` I/O).

        This is the turn's *own* desk only. A desk on another project (cross-desk
        delegate, ``folder_id``-scoped reads, bare chat with no binding at all)
        resolves a root this call never sees; those declare themselves as their
        workspace is built (``agentcore.fulfill.local_roots``).
        """
        raw = params.get("localRootId")
        root = str(raw).strip() if isinstance(raw, str) else ""
        if root and self._fulfill_bridge is not None:
            self._fulfill_bridge.declare_root(root)

    def _close_fulfiller(self) -> None:
        """Unregister this process's fulfiller (shutdown). Idempotent."""
        if self._fulfill_bridge is not None:
            self._fulfill_bridge.close()

    def _make_backend(
        self, *, external_mounts: list | dict | None = None
    ) -> ServerWorkspace:
        """Build the local-disk workspace backend for a turn / resume.

        The sidecar runs ON the user's machine and this root IS their real disk →
        ``location="local"``, so the engine gates a delegated worker's machine-touching
        tools (file_write / code_execute) behind the user's consent, just like cloud
        local mode. Without this the gate stays off (workers un-gated) even with
        approvals enabled, since the default backend reports "server".

        ``external_mounts`` (W3) are session read-only dirs under ``external/<alias>/``.
        """
        assert self._root is not None  # guarded by callers
        backend = ServerWorkspace(
            root=self._root,
            sandbox=SubprocessSandbox(),
            root_label=self._root.name or "workspace",
            location="local",
        )
        if external_mounts:
            from agentcore.workspace.external_mounts import (
                ExternalMount,
                normalize_mount_mode,
            )

            items: list[dict] = []
            if isinstance(external_mounts, list):
                items = [m for m in external_mounts if isinstance(m, dict)]
            elif isinstance(external_mounts, dict):
                items = [
                    {"alias": a, **m} if isinstance(m, dict) else {"alias": a}
                    for a, m in external_mounts.items()
                ]
            mounts: dict[str, ExternalMount] = {}
            for m in items:
                alias = str(m.get("alias") or "").strip()
                abs_path = str(m.get("absPath") or m.get("abs_path") or "").strip()
                if not alias or not abs_path:
                    continue
                mounts[alias] = ExternalMount(
                    alias=alias,
                    root_id=str(m.get("rootId") or m.get("root_id") or ""),
                    label=str(m.get("label") or alias),
                    abs_path=abs_path,
                    mode=normalize_mount_mode(str(m.get("mode") or "")),
                )
            if mounts:
                backend.attach_external_mounts(mounts)
        # Index maintenance: write paths / code_search only — not at turn entry.
        return backend

    def _suspension_hooks(
        self,
    ) -> tuple[
        Callable[[TurnSuspension], Awaitable[None]] | None,
        Callable[[str], Awaitable[None]] | None,
    ]:
        """The (saver, deleter) closures the pipeline wires into delegate / ask_user.

        Backed by the local paused-turn store so a pause persists before its wait and
        drops after a live resolve. ``(None, None)`` when no store ⇒ in-memory pause.
        """
        store = self._paused_store
        if store is None:
            return None, None
        return store.save, store.delete

    def _session_hooks(
        self, conversation_id: str
    ) -> tuple[
        Callable[[Any], Awaitable[None]] | None,
        Callable[[str], Awaitable[Any]] | None,
    ]:
        """The (saver, loader) closures the pipeline wires into delegate continuation.

        Backed by the local run-session store so a memory-roster miss can rehydrate
        across byte-cap eviction (parity with cloud ``session_callbacks``).
        ``(None, None)`` when no store ⇒ memory-only.
        """
        store = self._run_session_store
        if store is None:
            return None, None

        async def _persist(session: Any) -> None:
            await store.save(conversation_id, session)

        return _persist, store.load

    def _creds_for(
        self, conversation_id: str, trace_id: str = "", message_id: str = ""
    ) -> LLMCredentials | None:
        """Session creds + this turn's conversation/trace/message headers for the cloud
        inference proxy (Slice 4a), so spend attributes to the right conversation,
        every proxied LLM call joins this turn's trace (the write-back reuses the same
        id → ONE trace end-to-end, 打通气泡↔日志), and in-turn proxy rows carry the
        assistant ``message_id`` for daily-request quota (distinct from off-turn background
        rows that intentionally leave message_id NULL).

        Per-turn (one sidecar serves many conversations), so the session creds get a
        fresh per-turn copy. None creds (no inference JWT) stay None — startTurn /
        resume refuse before prepare. ``trace_id`` / ``message_id`` empty (untraced
        caller) ⇒ the header is omitted, not blank.
        """
        if self._creds is None:
            return None
        from agentcore.llm.credentials import INFERENCE_MESSAGE_HEADER

        extra = {
            **(self._creds.extra_headers or {}),
            INFERENCE_CONVERSATION_HEADER: conversation_id,
        }
        if trace_id:
            extra[INFERENCE_TRACE_HEADER] = trace_id
        if message_id:
            extra[INFERENCE_MESSAGE_HEADER] = message_id
        return replace(self._creds, extra_headers=extra)

    async def _send(self, message: dict[str, Any]) -> None:
        await self._write_line(protocol.encode_line(message))

    def _send_soon(self, message: dict[str, Any]) -> None:
        """Schedule a send on an independent task (for use during cancellation)."""
        task = asyncio.create_task(self._send(message))
        self._pending_sends.add(task)
        task.add_done_callback(self._pending_sends.discard)

    @staticmethod
    def _unique_request_ids(request_ids: list[Any] | None, primary: Any) -> list[Any]:
        """Preserve order; drop duplicates. Fall back to ``[primary]`` when empty."""
        out: list[Any] = []
        seen: set[Any] = set()
        for rid in list(request_ids or []) + ([primary] if primary is not None else []):
            if rid is None or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
        return out

    async def _send_to_request_ids(
        self,
        request_ids: list[Any] | None,
        primary: Any,
        make_message: Callable[[Any], dict[str, Any]],
    ) -> None:
        """Fan-out one RPC reply shape to primary + same-id joiners."""
        for rid in self._unique_request_ids(request_ids, primary):
            await self._send(make_message(rid))

    def _send_soon_to_request_ids(
        self,
        request_ids: list[Any] | None,
        primary: Any,
        make_message: Callable[[Any], dict[str, Any]],
    ) -> None:
        for rid in self._unique_request_ids(request_ids, primary):
            self._send_soon(make_message(rid))

    async def _reply(self, request_id: Any, result: Any) -> None:
        """Send a success response, unless the message was a notification (no id)."""
        if request_id is not None:
            await self._send(protocol.make_result(request_id, result))

    async def _reply_error(
        self, request_id: Any, code: int, message: str, *, data: Any = None
    ) -> None:
        """Send an error response, unless there is no RPC waiter (queue drain)."""
        if request_id is None:
            return
        await self._send(protocol.make_error(request_id, code, message, data=data))

    def _reply_error_soon(
        self, request_id: Any, code: int, message: str, *, data: Any = None
    ) -> None:
        if request_id is None:
            return
        self._send_soon(protocol.make_error(request_id, code, message, data=data))


def get_active_sidecar() -> SidecarServer | None:
    """Process-wide sidecar (harvest / host read folder scope + ``_creds_for``)."""
    return _active_sidecar


def set_active_sidecar(server: SidecarServer | None) -> None:
    """Install or clear the process-wide sidecar (initialize / shutdown / tests)."""
    global _active_sidecar, _sidecar_process
    _active_sidecar = server
    if server is not None:
        _sidecar_process = True


def is_sidecar_process() -> bool:
    """True when this process installed a sidecar (pointer may already be cleared)."""
    return _sidecar_process


def reset_active_sidecar_for_tests() -> None:
    """Drop the process-wide sidecar so later tests do not inherit a live instance."""
    global _active_sidecar, _sidecar_process
    _active_sidecar = None
    _sidecar_process = False
    from agentcore.runtime.turn.queue import reset_queue_starter

    reset_queue_starter()
