"""L3 team-browser (M0) sandbox session surface — long-lived Chromium in gVisor.

See ``docs/04-前端/前端技术与架构.md`` §9.12（BrowserSession）. This package holds the sandbox-side
control channel (driver + stdio RPC), the network isolation (netns + veth), the
SSRF egress proxy, and the runsc session orchestration.
"""

from agentcore.tools.sandbox.browser.protocol import (
    BROWSER_ACTIONS,
    STATE_CHANGING_ACTIONS,
    BrowserCommand,
    BrowserCommandResult,
    BrowserDriverCrashedError,
    BrowserSession,
    BrowserSessionAcquireError,
    BrowserSessionError,
    BrowserSessionProvider,
    BrowserSessionRequest,
    BrowserSessionsBusyError,
    ClickedReceipt,
    TypedReceipt,
)

__all__ = [
    "BROWSER_ACTIONS",
    "STATE_CHANGING_ACTIONS",
    "BrowserCommand",
    "BrowserCommandResult",
    "BrowserDriverCrashedError",
    "BrowserSession",
    "BrowserSessionAcquireError",
    "BrowserSessionError",
    "BrowserSessionProvider",
    "BrowserSessionRequest",
    "BrowserSessionsBusyError",
    "ClickedReceipt",
    "TypedReceipt",
]
