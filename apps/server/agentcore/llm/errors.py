"""LLM error context helpers — unified upstream diagnostics."""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import (
    RETRY_AFTER_FROM_HEADER,
    RETRY_AFTER_UNKNOWN,
    AgentCoreError,
    InferenceTokenExpiredError,
    LLMAuthError,
    LLMError,
    LLMInsufficientBalanceError,
    LLMKeyRequiredError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMUpstreamError,
    OurServiceUnavailableError,
    upstream_rate_limit_error,
    wire_moments,
)

# Keep in sync with ``db.errors.DATABASE_UNAVAILABLE_MESSAGE`` — do not import
# ``db.errors`` here (llm → db → repositories → llm.profiles cycle).
_OUR_SERVICE_UNAVAILABLE_MESSAGE = "AgentCore 服务暂时不可用，请稍后重试"

# Vendor / origin 530 (and a relayed 「（530）」sentence): the selected model, not us.
# Keep in sync with desktop ``SELECTED_MODEL_UNAVAILABLE_MESSAGE``.
SELECTED_MODEL_UNAVAILABLE_MESSAGE = (
    "你选的模型暂时不可用，请稍后再试或换一个模型。"
)

# Tools already ran this turn, then the vendor rejected the next request (4xx).
# Process stays on the timeline; this sentence is the bubble face. Not a Class B delete.
HALFWAY_VENDOR_REJECT_MESSAGE = "做到一半，对方拒绝了这次请求。"

# Our-cloud faults a retry cannot clear (server misconfiguration, not pressure).
_OUR_SERVICE_PERMANENT_CODES = frozenset(
    {ErrorCode.KEY_STORAGE_UNAVAILABLE, ErrorCode.PLATFORM_BILLING_UNAVAILABLE}
)

_BODY_PREVIEW_MAX = 500


class EmptyResponseDiagnosis(StrEnum):
    # Upstream returned HTML / login / gateway page instead of a chat completion.
    # (Formerly oauth_expired — that name falsely implied Sub2API OAuth expiry.)
    UPSTREAM_NON_API = "upstream_non_api"
    CONTENT_FILTERED = "content_filtered"
    MODEL_UNKNOWN = "model_unknown"
    SILENT_EMPTY = "silent_empty"
    FORMAT_MISMATCH = "format_mismatch"
    # Upstream finish_reason=length with empty body + no tools (protocol-proven).
    LENGTH_EMPTY = "length_empty"


# HTML shell or auth/login phrasing — not a model completion body.
_NON_API_MARKERS = re.compile(
    r"(<html|</html>|<!doctype|oauth|sign[\s_-]?in|login|unauthorized|access[\s_-]?denied)",
    re.IGNORECASE,
)
_MODEL_UNKNOWN_MARKERS = re.compile(
    r"(model[\s_-]?(not[\s_-]?found|does[\s_-]?not[\s_-]?exist|unknown|invalid)|"
    r"unknown[\s_-]?model|invalid[\s_-]?model)",
    re.IGNORECASE,
)

_DIAGNOSIS_LABELS: dict[EmptyResponseDiagnosis, str] = {
    EmptyResponseDiagnosis.UPSTREAM_NON_API: (
        "上游返回了网页或登录页，请检查服务商地址与鉴权"
    ),
    EmptyResponseDiagnosis.CONTENT_FILTERED: "内容被过滤",
    EmptyResponseDiagnosis.MODEL_UNKNOWN: "模型名未被上游识别",
    EmptyResponseDiagnosis.SILENT_EMPTY: "模型返回空内容",
    EmptyResponseDiagnosis.FORMAT_MISMATCH: "上游响应格式异常",
    EmptyResponseDiagnosis.LENGTH_EMPTY: "输出长度截断 · 返回空内容",
}


def diagnose_empty_response(
    *,
    raw_body: str | None,
    finish_reason: str | None = None,
    format_mismatch: bool = False,
) -> EmptyResponseDiagnosis:
    """Classify an empty LLM response from upstream body / finish_reason."""
    if format_mismatch:
        return EmptyResponseDiagnosis.FORMAT_MISMATCH
    if finish_reason == "content_filter":
        return EmptyResponseDiagnosis.CONTENT_FILTERED
    # Protocol field only — no reasoning-length / token-cap heuristics.
    if finish_reason == "length":
        return EmptyResponseDiagnosis.LENGTH_EMPTY
    text = (raw_body or "").strip()
    if text and _NON_API_MARKERS.search(text):
        return EmptyResponseDiagnosis.UPSTREAM_NON_API
    if text and _MODEL_UNKNOWN_MARKERS.search(text):
        return EmptyResponseDiagnosis.MODEL_UNKNOWN
    if text:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and data.get("error"):
                err = data["error"]
                err_text = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
                if _NON_API_MARKERS.search(err_text):
                    return EmptyResponseDiagnosis.UPSTREAM_NON_API
                if _MODEL_UNKNOWN_MARKERS.search(err_text):
                    return EmptyResponseDiagnosis.MODEL_UNKNOWN
        except json.JSONDecodeError:
            if "<" in text and ">" in text:
                return EmptyResponseDiagnosis.UPSTREAM_NON_API
            # A non-JSON ``text`` here is the streaming SSE tail (several ``data:``
            # lines), NOT a real format error — the genuine "couldn't parse any
            # chunk" case is signalled up front by the explicit ``format_mismatch``
            # flag. Fall through to SILENT_EMPTY so a clean tool_calls/stop finish
            # with empty deltas reads as "模型返回空内容" instead of the misleading
            # "上游响应格式异常".
    return EmptyResponseDiagnosis.SILENT_EMPTY


