"""OpenCode Go public list — admin USD window estimates only.

USD / 1M token rates (Peak / Off-Peak) captured 2026-09-10 (DeepSeek V4.1 Flash
public list; retired V4 Flash token rates match). Product Flash money is
official DeepSeek CNY in ``llm/pricing.py``, not this table.

SKU ``costMultiplier`` is **not** in the unit rates. Update USD numbers and
``PRICE_AS_OF`` together when the public list retags.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from agentcore.llm.deepseek_official_peak import is_deepseek_official_peak
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH, OPENCODE_GO_V41_FLASH
from agentcore.llm.provider.protocol import TokenUsage

# Admin card ``estimate_model``: representative id. ``PRICED_MODEL_IDS`` is the
# match set — leftover V4 Flash ledger rows share this public list.
MODEL_ID = OPENCODE_GO_V41_FLASH
PRICED_MODEL_IDS = frozenset({DEEPSEEK_V4_FLASH, OPENCODE_GO_V41_FLASH})
PRICE_AS_OF = date(2026, 9, 10)
CURRENCY_USD = "USD"

# USD / 1M tokens. Peak is exactly 2× Off-Peak on every published tier.
_OFF_PEAK: dict[str, Decimal] = {
    "input": Decimal("0.15"),
    "output": Decimal("0.60"),
    "cache_hit": Decimal("0.003"),
}
_PEAK: dict[str, Decimal] = {
    "input": Decimal("0.30"),
    "output": Decimal("1.20"),
    "cache_hit": Decimal("0.006"),
}

# tokens × (USD / 1M) → nano-USD. Same scale as billing nano (1 unit = 1e9).
_PER_MILLION_TO_NANO = Decimal(1000)


def is_opencode_go_peak(ts: datetime) -> bool:
    """Whether ``ts`` falls in DeepSeek official Flash peak hours (UTC)."""
    return is_deepseek_official_peak(ts)


def go_public_card(ts: datetime) -> dict[str, Decimal]:
    """Peak or Off-Peak card for this call's timestamp — never a blended rate."""
    return _PEAK if is_opencode_go_peak(ts) else _OFF_PEAK


def estimate_go_public_usd_nano(
    tokens: Mapping[str, Any] | None,
    at: datetime,
    *,
    model: str,
) -> int:
    """Public-list USD estimate for one ``cost_calls`` row, as integer nano-USD.

    ``model`` must be one of ``PRICED_MODEL_IDS`` (Go V4.1 Flash / leftover V4 Flash).
    Other catalog ids (``glm-5.2``, ``deepseek-v4-flash-free``, …) return 0 —
    do not apply the Flash card to a different model.

    Token-field semantics — verified against the write path, not the field names:

    * ``TokenUsage.from_openai_wire`` sets ``input`` = wire ``prompt_tokens``
      (the whole prompt) and ``output`` = wire ``completion_tokens`` (the whole
      completion). ``as_dict`` persists those five keys onto ``cost_calls.tokens``
      (``runtime/costing.py::priced_call_cost``).
    * On the native DeepSeek path, ``prompt_tokens == cache_hit + cache_miss``.
      ``input`` **already includes** both cache buckets. Pricing ``input`` at the
      Input rate *and* ``cache_hit`` at Cached Read would double-count.
    * ``reasoning`` is ``completion_tokens_details.reasoning_tokens``, a **subset**
      of ``completion_tokens``. ``output`` **already includes** reasoning.
      ``calculate_cost`` prices output whole and never adds reasoning again
      (``llm/pricing.py``). Same here — reasoning and output are billed once.

    OpenCode public-list mapping (this table, not curated CNY):

    * ``cache_hit`` → Cached Read
    * reconciled miss → Input, where
      ``miss = max(input − cache_hit, cache_miss)``
      (same guard as ``calculate_cost``: missing split ⇒ whole prompt as Input,
      not zero)
    * ``output`` → Output

    Peak / Off-Peak is chosen from **this call's** ``at``, never a window average.

    Admin window card is a public-list estimate, not an upstream invoice.
    """
    if (model or "").strip() not in PRICED_MODEL_IDS:
        return 0
    from agentcore.llm.pricing import reconcile_cache_miss_tokens

    usage = _usage_from_ledger(tokens)
    card = go_public_card(at)
    cache_miss_tokens = reconcile_cache_miss_tokens(
        usage.input_tokens, usage.cache_hit_tokens, usage.cache_miss_tokens
    )
    cached = _nano(usage.cache_hit_tokens, card["cache_hit"])
    uncached = _nano(cache_miss_tokens, card["input"])
    output = _nano(usage.output_tokens, card["output"])
    return cached + uncached + output


def _usage_from_ledger(tokens: Mapping[str, Any] | None) -> TokenUsage:
    return TokenUsage(
        input_tokens=_tokens_int(tokens, "input"),
        output_tokens=_tokens_int(tokens, "output"),
        reasoning_tokens=_tokens_int(tokens, "reasoning"),
        cache_hit_tokens=_tokens_int(tokens, "cache_hit"),
        cache_miss_tokens=_tokens_int(tokens, "cache_miss"),
    )


def _tokens_int(tokens: Mapping[str, Any] | None, key: str) -> int:
    if not tokens:
        return 0
    raw = tokens.get(key, 0)
    try:
        return max(int(raw or 0), 0)
    except (TypeError, ValueError):
        return 0


def _nano(tokens: int, price_per_million: Decimal) -> int:
    if tokens <= 0:
        return 0
    value = Decimal(tokens) * price_per_million * _PER_MILLION_TO_NANO
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
