"""Browser-session surface for the sandbox — the D9 control-channel contract.

This is the "会话面" the task adds to the sandbox provider *without* touching the
existing one-shot ``SandboxProvider.execute()``. A provider that can host a
long-lived, session-scoped Chromium (only ``GVisorBrowserSandbox`` today) opens a
:class:`BrowserSession`, sends it one :class:`BrowserCommand` at a time over a
stdio JSON-RPC channel, and closes it. Everything here is transport-agnostic and
import-light so the registry / tools / tests depend on the contract, not on the
Linux-only runsc orchestration that implements it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

# One live screencast frame handed from a session to its live-hub listener (M1 · D14):
# ``{"frame_b64": <jpeg base64>, "width": int, "height": int}`` — passed straight through
# (no host decode) onto the ``browser_live_frame`` SSE event.
BrowserFrameListener = Callable[[dict[str, Any]], None]

# Browser actions (D11 + console evidence). State-changing ones auto-capture a keyframe.
BROWSER_ACTIONS = (
    "navigate",
    "click",
    "type",
    "scroll",
    "snapshot",
    "screenshot",
    "console",
)
STATE_CHANGING_ACTIONS = frozenset({"navigate", "click", "type", "scroll"})


# ---------------------------------------------------------------------------
# Wire receipts shared with Local (Electron) host — field names are frozen.
# Executors only report facts; success/error judgment lives in the tool layer.
# ---------------------------------------------------------------------------


class TypedReceipt(TypedDict):
    """Post-condition facts after ``type`` (Local + Sandbox identical keys)."""

    ref: str
    requested_chars: int
    actual_chars: int
    matched: bool
    method: Literal["cdp_insertText"]


class ClickedReceipt(TypedDict):
    """Pre/post click facts (disabled includes ``disabled`` + ``aria-disabled``)."""

    ref: str
    was_disabled: bool
    role: str
    name: str


class BrowserSessionError(Exception):
    """Base for browser-session failures (mapped to a model-facing ToolResult).

    Optional ``code`` is a stable machine token for tool ``metadata.code`` (e.g.
    ``egress_unavailable`` / ``host_unavailable``). Subclasses may set it.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class BrowserSessionsBusyError(BrowserSessionError):
    """The process-wide concurrency cap is full and no idle session could be reaped.

    ``str()`` is the honest, model-facing reason (mirrors the code_execute slot-busy
    result) so the tool can surface it directly.
    """


class BrowserSessionAcquireError(BrowserSessionError):
    """Registry ``acquire`` refused — stable ``code`` for tool ``metadata.code``.

    Codes: ``session_not_found`` (explicit sid missing/dead),
    ``session_bound_elsewhere`` (host_kind mismatch or cross-run bind conflict).
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, code=code)


class BrowserDriverCrashedError(BrowserSessionError):
    """The in-sandbox driver died / the stdio channel broke.

    The registry drops the dead session so the next call rebuilds a fresh sandbox;
    the tool tells the AI the page state was lost (D9: crash → rebuild + inform).
    """


@dataclass
class BrowserSessionRequest:
    """Inputs to open one conversation's long-lived browser sandbox."""

    conversation_id: str
    # Absolute host path of the conversation's workspace root. Sandbox Chromium
    # execs into that workspace's desk guest — missing/non-dir fails honestly
    # (no diskless jail). Local Bridge may omit this.
    workspace_root: str | None = None
    # Viewport / keyframe geometry, threaded from settings so the driver renders at the
    # keyframe width from the first paint.
    viewport_width: int = 1280
    viewport_height: int = 800
    jpeg_quality: int = 70
    # Multi-session (M0): optional primary key + run binding + host kind. The registry
    # resolves/creates; the factory may ignore these (sandbox path only needs conversation).
    session_id: str | None = None
    run_id: str | None = None
    host_kind: str = "sandbox"


@dataclass
class BrowserCommand:
    """One command sent to the in-sandbox driver over the RPC channel."""

    action: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrowserCommandResult:
    """The driver's reply to one command.

    ``data`` is the structured, model-facing result (status/title/url/a11y tree/…).
    State-changing replies may also carry wire receipts aligned with Local host:

    - ``type`` → ``data["typed"]`` :class:`TypedReceipt`
    - ``click`` → ``data["clicked"]`` :class:`ClickedReceipt`
    - snapshots include ``elements`` (ref table + optional ``visible_text``) and
      Sandbox-only best-effort ``aria`` (Playwright ``aria_snapshot``; Local may
      leave ``aria`` empty).

    ``frame`` is the optional raw jpeg keyframe bytes (state-changing actions +
    ``screenshot``); the host applies the keyframe budget and writes it to the
    workspace — the sandbox never touches the real workspace.
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    frame: bytes | None = None


@runtime_checkable
class BrowserSession(Protocol):
    """A long-lived, session-scoped browser the registry keeps per conversation."""

    conversation_id: str
    created_at: float
    last_used: float

    @property
    def alive(self) -> bool:
        """False once the driver crashed / the session was closed."""
        ...

    async def send(self, command: BrowserCommand) -> BrowserCommandResult:
        """Run one command; raise :class:`BrowserDriverCrashedError` if the channel broke."""
        ...

    def set_frame_listener(self, listener: BrowserFrameListener | None) -> None:
        """Route driver-pushed live screencast frames to ``listener`` (None to unwire, M1)."""
        ...

    async def start_screencast(self) -> None:
        """Begin CDP screencast (live frames start flowing to the frame listener, M1)."""
        ...

    async def stop_screencast(self) -> None:
        """Stop CDP screencast (idempotent; best-effort on a dead session, M1)."""
        ...

    async def close(self) -> None:
        """Tear down the browser + sandbox (idempotent)."""
        ...


@runtime_checkable
class BrowserSessionProvider(Protocol):
    """The sandbox provider's optional session surface (added alongside execute())."""

    def supports_browser_sessions(self) -> bool:
        """True only for a real isolation boundary (cloud gVisor on Linux)."""
        ...

    async def open_browser_session(self, request: BrowserSessionRequest) -> BrowserSession:
        """Launch a long-lived browser sandbox; raise ``BrowserSessionError`` on failure."""
        ...