def _coerce_diagnosis(
    diagnosis: str | EmptyResponseDiagnosis | None,
) -> EmptyResponseDiagnosis | None:
    """Normalize wire/journal diagnosis keys (incl. legacy oauth_expired)."""
    if diagnosis is None:
        return None
    if isinstance(diagnosis, EmptyResponseDiagnosis):
        return diagnosis
    if diagnosis == "oauth_expired":
        return EmptyResponseDiagnosis.UPSTREAM_NON_API
    try:
        return EmptyResponseDiagnosis(diagnosis)
    except ValueError:
        return None


def empty_response_body_kind(raw_body: str | None) -> str:
    """Coarse body class for SSE error context / 排查包 (not the raw HTML)."""
    text = (raw_body or "").strip()
    if not text:
        return "empty"
    lowered = text[:2000].lower()
    if "<html" in lowered or "<!doctype" in lowered or '<div id="root"' in lowered:
        return "html"
    if text[0] in "{[":
        try:
            json.loads(text)
            return "json"
        except json.JSONDecodeError:
            return "text"
    return "text"


def empty_response_error_context(
    *,
    diagnosis: str | EmptyResponseDiagnosis | None,
    raw_preview: str | None = None,
    base_url: str | None = None,
) -> dict[str, str] | None:
    """SSE ``error.context`` for empty-response degraded (no raw HTML leak)."""
    ctx: dict[str, str] = {}
    key = _coerce_diagnosis(diagnosis)
    if key is not None:
        ctx["empty_diagnosis"] = key.value
    elif diagnosis is not None:
        ctx["empty_diagnosis"] = str(diagnosis)
    kind = empty_response_body_kind(raw_preview)
    if kind != "empty" or raw_preview is not None:
        ctx["body_kind"] = kind
    if base_url:
        ctx["base_url"] = base_url.rstrip("/")
    return ctx or None


def empty_response_event_message(diagnosis: str | EmptyResponseDiagnosis | None) -> str:
    """User-facing SSE error message for a degraded empty-response finish.

    ``length_empty`` is a first-round hard cutoff (not a streak), so its copy
    must not say「多次空响应」— that wording is reserved for the silent-empty ladder.
    """
    key = _coerce_diagnosis(diagnosis)
    if key is not None:
        if key is EmptyResponseDiagnosis.LENGTH_EMPTY:
            return f"模型空响应 · {_DIAGNOSIS_LABELS[key]}"
        label = _DIAGNOSIS_LABELS.get(key)
        base = "模型多次空响应"
        return f"{base} · {label}" if label else base
    if diagnosis is not None:
        return f"模型多次空响应 · {diagnosis}"
    return "模型多次空响应"


def empty_response_chip_label(diagnosis: str | EmptyResponseDiagnosis | None) -> str | None:
    """Short label for the degraded finish-reason chip (diagnosis only)."""
    if diagnosis is None:
        return None
    key = _coerce_diagnosis(diagnosis)
    if key is not None:
        return _DIAGNOSIS_LABELS.get(key)
    return str(diagnosis)


@dataclass(frozen=True)
class LLMErrorContext:
    upstream_status: int
    upstream_body_preview: str | None
    retry_attempts: int


