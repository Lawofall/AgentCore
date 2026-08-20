"""OpenCode Go **upstream** public list prices — ops estimate only.

This is **not** the curated nominal card in ``llm/pricing.py`` and **not** the
community BYOK snapshot. Those two answer「向用户收多少」; this table answers
「按公开单价，向上游大概付多少」so an operator can see distance to Go's
$12 / $30 / $60 windows.

Never imported by ``calculate_cost`` / quota / user-facing money. Read-time
only — tokens already sit on ``cost_calls``, no extra column.

Prices: OpenCode public list for ``deepseek-v4-flash``, captured 2026-08-18,
USD per 1M tokens. Cached Write is 「-」 for this model — no such tier.
Update the numbers **and** ``PRICE_AS_OF`` together when upstream retags.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from agentcore.llm.pricing import reconcile_cache_miss_tokens
from agentcore.llm.provider.protocol import TokenUsage

MODEL_ID = "deepseek-v4-flash"
PRICE_AS_OF = date(2026, 8, 18)
CURRENCY_USD = "USD"

# USD / 1M tokens. Peak is exactly 2× Off-Peak on every published tier.
_OFF_PEAK: dict[str, Decimal] = {
    "input": Decimal("0.22"),
    "output": Decimal("0.66"),
    "cache_hit": Decimal("0.007"),
}
_PEAK: dict[str, Decimal] = {
    "input": Decimal("0.44"),
    "output": Decimal("1.32"),
    "cache_hit": Decimal("0.014"),
}

# tokens × (USD / 1M) → nano-USD. Same scale as billing nano (1 unit = 1e9).
_PER_MILLION_TO_NANO = Decimal(1000)

# Peak = UTC [01:00, 04:00) ∪ [06:00, 10:00). Half-open so 04:00 and 10:00
# are Off-Peak (no double-count at the published boundaries).
_PEAK_HOURS = frozenset({1, 2, 3, 6, 7, 8, 9})


def is_opencode_go_peak(ts: datetime) -> bool:
    """Whether ``ts`` falls in an OpenCode Go Peak hour (UTC)."""
    hour = _as_utc(ts).hour
    return hour in _PEAK_HOURS


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

    ``model`` must be this table's id (``MODEL_ID``). Other catalog ids
    (``glm-5.2``, ``deepseek-v4-flash-free``, …) return 0 — do not apply the
    Flash card to a different model.

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

    Two uncertainties the admin card must keep visible (not encoded in the
    number): Go may apply an unpublished ``costMultiplier`` (default 1) before
    the window; and we have not packet-verified that the gateway forwards
    DeepSeek cache-hit fields. If it does not, Go prices those tokens as Input
    while we price recorded hits as Cached Read — this estimate undershoots.
    """
    if (model or "").strip() != MODEL_ID:
        return 0
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


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)
