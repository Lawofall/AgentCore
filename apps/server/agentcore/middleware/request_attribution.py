"""Bind HTTP request identity for pool-holder attribution.

Checkout listeners can only see what is already on the task's contextvars /
task name. Turn-level ids (trace / conversation / …) are often unbound when a
request session is checked out, so every HTTP request binds a cheap method +
path + short req id *before* any handler runs.

Also stamps the raw ``X-Client-Platform`` / ``X-Client-Version`` headers as
``client_platform`` / ``client_version`` so patrol can slice symptoms by
client build. These are request-scoped contextvars (same as ``http_*``), not
per-event kwargs — low cardinality, no extra emit sites. Missing header is
``-``; a present-but-empty (or whitespace-only) header is ``""`` so the two
never collapse.

Optional ``X-AgentCore-Stream-Path-Reason`` (desktop cloud-path enum) binds
``stream_path_reason`` only when the value is allowlisted; unknown / absent
headers are left unbound so GET/sidecar traffic does not stamp a dummy ``-``.

Pure ASGI (not BaseHTTPMiddleware) so it shares the same task as the route
handler — the place where ``get_session`` checkouts actually happen.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from starlette.types import ASGIApp, Receive, Scope, Send
from structlog.contextvars import bound_contextvars

# Explicit "header was not sent". Distinct from ``""`` (sent, empty after strip).
MISSING_CLIENT_HEADER = "-"

_PLATFORM_HEADER = b"x-client-platform"
_VERSION_HEADER = b"x-client-version"
_STREAM_PATH_REASON_HEADER = b"x-agentcore-stream-path-reason"
# Hard cap so a junk header cannot inflate every log line; real values are short
# (``desktop`` / ``0.9.4`` / ``dev``).
_MAX_PLATFORM_LEN = 32
_MAX_VERSION_LEN = 64

# Desktop ``CloudStreamPathReason`` — same enum as ``streamPathReason.ts``.
STREAM_PATH_REASONS = frozenset(
    {
        "switch_off",
        "no_local_engine",
        "probe_unhealthy",
        "probe_cache_bad",
        "no_local_target",
        "sidecar_fallback",
    }
)


def _raw_header(scope: Scope, name: bytes) -> str | None:
    """Return the header body if ``name`` is present (even when empty), else None."""
    for key, value in scope.get("headers") or ():
        if key != name:
            continue
        try:
            return value.decode("latin-1")
        except UnicodeDecodeError:
            return ""
    return None


def client_header_for_log(raw: str | None, *, max_len: int) -> str:
    """Map a raw header to the log-context value.

    ``None`` (absent) → ``MISSING_CLIENT_HEADER``. Present → stripped; empty
    after strip stays ``""``. Over-long values are truncated, not rejected.
    """
    if raw is None:
        return MISSING_CLIENT_HEADER
    stripped = raw.strip()
    if not stripped:
        return ""
    if len(stripped) > max_len:
        return stripped[:max_len]
    return stripped


def stream_path_reason_for_log(raw: str | None) -> str | None:
    """Allowlisted desktop cloud-path reason, or ``None`` to leave unbound.

    Unknown / empty values are dropped (not truncated into a fake enum).
    """
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in STREAM_PATH_REASONS:
        return value
    return None


class RequestAttributionMiddleware:
    """Stamp request identity + client headers onto the request task."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "?")
        path = str(scope.get("path") or "?")
        req_id = uuid4().hex[:12]
        platform = client_header_for_log(
            _raw_header(scope, _PLATFORM_HEADER), max_len=_MAX_PLATFORM_LEN
        )
        version = client_header_for_log(
            _raw_header(scope, _VERSION_HEADER), max_len=_MAX_VERSION_LEN
        )
        path_reason = stream_path_reason_for_log(
            _raw_header(scope, _STREAM_PATH_REASON_HEADER)
        )

        task = asyncio.current_task()
        if task is not None:
            # Replace the useless BaseHTTPMiddleware coro name so snapshots can
            # answer "which request" from ``task_name`` alone.
            task.set_name(f"http:{method} {path}")

        ctx: dict[str, str] = {
            "http_method": method,
            "http_path": path,
            "http_req_id": req_id,
            "client_platform": platform,
            "client_version": version,
        }
        if path_reason:
            ctx["stream_path_reason"] = path_reason
        with bound_contextvars(**ctx):
            await self.app(scope, receive, send)
