"""LLM-call observability: the single emit point for ``llm.call`` /
``llm.call_failed`` and optional ``llm.request`` / ``llm.response`` body capture.

Why a shared helper: production leaves are wrapped by
:func:`agentcore.llm.call_fence.observe_provider` (from ``build_provider``), which
calls :func:`log_llm_call` / :func:`log_llm_call_failed` so every path — turn
router leaf, background unary, sidecar ``inference/proxy`` — lands one uniform
line, attributed by ``scenario`` / ``model`` / ``attempt`` and (via
``contextvars``) by ``trace_id`` / ``conversation_id`` / worker identity. This is
the per-call layer the round/turn aggregates (``react.round_end`` /
``chat.turn_complete``) cannot give: per-model latency, finish_reason, and the
chat-vs-worker-vs-title-vs-memory split. Being that single point is also why the
prompt-prefix-cache probe hangs here (``cost.prefix_cache``, 审计议题 D4): it needs
the same per-call pairing of request messages with the provider's own usage split.

Bodies (the actual prompt + completion) are the lever for prompt tuning but are
large and sensitive, so they are OFF by default and only captured when
``settings.log_llm_bodies`` is on — always TRUNCATED and secret-redacted
(logging.mdc 铁律: never a BYOK key, never full file/message content). That single
switch fully controls them: when enabled they emit at ``info`` (not ``debug``), so
dev's default ``LOG_LEVEL=info`` surfaces them without also raising the global level
(which would flood the log with unrelated debug lines).
"""

from __future__ import annotations

from typing import Any

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.secrets import redact_secrets
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage

logger = get_logger("agentcore.llm.call")

# Caps for the (debug-only) body capture: per-message, then the whole prompt /
# response blob. Bounds log volume and limits how much sensitive text can leak.
_MSG_MAX_CHARS = 600
_BODY_MAX_CHARS = 2000


def _platform_credential_log_fields(*, source: str | None) -> dict[str, str]:
    """Attach pool-member id on platform-paid calls only (never BYOK / vendor)."""
    if source != "platform":
        return {}
    from agentcore.core.log_context import get_log_value

    cid = get_log_value("platform_credential_id")
    return {"platform_credential_id": cid} if cid else {}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit})"


def _redact(text: str) -> str:
    """SEC-001: scrub pasted keys from debug LLM body capture (shared scrubber)."""
    return redact_secrets(text)


def _format_prompt(messages: list[LLMMessage]) -> str:
    """Compact, per-message-clipped, redacted view of the request messages."""
    parts: list[str] = []
    for m in messages:
        body = m.content or ""
        if m.tool_calls:
            body += " " + " ".join(f"→{tc.function.name}()" for tc in m.tool_calls)
        if m.tool_call_id and not body:
            body = f"(tool result {m.tool_call_id})"
        parts.append(f"[{m.role}] {_clip(_redact(body), _MSG_MAX_CHARS)}")
    return _clip("\n".join(parts), _BODY_MAX_CHARS)


