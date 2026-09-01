"""LLMProvider protocol and core data types."""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# Shared retry / backoff knobs for provider I/O and engine stream consumers.
# Public so runtime.engine.stream (and tests) never touch provider privates.
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
BACKOFF_MULTIPLIER = 2.0
# Connect-class failures (ConnectTimeout / ConnectError): fail fast — 1 retry,
# 1s backoff (worst case ≈ connect_timeout + 1s + connect_timeout ≈ 21s with
# the 10s connect ceiling). Read timeout / 5xx keep MAX_RETRIES above.
CONNECT_MAX_RETRIES = 2
CONNECT_INITIAL_BACKOFF = 1.0
# Turn-scale scenarios invert that trade-off: a background one-shot (title /
# memory / compaction) only loses its own cheap output, but a chat turn or a
# delegated worker loses the whole run — teammates' prose, coordination state,
# minutes of wall clock — and the turn ends with no assistant message at all.
# So connect failures there get a real exponential chain, sized to ride out a
# proxy / tunnel restart (≈25s realistic, ≈47s if every connect also times out);
# a genuine outage still fails, just not within 5 seconds.
TURN_CONNECT_MAX_RETRIES = 4
TURN_CONNECT_INITIAL_BACKOFF = 2.0
# ``LLMRequest.scenario`` values that carry a whole turn. Mirrors the multi-round
# entries of ``llm.profiles.PROFILES`` (kept here, not imported, because profiles
# depends on this module); one-shot scenarios keep the fail-fast budget above.
TURN_SCALE_SCENARIOS = frozenset({"chat", "agent"})
# 429 / Retry-After: allow more attempts than generic I/O so short exponential
# Retry-After chains (2→4→8…) are actually waited, not abandoned on the 3rd hit.
RATE_LIMIT_MAX_RETRIES = 6
# The Retry-After ceiling is NOT declared here, and is not one number:
# ``llm.provider.call_budget`` derives it from what is left of *this* call's patience
# (``LLMRequest.retry_patience_seconds``), so a 45s fold waits out a cooldown a 20s
# title must refuse — and a fold some turn is blocked on waits out nothing at all.
# Callers with no patience fall back to the interactive ceiling
# ``core.errors.MAX_RETRY_AFTER``, which is single-sourced next to the 429 copy
# that quotes it. Hour-scale headers (e.g. 3600) are refused under every budget.


def connect_retry_policy(scenario: str) -> tuple[int, float]:
    """``(max_attempts, initial_backoff)`` for connect-class failures in ``scenario``."""
    if scenario in TURN_SCALE_SCENARIOS:
        return TURN_CONNECT_MAX_RETRIES, TURN_CONNECT_INITIAL_BACKOFF
    return CONNECT_MAX_RETRIES, CONNECT_INITIAL_BACKOFF


@dataclass
class ToolCallFunction:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction = field(default_factory=lambda: ToolCallFunction("", ""))


@dataclass
class ToolCallDelta:
    index: int
    id: str | None = None
    function_name: str | None = None
    arguments_delta: str | None = None


# OpenAI-compatible multimodal user content: str, or a list of text / image_url parts.
LLMContent = str | list[dict] | None


