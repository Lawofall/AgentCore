"""Webhook trigger helpers for user workflows.

Secret hashing mirrors refresh tokens (high-entropy random → SHA-256). Rate limit
and idempotency are process-local sliding windows / TTL maps (single-worker posture).
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import time
from threading import Lock

from agentcore.config import settings
from agentcore.core.errors import AuthenticationError, RateLimitedError
from agentcore.core.rate_limit import SlidingWindowRateLimiter
from agentcore.workflows.paths import webhook_path as webhook_path

_EVENT_TEXT_MAX = 16_000
_SECRET_BYTES = 32

# Module singletons — tests reset via ``reset_webhook_state``.
_webhook_rate_limiter = SlidingWindowRateLimiter(
    max_requests=settings.workflow_trigger_webhook_rate_limit_max,
    window_seconds=settings.workflow_trigger_webhook_rate_limit_window_seconds,
)
_idempotency_lock = Lock()
_idempotency: dict[str, tuple[str, float]] = {}  # key → (conversation_id, expires_at)


def hash_webhook_secret(raw: str) -> str:
    """Persist-only digest of a webhook secret (never store plaintext)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_webhook_secret() -> tuple[str, str]:
    """Return ``(raw, hash)``: send ``raw`` once, persist ``hash``."""
    raw = secrets.token_urlsafe(_SECRET_BYTES)
    return raw, hash_webhook_secret(raw)


def verify_webhook_secret(raw: str | None, expected_hash: str | None) -> bool:
    if not raw or not expected_hash:
        return False
    return secrets.compare_digest(hash_webhook_secret(raw), expected_hash)


def extract_secret_from_headers(
    *,
    authorization: str | None,
    x_webhook_secret: str | None,
) -> str | None:
    """Prefer ``Authorization: Bearer``, else ``X-AgentCore-Webhook-Secret``."""
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    if x_webhook_secret and x_webhook_secret.strip():
        return x_webhook_secret.strip()
    return None


def require_webhook_secret(
    *,
    authorization: str | None,
    x_webhook_secret: str | None,
    expected_hash: str | None,
) -> None:
    raw = extract_secret_from_headers(
        authorization=authorization, x_webhook_secret=x_webhook_secret
    )
    if not verify_webhook_secret(raw, expected_hash):
        raise AuthenticationError("Webhook 密钥无效")


def extract_event_text(body: bytes, *, content_type: str | None = None) -> str:
    """Prefer JSON ``text`` / ``message``; else truncated raw body as text."""
    ct = (content_type or "").lower()
    if "json" in ct and body:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict):
            for key in ("text", "message"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()[:_EVENT_TEXT_MAX]
        if isinstance(data, str) and data.strip():
            return data.strip()[:_EVENT_TEXT_MAX]
    text = body.decode("utf-8", errors="replace").strip()
    return text[:_EVENT_TEXT_MAX]


def enforce_webhook_rate_limit(workflow_id: str, *, now: float | None = None) -> None:
    """Per-workflow sliding window; raise :class:`RateLimitedError` when over cap."""
    if (
        not settings.rate_limit_enabled
        or settings.workflow_trigger_webhook_rate_limit_max <= 0
    ):
        return
    decision = _webhook_rate_limiter.check(workflow_id, now=now)
    if not decision.allowed:
        retry_after = max(1, math.ceil(decision.retry_after))
        raise RateLimitedError(
            f"Webhook 触发过于频繁，请约 {retry_after} 秒后再试。",
            retry_after=decision.retry_after,
        )


def idempotency_lookup(webhook_id: str, key: str) -> str | None:
    """Return prior ``conversation_id`` for ``(webhook_id, key)`` if still within TTL."""
    cache_key = f"{webhook_id}:{key}"
    now = time.monotonic()
    with _idempotency_lock:
        entry = _idempotency.get(cache_key)
        if entry is None:
            return None
        conversation_id, expires = entry
        if expires <= now:
            del _idempotency[cache_key]
            return None
        return conversation_id


def idempotency_store(webhook_id: str, key: str, conversation_id: str) -> None:
    ttl = float(settings.workflow_trigger_webhook_idempotency_ttl_seconds)
    cache_key = f"{webhook_id}:{key}"
    with _idempotency_lock:
        _idempotency[cache_key] = (conversation_id, time.monotonic() + ttl)
        if len(_idempotency) > 10_000:
            now = time.monotonic()
            dead = [k for k, (_, exp) in _idempotency.items() if exp <= now]
            for k in dead:
                del _idempotency[k]


def reset_webhook_state() -> None:
    """Clear rate-limit + idempotency state (test isolation)."""
    _webhook_rate_limiter.reset()
    with _idempotency_lock:
        _idempotency.clear()