def log_llm_call(
    *,
    scenario: str,
    model: str,
    usage: TokenUsage | None,
    finish_reason: str | None,
    latency_ms: int,
    stream: bool,
    messages: list[LLMMessage] | None = None,
    content: str | None = None,
    reasoning: str | None = None,
    tool_names: list[str] | None = None,
    credential_source: str | None = None,
    provider_name: str | None = None,
    attempt: int = 1,
) -> None:
    """Emit one ``llm.call`` metrics line (+ optional bodies when enabled).

    Inherits ``trace_id`` / ``conversation_id`` / ``agent_id`` / ``run_id`` /
    ``depth`` from contextvars, so calls attribute to their turn and worker.
    """
    u = usage or TokenUsage()
    # Lazy import: observability sits under provider → profiles; pricing imports
    # profiles, so a top-level import would cycle. calculate_cost itself is sync
    # and DB-free — the single billing price source (不变量 #2).
    from agentcore.llm.pricing import calculate_cost, resolve_credential_source

    explicit_source = (
        credential_source if credential_source in ("user", "platform", "vendor") else None
    )
    source = resolve_credential_source(
        credential_source=explicit_source,
        provider_name=provider_name,
        model=model,
    )
    priced = calculate_cost(model, u, credential_source=source)
    # cost_nano = platform/vendor billed nano (quota-facing); user estimates stay separate.
    cost_nano = 0 if priced.credential_source == "user" else priced.total
    cost_estimated_nano = priced.total if priced.credential_source == "user" else 0
    extra: dict[str, Any] = {}
    if tool_names:
        extra["tool_names"] = tool_names
    extra.update(_platform_credential_log_fields(source=source))
    logger.info(
        "llm.call",
        scenario=scenario,
        model=model,
        finish_reason=finish_reason or "stop",
        latency_ms=latency_ms,
        stream=stream,
        attempt=max(1, int(attempt)),
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        reasoning_tokens=u.reasoning_tokens,
        cache_hit_tokens=u.cache_hit_tokens,
        cache_miss_tokens=u.cache_miss_tokens,
        cost_nano=cost_nano,
        cost_estimated_nano=cost_estimated_nano,
        pricing_source=priced.pricing_source,
        **extra,
    )

    # 审计议题 D4: pair the billed cache split above with WHY it came out that way — the
    # provider matches ``system + history + user`` as one token prefix, so ``cache_hit_tokens``
    # alone cannot tell a tail edit from plain history growth. Same seam on purpose: every
    # path that lands one ``llm.call`` lands one ``cost.prefix_cache`` beside it. Observation
    # only, and never allowed to break a call (same rule as metering below).
    try:
        from agentcore.observability.prefix_cache import observe_prefix_cache

        observe_prefix_cache(
            scenario=scenario,
            model=model,
            messages=messages,
            input_tokens=u.input_tokens,
            cache_hit_tokens=u.cache_hit_tokens,
            cache_miss_tokens=u.cache_miss_tokens,
        )
    except Exception:  # noqa: BLE001 — observability must never break the LLM path
        pass

    # Cloud in-process metering: enqueue a cost_calls detail when the ledger
    # drainer is running (API server lifespan). Sidecar never starts the drain —
    # its spend is recorded by the cloud inference proxy instead. Proxy-forwarded
    # unary complete() calls still hit this path for llm.call latency logs, but
    # maybe_enqueue skips when scenario is PROXY_LLM_SCENARIO (proxy_spend only).
    if usage is not None and (usage.input_tokens or usage.output_tokens):
        try:
            from agentcore.billing.call_meter import maybe_enqueue_inprocess_call

            maybe_enqueue_inprocess_call(
                model=model,
                usage=usage,
                duration_ms=latency_ms,
                scenario=scenario,
                credential_source=source,
            )
        except Exception:  # noqa: BLE001 — metering must never break the LLM path
            pass
        # Turn 级累计：仅当 pipeline 绑定了 meter（用户回合）才记账；后台 title/memory 等无 meter。
        try:
            from agentcore.runtime.turn.token_budget import record_turn_tokens

            record_turn_tokens(usage.fuse_tokens)
        except Exception:  # noqa: BLE001 — budget meter must never break the LLM path
            pass

    if not settings.log_llm_bodies:
        return
    # Emitted at info (not debug) so the single ``log_llm_bodies`` switch is sufficient —
    # no need to also drop LOG_LEVEL to debug (which would flood unrelated debug lines).
    if messages is not None:
        logger.info("llm.request", scenario=scenario, model=model, prompt=_format_prompt(messages))
    if content is not None or reasoning is not None:
        logger.info(
            "llm.response",
            scenario=scenario,
            model=model,
            finish_reason=finish_reason or "stop",
            content=_clip(_redact(content or ""), _BODY_MAX_CHARS),
            reasoning=_clip(_redact(reasoning or ""), _BODY_MAX_CHARS),
        )


def log_llm_call_failed(
    *,
    scenario: str,
    model: str,
    latency_ms: int,
    error: str,
    stream: bool,
    attempt: int = 1,
    error_type: str | None = None,
    upstream_status: int | None = None,
    upstream_body_preview: str | None = None,
) -> None:
    """Emit one ``llm.call_failed`` line (observation only — no metering / retry).

    Always includes ``model`` + ambient ``credential_source`` (when bound). Optional
    ambient ``provider_id`` is attached when present — never ``base_url`` / secrets.
    Optional ``upstream_status`` / ``upstream_body_preview`` when the failure carried
    an HTTP upstream context (5xx diagnosis).
    """
    from agentcore.core.log_context import get_log_value

    extra: dict[str, Any] = {}
    if error_type:
        extra["error_type"] = error_type
    cred_src = get_log_value("credential_source")
    if cred_src:
        extra["credential_source"] = cred_src
    provider_id = get_log_value("provider_id")
    if provider_id:
        extra["provider_id"] = provider_id
    extra.update(_platform_credential_log_fields(source=cred_src or None))
    if upstream_status is not None:
        extra["upstream_status"] = int(upstream_status)
    if upstream_body_preview:
        extra["upstream_body_preview"] = upstream_body_preview
    logger.error(
        "llm.call_failed",
        scenario=scenario,
        model=model,
        latency_ms=latency_ms,
        attempt=max(1, int(attempt)),
        stream=stream,
        error=error,
        **extra,
    )
