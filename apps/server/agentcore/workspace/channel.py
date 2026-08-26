"""WorkspaceChannel — route a LocalWorkspace op to the desktop and await it.

This is the generalized form of the tool-approval mechanism (``runtime/
approvals.py``): the server suspends on an ``asyncio.Future`` and a separate HTTP
request settles it. Approvals carry a one-shot *decision*; this channel carries a
full *request → response* op exchange so a server-side ``LocalWorkspace`` can run
file / execution ops on the user's real machine without the engine ever touching
a ``Path``.

Flow (one op):

1. ``LocalWorkspace`` calls ``WorkspaceChannel.request(op, args)``.
2. The channel registers a Future, delivers a ``workspace_op_required`` frame on
   the device-level fulfill hub, and awaits the Future (bounded by
   ``timeout_seconds``).
3. The bound desktop client runs the op against the local directory and POSTs the
   structured result to the ops resolve endpoint, which settles the Future.
4. The channel returns the op's ``value`` on success, or re-raises the original
   ``WorkspaceError`` subclass on failure — so the (unchanged) tool layer maps it
   to the same user-facing message it does for ``ServerWorkspace``.

State is in-process (single-worker posture, same as the approval gate); front
with Redis to scale to multiple workers (see ``config.py``). A result the client
never delivers fails as a ``WorkspaceIOError`` after the timeout. A single settle
timeout fails only that op; **consecutive** real-op settle timeouts (N=2) mark the
channel sticky-dead for the turn (sibling inflight settle + later ops fail-fast),
so a dropped desktop never hangs the turn on cascaded deadlines. Concurrent
desktop round-trips are capped (``max_inflight``, default 16); extras queue before
suspend, and queue wait rides the outer tool wall clock. No online fulfiller →
typed failure without waiting out the deadline, named the way the turn-start
presence gate would have named it: a desktop that is online but no longer declares
this root reads as 未声明持有本会话的本地目录, not 无履约方. The one delay is a
desktop whose SSE dropped seconds ago — that op waits out a bounded reconnect
grace inside its own deadline (``fulfill/grace.py``) instead of failing blind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, NoReturn

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.fulfill.origin import ORIGIN_DEVICE_OFFLINE
from agentcore.runtime.events.client_tool_reattach import (
    CHANNEL_WORKSPACE,
    client_tool_payload,
    push_client_tool_required,
)
from agentcore.runtime.events.types import EventType
from agentcore.runtime.events.workspace import workspace_op_required
from agentcore.runtime.interaction import InteractionKind
from agentcore.runtime.ports import ClientRequestBridge
from agentcore.runtime.tool_deadline import derive_channel_timeout
from agentcore.workspace.limits import LOCAL_ROOT_NOT_HELD
from agentcore.workspace.protocol import (
    AlreadyExists,
    AmbiguousMatch,
    NoMatch,
    NotADirectory,
    NotAFile,
    NotUTF8,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
    WorkspaceIOError,
)

logger = get_logger(__name__)

# Background IndexMaintainer channel IO: timeouts must not sticky-dead the shared
# Local file channel (same spirit as ``probe_exec``). Bound around ``ensure_index``.
_index_io: ContextVar[bool] = ContextVar("workspace_index_io", default=False)


@contextmanager
def index_io_mode() -> Iterator[None]:
    """Mark the current task as background index IO (no sticky-dead on timeout)."""
    token = _index_io.set(True)
    try:
        yield
    finally:
        _index_io.reset(token)


def index_io_active() -> bool:
    return _index_io.get()


class WorkspaceOp(StrEnum):
    """The op names exchanged over the channel (one per ``WorkspaceBackend`` method).

    Shared by ``LocalWorkspace`` (which sends them) and the desktop handler (which
    dispatches on them); kept as one closed set so the two ends can never drift.
    """

    READ = "read"
    WRITE = "write"
    APPEND = "append"
    READ_BYTES = "read_bytes"
    READ_HEAD = "read_head"
    WRITE_BYTES = "write_bytes"
    LIST = "list"
    EXISTS = "exists"
    READ_LINES = "read_lines"
    LIST_TREE = "list_tree"
    INDEX_FILES = "index_files"
    MKDIR = "mkdir"
    DELETE = "delete"
    COPY = "copy"
    MOVE = "move"
    REPLACE = "replace"
    GREP = "grep"
    EXECUTE = "execute"
    # Language-service diagnostics (inner verify loop) — LocalWorkspace only;
    # ServerWorkspace returns unavailable without issuing this op. Desktop runs
    # TS/JS diagnostics for ``args.paths`` and returns
    # ``{status, reason?, diagnostics[]}``.
    DIAGNOSTICS = "diagnostics"
    # Probe which code_execute languages have a usable launcher on the user's
    # machine (PATH / Git Bash). Not a WorkspaceBackend method — issued at turn
    # prepare so the tool schema can drop unavailable languages (e.g. broken WSL
    # bash trampoline) before the model ever sees them.
    PROBE_EXEC = "probe_exec"
    # Local→云 handoff (双模式工作区 P2e / e1): pack the whole bound local root into
    # one archive (respecting ignore rules) so the server can stage + snapshot it.
    # NOT a WorkspaceBackend method — issued directly by the handoff orchestrator
    # (workspace/handoff.py), not by the engine/tools.
    ARCHIVE = "archive"
    # Desktop-channel Local turn baseline: zip / probe ``AgentCore/baselines/{id}.zip``
    # on the user disk (server has no Path.root). NOT a WorkspaceBackend method —
    # issued by turn_baseline / destructive gate via LocalWorkspace helpers.
    ENSURE_TURN_BASELINE = "ensure_turn_baseline"
    # Background process ops (双模式工作区 §四): spawn / read / stop / list long-lived
    # processes held by the desktop main process. NOT WorkspaceBackend methods — issued by
    # the worker-only ``terminal`` tool over the same channel (LocalWorkspace + sidecar).
    PROCESS_START = "process_start"
    PROCESS_READ = "process_read"
    PROCESS_STOP = "process_stop"
    PROCESS_LIST = "process_list"
    # Desktop U1–U3 user SCM: read-only status + controlled stage/commit/push/pull.
    # NOT WorkspaceBackend methods — renderer invokes via ``workspaceOp``; server
    # never issues these ops (kept in the closed set so ends cannot drift).
    GIT_REPO_STATUS = "git_repo_status"
    GIT_SCM = "git_scm"
    # Agent structured ``git`` on LocalWorkspace (no Path.root): run allowlisted
    # argv on the desktop. Args ``argv`` (+ optional ``timeout_seconds``, ``cwd`` =
    # D1a project subpath under the bound root; omit/empty = root is the project);
    # value ``{stdout, stderr, exit_code}``. UI SCM stays on git_repo_status/git_scm.
    GIT_RUN = "git_run"


# Map a serialized error ``kind`` back to its WorkspaceError subclass, so a remote
# failure re-raises as the exact type the tool layer already catches. Anything
# unrecognized degrades to WorkspaceIOError (a generic I/O failure) rather than
# leaking as an unhandled exception.
_ERROR_KINDS: dict[str, type[WorkspaceError]] = {
    "OutsideWorkspace": OutsideWorkspace,
    "PathNotFound": PathNotFound,
    "NotAFile": NotAFile,
    "NotADirectory": NotADirectory,
    "AlreadyExists": AlreadyExists,
    "NotUTF8": NotUTF8,
    "NoMatch": NoMatch,
    "WorkspaceIOError": WorkspaceIOError,
}


def raise_op_error(error: dict[str, Any]) -> NoReturn:
    """Re-raise a serialized desktop op failure as its typed ``WorkspaceError``.

    ``AmbiguousMatch`` carries a ``count`` (used in the str_replace message), so it
    is reconstructed specially; every other kind maps by name.
    """
    kind = str(error.get("kind", ""))
    detail = str(error.get("detail", "") or "")
    if kind == "AmbiguousMatch":
        try:
            count = int(error.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        raise AmbiguousMatch(count, detail)
    cls = _ERROR_KINDS.get(kind, WorkspaceIOError)
    raise cls(detail)


# Shared detail fragment so ``is_channel_dead_detail`` / ``is_liveness_timeout_detail``
# keep matching channel-dead fail-fast / sibling cancel envelopes (capacity ≠ liveness).
_CHANNEL_DEAD_DETAIL = "local workspace channel dead（活性挂起）"

# Real-op settle timeouts must streak this many times before sticky-dead (success clears).
_STICKY_AFTER_CONSECUTIVE_TIMEOUTS = 2


@dataclass
class WorkspaceChannel:
    """Suspends one LocalWorkspace op until the bound desktop runs it.

    One channel per local-mode turn (constructed where ``user_id`` is available),
    bound to one desktop FS ``root_id``. ``request`` is the only entry point;
    ``LocalWorkspace`` builds the JSON-safe ``args`` and interprets the returned
    ``value`` per op. Delivery goes through the fulfill hub — no online fulfiller
    fails with a typed ``WorkspaceIOError`` without burning the deadline (a
    just-dropped desktop first gets its reconnect grace, see ``fulfill/grace.py``).

    Sticky dead: **consecutive** transport ``TimeoutError``s on **real** workspace
    ops (desktop liveness hang, N=2) mark the channel dead for the rest of the turn —
    subsequent ``request``s fail-fast without delivery, and same-channel inflight ops
    are settled with a failure envelope so they do not burn the remaining deadline.
    A single settle timeout fails only that op (no sibling cancel, no sticky).
    A successful settle clears the consecutive-timeout streak.
    ``probe_exec`` (language advertise probe at turn prepare) is exempt: its
    timeout/failure only fail-closes the language surface, and must not sticky-dead
    the file channel. Background index IO (``index_io_mode``) is likewise exempt so
    an IndexMaintainer hang cannot drag tool-family siblings into channel-dead.

    Bounded in-flight: a semaphore caps concurrent desktop round-trips
    (``max_inflight``, default 16). Extra callers queue before suspend; queue wait
    rides the outer tool wall clock (no separate channel timeout stretch).
    """

    user_id: str
    conversation_id: str
    registry: ClientRequestBridge
    timeout_seconds: float
    root_id: str = ""  # which desktop FS root this workspace is bound to (P2d)
    max_inflight: int = 16  # concurrent suspends; settings.workspace_channel_max_inflight
    _dead: bool = field(default=False, init=False, repr=False)
    _inflight: set[str] = field(default_factory=set, init=False, repr=False)
    _consecutive_settle_timeouts: int = field(default=0, init=False, repr=False)
    _sem: asyncio.Semaphore | None = field(default=None, init=False, repr=False)

    @property
    def is_dead(self) -> bool:
        """True after sticky-dead (consecutive real-op liveness hangs)."""
        return self._dead

    def _get_sem(self) -> asyncio.Semaphore:
        """Lazy semaphore so it binds to the running event loop on first acquire."""
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.max_inflight))
        return self._sem

    def _fail_inflight_siblings(self, *, trigger_request_id: str) -> None:
        """Settle other same-channel awaits with a channel-dead failure envelope."""
        envelope = {
            "ok": False,
            "error": {"kind": "WorkspaceIOError", "detail": _CHANNEL_DEAD_DETAIL},
        }
        for rid in list(self._inflight):
            if rid == trigger_request_id:
                continue
            # resolve → Future.set_result; already-done / unknown is a no-op.
            self.registry.resolve(rid, envelope, conversation_id=self.conversation_id)

    def _mark_dead(self, *, op: str, request_id: str) -> None:
        """Consecutive liveness hangs reached N: sticky-dead + cancel siblings (idempotent)."""
        if self._dead:
            return
        self._dead = True
        logger.info(
            "workspace.channel_dead",
            op=op,
            request_id=request_id,
            conversation_id=self.conversation_id,
            consecutive_timeouts=self._consecutive_settle_timeouts,
        )
        self._fail_inflight_siblings(trigger_request_id=request_id)

    def _reject_if_dead(self, op_name: str) -> None:
        if not self._dead:
            return
        logger.info(
            "workspace.op_rejected_channel_dead",
            op=op_name,
            conversation_id=self.conversation_id,
        )
        raise WorkspaceIOError(
            f"local workspace op '{op_name}' rejected: channel dead（活性挂起）"
        )

    async def request(
        self,
        op: WorkspaceOp | str,
        args: dict[str, Any],
        *,
        timeout: float | None = None,
        root_id: str | None = None,
    ) -> Any:
        """Emit the op, await the desktop's result, and return it (or raise).

        Returns the op's ``value`` on success. Raises the typed ``WorkspaceError``
        the desktop reported on failure, or ``WorkspaceIOError`` on timeout / a
        malformed result envelope — never hangs and never leaks an untyped error,
        so the tool layer's existing ``except WorkspaceError`` keeps working.

        ``timeout`` overrides the channel-wide ``timeout_seconds`` for this one op.
        A long-running ``execute`` passes its own (code timeout + slack) so the
        desktop's execution limit stays authoritative and a legal long run is not
        cut off by the flat file-op deadline (双模式工作区 P2d 执行门).

        When called inside ``tool_exec``, the effective deadline is **derived from
        the outer tool liveness budget** (minus settle slack) — never a second
        independent 60s clock. ``timeout_ms`` is echoed on the payload so the
        desktop can AbortSignal the in-flight IPC op; late settle after discard
        remains a stale 404 no-op.

        ``root_id`` overrides the channel's bound root for this one op (W3 session
        read-only mounts under ``external/<alias>/``); omit to use the workspace
        binding root. Does not change the conversation workspace binding contract.

        After consecutive real-op liveness timeouts (N=2) the channel stays
        sticky-dead: new requests raise immediately (no delivery) so a hung desktop
        cannot cascade into more 60s waits. A single timeout fails only that op.
        ``probe_exec`` and background index-IO timeouts do not enter that sticky
        state (and do not advance the consecutive-timeout streak).

        Concurrency order: dead-check → acquire slot → dead-check → suspend. A
        waiter that obtains a slot after the channel died fail-fasts without delivery.
        """
        op_name = str(op)
        self._reject_if_dead(op_name)
        counts_toward_sticky = (
            op_name != WorkspaceOp.PROBE_EXEC and not index_io_active()
        )

        sem = self._get_sem()
        await sem.acquire()
        try:
            # Re-check after queueing: channel may have died while we waited.
            self._reject_if_dead(op_name)

            request_id = new_id()
            deadline = derive_channel_timeout(
                explicit=timeout,
                channel_default=self.timeout_seconds,
            )
            timeout_ms = max(1, int(deadline * 1000))
            rid = self.root_id if root_id is None else root_id
            deliver_root: str | None = rid if rid else None

            def _emit_op_required() -> None:
                """Push to fulfill hub; settle when nobody can run it.

                The absence is named, not lumped: origin device gone, root no
                longer held, or no fulfiller at all. A desktop that just dropped
                its SSE holds the op for a bounded grace inside ``deadline``
                instead (``fulfill/grace.py``).
                """
                push_client_tool_required(
                    user_id=self.user_id,
                    conversation_id=self.conversation_id,
                    channel=CHANNEL_WORKSPACE,
                    root_id=deliver_root,
                    event=workspace_op_required(
                        request_id=request_id,
                        conversation_id=self.conversation_id,
                        root_id=rid,
                        op=op_name,
                        args=args,
                        timeout_ms=timeout_ms,
                    ),
                    registry=self.registry,
                    request_id=request_id,
                    error_kind="WorkspaceIOError",
                    error_detail=(
                        f"local workspace op '{op_name}' failed: no fulfiller（无履约方）"
                    ),
                    origin_offline_detail=(
                        f"local workspace op '{op_name}' failed: "
                        f"{ORIGIN_DEVICE_OFFLINE}"
                    ),
                    root_not_held_detail=(
                        f"local workspace op '{op_name}' failed: "
                        f"{LOCAL_ROOT_NOT_HELD}"
                    ),
                    deadline_seconds=deadline,
                )

            self._inflight.add(request_id)
            try:
                try:
                    result = await self.registry.suspend(
                        request_id,
                        self.conversation_id,
                        kind=InteractionKind.CLIENT_TOOL,
                        payload=client_tool_payload(
                            CHANNEL_WORKSPACE,
                            EventType.WORKSPACE_OP_REQUIRED.value,
                            params={
                                "root_id": rid,
                                "op": op_name,
                                "args": args,
                                "timeout_ms": timeout_ms,
                            },
                            user_id=self.user_id,
                        ),
                        timeout=deadline,
                        on_suspended=_emit_op_required,
                    )
                except TimeoutError as e:
                    # Attribute from channel fields (not contextvars) so background
                    # index / detached tasks stay replayable in logs/dev.jsonl.
                    timeout_fields: dict[str, Any] = {
                        "op": op_name,
                        "request_id": request_id,
                        "conversation_id": self.conversation_id,
                        "timeout_ms": timeout_ms,
                    }
                    if rid:
                        timeout_fields["root_id"] = rid
                    path = args.get("path")
                    if path is not None:
                        timeout_fields["path"] = path
                    directory = args.get("directory")
                    if directory is not None:
                        timeout_fields["directory"] = directory
                    logger.info("workspace.op_timeout", **timeout_fields)
                    # probe_exec / background index IO: fail the op only — never
                    # sticky-dead the shared file channel or cancel tool siblings.
                    if counts_toward_sticky:
                        self._consecutive_settle_timeouts += 1
                        if (
                            self._consecutive_settle_timeouts
                            >= _STICKY_AFTER_CONSECUTIVE_TIMEOUTS
                        ):
                            self._mark_dead(op=op_name, request_id=request_id)
                            raise WorkspaceIOError(
                                f"local workspace op '{op_name}' timed out; "
                                f"channel dead（活性挂起）"
                            ) from e
                    raise WorkspaceIOError(
                        f"local workspace op '{op_name}' timed out（活性挂起）"
                    ) from e
            finally:
                self._inflight.discard(request_id)
        finally:
            sem.release()

        # Any non-timeout settle clears the hang streak (ok or typed desktop error).
        if counts_toward_sticky:
            self._consecutive_settle_timeouts = 0

        if not isinstance(result, dict) or not result.get("ok"):
            error = result.get("error") if isinstance(result, dict) else None
            raise_op_error(error or {"kind": "WorkspaceIOError", "detail": "malformed op result"})
        return result.get("value")


def raise_if_backend_channel_dead(backend: object | None) -> None:
    """Abort when a local backend's ``WorkspaceChannel`` is already sticky-dead.

    Used by prepare / turn_runner so a dead desktop channel fails the turn before
    assemble + LLM (tools would only reject afterward). No-op for cloud / no-channel
    backends. Raises ``WorkspaceIOError`` with a user-honest prepare-abort message.
    """
    if backend is None:
        return
    channel = getattr(backend, "_channel", None)
    if not isinstance(channel, WorkspaceChannel) or not channel.is_dead:
        return
    from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT

    logger.info(
        "workspace.prepare_aborted_channel_dead",
        conversation_id=getattr(channel, "conversation_id", None),
    )
    raise WorkspaceIOError(CHANNEL_DEAD_PREPARE_ABORT)
