"""OpenCode Zen/Go outbound headers — session affinity + product User-Agent.

OpenCode Go requires a stable ``x-opencode-session`` on coding-agent traffic so
it can route and prompt-cache. Missing it is a hard 400 (as of 2026-09-06).
Value = our ``conversation_id`` (already sticky for the platform pool). Do not
freeze this onto a cached HTTP client: ``PlatformProvider`` keys leaves by
``(api_key, base_url)``, so per-request headers are the only safe seam.

User-Agent is ``AgentCore/1.0`` (own product name, not ``python-httpx`` and not
the official CLI). ``GET /models`` does not need the session header.
"""

from __future__ import annotations

from uuid import uuid4

from agentcore.llm.byok_provider_presets import is_opencode_byok_endpoint

OPENCODE_SESSION_HEADER = "x-opencode-session"
OPENCODE_USER_AGENT = "AgentCore/1.0"


def opencode_client_headers(base_url: str) -> dict[str, str]:
    """Default client headers for an OpenCode Zen/Go leaf (User-Agent only)."""
    if not is_opencode_byok_endpoint(base_url):
        return {}
    return {"User-Agent": OPENCODE_USER_AGENT}


def _ascii_token(raw: str) -> str | None:
    token = raw.strip()
    if not token:
        return None
    try:
        token.encode("ascii")
    except UnicodeEncodeError:
        return None
    return token


def opencode_session_headers(base_url: str) -> dict[str, str]:
    """Per-request ``x-opencode-session`` for OpenCode ``POST /chat/completions``.

    Conversation id wins (stable across CEO / worker / title / memory). Probe /
    chrome without a conversation uses ``probe:{trace_id}`` or a fresh probe id.
    Never an empty value — that passes the gate but destroys prompt cache.
    """
    if not is_opencode_byok_endpoint(base_url):
        return {}
    from agentcore.core.log_context import get_log_value

    session = _ascii_token(get_log_value("conversation_id"))
    if session is None:
        trace = _ascii_token(get_log_value("trace_id"))
        session = f"probe:{trace}" if trace else f"probe:{uuid4().hex}"
    return {OPENCODE_SESSION_HEADER: session}
