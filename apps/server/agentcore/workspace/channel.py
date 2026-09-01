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
never delivers fails as a ``WorkspaceIOError`` after the timeout — **that op
only**. Timeouts do not declare the desk disconnected; whether files are
connected is fulfiller presence (``workspace.presence``), the same fact the
turn-start gate asks. An op the desktop has already started is failed immediately
when the fulfill transport drops (desktop POSTs 「桌面在重连，请再试这一下」);
ops not yet delivered still wait reconnect grace. Concurrent desktop round-trips are capped
(``max_inflight``, default 16); extras queue before suspend, and queue wait
rides the outer tool wall clock. No online fulfiller → typed failure without
waiting out the deadline, named the way the turn-start presence gate would have
named it: a desktop that is online but no longer declares this root reads as
未声明持有本会话的本地目录, not 无履约方. The one delay is a desktop whose SSE
dropped seconds ago — that op waits out a bounded reconnect grace inside its
own deadline (``fulfill/grace.py``) instead of failing blind.
"""

from __future__ import annotations

import asyncio
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
    # Language-service diagnostics (inner verify loop). LocalWorkspace and
    # sidecar ServerWorkspace(location=local) issue this op; cloud desks return
    # unavailable without delivery. Desktop runs TS/JS diagnostics for
    # ``args.paths`` and returns ``{status, reason?, diagnostics[]}``.
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
    # model-facing ``run`` (background / process actions) over the same channel
    # (LocalWorkspace + sidecar).
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


@dataclass
class WorkspaceChannel:
    """Suspends one LocalWorkspace op until the bound desktop runs it.

    One channel per local-mode turn (constructed where ``user_id`` is available),
    bound to one desktop FS ``root_id``. ``request`` is the only entry point;
    ``LocalWorkspace`` builds the JSON-safe ``args`` and interprets the returned
    ``value`` per op. Delivery goes through the fulfill hub — no online fulfiller
    fails with a typed ``WorkspaceIOError`` without burning the deadline (a
    just-dropped desktop first gets its reconnect grace, see ``fulfill/grace.py``).

    A settle timeout fails only that op. It does not cancel siblings, does not
    fail-fast later ops, and does not mean the desk is disconnected — presence
    is ``workspace.presence.local_workspace_files_reachable``.

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
    _inflight: set[str] = field(default_factory=set, init=False, repr=False)
    _sem: asyncio.Semaphore | None = field(default=None, init=False, repr=False)

    def _get_sem(self) -> asyncio.Semaphore:
        """Lazy semaphore so it binds to the running event loop on first acquire."""
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.max_inflight))
        return self._sem

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
        """
        op_name = str(op)
        sem = self._get_sem()
        await sem.acquire()
        try:
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
                    raise WorkspaceIOError(
                        f"local workspace op '{op_name}' timed out（活性挂起）"
                    ) from e
            finally:
                self._inflight.discard(request_id)
        finally:
            sem.release()

        if not isinstance(result, dict) or not result.get("ok"):
            error = result.get("error") if isinstance(result, dict) else None
            raise_op_error(error or {"kind": "WorkspaceIOError", "detail": "malformed op result"})
        return result.get("value")
