"""LLM resilience layer protocol.

Authoritative **429 decision order** (not wrap order: stall wraps fence wraps
leaf). Implementations stay split (``call_fence`` / ``cooldown_gate`` /
platform pool / ``openai_compatible`` / ``runtime.engine.stream``); this
module does not retry, swap credentials, or change success/failure semantics.

On HTTP 429 the leaf records pool cooling, **fails over first**, then arms
the shared cooldown / fail-fast / sleep. Stall idle is orthogonal (TimeoutError
only) and listed last as a separate failure class.

``decision_spine`` uses :func:`summarize_degradation` so one line can answer
「这回合经历了哪些降级」. Mapping uses **existing** log event names only —
no new emit.

Post-commit stream stall (``llm.stream_stalled`` with ``committed=true``) is
salvage only, not a retry layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

ResilienceLayer = Literal[
    "admission",
    "credential_pool",
    "cooldown",
    "leaf_retry",
    "stream_stall_precommit",
]

LAYER_ORDER: tuple[ResilienceLayer, ...] = (
    "admission",
    "credential_pool",
    "cooldown",
    "leaf_retry",
    "stream_stall_precommit",
)

# Adjacent counts: not LLM leaf layers, still part of the turn-level summary.
EXTRA_COUNT_KEYS: tuple[str, ...] = (
    "stream_stall_salvage",
    "empty_response",
    "web_search_cloud_fallback",
    "web_search_cloud_fallback_failed",
)

COUNT_KEYS: tuple[str, ...] = LAYER_ORDER + EXTRA_COUNT_KEYS

# Engine stream stall retry (pre-commit). Leaf ``llm.call_retried`` uses other reasons.
STREAM_STALL_RETRY_REASON = "stream_stall"

# Name → layer when no extra field is needed to disambiguate.
_EVENT_LAYER: dict[str, ResilienceLayer] = {
    "llm.turn_auth_dead": "admission",
    "billing.call_quota_refused": "admission",
    "llm.rate_limit_no_retry": "cooldown",
    "platform_pool.failover": "credential_pool",
}

_EVENT_EXTRA: dict[str, str] = {
    "llm.empty_response": "empty_response",
    "tool.web_search_cloud_fallback": "web_search_cloud_fallback",
    "tool.web_search_cloud_fallback_failed": "web_search_cloud_fallback_failed",
}


def count_key_for_event(event: Mapping[str, Any]) -> str | None:
    """Map one log event to a :data:`COUNT_KEYS` member, or ``None`` if unmapped."""
    name = str(event.get("event") or "")
    if name == "llm.call_retried":
        if str(event.get("reason") or "") == STREAM_STALL_RETRY_REASON:
            return "stream_stall_precommit"
        return "leaf_retry"
    if name == "llm.stream_stalled":
        if event.get("committed"):
            return "stream_stall_salvage"
        return "stream_stall_precommit"
    layer = _EVENT_LAYER.get(name)
    if layer is not None:
        return layer
    return _EVENT_EXTRA.get(name)


def layer_for_event(event: Mapping[str, Any]) -> ResilienceLayer | None:
    """LLM layer for ``event``, or ``None`` when it is extra / unmapped."""
    key = count_key_for_event(event)
    for layer in LAYER_ORDER:
        if key == layer:
            return layer
    return None


def format_degradation_summary(counts: Mapping[str, int]) -> str:
    """Non-zero counts in protocol order — empty string when nothing fired."""
    return " ".join(f"{key}={counts[key]}" for key in COUNT_KEYS if counts.get(key, 0) > 0)


def summarize_degradation(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Fold log events into a degradation block, or ``None`` when every count is 0."""
    counts = {key: 0 for key in COUNT_KEYS}
    for ev in events:
        key = count_key_for_event(ev)
        if key is not None:
            counts[key] += 1
    if not any(counts.values()):
        return None
    return {
        "layers": list(LAYER_ORDER),
        "counts": counts,
        "summary": format_degradation_summary(counts),
    }
