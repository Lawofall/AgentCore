"""Generic OpenAI-compatible LLM provider — the single production implementation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, NamedTuple

import httpx

from agentcore.core.errors import (
    MAX_RETRY_AFTER,
    RETRY_AFTER_FROM_BACKOFF,
    RETRY_AFTER_FROM_HEADER,
    RETRY_AFTER_UNKNOWN,
    InferenceTokenExpiredError,
    LLMAuthError,
    LLMClientClosedError,
    LLMError,
    LLMInsufficientBalanceError,
    LLMInvalidResponseError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
    is_llm_client_closed_error,
    mark_llm_leaf_exhausted,
    upstream_rate_limit_error,
)
from agentcore.core.logging import get_logger
from agentcore.core.net import abort_httpx_response, outbound_async_client
from agentcore.core.task_cancel import raise_if_task_cancelled
from agentcore.llm.errors import (
    apply_locator_context,
    body_preview,
    client_error_message,
    diagnose_empty_response,
    inference_envelope_error,
    is_auth_rejection,
    is_balance_exhausted,
    is_non_retryable_client_status,
    is_temperature_deprecated,
    opencode_credits_product_message,
    opencode_go_limit_name,
    opencode_structured_error_type,
    opencode_typed_client_error,
    opencode_typed_rate_limit_message,
    our_inference_service_5xx_error,
    parse_agentcore_error_envelope,
    unsupported_tool_schema_error_details,
    upstream_client_error,
    upstream_error,
)
from agentcore.llm.provider.call_budget import provider_retry_ceiling
from agentcore.llm.provider.cooldown_gate import (
    arm_cooldown,
    clear_cooldown,
    cooldown_key,
    cooldown_remaining,
    peek_cooldown,
    silent_cooldown_seconds,
)
from agentcore.llm.provider.protocol import (
    BACKOFF_MULTIPLIER,
    CONNECT_INITIAL_BACKOFF,
    CONNECT_MAX_RETRIES,
    INITIAL_BACKOFF,
    MAX_RETRIES,
    RATE_LIMIT_MAX_RETRIES,
    TURN_CONNECT_MAX_RETRIES,
    TURN_SCALE_SCENARIOS,
    LLMChunk,
    LLMRequest,
    LLMResponse,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
    connect_retry_policy,
)
from agentcore.llm.provider.wire_dialect import resolve_wire_dialect, wire_model_leaf
from agentcore.llm.sub2api_probe import probe_sub2api_diagnosis_result
from agentcore.llm.tool_arguments import coerce_openai_tool_arguments

logger = get_logger(__name__)


def _request_attribution_headers() -> dict[str, str]:
    """Merge billing attribution headers when talking to the cloud inference proxy.

    No-op (empty) when log context has no run stamps — BYOK / direct upstream
    calls ignore unknown headers; the cloud proxy uses them for cost_calls.
    """
    try:
        from agentcore.billing.attribution import attribution_headers_from_context

        return attribution_headers_from_context()
    except Exception:  # noqa: BLE001 — never let billing headers break LLM I/O
        return {}


# Local aliases keep call sites readable; values live on the public protocol layer.
_MAX_RETRIES = MAX_RETRIES
_INITIAL_BACKOFF = INITIAL_BACKOFF
_BACKOFF_MULTIPLIER = BACKOFF_MULTIPLIER
_CONNECT_MAX_RETRIES = CONNECT_MAX_RETRIES
_CONNECT_INITIAL_BACKOFF = CONNECT_INITIAL_BACKOFF
_RATE_LIMIT_MAX_RETRIES = RATE_LIMIT_MAX_RETRIES
# Not a protocol knob: the same ceiling decides the 429 copy (core.errors).
_MAX_RETRY_AFTER = MAX_RETRY_AFTER
# Loop ceiling must cover the longest retry policy (429 chains need more slots
# than generic 5xx); per-error ``_can_retry_attempt`` still enforces the tighter
# cap for non-rate-limit failures.
_IO_ATTEMPT_CEILING = max(
    _MAX_RETRIES, _RATE_LIMIT_MAX_RETRIES, _CONNECT_MAX_RETRIES, TURN_CONNECT_MAX_RETRIES
)
# Unary completions can run 150s+ for long-form writing; streaming read timeout is
# per-chunk idle, so a generous ceiling avoids false positives on slow generations.
_REQUEST_TIMEOUT = 300.0
# Thinking models (e.g. DeepSeek V4) burn tokens on reasoning before any tool_calls;
# keep a floor so the probe is not starved by a tiny completion budget.
_PROBE_TOOLS_MAX_TOKENS = 256
_PROBE_TOOLS_RETRY = "retry_without_required"
# Body must mention tools/function-calling AND a rejection cue — avoids treating
# generic 4xx (auth, quota, bad model id) as "does not support tools".
_TOOLS_PARAM_MARKERS = re.compile(
    r"\b(tools?|tool[_-]?choice|function[_-]?call(?:ing)?|functions)\b",
    re.IGNORECASE,
)
_TOOLS_REJECT_MARKERS = re.compile(
    r"(not\s+support|unsupported|does\s+not\s+support|invalid|unknown|"
    r"not\s+allowed|not\s+available|unrecognized|no\s+longer\s+supported|"
    r"不支持|无效|未知)",
    re.IGNORECASE,
)


def _is_tools_unsupported_rejection(status: int, body: str) -> bool:
    """True when a 4xx body clearly rejects tools / function calling parameters."""
    if status < 400 or status >= 500 or status == 429:
        return False
    # Auth / payment / missing route are not evidence about tool support.
    if status in (401, 402, 403, 404):
        return False
    if not body.strip():
        return False
    return bool(_TOOLS_PARAM_MARKERS.search(body) and _TOOLS_REJECT_MARKERS.search(body))


def _usage_from(usage_data: dict) -> TokenUsage:
    """Wire-usage parse — both DeepSeek and OpenAI prompt-cache dialects (protocol.py)."""
    return TokenUsage.from_openai_wire(usage_data)


def _reasoning_text(obj: dict | None) -> str | None:
    """DeepSeek ``reasoning_content`` plus OpenAI-compatible aliases.

    Some relays (OpenCode Go) stream CoT on ``reasoning`` / ``reasoning_text``
    and leave ``reasoning_content`` empty. First non-empty string wins.
    """
    if not obj:
        return None
    for key in ("reasoning_content", "reasoning", "reasoning_text"):
        val = obj.get(key)
        if isinstance(val, str) and val:
            return val
    return None


class RetryAfter(NamedTuple):
    """A 429's cooldown, kept inseparable from whose number it is.

    ``seconds`` is what control flow acts on and may well be ours; ``declared`` is
    the only value that may be logged as something upstream said, and is ``None``
    whenever the header was absent, unusable or already expired.
    """

    seconds: float
    declared: float | None

    @property
    def source(self) -> str:
        return RETRY_AFTER_FROM_HEADER if self.declared is not None else RETRY_AFTER_FROM_BACKOFF


def _parse_retry_after(raw: str | None, backoff: float) -> RetryAfter:
    """Parse an HTTP ``Retry-After`` header (RFC 7231): either delta-seconds or an
    HTTP-date. Any absent/malformed value falls back to ``backoff`` so a 429 never
    escapes the retry/error mapping as a generic 502 (audit 01 F9).

    That fallback is *our* exponential backoff, so it comes back tagged: ``seconds``
    paces the retry, ``declared`` stays ``None``. Logs that flatten the two lose the
    difference between「上游要我们等 32 秒」and「上游没说，我们自己退到了 32 秒」—
    which is how 138 header-less production give-ups got read as a near-miss on the
    ceiling. ``declared`` may be hour-scale: callers pass ``seconds`` through
    :func:`_retry_wait` before sleeping, so ``wait_sec`` in logs is the clamped sleep.
    """
    if raw is None:
        return RetryAfter(backoff, None)
    raw = raw.strip()
    if not raw:
        return RetryAfter(backoff, None)
    try:
        declared = float(raw)
    except ValueError:
        pass
    else:
        return RetryAfter(declared, declared)
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return RetryAfter(backoff, None)
    if when is None:
        return RetryAfter(backoff, None)
    now = datetime.now(when.tzinfo or UTC)
    delta = (when - now).total_seconds()
    return RetryAfter(delta, delta) if delta > 0 else RetryAfter(backoff, None)


def _retry_wait(
    retry_after: float | None, backoff: float, *, ceiling: float | None = None
) -> float:
    """Map a cooldown (or None) to the seconds we will actually sleep.

    Callers must refuse retry via :func:`_rate_limit_should_retry` under the same
    ``ceiling``; this helper only clamps sleep for accepted retries (legacy absurd
    branch still falls back to ``backoff`` if invoked). ``ceiling`` defaults to the
    interactive one. It hands back no second「for the logs」value on purpose:
    whether a number is upstream's rides on :attr:`RetryAfter.declared` /
    :attr:`LLMRateLimitError.retry_after_source`, never on having survived a clamp.
    """
    limit = _MAX_RETRY_AFTER if ceiling is None else ceiling
    if retry_after is None:
        return backoff
    if retry_after > limit:
        return backoff
    return retry_after if retry_after > 0 else backoff


def _interactive_rate_limit_fail_fast(
    *,
    scenario: str | None,
    cooldown_source: str | None,
    retry_after: float | None,
    attempt: int | None,
) -> bool:
    """Turn-scale 429s sit only an attested short ``Retry-After``.

    A header-less 429 (local backoff / unknown) returns immediately — guessing
    a wait and occupying the turn is how one 429 became two ``run_failed``
    frames plus a CEO slam. Attested ``Retry-After`` is waited as stated when
    it fits :func:`silent_cooldown_seconds` (default 10s); longer ones bubble
    up. ``cooldown_source is None`` keeps the old ceiling-only gate for
    callers that have not stamped provenance.
    """
    if cooldown_source is None or scenario not in TURN_SCALE_SCENARIOS:
        return False
    _ = attempt
    if cooldown_source != RETRY_AFTER_FROM_HEADER:
        return True
    if retry_after is None:
        return True
    return retry_after > silent_cooldown_seconds()


def _rate_limit_should_retry(
    retry_after: float | None,
    *,
    ceiling: float | None = None,
    scenario: str | None = None,
    cooldown_source: str | None = None,
    attempt: int | None = None,
) -> bool:
    """Whether a 429 is worth sitting out on the budget this call has left.

    Upstream sometimes returns ``Retry-After: 3600``. Blind exponential backoff
    still burns ~1min of empty retries and looks like a hung worker. ``ceiling``
    comes from :func:`~agentcore.llm.provider.call_budget.retry_after_ceiling` —
    the caller's remaining wall clock, or the interactive ``MAX_RETRY_AFTER`` when
    there is no deadline — and anything past it fails immediately so the UI can
    surface rate-limit instead of spinning.

    Turn-scale scenarios (chat / agent) sit only an attested short
    ``Retry-After``; a header-less / unknown cooldown fails immediately.
    Omitting ``cooldown_source`` keeps the ceiling-only behaviour so
    ``_rate_limit_should_retry(5.0)`` stays True. Title / compaction keep the
    2→4→8→16 chain.
    """
    if _interactive_rate_limit_fail_fast(
        scenario=scenario,
        cooldown_source=cooldown_source,
        retry_after=retry_after,
        attempt=attempt,
    ):
        return False
    limit = _MAX_RETRY_AFTER if ceiling is None else ceiling
    return not (retry_after is not None and retry_after > limit)


def _cooldown_fields(seconds: float | None, source: str | None) -> dict[str, object]:
    """The 429 provenance triple every rate-limit log line carries.

    ``retry_after_sec`` keeps the plain meaning its name promises — the cooldown
    *upstream stated* — and is ``None`` when upstream stated none, so a reader (or a
    saved query) filtering on it can no longer pick up numbers we generated.
    ``cooldown_sec`` is what the decision was actually made on, whoever's number it is.
    """
    return {
        "retry_after_sec": seconds if source == RETRY_AFTER_FROM_HEADER else None,
        "cooldown_sec": seconds,
        "cooldown_source": source,
    }


def _error_cooldown(error: Exception) -> tuple[float | None, str | None]:
    """``(cooldown seconds, provenance)`` of a caught error — ``(None, None)`` when it
    carries no cooldown at all (plain 5xx / transport retries)."""
    if isinstance(error, LLMRateLimitError):
        return error.retry_after, error.retry_after_source
    if isinstance(error, LLMQuotaExceededError):
        raw = error.details.get("retry_after")
        try:
            seconds = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            seconds = None
        return seconds, getattr(error, "retry_after_source", None)
    return None, None


def _no_retry_reason(
    source: str | None,
    *,
    retry_after: float | None = None,
    ceiling: float | None = None,
    scenario: str | None = None,
    attempt: int | None = None,
) -> str:
    """Why we stopped: upstream's cooldown outran the budget, our own next backoff
    did, or an interactive turn refused to sit (header-less / unknown, or an
    attested wait past the silent threshold). Day-scale headers still log as
    ``retry_after_too_large``; ``interactive_fail_fast`` is the chat/agent
    give-up that does not occupy the turn."""
    limit = _MAX_RETRY_AFTER if ceiling is None else ceiling
    ceiling_blocks = retry_after is not None and retry_after > limit
    if not ceiling_blocks and _interactive_rate_limit_fail_fast(
        scenario=scenario,
        cooldown_source=source,
        retry_after=retry_after,
        attempt=attempt,
    ):
        return "interactive_fail_fast"
    return (
        "backoff_exceeds_budget"
        if source == RETRY_AFTER_FROM_BACKOFF
        else "retry_after_too_large"
    )


class OpenAICompatibleProvider:
    """OpenAI-compatible ``/chat/completions`` provider."""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        extra_headers: dict[str, str] | None = None,
        display_name: str | None = None,
    ) -> None:
        self._name = name
        # User-facing label for error copy. Logs / pricing keep ``_name``.
        # Never fall back to internal credential sources ``user`` / ``platform``.
        shown = (display_name or "").strip()
        if shown:
            self._display_name = shown
        elif name == "platform":
            self._display_name = "平台"
        elif name == "user":
            self._display_name = "服务商"
        else:
            self._display_name = name
        from agentcore.llm.credentials import require_http_header_safe_api_key

        self._api_key = require_http_header_safe_api_key(api_key)
        self._base_url = base_url.rstrip("/")
        self._extra_headers = dict(extra_headers) if extra_headers else None
        if self._extra_headers:
            for _hk, hv in self._extra_headers.items():
                try:
                    str(hv).encode("ascii")
                except UnicodeEncodeError as e:
                    from agentcore.core.errors import ValidationError

                    raise ValidationError(
                        "自定义请求头含有非 ASCII 字符，无法发送。请检查服务商额外 Header。"
                    ) from e
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._extra_headers:
            headers.update(self._extra_headers)
        self._client = outbound_async_client(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=10.0),
        )
        self._cooldown_key = cooldown_key(self._name, self._api_key, self._base_url)

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def _is_inference_hop(self) -> bool:
        """True when this leaf talks to our own cloud proxy instead of a vendor."""
        return "/inference/" in self._base_url

    @property
    def _credential_source(self) -> str | None:
        """Who funds this leaf's key — the platform-vs-BYOK CTA split (平台LLM接入 §二).

        ``None`` on the inference hop: the sidecar's local carrier defaults its
        source to ``user`` whoever actually pays, so the payer is only knowable
        from the cloud's own envelope (``inference_envelope_error``). Guessing
        there would brand a BYOK wall as a platform one.
        """
        if self._is_inference_hop:
            return None
        return "platform" if self._name == "platform" else "user"

    def _insufficient_balance_error(
        self, *, status: int, body: bytes | str | None = None
    ) -> LLMInsufficientBalanceError:
        """Balance wall with OpenCode CreditsError family copy when typed."""
        message = None
        if opencode_structured_error_type(body) == "creditserror":
            message = opencode_credits_product_message(
                platform=self._name == "platform"
            )
        return LLMInsufficientBalanceError(
            message,
            provider_name=self._name,
            display_name=self._display_name,
            upstream_status=status,
            upstream_body_preview=body_preview(body),
        )

    def _opencode_typed_client_error(
        self, *, status: int, body: bytes | str | None
    ) -> LLMError | None:
        return opencode_typed_client_error(
            body,
            status=status,
            platform=self._name == "platform",
        )

    async def _await_shared_cooldown(
        self, *, scenario: str, ceiling: float, attempt: int, stream: bool
    ) -> None:
        """If a sibling armed a slot, raise immediately — do not sleep or probe."""
        remaining = cooldown_remaining(self._cooldown_key)
        if remaining <= 0:
            remaining = self._platform_account_remaining()
        if remaining <= 0:
            return
        if await self._try_platform_pool_failover():
            return
        slot = peek_cooldown(self._cooldown_key)
        source = slot.source if slot is not None else RETRY_AFTER_UNKNOWN
        seconds = slot.seconds if slot is not None else remaining
        logger.info(
            "llm.rate_limit_no_retry",
            provider=self._name,
            scenario=scenario,
            attempt=attempt + 1,
            **_cooldown_fields(seconds, source),
            ceiling_sec=ceiling,
            stream=stream,
            reason="shared_cooldown",
        )
        err = upstream_rate_limit_error(
            seconds,
            credential_source=self._credential_source,
            retry_ceiling=ceiling,
            retry_after_source=source,
        )
        mark_llm_leaf_exhausted(err)
        raise err

    def _uses_platform_pool(self) -> bool:
        return self._name == "platform" and not self._is_inference_hop

    def _enforce_declared_tool_surface(self, payload: dict) -> None:
        """Fail locally when this pool member declared a cap the assembled tools exceed."""
        tools = payload.get("tools")
        if not isinstance(tools, list) or not tools:
            return
        if not self._uses_platform_pool():
            return
        from agentcore.llm.tool_surface import enforce_platform_member_tool_surface

        enforce_platform_member_tool_surface(
            tools, api_key=self._api_key, base_url=self._base_url
        )

    @staticmethod
    def _is_pool_failover_signal(error: LLMError) -> bool:
        # Long attested 429s become LLMQuotaExceededError on the platform leaf;
        # that is the fill-first switch signal, not a reason to stick to the key.
        # 403 RegionError is a per-workspace config miss: hop before commit.
        # 401 stays off this list — ban/bad-key must not walk the rest of the pool.
        if isinstance(error, LLMAuthError):
            return False
        if isinstance(error, (LLMRateLimitError, LLMQuotaExceededError)):
            return True
        preview = error.details.get("upstream_body_preview")
        if not isinstance(preview, str):
            return False
        return opencode_structured_error_type(preview) == "regionerror"

    def _platform_account_remaining(self) -> float:
        if not self._uses_platform_pool():
            return 0.0
        from agentcore.llm.platform_pool_scheduler import platform_account_remaining

        return platform_account_remaining(self._api_key, self._base_url)

    def _record_platform_pool_rate_limit(
        self, *, retry_after: RetryAfter, body: bytes | str | None
    ) -> None:
        if not self._uses_platform_pool():
            return
        from agentcore.llm.platform_pool_scheduler import record_platform_rate_limit

        record_platform_rate_limit(
            api_key=self._api_key,
            base_url=self._base_url,
            retry_after_seconds=retry_after.seconds,
            retry_after_source=retry_after.source,
            limit_name=opencode_go_limit_name(body),
        )

    def _record_platform_pool_block(self, *, reason: str = "upstream_401") -> None:
        if not self._uses_platform_pool():
            return
        from agentcore.llm.platform_pool_scheduler import record_platform_auth_block

        record_platform_auth_block(
            api_key=self._api_key, base_url=self._base_url, reason=reason
        )

    async def _try_platform_pool_failover(self) -> bool:
        """Rebind to the next fill-first member. True = retry now, do not sleep."""
        if not self._uses_platform_pool():
            return False
        from agentcore.llm.credentials import (
            bind_platform_credential_id,
            require_http_header_safe_api_key,
        )
        from agentcore.llm.platform_pool_scheduler import failover_member, member_for_credentials

        current = member_for_credentials(self._api_key, self._base_url)
        nxt = failover_member(api_key=self._api_key, base_url=self._base_url)
        if nxt is None:
            return False
        new_key = require_http_header_safe_api_key(nxt.api_key)
        new_url = nxt.base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {new_key}",
            "Content-Type": "application/json",
        }
        if self._extra_headers:
            headers.update(self._extra_headers)
        new_client = outbound_async_client(
            base_url=new_url,
            headers=headers,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=10.0),
        )
        old = self._client
        from_id = current.id if current is not None else ""
        self._api_key = new_key
        self._base_url = new_url
        self._client = new_client
        self._cooldown_key = cooldown_key(self._name, self._api_key, self._base_url)
        bind_platform_credential_id(nxt.id)
        logger.info(
            "platform_pool.failover",
            from_credential_id=from_id,
            to_credential_id=nxt.id,
        )
        with contextlib.suppress(Exception):
            await old.aclose()
        return True

    def _rate_limit_retry_plan(
        self,
        error: LLMError,
        *,
        attempt: int,
        ceiling: float,
        scenario: str,
        backoff: float,
        stream: bool,
    ) -> float:
        """Arm the shared gate. Return seconds to sleep, or raise (leaf exhausted)."""
        retry_after, cooldown_source = _error_cooldown(error)
        if retry_after is not None and retry_after > 0:
            arm_cooldown(
                self._cooldown_key,
                retry_after,
                cooldown_source or RETRY_AFTER_UNKNOWN,
            )
        max_attempts = (
            _RATE_LIMIT_MAX_RETRIES
            if isinstance(error, LLMRateLimitError)
            else _MAX_RETRIES
        )
        if not error.retryable or not self._can_retry_attempt(
            attempt, max_attempts=max_attempts
        ):
            if isinstance(error, LLMRateLimitError):
                mark_llm_leaf_exhausted(error)
            raise error
        if isinstance(error, LLMRateLimitError) and not _rate_limit_should_retry(
            retry_after,
            ceiling=ceiling,
            scenario=scenario,
            cooldown_source=cooldown_source,
            attempt=attempt,
        ):
            logger.info(
                "llm.rate_limit_no_retry",
                provider=self._name,
                scenario=scenario,
                attempt=attempt + 1,
                **_cooldown_fields(retry_after, cooldown_source),
                ceiling_sec=ceiling,
                stream=stream,
                reason=_no_retry_reason(
                    cooldown_source,
                    retry_after=retry_after,
                    ceiling=ceiling,
                    scenario=scenario,
                    attempt=attempt,
                ),
            )
            mark_llm_leaf_exhausted(error)
            raise error
        wait = _retry_wait(retry_after, backoff, ceiling=ceiling)
        logger.info(
            "llm.call_retried",
            provider=self._name,
            attempt=attempt + 1,
            max_attempts=max_attempts,
            wait_sec=wait,
            **_cooldown_fields(retry_after, cooldown_source),
            stream=stream,
            reason=type(error).__name__,
        )
        return wait

    def clone(self) -> OpenAICompatibleProvider:
        """Independent HTTP client with the same credentials (coordination drive ownership)."""
        return OpenAICompatibleProvider(
            name=self._name,
            api_key=self._api_key,
            base_url=self._base_url,
            extra_headers=self._extra_headers,
            display_name=self._display_name,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self._build_payload(request, stream=False)
        start = time.monotonic()
        data = await self._request_with_retry(
            payload, scenario=request.scenario, patience=request.retry_patience_seconds
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        choice = data["choices"][0]
        message = choice["message"]
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    function=ToolCallFunction(
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    ),
                )
                for tc in message["tool_calls"]
            ]
        usage = _usage_from(data.get("usage", {}))
        finish_reason = choice.get("finish_reason", "stop")
        content = message.get("content") or ""
        raw_body_preview = body_preview(json.dumps(data, ensure_ascii=False))
        empty_diagnosis: str | None = None
        if not content and not tool_calls:
            diagnosis = diagnose_empty_response(
                raw_body=raw_body_preview,
                finish_reason=finish_reason,
            )
            empty_diagnosis = diagnosis.value
            logger.warning(
                "llm.empty_response",
                model=data.get("model", request.model),
                scenario=request.scenario,
                raw_body_preview=raw_body_preview,
                finish_reason=finish_reason,
                usage=usage.as_dict(),
                diagnosis=empty_diagnosis,
                base_url=self._base_url,
            )
        # Success / failure metrics: ``observe_provider`` fence (build_provider).
        return LLMResponse(
            content=content,
            reasoning_content=_reasoning_text(message) or message.get("reasoning_content"),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            model=data.get("model", request.model),
            latency_ms=latency_ms,
            empty_diagnosis=empty_diagnosis,
            empty_raw_preview=raw_body_preview if empty_diagnosis else None,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Parse-and-retry stream: retry loop sees semantic commit state in-place.

        ``committed`` flips on the first content or tool_call delta. Reasoning,
        role-only, usage-only, and keepalive chunks do not commit. Pre-commit
        transport/upstream failures transparently retry the whole request;
        a clean EOF after reasoning-only (no ``finish_reason``, no content /
        tools) is the same class — some gateways close the SSE mid-think
        without raising. Post-commit disconnect yields ``aborted`` instead of
        raising so the engine can keep the partial. A transparent retry yields
        ``stream_reset`` so consumers drop ephemeral reasoning before the next
        attempt.
        """
        # Sidecar→localhost inference SSE can stall at 0 chunks while the proxy
        # still finishes upstream (proxy_spend_enqueued then llm.stream_stalled).
        # Opt-in unary bypass for dogfood / probe: AGENTCORE_INFERENCE_UNARY=1.
        if (
            os.environ.get("AGENTCORE_INFERENCE_UNARY", "").strip().lower()
            in {"1", "true", "yes"}
            and self._is_inference_hop
        ):
            logger.info(
                "llm.inference_unary_bypass",
                base_url=self._base_url,
                scenario=request.scenario,
            )
            resp = await self.complete(request)
            if resp.reasoning_content:
                yield LLMChunk(delta_reasoning=resp.reasoning_content)
            if resp.content:
                yield LLMChunk(delta_content=resp.content)
            if resp.tool_calls:
                deltas = [
                    ToolCallDelta(
                        index=i,
                        id=tc.id,
                        function_name=tc.function.name,
                        arguments_delta=tc.function.arguments,
                    )
                    for i, tc in enumerate(resp.tool_calls)
                ]
                yield LLMChunk(delta_tool_calls=deltas)
            yield LLMChunk(
                finish_reason=resp.finish_reason,
                usage=resp.usage,
                empty_diagnosis=resp.empty_diagnosis,
                empty_raw_preview=resp.empty_raw_preview,
            )
            return

        payload = self._build_payload(request, stream=True)
        last_error: Exception | None = None
        backoff = _INITIAL_BACKOFF
        # Connect-class failures run their own budget/backoff chain (a dropped
        # turn costs far more than a dropped one-shot); tracked separately so a
        # 5xx retry in between does not reset or inflate it.
        connect_max, connect_backoff = connect_retry_policy(request.scenario)
        yielded_ephemeral = False
        started = time.monotonic()
        self._ensure_client_open()

        for attempt in range(_IO_ATTEMPT_CEILING):
            raise_if_task_cancelled()
            self._enforce_declared_tool_surface(payload)
            # Same per-attempt narrowing as the unary loop. Streaming turns are the
            # interactive ones and carry no patience, so this is normally the
            # unchanged ``MAX_RETRY_AFTER`` — and a request that arrived carrying one
            # anyway is refused there rather than quietly re-timing the turn.
            ceiling = provider_retry_ceiling(
                scenario=request.scenario,
                patience=request.retry_patience_seconds,
                elapsed=time.monotonic() - started,
            )
            await self._await_shared_cooldown(
                scenario=request.scenario,
                ceiling=ceiling,
                attempt=attempt,
                stream=True,
            )
            committed = False
            lines_seen = 0
            has_content = False
            has_tool_calls = False
            last_lines: list[str] = []
            last_finish_reason: str | None = None
            last_usage: TokenUsage | None = None
            json_parse_failures = 0
            parsed_chunks = 0
            forwarded_diagnosis: str | None = None
            forwarded_preview: str | None = None

            in_flight: httpx.Response | None = None
            try:
                self._ensure_client_open()
                async with self._client.stream(
                    "POST",
                    "/chat/completions",
                    json=payload,
                    headers=_request_attribution_headers() or None,
                ) as response:
                    in_flight = response
                    body = await response.aread() if response.status_code >= 400 else None
                    if body is not None and self._try_omit_temperature_once(
                        payload, response.status_code, body, stream=True
                    ):
                        continue
                    self._raise_for_status(
                        response.status_code,
                        backoff,
                        response.headers,
                        body=body,
                        attempt=attempt,
                        scenario=request.scenario,
                        retry_ceiling=ceiling,
                        payload=payload,
                    )
                    clear_cooldown(self._cooldown_key)
                    async for line in response.aiter_lines():
                        lines_seen += 1
                        if len(last_lines) >= 5:
                            last_lines.pop(0)
                        last_lines.append(line)

                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            json_parse_failures += 1
                            continue
                        parsed_chunks += 1
                        # Proxied upstream forwards empty-response diagnosis inline (01 F8).
                        if data.get("empty_diagnosis"):
                            forwarded_diagnosis = data["empty_diagnosis"]
                            forwarded_preview = data.get("empty_raw_preview")
                            continue
                        # Proxied upstream relays the stream-control signals inline (mirror
                        # of empty_diagnosis) so this hop reconstructs the same LLMChunk
                        # protocol it would see talking to the real upstream directly:
                        # a transparent pre-commit retry (stream_reset) and a post-commit
                        # disconnect salvage (aborted) survive the proxy re-serialization.
                        if data.get("stream_reset"):
                            yield LLMChunk(stream_reset=True)
                            continue
                        if data.get("aborted"):
                            yield LLMChunk(aborted=True)
                            return
                        choices = data.get("choices") or [{}]
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        content_delta = delta.get("content")
                        reasoning_delta = _reasoning_text(delta)
                        raw_tool_calls = delta.get("tool_calls")
                        if content_delta:
                            has_content = True
                            committed = True
                        if raw_tool_calls:
                            has_tool_calls = True
                            committed = True
                        tc_deltas = None
                        if raw_tool_calls:
                            tc_deltas = [
                                ToolCallDelta(
                                    index=tc.get("index", 0),
                                    id=tc.get("id"),
                                    function_name=tc.get("function", {}).get("name"),
                                    arguments_delta=tc.get("function", {}).get("arguments"),
                                )
                                for tc in raw_tool_calls
                            ]
                        if choice.get("finish_reason"):
                            last_finish_reason = choice.get("finish_reason")
                        usage = _usage_from(data["usage"]) if data.get("usage") else None
                        if usage:
                            last_usage = usage
                        if reasoning_delta:
                            yielded_ephemeral = True
                        yield LLMChunk(
                            delta_content=content_delta,
                            delta_reasoning=reasoning_delta,
                            delta_tool_calls=tc_deltas,
                            finish_reason=choice.get("finish_reason"),
                            usage=usage,
                        )

                if not has_content and not has_tool_calls:
                    if forwarded_diagnosis is not None:
                        yield LLMChunk(
                            empty_diagnosis=forwarded_diagnosis,
                            empty_raw_preview=forwarded_preview,
                        )
                        return
                    raw_body_preview = body_preview("\n".join(last_lines))
                    format_mismatch = json_parse_failures > 0 and parsed_chunks == 0
                    # Clean EOF after thinking tokens, no protocol finish — not
                    # silent_empty. Same pre-commit retry as a raised disconnect.
                    if (
                        yielded_ephemeral
                        and last_finish_reason is None
                        and not format_mismatch
                        and self._can_retry_attempt(attempt)
                    ):
                        yield LLMChunk(stream_reset=True)
                        yielded_ephemeral = False
                        backoff = await self._sleep_before_retry(
                            attempt=attempt,
                            backoff=backoff,
                            stream=True,
                            reason="reasoning_only_incomplete",
                            partial_sse_lines=lines_seen,
                        )
                        continue
                    diagnosis = diagnose_empty_response(
                        raw_body=raw_body_preview,
                        finish_reason=last_finish_reason,
                        format_mismatch=format_mismatch,
                    )
                    logger.warning(
                        "llm.empty_response",
                        model=request.model,
                        scenario=request.scenario,
                        raw_body_preview=raw_body_preview,
                        finish_reason=last_finish_reason,
                        usage=last_usage.as_dict() if last_usage else {},
                        diagnosis=diagnosis.value,
                        base_url=self._base_url,
                        sse_tail=last_lines,
                    )
                    yield LLMChunk(
                        empty_diagnosis=diagnosis.value,
                        empty_raw_preview=raw_body_preview,
                    )
                return

            except asyncio.CancelledError:
                await abort_httpx_response(in_flight)
                raise
            except LLMUpstreamError as e:
                last_error = e
                if committed:
                    logger.warning(
                        "llm.stream_partial_disconnect",
                        provider=self._name,
                        partial_sse_lines=lines_seen,
                        reason=f"upstream_{e.details.get('upstream_status', 500)}",
                        committed=True,
                    )
                    yield LLMChunk(aborted=True)
                    return
                if not e.retryable or not self._can_retry_attempt(attempt):
                    await self._finalize_upstream_error(e, attempt)
                if yielded_ephemeral:
                    yield LLMChunk(stream_reset=True)
                    yielded_ephemeral = False
                backoff = await self._sleep_before_retry(
                    attempt=attempt,
                    backoff=backoff,
                    stream=True,
                    reason=f"upstream_{e.details.get('upstream_status', 500)}",
                    partial_sse_lines=lines_seen,
                )
            except (LLMRateLimitError, LLMError) as e:
                last_error = e
                if committed:
                    logger.warning(
                        "llm.stream_partial_disconnect",
                        provider=self._name,
                        partial_sse_lines=lines_seen,
                        reason=type(e).__name__,
                        committed=True,
                    )
                    yield LLMChunk(aborted=True)
                    return
                if self._is_pool_failover_signal(e) and (
                    await self._try_platform_pool_failover()
                ):
                    if yielded_ephemeral:
                        yield LLMChunk(stream_reset=True)
                        yielded_ephemeral = False
                    continue
                wait = self._rate_limit_retry_plan(
                    e,
                    attempt=attempt,
                    ceiling=ceiling,
                    scenario=request.scenario,
                    backoff=backoff,
                    stream=True,
                )
                if yielded_ephemeral:
                    yield LLMChunk(stream_reset=True)
                    yielded_ephemeral = False
                await asyncio.sleep(wait)
                clear_cooldown(self._cooldown_key)
                backoff *= _BACKOFF_MULTIPLIER
            except httpx.TimeoutException as e:
                raise_if_task_cancelled(e)
                last_error = LLMTimeoutError(f"连接 {self._display_name} 超时，请检查网络后重试")
                if committed:
                    logger.warning(
                        "llm.stream_partial_disconnect",
                        provider=self._name,
                        partial_sse_lines=lines_seen,
                        reason="timeout",
                        committed=True,
                    )
                    yield LLMChunk(aborted=True)
                    return
                is_connect = isinstance(e, httpx.ConnectTimeout)
                max_attempts = connect_max if is_connect else _MAX_RETRIES
                if not self._can_retry_attempt(attempt, max_attempts=max_attempts):
                    raise last_error from e
                if yielded_ephemeral:
                    yield LLMChunk(stream_reset=True)
                    yielded_ephemeral = False
                next_backoff = await self._sleep_before_retry(
                    attempt=attempt,
                    backoff=connect_backoff if is_connect else backoff,
                    stream=True,
                    reason="connect_timeout" if is_connect else "timeout",
                    partial_sse_lines=lines_seen,
                    max_attempts=max_attempts,
                )
                if is_connect:
                    connect_backoff = next_backoff
                else:
                    backoff = next_backoff
            except httpx.HTTPError as e:
                raise_if_task_cancelled(e)
                last_error = self._network_error_to_llm(e)
                if committed:
                    logger.warning(
                        "llm.stream_partial_disconnect",
                        provider=self._name,
                        partial_sse_lines=lines_seen,
                        reason=type(e).__name__,
                        committed=True,
                    )
                    yield LLMChunk(aborted=True)
                    return
                is_connect = self._is_connect_failure(e)
                max_attempts = connect_max if is_connect else _MAX_RETRIES
                if not last_error.retryable or not self._can_retry_attempt(
                    attempt, max_attempts=max_attempts
                ):
                    raise last_error from e
                if yielded_ephemeral:
                    yield LLMChunk(stream_reset=True)
                    yielded_ephemeral = False
                next_backoff = await self._sleep_before_retry(
                    attempt=attempt,
                    backoff=connect_backoff if is_connect else backoff,
                    stream=True,
                    reason=type(e).__name__,
                    partial_sse_lines=lines_seen,
                    max_attempts=max_attempts,
                )
                if is_connect:
                    connect_backoff = next_backoff
                else:
                    backoff = next_backoff
            except RuntimeError as e:
                translated = self._translate_closed_client(e)
                if isinstance(translated, LLMClientClosedError):
                    raise translated from e
                raise

        raise last_error or LLMError(f"{self._display_name} 多次重试后仍失败，请稍后重试")

    def _build_payload(self, request: LLMRequest, *, stream: bool) -> dict:
        # Default wire shape is clean OpenAI Chat Completions. Dialect flags
        # (reasoning_content echo / thinking.type / omit temperature) come from
        # ``wire_dialect`` — broadcasting DeepSeek/Hy3 fields to other relays
        # triggers invalid_request on multi-turn tool loops.
        dialect = resolve_wire_dialect(request.model, base_url=self._base_url)
        messages = []
        for msg in request.messages:
            m: dict = {"role": msg.role}
            if msg.content is not None:
                m["content"] = msg.content
            elif msg.role == "assistant" and msg.tool_calls:
                # OpenAI-compatible tool turns always carry content (possibly empty).
                m["content"] = ""
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": coerce_openai_tool_arguments(tc.function.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            if dialect.echo_reasoning_content:
                if msg.reasoning_content is not None:
                    m["reasoning_content"] = msg.reasoning_content
                elif msg.role == "assistant" and msg.tool_calls:
                    # Thinking mode: assistant tool-call turns must echo
                    # reasoning_content (empty string when the model omitted it).
                    m["reasoning_content"] = ""
            messages.append(m)

        payload: dict = {
            "model": request.model,
            "messages": messages,
            "stream": stream,
        }
        # Restricted Anthropic leaves reject temperature → omit (not default).
        if not dialect.omit_temperature:
            payload["temperature"] = request.temperature
        dialect.apply_token_limit(payload, request.max_tokens)
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = request.tool_choice
        if stream:
            payload["stream_options"] = {"include_usage": True}
        # thinking_type_switch models: send the switch explicitly. Official
        # DeepSeek treats omit as on; OpenCode Go / some relays treat omit as
        # off (no reasoning_content, reasoning_tokens=0). None and True both
        # mean on. Background one-shots (title / memory / …) must send
        # disabled or a tight max_tokens budget is eaten by reasoning and
        # the JSON body comes back empty → fallback_title = raw user input.
        if dialect.thinking_type_switch:
            if request.thinking is False:
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["thinking"] = {"type": "enabled"}
                # Official V4 default effort is high. Some relays honor
                # reasoning_effort but ignore thinking.type — without it the
                # stream has no CoT (OpenCode Go dogfood 2026-08-19).
                if wire_model_leaf(request.model).startswith("deepseek-v4"):
                    payload["reasoning_effort"] = "high"
        return payload

    async def _log_sub2api_diagnosis(self, err: LLMUpstreamError) -> LLMUpstreamError:
        """Record why the operator's Sub2API relay 503'd — log surface only.

        Everything the probe reports (OAuth expiry, the 5h window, an account
        address) belongs to the *operator's* upstream accounts. In platform mode
        the user has no key of their own, so none of it is theirs to act on:
        on the bubble it sent people off to re-login ChatGPT and hunt for a
        binding screen this product does not have. The user keeps the honest
        「上游模型服务暂时不可用」sentence; ops read the diagnosis here.
        """
        from agentcore.config.settings import settings

        status = err.details.get("upstream_status", 0)
        if settings.billing_mode != "platform" or status != 503:
            return err

        probe = await probe_sub2api_diagnosis_result()
        if probe is None:
            return err

        logger.warning(
            "llm.upstream_error",
            provider=self._name,
            status_code=status,
            sub2api_diagnosis=probe.diagnosis,
            sub2api_account=probe.account_email_masked,
        )
        return err

    def _raise_for_status(
        self,
        status_code: int,
        backoff: float,
        headers,
        *,
        body: bytes | None = None,
        attempt: int = 0,
        scenario: str = "chat",
        retry_ceiling: float | None = None,
        payload: dict | None = None,
    ) -> None:
        # Sidecar→cloud hop: our own error envelope is the first truth source, and
        # the status-based classification below is the fallback for answers we did
        # not phrase (gateway pages, bare 401 on the JWT). The proxy flattens every
        # typed error onto 402 / 429 / 502, so reading the number instead of the
        # code turns an exhausted quota into vendor throttling and a missing BYOK
        # key into an empty wallet — see ``inference_envelope_error``.
        if self._is_inference_hop:
            envelope_error = inference_envelope_error(
                status=status_code, body=body, retry_ceiling=retry_ceiling
            )
            if envelope_error is not None:
                if headers.get("x-upstream-retried"):
                    # The cloud leaf already spent a retry budget on this fault.
                    envelope_error.retryable = False
                logger.warning(
                    "llm.inference_envelope_relay",
                    provider=self._name,
                    status_code=status_code,
                    error_code=envelope_error.code,
                    retryable=envelope_error.retryable,
                    body_preview=body_preview(body),
                )
                raise envelope_error
        if status_code == 429:
            cooldown = _parse_retry_after(headers.get("retry-after"), backoff)
            if not _rate_limit_should_retry(
                cooldown.seconds,
                ceiling=retry_ceiling,
                scenario=scenario,
                cooldown_source=cooldown.source,
                attempt=attempt,
            ):
                # Ceiling refusals come back non-retryable and skip the loop's
                # own guard. Interactive fail-fast stays retryable, so the loop
                # emits this line.
                limit = _MAX_RETRY_AFTER if retry_ceiling is None else retry_ceiling
                if cooldown.seconds is not None and cooldown.seconds > limit:
                    logger.info(
                        "llm.rate_limit_no_retry",
                        provider=self._name,
                        scenario=scenario,
                        attempt=attempt + 1,
                        **_cooldown_fields(cooldown.seconds, cooldown.source),
                        ceiling_sec=limit,
                        reason=_no_retry_reason(
                            cooldown.source,
                            retry_after=cooldown.seconds,
                            ceiling=retry_ceiling,
                            scenario=scenario,
                            attempt=attempt,
                        ),
                    )
            err = upstream_rate_limit_error(
                cooldown.seconds,
                credential_source=self._credential_source,
                retry_ceiling=retry_ceiling,
                retry_after_source=cooldown.source,
            )
            overlay = opencode_typed_rate_limit_message(
                body, platform=self._name == "platform"
            )
            if overlay:
                err.message = overlay
            self._record_platform_pool_rate_limit(retry_after=cooldown, body=body)
            raise err
        if status_code in (401, 403):
            logger.warning(
                "llm.client_error",
                provider=self._name,
                status_code=status_code,
                body_preview=body_preview(body),
            )
            # Sidecar cloud proxy: Bearer is the short-lived inference JWT, not a BYOK key.
            # Map to a distinct code so the client remints / retries instead of
            # offering a Key-config CTA.
            if self._is_inference_hop:
                raise InferenceTokenExpiredError(
                    upstream_status=status_code,
                    upstream_body_preview=body_preview(body),
                )
            typed = self._opencode_typed_client_error(
                status=status_code, body=body
            )
            if typed is not None:
                if opencode_structured_error_type(body) == "regionerror":
                    self._record_platform_pool_block(reason="regionerror")
                raise typed
            if is_balance_exhausted(body):
                raise self._insufficient_balance_error(status=status_code, body=body)
            if is_auth_rejection(status_code, body):
                # Product copy only (platform + BYOK): never echo upstream gateway
                # tutorials (e.g. CC Switch). Upstream text stays in preview / logs.
                self._record_platform_pool_block()
                raise LLMAuthError(
                    provider_name=self._name,
                    display_name=self._display_name,
                    upstream_status=status_code,
                    upstream_body_preview=body_preview(body),
                )
            raise upstream_client_error(
                client_error_message(self._display_name, status_code, body),
                status=status_code,
                body=body,
                **unsupported_tool_schema_error_details(
                    body, payload=payload, profile=scenario
                ),
            )
        if status_code == 402:
            raise self._insufficient_balance_error(status=status_code, body=body)
        if status_code >= 500:
            preview = body_preview(body)
            logger.warning(
                "llm.upstream_error",
                provider=self._name,
                status_code=status_code,
                attempt=attempt + 1,
                body_preview=preview,
            )
            # Sidecar→cloud /inference/: 5xx is our cloud unless the body is our
            # envelope with an LLM_* code (true upstream, wrapped by the proxy).
            # Never sniff free text / vendor gateway tutorials.
            relayed: str | None = None
            envelope = None
            if self._is_inference_hop:
                our_err = our_inference_service_5xx_error(status=status_code, body=body)
                if our_err is not None:
                    raise our_err
                # True upstream behind the proxy: the cloud leaf already phrased the
                # vendor's real status, while ours is only the proxy's 502 relay code.
                # Reuse that sentence (our own copy, never vendor text) so the number
                # on the bubble is the one the vendor actually returned.
                envelope = parse_agentcore_error_envelope(body)
                relayed = envelope.message if envelope else None
            # Product face (A′ · 2026-08-04): never put credential leaf names
            # (``platform`` / BYOK ``user`` / vendor) or「服务端错误」on the bubble —
            # those read as AgentCore itself failing. Upstream body stays in preview.
            message = relayed or f"上游模型服务暂时不可用（{status_code}），请稍后再试"
            err = upstream_error(
                message,
                status=status_code,
                body=body,
                retry_attempts=attempt,
            )
            if envelope is not None:
                apply_locator_context(err, envelope.context)
            if headers.get("x-upstream-retried"):
                err.retryable = False
            raise err
        if is_non_retryable_client_status(status_code) or 400 <= status_code < 500:
            logger.warning(
                "llm.client_error",
                provider=self._name,
                status_code=status_code,
                body_preview=body_preview(body),
            )
            typed = self._opencode_typed_client_error(
                status=status_code, body=body
            )
            if typed is not None:
                raise typed
            raise upstream_client_error(
                client_error_message(self._display_name, status_code, body),
                status=status_code,
                body=body,
                **unsupported_tool_schema_error_details(
                    body, payload=payload, profile=scenario
                ),
            )

    @staticmethod
    def _is_dns_failure(exc: BaseException) -> bool:
        """True when the transport failure is hostname resolution (not TCP/TLS)."""
        detail = str(exc).strip().lower()
        return any(
            needle in detail
            for needle in (
                "name or service not known",
                "getaddrinfo failed",
                "nodename nor servname",
                "temporary failure in name resolution",
                "errno -2",
            )
        )

    def _probe_connect_error(self, exc: httpx.HTTPError) -> LLMError:
        """User-facing connect error for settings「测试连接」/ model discovery."""
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError(f"连接 {self._display_name} 超时，请检查网络后重试")
        if self._is_dns_failure(exc):
            return LLMError(
                f"无法连接 {self._display_name}：域名无法解析。"
                "请确认 Base URL 拼写正确，且为公网可达地址"
                "（公司内网域名通常无法从云端访问）"
            )
        return LLMError(
            f"无法连接 {self._display_name}：端点不可达，请确认 Base URL 可从公网访问"
        )

    def _network_error_to_llm(self, exc: httpx.HTTPError) -> LLMError:
        """Map transient transport failures to retryable LLM errors."""
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError("连接上游模型服务超时，请检查网络后重试")
        if self._is_dns_failure(exc):
            return upstream_error(
                "上游域名无法解析；请确认 Base URL 为公网可达地址"
                "（公司内网域名通常无法从云端访问）",
                status=502,
                body=str(exc).strip().encode() or b"dns",
            )
        detail = str(exc).strip() or type(exc).__name__
        return upstream_error(
            "上游模型服务连接中断，请稍后再试",
            status=502,
            body=detail.encode(),
        )

    @staticmethod
    def _is_connect_failure(exc: BaseException) -> bool:
        """True for httpx connect-class failures (not read / write timeouts)."""
        return isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError))

    def _can_retry_attempt(self, attempt: int, *, max_attempts: int | None = None) -> bool:
        limit = _MAX_RETRIES if max_attempts is None else max_attempts
        return attempt < limit - 1

    def _try_omit_temperature_once(
        self,
        payload: dict,
        status_code: int,
        body: bytes | None,
        *,
        stream: bool,
    ) -> bool:
        """Strip ``temperature`` and signal one retry when upstream rejects it.

        Only fires for HTTP 400 bodies already classified by
        :func:`is_temperature_deprecated`, and only while the payload still
        carries ``temperature`` — so at most one extra request. Other 4xx stay
        on the normal raise path.
        """
        if status_code != 400 or "temperature" not in payload:
            return False
        if not is_temperature_deprecated(body):
            return False
        del payload["temperature"]
        logger.info(
            "llm.temperature_omitted_retry",
            provider=self._name,
            model=payload.get("model"),
            stream=stream,
            body_preview=body_preview(body),
        )
        return True

    async def _sleep_before_retry(
        self,
        *,
        attempt: int,
        backoff: float,
        stream: bool,
        reason: str,
        partial_sse_lines: int = 0,
        max_attempts: int | None = None,
    ) -> float:
        raise_if_task_cancelled()
        wait = backoff
        logger.info(
            "llm.call_retried",
            provider=self._name,
            attempt=attempt + 1,
            max_attempts=max_attempts if max_attempts is not None else _MAX_RETRIES,
            wait_sec=wait,
            stream=stream,
            reason=reason,
            partial_sse_lines=partial_sse_lines or None,
        )
        await asyncio.sleep(wait)
        return backoff * _BACKOFF_MULTIPLIER

    async def _finalize_upstream_error(
        self, err: LLMUpstreamError, attempt: int
    ) -> LLMUpstreamError:
        final = upstream_error(
            err.message,
            status=err.details.get("upstream_status", 500),
            body=err.details.get("upstream_body_preview"),
            retry_attempts=attempt + 1,
        )
        raise await self._log_sub2api_diagnosis(final) from err

    async def _request_with_retry(
        self, payload: dict, *, scenario: str = "chat", patience: float | None = None
    ) -> dict:
        last_error: Exception | None = None
        backoff = _INITIAL_BACKOFF
        # Mirrors ``stream``: connect-class failures keep their own budget/backoff.
        connect_max, connect_backoff = connect_retry_policy(scenario)
        started = time.monotonic()
        self._ensure_client_open()
        for attempt in range(_IO_ATTEMPT_CEILING):
            raise_if_task_cancelled()
            self._enforce_declared_tool_surface(payload)
            # Recomputed each attempt: a 429 we already slept off spent part of the
            # caller's wall clock, so the next cooldown is judged against what is
            # actually left rather than the patience we started with.
            ceiling = provider_retry_ceiling(
                scenario=scenario, patience=patience, elapsed=time.monotonic() - started
            )
            await self._await_shared_cooldown(
                scenario=scenario, ceiling=ceiling, attempt=attempt, stream=False
            )
            try:
                self._ensure_client_open()
                response = await self._client.post(
                    "/chat/completions",
                    json=payload,
                    headers=_request_attribution_headers() or None,
                )
                body = response.content if response.status_code >= 400 else None
                if body is not None and self._try_omit_temperature_once(
                    payload, response.status_code, body, stream=False
                ):
                    continue
                self._raise_for_status(
                    response.status_code,
                    backoff,
                    response.headers,
                    body=body,
                    attempt=attempt,
                    scenario=scenario,
                    retry_ceiling=ceiling,
                    payload=payload,
                )
                clear_cooldown(self._cooldown_key)
                try:
                    return response.json()
                except ValueError as e:
                    # 2xx HTML / non-JSON (gateway login page, etc.): not transient —
                    # retrying the same endpoint just spins. Mirror list_models.
                    raise LLMInvalidResponseError(
                        f"{self._display_name} 响应格式无效"
                    ) from e
            except asyncio.CancelledError:
                raise
            except LLMUpstreamError as e:
                last_error = e
                if not e.retryable or not self._can_retry_attempt(attempt):
                    await self._finalize_upstream_error(e, attempt)
                backoff = await self._sleep_before_retry(
                    attempt=attempt,
                    backoff=backoff,
                    stream=False,
                    reason=f"upstream_{e.details.get('upstream_status', 500)}",
                )
            except (LLMRateLimitError, LLMError) as e:
                last_error = e
                if self._is_pool_failover_signal(e) and (
                    await self._try_platform_pool_failover()
                ):
                    continue
                wait = self._rate_limit_retry_plan(
                    e,
                    attempt=attempt,
                    ceiling=ceiling,
                    scenario=scenario,
                    backoff=backoff,
                    stream=False,
                )
                await asyncio.sleep(wait)
                clear_cooldown(self._cooldown_key)
                backoff *= _BACKOFF_MULTIPLIER
            except RuntimeError as e:
                translated = self._translate_closed_client(e)
                if isinstance(translated, LLMClientClosedError):
                    raise translated from e
                raise
            except httpx.TimeoutException as e:
                raise_if_task_cancelled(e)
                last_error = LLMTimeoutError(f"连接 {self._display_name} 超时，请检查网络后重试")
                is_connect = isinstance(e, httpx.ConnectTimeout)
                max_attempts = connect_max if is_connect else _MAX_RETRIES
                if not self._can_retry_attempt(attempt, max_attempts=max_attempts):
                    raise last_error from e
                next_backoff = await self._sleep_before_retry(
                    attempt=attempt,
                    backoff=connect_backoff if is_connect else backoff,
                    stream=False,
                    reason="connect_timeout" if is_connect else "timeout",
                    max_attempts=max_attempts,
                )
                if is_connect:
                    connect_backoff = next_backoff
                else:
                    backoff = next_backoff
            except httpx.HTTPError as e:
                raise_if_task_cancelled(e)
                last_error = self._network_error_to_llm(e)
                is_connect = self._is_connect_failure(e)
                max_attempts = connect_max if is_connect else _MAX_RETRIES
                if not last_error.retryable or not self._can_retry_attempt(
                    attempt, max_attempts=max_attempts
                ):
                    raise last_error from e
                next_backoff = await self._sleep_before_retry(
                    attempt=attempt,
                    backoff=connect_backoff if is_connect else backoff,
                    stream=False,
                    reason=type(e).__name__,
                    max_attempts=max_attempts,
                )
                if is_connect:
                    connect_backoff = next_backoff
                else:
                    backoff = next_backoff
        raise last_error or LLMError(f"{self._display_name} 多次重试后仍失败，请稍后重试")

    async def probe(self, *, model: str) -> None:
        dialect = resolve_wire_dialect(model, base_url=self._base_url)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        }
        dialect.apply_token_limit(payload, 1)
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise_if_task_cancelled(e)
            raise self._probe_connect_error(e) from e
        code = response.status_code
        # 429 = authenticated but throttled — treat as reachable without body parse.
        if code == 429:
            return
        if code < 300:
            self._require_chat_completions_body(response.content)
            return
        if 400 <= code < 500:
            typed = self._opencode_typed_client_error(
                status=code, body=response.content
            )
            if typed is not None:
                raise typed
        if code in (401, 403):
            preview = body_preview(response.content)
            logger.warning(
                "llm.client_error",
                provider=self._name,
                status_code=code,
                body_preview=preview,
            )
            if is_balance_exhausted(response.content):
                raise self._insufficient_balance_error(status=code, body=response.content)
            if is_auth_rejection(code, response.content):
                raise LLMError(
                    f"{self._display_name} API Key 无效或无权限（鉴权失败），请检查后重试",
                    upstream_status=code,
                    upstream_body_preview=preview,
                )
            raise upstream_client_error(
                client_error_message(self._display_name, code, response.content),
                status=code,
                body=response.content,
            )
        if code == 402:
            raise self._insufficient_balance_error(status=code, body=response.content)
        if code == 404:
            # Must read body: model-id 404s (Not found the model / resource_not_found)
            # must not be mislabelled as a bad base_url.
            raise LLMError(client_error_message(self._display_name, code, response.content))
        if code >= 500:
            raise LLMError(f"上游模型服务暂时不可用（{code}），请稍后再试")
        raise LLMError(f"{self._display_name} 连通测试失败（HTTP {code}）")

    def _require_chat_completions_body(self, content: bytes | str | None) -> None:
        """Reject 2xx HTML / non-JSON / non-chat shells that used to soft-green probe."""
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace").strip()
        else:
            text = (content or "").strip()
        if not text:
            raise LLMError(
                f"{self._display_name} 连通测试返回空响应。"
                "请检查 Base URL（自定义地址通常需含 /v1）与 API Key。"
            )
        lowered = text[:2000].lower()
        if "<html" in lowered or "<!doctype" in lowered or '<div id="root"' in lowered:
            raise LLMError(
                f"{self._display_name} 连通测试返回了网页而非模型接口。"
                "请检查 Base URL（自定义地址通常需含 /v1，"
                "例如 https://api.example.com/v1）。"
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(
                f"{self._display_name} 连通测试响应不是合法 JSON。"
                "请检查 Base URL（自定义地址通常需含 /v1）。"
            ) from e
        if not isinstance(data, dict):
            raise LLMError(f"{self._display_name} 连通测试响应格式无效（非对象）")
        if data.get("error"):
            err = data["error"]
            err_text = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
            raise LLMError(
                f"{self._display_name} 连通测试被上游拒绝：{err_text[:200]}"
            )
        choices = data.get("choices")
        if not isinstance(choices, list):
            raise LLMError(
                f"{self._display_name} 连通测试响应缺少 choices，不是 chat completions。"
                "请检查 Base URL 是否指向 OpenAI 兼容接口（通常含 /v1）。"
            )
    async def probe_tools(self, *, model: str) -> bool | None:
        """Probe whether the endpoint *accepts* tool calling (three-state).

        - ``True``: strong evidence — response included ``tool_calls``
        - ``False``: 4xx body clearly rejects tools / tools parameters
        - ``None``: unknown — 2xx without tool_calls, timeout, network, 429,
          auth errors, or any ambiguous failure (never pretend False)

        Strategy: try ``tool_choice="required"`` first; on HTTP 400 when the
        dialect allows (``retry_forced_tool_choice_on_400``, e.g. DeepSeek V4)
        fall back to omitting tool_choice.
        """
        outcome = await self._probe_tools_once(model=model, tool_choice="required")
        if outcome == _PROBE_TOOLS_RETRY:
            outcome = await self._probe_tools_once(model=model, tool_choice=None)
        if outcome == _PROBE_TOOLS_RETRY:
            return None
        return outcome

    async def _probe_tools_once(
        self, *, model: str, tool_choice: str | None
    ) -> bool | None | Literal["retry_without_required"]:
        dialect = resolve_wire_dialect(model, base_url=self._base_url)
        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": "Call the dummy tool."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "dummy_probe",
                        "description": "Connectivity probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "stream": False,
        }
        dialect.apply_token_limit(payload, _PROBE_TOOLS_MAX_TOKENS)
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.HTTPError):
            return None
        code = response.status_code
        if code == 429 or code >= 500:
            return None
        if 200 <= code < 300:
            try:
                data = response.json()
            except ValueError:
                return None
            choices = data.get("choices") or []
            if not choices:
                return None
            message = choices[0].get("message") or {}
            return True if message.get("tool_calls") else None
        # Dialect-gated: forced tool_choice 400 → retry without it.
        if (
            code == 400
            and tool_choice == "required"
            and dialect.retry_forced_tool_choice_on_400
        ):
            return _PROBE_TOOLS_RETRY
        try:
            body = response.text or ""
        except Exception:  # noqa: BLE001 — body read is best-effort for classification
            body = ""
        if _is_tools_unsupported_rejection(code, body):
            return False
        return None

    async def list_models(self) -> list[str]:
        """Discover the endpoint's real model ids via the OpenAI-standard ``GET /models``.

        Returns the ``data[].id`` list (order preserved, de-duped). Raises ``LLMError``
        on any transport / non-2xx / malformed-body failure so the caller (the model
        catalog) can degrade gracefully instead of surfacing a 500 — this is a
        best-effort discovery probe, never a turn-critical path.
        """
        try:
            response = await self._client.get("/models")
        except httpx.HTTPError as e:
            raise_if_task_cancelled(e)
            raise self._probe_connect_error(e) from e
        code = response.status_code
        if 400 <= code < 500:
            typed = self._opencode_typed_client_error(
                status=code, body=response.content
            )
            if typed is not None:
                raise typed
        if code in (401, 403):
            if "/inference/" in self._base_url:
                raise InferenceTokenExpiredError(
                    upstream_status=code,
                    upstream_body_preview=body_preview(response.content),
                )
            if is_balance_exhausted(response.content):
                raise self._insufficient_balance_error(status=code, body=response.content)
            raise LLMAuthError(
                provider_name=self._name,
                display_name=self._display_name,
                upstream_status=code,
                upstream_body_preview=body_preview(response.content),
            )
        if code == 402:
            raise self._insufficient_balance_error(status=code, body=response.content)
        if code >= 400:
            raise LLMError(f"{self._display_name} 列出模型失败（HTTP {code}）")
        try:
            data = response.json()
        except ValueError as e:
            raise LLMInvalidResponseError(
                f"{self._display_name} 模型列表响应格式无效"
            ) from e
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise LLMError(f"{self._display_name} 模型列表响应缺少 data 字段")
        ids: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                ids.append(model_id)
        return ids

    def _ensure_client_open(self) -> None:
        """Fail fast with a typed non-retryable error when turn teardown closed us."""
        if self._client.is_closed:
            raise LLMClientClosedError()

    @staticmethod
    def _translate_closed_client(exc: BaseException) -> BaseException:
        if is_llm_client_closed_error(exc) and not isinstance(exc, LLMClientClosedError):
            msg = str(exc).strip()
            return LLMClientClosedError(msg) if msg else LLMClientClosedError()
        return exc

    async def close(self) -> None:
        await self._client.aclose()
