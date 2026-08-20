"""Shared mappers: a ledger aggregate dict → the cost/usage API schema.

Single source for turning a ``cost_events`` rollup (the repository's dict shape)
into the wire schema, shared by the per-user 用量 endpoints (``usage.py``) and the
admin 全站看板 (``admin.py``). The ledger is already priced (用量/成本不变量 #2:
``calculate_cost`` ran at write time), so this only *reads* the stored components
— it never re-prices and never converts currencies. ``cny_total`` is the major
unit of the breakdown's own ``currency`` (``nano / 1e9``); no FX.
"""

from agentcore.api.schemas import CostBreakdown, UsageBreakdown
from agentcore.llm.pricing import CURRENCY_CNY, nano_to_major, project_cache_miss_tokens


def cost_breakdown(cost: dict) -> CostBreakdown:
    """Map a billed ledger cost dict (integer nano components) to the API schema.

    Billed spend is CNY (curated cards), so an absent ``currency`` defaults there.
    """
    total = int(cost.get("total", 0))
    return CostBreakdown(
        input=int(cost.get("input", 0)),
        cached=int(cost.get("cached", 0)),
        output=int(cost.get("output", 0)),
        total=total,
        currency=str(cost.get("currency") or CURRENCY_CNY),
        cny_total=nano_to_major(total),
        pricing_source=str(cost.get("pricing_source") or "curated"),
    )


def estimated_cost_breakdown(
    *,
    estimated_nano: int = 0,
    cost: dict | None = None,
) -> CostBreakdown | None:
    """BYOK estimate breakdown, or ``None`` when there is nothing to show.

    These numbers come off the community snapshot, i.e. **USD** — the rollup that
    built ``cost`` stamps the currency, and it is carried through untouched.
    """
    body = cost or {}
    total = int(estimated_nano or body.get("total", 0) or 0)
    if total <= 0 and not any(int(body.get(k, 0) or 0) for k in ("input", "cached", "output")):
        return None
    return CostBreakdown(
        input=int(body.get("input", 0)),
        cached=int(body.get("cached", 0)),
        output=int(body.get("output", 0)),
        total=total,
        currency=str(body.get("currency") or CURRENCY_CNY),
        cny_total=nano_to_major(total),
        pricing_source=str(body.get("pricing_source") or "estimated"),
    )


def usage_breakdown(tokens: dict) -> UsageBreakdown:
    """Map a ledger token dict to the API schema (absent keys → 0).

    Omitted cache split (input>0 and hit/miss both 0) projects miss via the
    pricing guard so the client does not render「0 命中 / 无缓存」. Amounts are
    already priced at write time — this does not re-price.
    """
    input_tokens = int(tokens.get("input", 0))
    cache_hit = int(tokens.get("cache_hit", 0))
    cache_miss = int(tokens.get("cache_miss", 0))
    return UsageBreakdown(
        input=input_tokens,
        output=int(tokens.get("output", 0)),
        reasoning=int(tokens.get("reasoning", 0)),
        cache_hit=cache_hit,
        cache_miss=project_cache_miss_tokens(input_tokens, cache_hit, cache_miss),
    )