def llm_content_text(content: LLMContent) -> str:
    """Extract plain text from ``LLMMessage.content`` (str or multimodal parts).

    Image parts are skipped; used by governance / strip sites that must not assume str.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                chunks.append(str(part.get("text") or ""))
        return "".join(chunks)
    return str(content)


def build_multimodal_user_content(
    text: str, image_parts: list[dict]
) -> str | list[dict]:
    """Build user ``content``: plain str when no images; else text + image_url parts."""
    if not image_parts:
        return text
    stripped = (text or "").strip()
    parts: list[dict] = [
        {
            "type": "text",
            "text": stripped if stripped else "（用户附上了图片）",
        }
    ]
    parts.extend(image_parts)
    return parts


@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: LLMContent = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    # Tool-result audience (``ceo`` = orchestration; not sent to the LLM wire).
    audience: str | None = None


@dataclass
class LLMRequest:
    messages: list[LLMMessage]
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict] | None = None
    tool_choice: Literal["auto", "none", "required"] = "auto"
    stream: bool = True
    scenario: str = "chat"
    # None / True → thinking.type=enabled on thinking_type_switch models.
    # False → disabled. Do not omit: some gateways treat omit as off.
    thinking: bool | None = None
    # Seconds of this call's wall clock that may be spent *asleep* waiting out a 429
    # (``llm.provider.call_budget.complete_within_budget`` derives it from the
    # caller's deadline and whether a turn is blocked on the call, then stamps it).
    # ``0.0`` = fail on the first dated cooldown; ``None`` = no deadline at all, so
    # the interactive ceiling applies. Honoured only for the silent-degrade
    # scenarios — see ``call_budget.provider_retry_ceiling``.
    retry_patience_seconds: float | None = None


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    # Largest single-request prompt this accumulator has seen. ``input_tokens``
    # sums every round (billing); fit-check / near-ceiling compare this field
    # to the model's window. ``__add__`` keeps the max, never sums.
    last_prompt_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def fuse_tokens(self) -> int:
        """Runaway fuse: new work only (cache miss + output).

        Billing still uses :attr:`total_tokens` (full prompt every round). The
        per-worker ceiling must not count a repeated cached prefix as spend —
        otherwise a long coding run hits the fuse while most tokens are cache
        hits. Providers that omit the split (hit=0 and miss=0) fall back to
        ``total_tokens`` so the fuse still fires. OpenAI-style hit-only wires
        derive miss as ``input − hit``.
        """
        hit = int(self.cache_hit_tokens or 0)
        miss = int(self.cache_miss_tokens or 0)
        if miss <= 0 and hit <= 0:
            return self.total_tokens
        if miss <= 0:
            miss = max(int(self.input_tokens or 0) - hit, 0)
        return miss + int(self.output_tokens or 0)

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cache_hit_tokens=self.cache_hit_tokens + other.cache_hit_tokens,
            cache_miss_tokens=self.cache_miss_tokens + other.cache_miss_tokens,
            last_prompt_tokens=max(self.last_prompt_tokens, other.last_prompt_tokens),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "reasoning": self.reasoning_tokens,
            "cache_hit": self.cache_hit_tokens,
            "cache_miss": self.cache_miss_tokens,
            "last_prompt": self.last_prompt_tokens,
        }

    @classmethod
    def from_usage_dict(cls, usage: Mapping[str, int]) -> "TokenUsage":
        return cls(
            input_tokens=usage.get("input", 0),
            output_tokens=usage.get("output", 0),
            reasoning_tokens=usage.get("reasoning", 0),
            cache_hit_tokens=usage.get("cache_hit", 0),
            cache_miss_tokens=usage.get("cache_miss", 0),
            last_prompt_tokens=int(usage.get("last_prompt", 0) or 0),
        )

    @classmethod
    def from_call_meta(cls, usage_meta: Mapping[str, Any] | None) -> "TokenUsage":
        """One upstream call's usage block (``input_tokens`` keys, not short keys)."""
        meta = usage_meta or {}
        inp = int(meta.get("input_tokens", 0) or 0)
        return cls(
            input_tokens=inp,
            output_tokens=int(meta.get("output_tokens", 0) or 0),
            reasoning_tokens=int(meta.get("reasoning_tokens", 0) or 0),
            cache_hit_tokens=int(meta.get("cache_hit_tokens", 0) or 0),
            cache_miss_tokens=int(meta.get("cache_miss_tokens", 0) or 0),
            last_prompt_tokens=inp,
        )

    @classmethod
    def from_openai_wire(cls, usage_data: Mapping[str, Any]) -> "TokenUsage":
        """Parse a ``usage`` block from an OpenAI-compatible ``/chat/completions`` reply.

        The single wire-usage parser (gateway / vision reader / inference proxy all
        route here). Two prompt-cache dialects exist in the wild and we must read
        both — a cache hit the parser can't see is billed as a full miss for platform
        models and invisible in usage observability:

        - **DeepSeek style** (explicit split): ``prompt_cache_hit_tokens`` +
          ``prompt_cache_miss_tokens``. Preferred when present (authoritative).
        - **OpenAI style** (hit only): ``prompt_tokens_details.cached_tokens`` is the
          cached portion; the uncached remainder is derived as
          ``prompt_tokens − cached_tokens`` (clamped at 0). Mirrors the pricing-side
          reconciliation ``cache_miss = max(input − hit, miss)`` so the two layers
          agree (llm/pricing.py::calculate_cost).

        Neither present → both 0; pricing then reconciles the whole prompt as a miss.
        Values ride ``int(x or 0)`` so ``null`` fields from lenient proxies parse as 0.
        """
        completion_details = usage_data.get("completion_tokens_details") or {}
        prompt_details = usage_data.get("prompt_tokens_details") or {}
        input_tokens = int(usage_data.get("prompt_tokens", 0) or 0)
        cache_hit = int(usage_data.get("prompt_cache_hit_tokens", 0) or 0)
        cache_miss = int(usage_data.get("prompt_cache_miss_tokens", 0) or 0)
        if not cache_hit and not cache_miss:
            cached = int(prompt_details.get("cached_tokens", 0) or 0)
            if cached:
                cache_hit = cached
                cache_miss = max(input_tokens - cached, 0)
        return cls(
            input_tokens=input_tokens,
            output_tokens=int(usage_data.get("completion_tokens", 0) or 0),
            reasoning_tokens=int(completion_details.get("reasoning_tokens", 0) or 0),
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
            last_prompt_tokens=input_tokens,
        )


@dataclass
class LLMResponse:
    content: str = ""
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"] = "stop"
    model: str = ""
    latency_ms: int = 0
    empty_diagnosis: str | None = None
    empty_raw_preview: str | None = None


@dataclass
class LLMChunk:
    delta_content: str | None = None
    delta_reasoning: str | None = None
    delta_tool_calls: list[ToolCallDelta] | None = None
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    empty_diagnosis: str | None = None
    empty_raw_preview: str | None = None
    # Control signals (mutually exclusive with normal deltas when set):
    # stream_reset — transparent pre-commit retry; consumer must drop ephemeral
    #   reasoning and reset the live view before the next attempt's chunks.
    # aborted — post-commit disconnect; consumer keeps the partial and must not
    #   treat the stream as a hard raise/discard.
    stream_reset: bool = False
    aborted: bool = False


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    # Sync ``def`` (not ``async def``): real leaves are async generators — calling
    # ``stream()`` returns an AsyncIterator immediately; mypy models ``async def``
    # Protocol stubs as Coroutine[…, AsyncIterator] which breaks callers.
    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]: ...

    async def close(self) -> None: ...

    @property
    def base_url(self) -> str | None:
        """Upstream API root for empty-response / error diagnostics; ``None`` if N/A."""
        ...
