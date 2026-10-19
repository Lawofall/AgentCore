"""Per-turn LLM credential carrier and cloud-proxy HTTP headers.

Attribution headers let the inference proxy stamp each proxied LLM call with
the sidecar worker's run tree (run / agent / role / persona) so per-call detail
rows and per-run aggregates stay isomorphic with in-process cloud turns.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from agentcore.llm.profiles import PLATFORM_MODEL_FLASH

INFERENCE_CONVERSATION_HEADER = "X-AgentCore-Conversation"
INFERENCE_TRACE_HEADER = "X-AgentCore-Trace"
INFERENCE_MESSAGE_HEADER = "X-AgentCore-Message"
INFERENCE_RUN_HEADER = "X-AgentCore-Run"
INFERENCE_PARENT_RUN_HEADER = "X-AgentCore-Parent-Run"
INFERENCE_AGENT_HEADER = "X-AgentCore-Agent"
INFERENCE_ROLE_HEADER = "X-AgentCore-Role"
INFERENCE_PERSONA_HEADER = "X-AgentCore-Persona"
INFERENCE_CALL_HEADER = "X-AgentCore-Call"

CredentialOrigin = Literal["user", "platform"]

# Operator aliases for logs / cost_calls. Rejects key-shaped values (sk-… / last-4).
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


@dataclass(frozen=True)
class LLMCredentials:
    api_key: str
    base_url: str
    default_model: str = field(default=PLATFORM_MODEL_FLASH)
    extra_headers: dict[str, str] | None = None
    # Call-level origin for pricing: user BYOK → estimated ledger; platform → billed.
    source: CredentialOrigin = "user"
    # Optional cheaper model for background purposes (legacy carrier; multi-provider
    # resolves the background model via the account-level pointer, not this field).
    background_model: str | None = None
    # The BYOK provider these credentials came from (user_llm_providers.id), when a
    # specific 服务商 was resolved. None for the platform key / unspecified.
    provider_id: str | None = None
    # User-facing 服务商 label from ``user_llm_providers.label`` (BYOK only).
    # Carried so ``build_provider`` can set leaf display names without re-querying DB.
    # Never used as the log ``provider`` field — that stays ``source`` / vendor prefix.
    label: str | None = None
    # Saved vendor identity (``openai`` / ``opencode_go`` / ``custom`` …). Protocol
    # dispatch reads this, not whether ``base_url`` matches a preset URL.
    vendor_id: str = "custom"
    # Which platform-pool member funded this call. Alias or stable hash of
    # (api_key, base_url) — never key plaintext / last-4. Logs + cost_calls only;
    # never SSE error context. None on BYOK / vendor.
    platform_credential_id: str | None = None


def sanitize_platform_credential_alias(raw: str | None, *, api_key: str = "") -> str | None:
    """Keep a configured alias only when it cannot be a key (or the key's last 4)."""
    text = (raw or "").strip()
    if not text or not _ALIAS_RE.fullmatch(text):
        return None
    lowered = text.lower()
    if lowered.startswith("sk-") or lowered.startswith("sk_"):
        return None
    key = (api_key or "").strip()
    if key and text == key:
        return None
    if len(key) >= 4 and text.endswith(key[-4:]):
        return None
    return text


def hash_platform_credential_id(api_key: str, base_url: str = "") -> str:
    """Stable non-secret id for a ``(api_key, base_url)`` pair (cooldown_gate posture)."""
    material = f"{api_key}\0{base_url}".encode()
    return "pk_" + hashlib.sha256(material).hexdigest()[:16]


def derive_platform_credential_id(api_key: str, base_url: str = "") -> str:
    """Resolve the log/ledger id for one platform credential.

    Precedence: matching ``PLATFORM_MODEL_CREDENTIALS`` entry ``id`` →
    ``PLATFORM_CREDENTIAL_ID`` when this is the shared default pair → hash.
    Empty key → ``""`` (caller skips bind).
    """
    key = (api_key or "").strip()
    url = (base_url or "").strip()
    if not key:
        return ""

    from agentcore.config import settings
    from agentcore.config.platform import parse_platform_model_credentials

    default_key = settings.platform_api_key.strip()
    default_url = (settings.platform_base_url or "").strip()
    parsed = parse_platform_model_credentials(settings.platform_model_credentials)
    for entry in parsed.values():
        entry_key = (entry.get("api_key") or "").strip() or default_key
        entry_url = (entry.get("base_url") or "").strip() or default_url
        if entry_key != key or entry_url != url:
            continue
        alias = sanitize_platform_credential_alias(entry.get("id"), api_key=key)
        if alias:
            return alias

    if key == default_key and url == default_url:
        alias = sanitize_platform_credential_alias(settings.platform_credential_id, api_key=key)
        if alias:
            return alias
    return hash_platform_credential_id(key, url)


def bind_platform_credential_id(credential_id: str | None) -> None:
    """Bind or drop ambient ``platform_credential_id`` (call-level; mixed-router safe)."""
    from agentcore.core.log_context import bind_log_context, unbind_log_context

    cid = (credential_id or "").strip()
    if cid:
        bind_log_context(platform_credential_id=cid)
    else:
        unbind_log_context("platform_credential_id")


def bind_credential_pricing_context(creds: LLMCredentials | None) -> None:
    """Bind call-level ``credential_source`` (+ optional ``provider_id``) into structlog.

    Fresh turns (:func:`prepare_chat_turn`) and durable resumes must both call this
    before any LLM work — ``calculate_cost`` / ``log_llm_call`` / the call meter read
    ambient ``credential_source`` when the call site does not pass an explicit source.
    Skipping the bind on resume made BYOK calls fall through to ``platform`` and
    write false billed amounts.

    When ``creds.provider_id`` is set it is bound as ambient ``provider_id`` so
    failure / stall / turn-complete lines can attach it without logging secrets or
    ``base_url``. Platform / unset → leave ``provider_id`` unbound (callers skip).

    Platform calls also bind ``platform_credential_id`` (logs + ledger). BYOK
    unbinds it so a prior platform extra in the same task cannot leak.
    """
    from agentcore.core.log_context import bind_log_context

    if creds is None:
        bind_log_context(credential_source="platform")
        from agentcore.config import settings

        bind_platform_credential_id(
            derive_platform_credential_id(settings.platform_api_key, settings.platform_base_url)
        )
        return
    bind_log_context(credential_source=creds.source)
    if creds.source == "platform":
        bind_platform_credential_id(
            creds.platform_credential_id
            or derive_platform_credential_id(creds.api_key, creds.base_url)
        )
    else:
        bind_platform_credential_id(None)
    if creds.provider_id:
        bind_log_context(provider_id=creds.provider_id)


_API_KEY_NON_ASCII_MESSAGE = (
    "API Key 含有全角或中文符号，无法用于请求。"
    "请重新复制粘贴纯英文/数字的 Key，勿混入中文括号等字符。"
)


def require_http_header_safe_api_key(api_key: str) -> str:
    """Reject keys that cannot be placed in an HTTP ``Authorization`` header.

    httpx encodes header values as ASCII; fullwidth punctuation (e.g. ``（``)
    otherwise raises ``UnicodeEncodeError`` → unhandled 500 on /test and chat.
    """
    from agentcore.core.errors import ValidationError

    text = (api_key or "").strip()
    if not text:
        raise ValidationError("API Key 不能为空")
    try:
        text.encode("ascii")
    except UnicodeEncodeError as e:
        raise ValidationError(_API_KEY_NON_ASCII_MESSAGE) from e
    return text
