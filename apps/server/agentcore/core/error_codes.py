"""Canonical catalog of user-facing AgentCore error codes — the single backend
directory the rest of the server references instead of bare string literals.

Every ``code`` that can reach a client is declared here exactly once: the
``code`` on an :class:`~agentcore.core.errors.AgentCoreError` HTTP body *and*
the ``code`` of an SSE ``error`` event. Declaring them in one ``StrEnum`` makes
the set greppable, documented, and impossible to silently drift apart across
modules (e.g. one place emitting ``"NOT_FOUND"`` and another ``"NotFound"``).

Single-source discipline:
  - ``core/errors.py`` sets each ``AgentCoreError`` subclass's ``code`` from a
    member here (guarded by ``tests/test_error_codes.py``).
  - The SSE emitters (pipeline / conversation service / engine / handoff) pass
    members here to ``error_event`` rather than literals.
  - Frontend catalog: ``pnpm gen:types`` dumps this enum into
    ``packages/contract-types/src/errorCodes.generated.ts`` (policy overlays such
    as key-config / non-retriable stay hand-written in ``errorCodes.ts``).

Grouping below is by ORIGIN, not HTTP status.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """All user-facing error codes (SSE ``error`` events + ``AgentCoreError``).

    A ``StrEnum`` so a member is a drop-in ``str`` everywhere a code is expected
    (``==`` comparisons, ``json.dumps`` payloads, SSE/HTTP bodies) while still
    being the one declared catalog.
    """

    # ── Generic / pipeline plumbing ──────────────────────────────────────
    INTERNAL_ERROR = "INTERNAL_ERROR"  # AgentCoreError base; unexpected server fault
    PIPELINE_ERROR = "PIPELINE_ERROR"  # chat pipeline crashed with no coded cause
    STREAM_ERROR = "STREAM_ERROR"  # SSE turn crashed before a coded cause surfaced
    INVALID = "INVALID"  # malformed request inside an already-open stream

    # ── Request validation / resource (also AgentCoreError → HTTP) ───────
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    CLIENT_TOO_OLD = "CLIENT_TOO_OLD"  # below DESKTOP_/MOBILE_MIN_VERSION (HTTP 426)

    # ── Auth / quota / rate ──────────────────────────────────────────────
    AUTH_ERROR = "AUTH_ERROR"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    GONE = "GONE"
    FORBIDDEN = "FORBIDDEN"
    # Cookie-session mutating request without a usable X-CSRF-Token (HTTP 403,
    # emitted by CsrfMiddleware before the route runs).
    CSRF_FAILED = "CSRF_FAILED"
    ADMIN_PRODUCT_FORBIDDEN = "ADMIN_PRODUCT_FORBIDDEN"
    MFA_REQUIRED = "MFA_REQUIRED"
    MFA_SETUP_REQUIRED = "MFA_SETUP_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"

    # ── LLM provider (DeepSeek / BYOK) ───────────────────────────────────
    LLM_ERROR = "LLM_ERROR"
    # Empty-response ladder → finish_reason=degraded; dedicated code so clients
    # do not treat it as transport failure (Base URL / API Key escalation).
    LLM_EMPTY_RESPONSE = "LLM_EMPTY_RESPONSE"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_INSUFFICIENT_BALANCE = "LLM_INSUFFICIENT_BALANCE"  # valid key, empty wallet (402)
    LLM_KEY_INVALID = "LLM_KEY_INVALID"  # configured key rejected mid-turn (401/403)
    INFERENCE_TOKEN_EXPIRED = "INFERENCE_TOKEN_EXPIRED"  # sidecar cloud-proxy JWT invalid/expired
    LLM_KEY_REQUIRED = "LLM_KEY_REQUIRED"  # no BYOK key at preflight (402)
    PLATFORM_BILLING_UNAVAILABLE = "PLATFORM_BILLING_UNAVAILABLE"  # platform mode but no operator key (503)
    KEY_STORAGE_UNAVAILABLE = "KEY_STORAGE_UNAVAILABLE"  # no master encryption key (503)
    # Primary pool exhausted (or DB unreachable on the request path) → HTTP 503.
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"

    # ── Tools / sandbox ──────────────────────────────────────────────────
    TOOL_ERROR = "TOOL_ERROR"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"

    # ── Handoff (跨端接力) ─────────────────────────────────────────────────
    HANDOFF_DISPATCH_FAILED = "HANDOFF_DISPATCH_FAILED"
    HANDOFF_FAILED = "HANDOFF_FAILED"
    HANDOFF_SNAPSHOT_NOT_FOUND = "HANDOFF_SNAPSHOT_NOT_FOUND"
    HANDOFF_APPLY_FAILED = "HANDOFF_APPLY_FAILED"