def body_preview(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.strip()
    if not text:
        return None
    if len(text) > _BODY_PREVIEW_MAX:
        return text[:_BODY_PREVIEW_MAX] + "…"
    return text


def _extract_upstream_message(preview: str | None) -> str | None:
    if not preview:
        return None
    try:
        data = json.loads(preview)
    except json.JSONDecodeError:
        return preview if len(preview) <= 200 else preview[:200] + "…"
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if msg:
            return str(msg)
    if isinstance(err, str):
        return err
    msg = data.get("message")
    return str(msg) if msg else None


# OpenCode Go (and similar relays) put the real vendor subcode in the message
# as ``[unsupported_tool_schema]``, often with a parenthetical reason
# ``(tool_count_limit)``. JSON ``error.code`` is frequently missing or a generic
# ``invalid_request_error`` — so the bracket token is the authoritative subcode.
_VENDOR_CODE_IN_MESSAGE = re.compile(r"\[([a-z][a-z0-9_]{1,64})\]", re.IGNORECASE)
_VENDOR_SUBREASON_IN_MESSAGE = re.compile(r"\(([a-z][a-z0-9_]{1,64})\)", re.IGNORECASE)
_UNSUPPORTED_TOOL_SCHEMA = "unsupported_tool_schema"


def _extract_json_error_code(preview: str | None) -> str | None:
    if not preview:
        return None
    try:
        data = json.loads(preview)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        return str(code) if code else None
    return None


def _extract_bracket_vendor_code(preview: str | None) -> str | None:
    """Vendor subcode in ``error.message``, e.g. ``[unsupported_tool_schema]``."""
    extracted = _extract_upstream_message(preview) or ""
    match = _VENDOR_CODE_IN_MESSAGE.search(extracted)
    return match.group(1).lower() if match else None


def _extract_upstream_code(preview: str | None) -> str | None:
    """JSON ``error.code``, else the ``[vendor_code]`` token in the message."""
    return _extract_json_error_code(preview) or _extract_bracket_vendor_code(preview)


def _extract_vendor_code(preview: str | None) -> str | None:
    """Prefer ``[vendor_code]`` in the message; JSON ``error.code`` is fallback.

    Bracket-first because Go 400s keep a generic JSON code (or none) and stamp
    the real subcode in ``[…]``.
    """
    bracket = _extract_bracket_vendor_code(preview)
    if bracket:
        return bracket
    code = (_extract_json_error_code(preview) or "").strip().lower()
    return code or None


def _extract_vendor_subreason(preview: str | None) -> str | None:
    """Parenthetical reason after the vendor bracket, e.g. ``(tool_count_limit)``."""
    extracted = _extract_upstream_message(preview) or ""
    bracket = _VENDOR_CODE_IN_MESSAGE.search(extracted)
    haystack = extracted[bracket.end() :] if bracket else extracted
    match = _VENDOR_SUBREASON_IN_MESSAGE.search(haystack)
    return match.group(1).lower() if match else None


@dataclass(frozen=True)
class AgentCoreErrorEnvelope:
    """Our wire envelope ``{"error":{"code","message","context"}}`` (catalogued code only)."""

    code: str
    message: str | None
    context: dict | None = None


def _envelope_raw_text(body: bytes | str | None) -> str | None:
    """Full wire body for envelope JSON. Never the log ``body_preview`` truncation."""
    if body is None:
        return None
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    text = text.strip()
    return text or None


def parse_agentcore_error_envelope(
    body: bytes | str | None,
) -> AgentCoreErrorEnvelope | None:
    """Parse our structured error envelope; None if shape/code is not ours.

    Only trusts ``{"error":{"code": <ErrorCode>, "message": ...}}``. Does not
    sniff free text or vendor gateway tutorials (CC Switch, etc.).

    Parses the **full** body. ``body_preview`` is a log cap (500 chars); feeding
    it here truncates a legal envelope that carries ``context.upstream_body_preview``
    and the sidecar then brands a vendor 530 as AgentCore.
    """
    raw = _envelope_raw_text(body)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if not isinstance(err, dict):
        return None
    raw_code = err.get("code")
    if not isinstance(raw_code, str) or not raw_code.strip():
        return None
    try:
        catalogued = ErrorCode(raw_code.strip())
    except ValueError:
        return None
    raw_msg = err.get("message")
    message = str(raw_msg).strip() if raw_msg is not None else None
    if message == "":
        message = None
    raw_ctx = err.get("context")
    context = raw_ctx if isinstance(raw_ctx, dict) else None
    return AgentCoreErrorEnvelope(code=catalogued.value, message=message, context=context)


def is_llm_family_error_code(code: str) -> bool:
    """True when the catalogued code is the LLM_* upstream-family prefix."""
    return code.startswith("LLM_")


class _EnvelopeLeafError(Protocol):
    """Constructor shape every leaf in the table below has to offer.

    ``message`` is optional because the envelope's is: a wire error whose
    ``message`` is missing, null or blank parses to ``None`` (see
    :func:`parse_agentcore_error_envelope`). Each leaf answers that with its own
    default copy — the CTA the client routes on — so the table may only hold
    classes that accept ``None`` rather than blanking the message.
    """

    def __call__(self, message: str | None = None, **details: Any) -> LLMError: ...


# Envelope code → the leaf error it stands for. Only codes a client *branches* on
# live here (key-config CTA, retry affordance, JWT remint): those are the ones a
# flattened code silently mistranslates. Everything else keeps falling through to
# the vendor-status heuristics, so this table never grows a case per HTTP status.
# ``LLM_RATE_LIMIT`` is built separately — its copy derives from ``retry_after``,
# not from the envelope message.
_ENVELOPE_LEAF_ERRORS: dict[str, _EnvelopeLeafError] = {
    ErrorCode.QUOTA_EXCEEDED: LLMQuotaExceededError,
    ErrorCode.LLM_KEY_REQUIRED: LLMKeyRequiredError,
    ErrorCode.LLM_KEY_INVALID: LLMAuthError,
    ErrorCode.LLM_INSUFFICIENT_BALANCE: LLMInsufficientBalanceError,
    ErrorCode.INFERENCE_TOKEN_EXPIRED: InferenceTokenExpiredError,
}


def _envelope_retry_after(context: dict) -> float | None:
    raw = context.get("retry_after")
    if raw is None:
        return None
    with contextlib.suppress(TypeError, ValueError):
        return float(raw)
    return None


def inference_envelope_error(
    *,
    status: int,
    body: bytes | str | None,
    retry_ceiling: float | None = None,
) -> LLMError | None:
    """Rebuild the typed error our ``/inference/`` hop already classified.

    On that hop the envelope — not the HTTP status — is the truth source. The proxy
    flattens every typed error onto 402 / 429 / 502, and it reports faults no vendor
    status can express (an exhausted allowance, a missing BYOK key), so classifying
    the response by its number reads quota exhaustion as vendor throttling and a
    missing key as an empty wallet. The cloud leaf also phrased the copy with the
    real provider label, so its ``message`` beats anything we could compose here.

    Returns ``None`` when the body is not our envelope, or carries a code no client
    branches on — the caller then falls back to the vendor-status heuristics, which
    stay the only source of truth on a direct-to-vendor hop.

    ``retry_ceiling`` rides through to the rebuilt 429 so a budgeted call decides
    the same way on this hop as it would talking to a vendor directly; the cloud
    leaf's own「already retried」opt-out is applied by the caller afterwards.
    """
    envelope = parse_agentcore_error_envelope(body)
    if envelope is None:
        return None
    context = envelope.context or {}
    # Prefer the vendor status / body the cloud leaf recorded: ours is only the
    # relay's number (same reason its ``message`` names the vendor's real status).
    upstream_status = context.get("upstream_status")
    if not isinstance(upstream_status, int):
        upstream_status = status
    preview = context.get("upstream_body_preview")
    if not isinstance(preview, str) or not preview.strip():
        preview = body_preview(body)
    details: dict[str, Any] = {
        "upstream_status": upstream_status,
        "upstream_body_preview": preview,
    }
    # Keeps the platform-vs-BYOK CTA split intact across the hop (平台LLM接入 §二).
    raw_source = context.get("credential_source")
    source: str | None = raw_source if raw_source in ("user", "platform") else None

    if envelope.code == ErrorCode.LLM_RATE_LIMIT:
        retry = _envelope_retry_after(context)
        # Envelope ``retry_after`` is only present when the leaf attested a header
        # (ErrorContext documents it as 上游 Retry-After). Rebuild with that source
        # so the hop does not treat a real header as unknown and drop the seconds.
        return upstream_rate_limit_error(
            retry,
            credential_source=source,
            retry_ceiling=retry_ceiling,
            retry_after_source=(
                RETRY_AFTER_FROM_HEADER if retry is not None else RETRY_AFTER_UNKNOWN
            ),
            **details,
        )
    leaf = _ENVELOPE_LEAF_ERRORS.get(envelope.code)
    if leaf is None:
        return None
    if source is not None:
        details["credential_source"] = source
    return leaf(envelope.message, **details)


def upstream_unavailable_message(status: int) -> str:
    """Product sentence for a vendor 5xx (direct hop or relayed after LLM_* envelope)."""
    if status == 530:
        return SELECTED_MODEL_UNAVAILABLE_MESSAGE
    return f"上游模型服务暂时不可用（{status}），请稍后再试"


def vendor_5xx_product_message(
    *,
    http_status: int,
    relayed: str | None,
    envelope: AgentCoreErrorEnvelope | None,
) -> str:
    """Bubble copy for vendor 5xx: 530 names the selected model, never AgentCore."""
    status = http_status
    if envelope is not None and isinstance(envelope.context, dict):
        raw = envelope.context.get("upstream_status")
        if isinstance(raw, int):
            status = raw
    if status == 530 or (relayed and "（530）" in relayed):
        return SELECTED_MODEL_UNAVAILABLE_MESSAGE
    return relayed or upstream_unavailable_message(status)


def overlay_progress_failure_message(
    *,
    code: str | None,
    message: str | None,
    context: dict | None,
) -> str:
    """When tools already ran and the next LLM call died: keep a face, not an empty bubble.

    4xx → halfway reject. 530 → selected model down. Other copy passes through.
    ``code`` is accepted for call-site symmetry; status on ``context`` is the gate.
    """
    _ = code
    status = None
    if isinstance(context, dict):
        raw = context.get("upstream_status")
        if isinstance(raw, int):
            status = raw
    if status == 530:
        return SELECTED_MODEL_UNAVAILABLE_MESSAGE
    if status is not None and 400 <= status < 500:
        return HALFWAY_VENDOR_REJECT_MESSAGE
    text = (message or "").strip()
    if text:
        return text
    if status is not None and status >= 500:
        return upstream_unavailable_message(status)
    return message or ""


def our_inference_service_5xx_error(
    *,
    status: int,
    body: bytes | str | None,
) -> OurServiceUnavailableError | None:
    """Map a 5xx from our ``/inference/`` hop to a coded our-side error.

    Returns ``None`` when the body is our envelope with an LLM_* code — that
    means the problem is truly upstream and the caller should keep upstream
    semantics. Bare **530** without an our-cloud envelope is also ``None``:
    Cloudflare/origin 530 is not proof our process died, and a truncated
    LLM_* envelope used to take this path and say AgentCore.

    Any other 5xx on this hop (pool exhaustion, internal fault, missing
    catalog envelope, bare 502/503 gateway page) is our cloud, not the vendor.
    """
    envelope = parse_agentcore_error_envelope(body)
    if envelope is not None and is_llm_family_error_code(envelope.code):
        return None
    if status == 530 and envelope is None:
        return None

    # No envelope (bare gateway page, reverse-proxy 502/503) stays INTERNAL_ERROR:
    # naming a specific fault we cannot prove would poison the very logs used to
    # tell our own outages apart from the vendor's.
    err = OurServiceUnavailableError(
        (envelope.message if envelope else None) or _OUR_SERVICE_UNAVAILABLE_MESSAGE,
        upstream_status=status,
        upstream_body_preview=body_preview(body),
    )
    if envelope is not None:
        err.code = envelope.code
        err.retryable = envelope.code not in _OUR_SERVICE_PERMANENT_CODES
    err.status_code = status if status >= 500 else 503
    return err


def _extract_upstream_error_type(preview: str | None) -> str | None:
    """Anthropic-style ``error.type`` — Zen carries its error class here, not ``code``."""
    if not preview:
        return None
    try:
        data = json.loads(preview)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        kind = err.get("type")
        return str(kind) if kind else None
    return None


_AUTH_BODY_CODES = frozenset(
    {
        "key_expired",
        "invalid_api_key",
        "authentication_error",
        "invalid_api_token",
        "account_deactivated",
    }
)
_AUTH_BODY_MARKERS = re.compile(
    r"(api[\s_-]?key|access[\s_-]?token|unauthorized|authentication|"
    r"key[\s_-]?expired|expired|鉴权|无效.*key|key.*无效)",
    re.IGNORECASE,
)
# Balance exhaustion answered with 401/403 instead of the conventional 402
# (OpenCode: ``{"error":{"type":"CreditsError",…}}`` — payment / subscription /
# empty wallet). Body-proven only — a bare 401 with no balance marker stays auth.
# Go *quota* exhaustion is ``GoUsageLimitError`` (429), not CreditsError.
_BALANCE_BODY_CODES = frozenset(
    {
        "insufficient_balance",
        "insufficient_credits",
        "creditserror",
        "credits_error",
    }
)
_BALANCE_MARKERS = re.compile(
    r"(insufficient\s+(balance|credits)|out\s+of\s+credits|余额不足|账户余额)",
    re.IGNORECASE,
)
# Upstream 404 that names a missing / denied *model* (not a wrong base_url path).
_MODEL_404_CODES = frozenset(
    {
        "resource_not_found",
        "model_not_found",
        "model_not_available",
        "invalid_model",
        "unknown_model",
    }
)
_MODEL_404_MARKERS = re.compile(
    r"(not\s+found\s+the\s+model|model[\s_-]?(not[\s_-]?found|does[\s_-]?not[\s_-]?exist|"
    r"unknown|invalid|unavailable)|resource_not_found|"
    r"permission\s+denied.*model|model.*permission\s+denied|"
    r"找不到.*模型|模型.*(不存在|不可用|未找到|无权限))",
    re.IGNORECASE,
)
# Structured upstream error.message only (already extracted) — not free-text hard gate.
# Anthropic: ``temperature` is deprecated``; Moonshot: ``invalid temperature: only 1 is allowed``.
_TEMPERATURE_DEPRECATED_MARKERS = re.compile(
    r"("
    r"`?temperature`?\s+is\s+deprecated"
    r"|invalid\s+temperature"
    r"|temperature[^.\n]{0,120}?only\s+\d+\s+is\s+allowed"
    r"|only\s+\d+\s+is\s+allowed[^.\n]{0,120}?temperature"
    r")",
    re.IGNORECASE,
)
# Context / prompt overflow (⑦A · 2026-08-08): never echo upstream walls like
# "This model's maximum context length is … you requested …".
_CONTEXT_OVERFLOW_CODES = frozenset(
    {
        "context_length_exceeded",
        "context_overflow",
        "prompt_too_long",
        "input_too_long",
    }
)
_CONTEXT_OVERFLOW_MARKERS = re.compile(
    r"(maximum\s+context\s+length|context_length_exceeded|context\s+overflow|"
    r"prompt\s+is\s+too\s+long|prompt\s+too\s+long|"
    r"exceeds?\s+(the\s+)?(maximum\s+)?context|"
    r"context\s+window|"
    r"输入过长|上下文.*(过长|超限|溢出|超过)|超过.*上下文)",
    re.IGNORECASE,
)
# Product face — short Chinese; upstream body stays in preview / logs only.
CONTEXT_OVERFLOW_PRODUCT = (
    "这条对话对当前模型太长了。请开新对话，或换一个更能装长对话的模型。"
)
_CONTEXT_OVERFLOW_PRODUCT = CONTEXT_OVERFLOW_PRODUCT

# OpenCode Go tool-schema 400 (entry reject, 0 token). Honest: upstream limit,
# no charge. Do not promise auto-retry or auto-trim — we have neither.
UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE = (
    "上游服务端拒绝了本次请求：工具数量或规模超过该模型允许的上限。"
    "请求在入口即被拒绝，未消耗 token、未产生费用。"
)
UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE = (
    "上游服务端拒绝了本次请求：工具定义含有该模型不支持的字段。"
    "请求在入口即被拒绝，未消耗 token、未产生费用。"
)
UNSUPPORTED_TOOL_SCHEMA_MESSAGE = (
    "上游服务端拒绝了本次请求：工具定义不被该模型支持。"
    "请求在入口即被拒绝，未消耗 token、未产生费用。"
)


# ---------------------------------------------------------------------------
# OpenCode structured ``error.type`` table (console source, 2026-08-18).
# Envelope: ``{"type":"error","error":{"type": <class name>, "message": …}}``.
# Classifier = nested ``error.type`` only. Top-level ``Router.Unavailable`` is
# a different envelope and is out of this table. Add rows only from observed
# class names — do not invent types, do not scan ``error.message``.
# ---------------------------------------------------------------------------
_OPENCODE_TYPED = frozenset(
    {
        "regionerror",  # 403
        "autherror",  # 401
        "creditserror",  # 401 — payment / subscription / empty wallet
        "monthlylimiterror",  # 401 — workspace monthly cap
        "userlimiterror",  # 401 — member cap
        "modelerror",  # 401 — unsupported / disabled / trial ended
        "ratelimiterror",  # 429
        "freeusagelimiterror",  # 429
        "gousagelimiterror",  # 429 — Go subscription window
        "blackusagelimiterror",  # 429
        "error",  # 500 literal fallback (not a class name)
    }
)
OPENCODE_TYPED_KINDS = _OPENCODE_TYPED
_OPENCODE_NON_AUTH = frozenset(
    {
        "regionerror",
        "creditserror",
        "monthlylimiterror",
        "userlimiterror",
        "modelerror",
        "ratelimiterror",
        "freeusagelimiterror",
        "gousagelimiterror",
        "blackusagelimiterror",
    }
)
_OPENCODE_LIMIT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
# Scheduler reads the same field but must accept the upstream tokens with a
# space (``5 hour``). Product-copy sanitizer above stays ASCII-token only.
_OPENCODE_GO_LIMIT_NAME = re.compile(r"^[A-Za-z0-9._ -]{1,32}$")
_OPENCODE_WORKSPACE_GO_URL = re.compile(
    r"https://opencode\.ai/workspace/[A-Za-z0-9_-]+/go"
)

OPENCODE_CREDITS_MESSAGE = (
    "OpenCode 账户余额不足、订阅未激活或未绑定支付方式。"
    "请在 OpenCode 控制台完成充值或订阅后重试。"
)
OPENCODE_GO_QUOTA_MESSAGE = (
    "OpenCode Go 订阅配额已用尽（5 小时 / 周 / 月）。请稍后再试，"
    "或在 OpenCode 控制台开启 Use balance 以回落 Zen 余额。"
)
OPENCODE_FREE_USAGE_MESSAGE = "OpenCode 免费额度已用尽。请稍后再试。"
OPENCODE_MONTHLY_LIMIT_MESSAGE = (
    "OpenCode 工作区已达月度用量上限。请稍后再试，或在 OpenCode 控制台调整限额。"
)
OPENCODE_USER_LIMIT_MESSAGE = (
    "OpenCode 工作区成员用量已达上限。请稍后再试，或在 OpenCode 控制台调整限额。"
)
OPENCODE_MODEL_UNAVAILABLE_MESSAGE = (
    "该模型当前不可用（不支持、已禁用或试用已结束）。请更换模型后重试。"
)
OPENCODE_REGION_BYOK_MESSAGE = (
    "该模型需在 OpenCode 控制台为工作区开启中国区托管授权后才能使用。"
)
OPENCODE_REGION_PLATFORM_MESSAGE = (
    "平台侧该模型尚未就绪，请稍后重试或改用自己的 API Key。"
)
OPENCODE_PLATFORM_USAGE_MESSAGE = (
    "平台侧用量暂时受限，请稍后重试或改用自己的 API Key。"
)
OPENCODE_PLATFORM_MODEL_MESSAGE = (
    "平台侧该模型暂不可用，请稍后重试或改用自己的 API Key。"
)


def opencode_structured_error_type(body: bytes | str | None) -> str | None:
    """Lowercased nested ``error.type``, or ``None``.

    Reads only the structured field. Unknown values are returned so callers can
    extend the table; they must not be guessed into product copy.
    """
    kind = (_extract_upstream_error_type(body_preview(body)) or "").strip().lower()
    return kind or None


def is_opencode_region_error(body: bytes | str | None) -> bool:
    return opencode_structured_error_type(body) == "regionerror"


def _opencode_error_object(body: bytes | str | None) -> dict[str, Any] | None:
    preview = body_preview(body)
    if not preview:
        return None
    try:
        data = json.loads(preview)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    return err if isinstance(err, dict) else None


def _opencode_workspace_go_url(body: bytes | str | None) -> str | None:
    extracted = (_extract_upstream_message(body_preview(body)) or "").strip()
    if not extracted:
        return None
    match = _OPENCODE_WORKSPACE_GO_URL.search(extracted)
    return match.group(0) if match else None


def _opencode_limit_name(body: bytes | str | None) -> str | None:
    err = _opencode_error_object(body)
    if err is None:
        return None
    meta = err.get("metadata")
    if not isinstance(meta, dict):
        return None
    name = meta.get("limitName")
    if not isinstance(name, str):
        return None
    token = name.strip()
    return token if _OPENCODE_LIMIT_NAME.fullmatch(token) else None


def opencode_go_limit_name(body: bytes | str | None) -> str | None:
    """Raw ``metadata.limitName`` for the pool state machine (may contain spaces)."""
    err = _opencode_error_object(body)
    if err is None:
        return None
    meta = err.get("metadata")
    if not isinstance(meta, dict):
        return None
    name = meta.get("limitName")
    if not isinstance(name, str):
        return None
    token = name.strip()
    return token if token and _OPENCODE_GO_LIMIT_NAME.fullmatch(token) else None


def opencode_region_product_message(
    body: bytes | str | None, *, platform: bool
) -> str:
    if platform:
        return OPENCODE_REGION_PLATFORM_MESSAGE
    url = _opencode_workspace_go_url(body)
    if url:
        return f"{OPENCODE_REGION_BYOK_MESSAGE}请前往 {url} 完成授权后再试。"
    return OPENCODE_REGION_BYOK_MESSAGE


def opencode_credits_product_message(*, platform: bool) -> str | None:
    """CreditsError family copy. Platform keeps the default 余额不足 / BYOK-exit sentence."""
    if platform:
        return None
    return OPENCODE_CREDITS_MESSAGE


def opencode_typed_client_error(
    body: bytes | str | None,
    *,
    status: int,
    platform: bool,
) -> LLMError | None:
    """Product copy for OpenCode 4xx types that are not auth / credits / 429.

    AuthError and CreditsError stay on the existing auth / balance paths.
    429 types stay on the rate-limit path (see ``opencode_typed_rate_limit_message``).
    The 500 literal ``"error"`` stays on the existing 5xx path. Unknown types
    fall through. Platform never echoes workspace URL / id.
    """
    kind = opencode_structured_error_type(body)
    if kind == "regionerror":
        return upstream_client_error(
            opencode_region_product_message(body, platform=platform),
            status=status,
            body=body,
        )
    if kind == "monthlylimiterror":
        copy = (
            OPENCODE_PLATFORM_USAGE_MESSAGE if platform else OPENCODE_MONTHLY_LIMIT_MESSAGE
        )
        return upstream_client_error(copy, status=status, body=body)
    if kind == "userlimiterror":
        copy = (
            OPENCODE_PLATFORM_USAGE_MESSAGE if platform else OPENCODE_USER_LIMIT_MESSAGE
        )
        return upstream_client_error(copy, status=status, body=body)
    if kind == "modelerror":
        copy = (
            OPENCODE_PLATFORM_MODEL_MESSAGE
            if platform
            else OPENCODE_MODEL_UNAVAILABLE_MESSAGE
        )
        return upstream_client_error(copy, status=status, body=body)
    return None


def opencode_typed_rate_limit_message(
    body: bytes | str | None, *, platform: bool
) -> str | None:
    """Overlay for proven OpenCode 429 types with distinct product copy.

    ``RateLimitError`` / ``BlackUsageLimitError`` keep the existing 上游限流 sentence.
    Does not change retry / ``retry_after`` handling — callers overlay ``.message``
    on the error ``upstream_rate_limit_error`` already built.
    """
    kind = opencode_structured_error_type(body)
    if kind == "gousagelimiterror":
        if platform:
            return OPENCODE_PLATFORM_USAGE_MESSAGE
        name = _opencode_limit_name(body)
        if name:
            return (
                f"OpenCode Go 订阅配额已用尽（{name}）。请稍后再试，"
                "或在 OpenCode 控制台开启 Use balance 以回落 Zen 余额。"
            )
        return OPENCODE_GO_QUOTA_MESSAGE
    if kind == "freeusagelimiterror":
        if platform:
            return OPENCODE_PLATFORM_USAGE_MESSAGE
        return OPENCODE_FREE_USAGE_MESSAGE
    return None


def is_balance_exhausted(body: bytes | str | None) -> bool:
    """True when the upstream body proves an exhausted balance, whatever the status.

    Vendors disagree on the status code — DeepSeek answers 402, OpenCode answers
    401 with a ``CreditsError`` body — so the body is authoritative here.
    A bare 401 carrying no balance marker stays an auth failure.
    OpenCode ``GoUsageLimitError`` and other non-credits typed errors are not
    balance, even if ``error.message`` happens to mention credits.
    """
    preview = body_preview(body)
    if not preview:
        return False
    code = (_extract_upstream_code(preview) or "").strip().lower()
    kind = (_extract_upstream_error_type(preview) or "").strip().lower()
    if kind in _OPENCODE_NON_AUTH and kind != "creditserror":
        return False
    if code in _BALANCE_BODY_CODES or kind in _BALANCE_BODY_CODES:
        return True
    extracted = _extract_upstream_message(preview) or ""
    return bool(extracted and _BALANCE_MARKERS.search(extracted))


def is_auth_rejection(status_code: int, body: bytes | str | None) -> bool:
    """True when a 401/403 should surface as key/auth failure (not model-not-allowed)."""
    kind = opencode_structured_error_type(body)
    if kind == "autherror":
        return True
    if kind in _OPENCODE_NON_AUTH or is_balance_exhausted(body):
        return False
    if status_code == 401:
        return True
    if status_code != 403:
        return False
    preview = body_preview(body)
    code = (_extract_upstream_code(preview) or "").lower()
    if code in _AUTH_BODY_CODES:
        return True
    # Explicit non-auth 403s (model allowlist, etc.) stay as generic client errors.
    if code in {"model_not_allowed", "model_not_found", "insufficient_quota"}:
        return False
    extracted = _extract_upstream_message(preview) or ""
    return bool(_AUTH_BODY_MARKERS.search(extracted))


def is_model_not_found_404(body: bytes | str | None) -> bool:
    """True when an HTTP 404 body points at a missing/denied model id (not a path)."""
    preview = body_preview(body)
    code = (_extract_upstream_code(preview) or "").lower()
    if code in _MODEL_404_CODES:
        return True
    extracted = _extract_upstream_message(preview) or ""
    if extracted and _MODEL_404_MARKERS.search(extracted):
        return True
    # Non-JSON body / raw text still mentioning the model.
    return bool(preview and _MODEL_404_MARKERS.search(preview))


def is_context_overflow(body: bytes | str | None) -> bool:
    """True when upstream rejects the request for context / prompt length."""
    preview = body_preview(body)
    code = (_extract_upstream_code(preview) or "").lower()
    if code in _CONTEXT_OVERFLOW_CODES:
        return True
    extracted = _extract_upstream_message(preview) or ""
    if extracted and _CONTEXT_OVERFLOW_MARKERS.search(extracted):
        return True
    return bool(preview and _CONTEXT_OVERFLOW_MARKERS.search(preview))


def is_temperature_deprecated(body: bytes | str | None) -> bool:
    """True when upstream error.message says temperature is rejected/deprecated.

    Matches the same structured markers as :func:`client_error_message` product
    copy — not free-text hard gate. Used by the omit-temperature retry path.
    """
    extracted = _extract_upstream_message(body_preview(body)) or ""
    return bool(extracted and _TEMPERATURE_DEPRECATED_MARKERS.search(extracted))


def is_unsupported_tool_schema(body: bytes | str | None) -> bool:
    """True when upstream rejected the request for an unsupported tool schema."""
    return _extract_vendor_code(body_preview(body)) == _UNSUPPORTED_TOOL_SCHEMA


def unsupported_tool_schema_product_message(body: bytes | str | None) -> str:
    """Honest Chinese copy for OpenCode Go ``unsupported_tool_schema`` 400s."""
    reason = _extract_vendor_subreason(body_preview(body))
    if reason == "tool_count_limit":
        return UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE
    if reason == "unsupported_keyword":
        return UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE
    return UNSUPPORTED_TOOL_SCHEMA_MESSAGE


def unsupported_tool_schema_error_details(
    body: bytes | str | None,
    *,
    payload: dict | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Client-visible locator fields for 排查包. Never includes credentials."""
    if not is_unsupported_tool_schema(body):
        return {}
    details: dict[str, Any] = {
        "vendor_code": _extract_vendor_code(body_preview(body)) or _UNSUPPORTED_TOOL_SCHEMA,
    }
    if payload is not None:
        model = payload.get("model")
        if isinstance(model, str) and model.strip():
            details["model"] = model.strip()
        tools = payload.get("tools")
        details["tool_count"] = len(tools) if isinstance(tools, list) else 0
    if isinstance(profile, str) and profile.strip():
        details["profile"] = profile.strip()
    return details


def apply_locator_context(err: LLMError, context: dict | None) -> None:
    """Copy vendor locator fields from a relayed envelope onto ``err.details``.

    Only fires when the envelope already classified a vendor subcode — generic
    5xx relays stay untouched. Credentials are not copied.
    """
    if not isinstance(context, dict):
        return
    vendor = context.get("vendor_code")
    if not isinstance(vendor, str) or not vendor.strip():
        return
    err.details["vendor_code"] = vendor.strip()
    model = context.get("model")
    if isinstance(model, str) and model.strip():
        err.details["model"] = model.strip()
    profile = context.get("profile")
    if isinstance(profile, str) and profile.strip():
        err.details["profile"] = profile.strip()
    tool_count = context.get("tool_count")
    if isinstance(tool_count, int):
        err.details["tool_count"] = tool_count
    status = context.get("upstream_status")
    if isinstance(status, int):
        err.details["upstream_status"] = status
    preview = context.get("upstream_body_preview")
    if isinstance(preview, str) and preview.strip():
        err.details["upstream_body_preview"] = preview


def _locator_fields_from_details(details: dict) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    vendor = details.get("vendor_code")
    if isinstance(vendor, str) and vendor.strip():
        out["vendor_code"] = vendor.strip()
    model = details.get("model")
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()
    profile = details.get("profile")
    if isinstance(profile, str) and profile.strip():
        out["profile"] = profile.strip()
    tool_count = details.get("tool_count")
    if isinstance(tool_count, int):
        out["tool_count"] = tool_count
    return out


def client_error_message(
    provider_name: str, status_code: int, body: bytes | str | None
) -> str:
    extracted = _extract_upstream_message(body_preview(body))
    if status_code == 404:
        if is_model_not_found_404(body):
            if extracted:
                return (
                    f"{provider_name} {extracted}。"
                    "请更换默认模型后重试"
                )
            return f"{provider_name} 指定的模型不可用（404），请更换默认模型后重试"
        if extracted:
            return f"{provider_name} {extracted}"
        return f"{provider_name} 接口地址不可达（404），请检查 base_url 配置"
    # 413 / body-proven overflow: product Chinese only (⑦A) — no upstream wall.
    if status_code == 413 or is_context_overflow(body):
        return _CONTEXT_OVERFLOW_PRODUCT
    if status_code == 400 and is_temperature_deprecated(body):
        return f"{provider_name} 当前模型不接受 temperature 参数，请重试或更换模型"
    if is_unsupported_tool_schema(body):
        return unsupported_tool_schema_product_message(body)
    if extracted:
        return f"{provider_name} {extracted}"
    if status_code == 400:
        return f"{provider_name} 请求格式被拒绝（400），请检查模型与参数配置"
    return f"{provider_name} 请求被拒绝（{status_code}），请稍后再试"


def upstream_client_error(
    message: str,
    *,
    status: int,
    body: bytes | str | None = None,
    **details: Any,
) -> LLMError:
    merged: dict[str, Any] = {
        "upstream_status": status,
        "upstream_body_preview": body_preview(body),
    }
    for key, value in details.items():
        if value is not None:
            merged[key] = value
    return LLMError(message, **merged)


def upstream_error(
    message: str,
    *,
    status: int,
    body: bytes | str | None = None,
    retry_attempts: int = 0,
) -> LLMUpstreamError:
    ctx = LLMErrorContext(
        upstream_status=status,
        upstream_body_preview=body_preview(body),
        retry_attempts=retry_attempts,
    )
    # ``status_code`` deliberately stays the class default 502 (bad gateway): it is
    # the status *we* answer with when relaying a vendor fault, and inference-proxy
    # callers key「our 5xx vs the vendor's」off it. The real upstream status rides in
    # ``details`` and in the message the caller composed.
    return LLMUpstreamError(
        message,
        upstream_status=ctx.upstream_status,
        upstream_body_preview=ctx.upstream_body_preview,
        retry_attempts=ctx.retry_attempts,
    )


def is_retryable_upstream_status(status: int) -> bool:
    """5xx upstream failures are transient; 4xx client errors are not."""
    return status >= 500


def is_non_retryable_client_status(status: int) -> bool:
    """Explicit client/auth/balance failures — never retry."""
    return status in (400, 401, 402, 403)


def error_context_from(exc: BaseException) -> dict[str, int | str | float | None] | None:
    """Extract LLM upstream context for SSE / API payloads."""
    if not isinstance(exc, AgentCoreError):
        return None

    status = exc.details.get("upstream_status")
    retry_after = exc.details.get("retry_after")
    if isinstance(exc, LLMRateLimitError):
        source = getattr(exc, "retry_after_source", RETRY_AFTER_UNKNOWN)
        if source != RETRY_AFTER_FROM_HEADER:
            # Unattested cooldown stays on the exception for the engine, not the wire.
            retry_after = None
        elif retry_after is None:
            retry_after = getattr(exc, "retry_after", None)
    credential_source = exc.details.get("credential_source")
    moments = wire_moments(exc)
    locator = _locator_fields_from_details(exc.details)

    if (
        status is None
        and retry_after is None
        and not moments
        and not locator
        and not isinstance(exc, LLMRateLimitError)
        and credential_source not in ("user", "platform")
    ):
        return None

    ctx: dict[str, int | str | float | None] = {}
    if status is not None:
        ctx["upstream_status"] = status
        ctx["upstream_body_preview"] = exc.details.get("upstream_body_preview")
        ctx["retry_attempts"] = exc.details.get("retry_attempts", 0)
    if retry_after is not None:
        with contextlib.suppress(TypeError, ValueError):
            ctx["retry_after"] = float(retry_after)
    ctx.update(moments)
    ctx.update(locator)
    # Vendor-schema locators are for 排查包; credentials stay on server logs
    # (join via trace_id). Other LLM_* CTAs still need the user/platform split.
    if credential_source in ("user", "platform") and "vendor_code" not in locator:
        ctx["credential_source"] = credential_source
    # No Sub2API relay diagnosis here on purpose: it describes the *operator's*
    # upstream accounts, and this dict is the user-visible SSE / REST error
    # context. It stays on the log surface (``llm.upstream_error``).
    # Same posture: ``platform_credential_id`` (which pool member paid) is
    # logs + ``cost_calls`` only — never copied onto this wire context.
    return ctx or None
