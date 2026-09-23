"""OpenCode Zen/Go outbound headers — session affinity + product User-Agent.

OpenCode Go requires a stable ``x-opencode-session`` on coding-agent traffic so
it can route and prompt-cache. Missing it is a hard 400 (as of 2026-09-06).
Their disk cache is partitioned by this header. Value = ``user:{user_id}`` so a
new conversation reuses the prefix already warmed for that user on this
upstream. No user id → ``conversation_id`` (CEO / worker / title in that chat
still share one header). Do not freeze this — or ``Authorization`` — onto the
origin-pooled HTTP client: multi-tenant / failover / extra headers must stay
per-request. The platform pool still pins a key per conversation; this header
does not.

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

    ``user:{user_id}`` wins so new chats share one cache namespace. Conversation
    id is the fallback when the call has no user. Probe / chrome without either
    uses ``probe:{trace_id}`` or a fresh probe id. Never an empty value — that
    passes the gate but destroys prompt cache.
    """
    if not is_opencode_byok_endpoint(base_url):
        return {}
    from agentcore.core.log_context import get_log_value

    user = _ascii_token(get_log_value("user_id"))
    if user is not None:
        session = f"user:{user}"
    else:
        session = _ascii_token(get_log_value("conversation_id"))
    if session is None:
        trace = _ascii_token(get_log_value("trace_id"))
        session = f"probe:{trace}" if trace else f"probe:{uuid4().hex}"
    return {OPENCODE_SESSION_HEADER: session}
