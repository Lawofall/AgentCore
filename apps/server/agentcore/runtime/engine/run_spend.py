"""Push a run's cumulative spend after each model call returns.

The amount is the sum of per-call prices, not ``calculate_cost`` on the summed
tokens: Flash peak/off-peak follows the clock of each call. The frame is
ephemeral; the terminal run frame is what reload reads.
"""

from __future__ import annotations

from dataclasses import asdict

from agentcore.core.logging import get_logger
from agentcore.llm.pricing import CURRENCY_CNY, Cost, calculate_cost
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.events.run import run_spend
from agentcore.runtime.events.sink import EventSink

logger = get_logger(__name__)


def _add_cost(left: Cost, right: Cost) -> Cost:
    if left.currency != right.currency:
        logger.warning(
            "cost.currency_mixed",
            bucket="run_spend",
            currencies=sorted({left.currency, right.currency}),
            kept=left.currency,
        )
        return left
    source = left.pricing_source if left.pricing_source == right.pricing_source else "curated"
    return Cost(
        input=left.input + right.input,
        cached=left.cached + right.cached,
        output=left.output + right.output,
        total=left.total + right.total,
        currency=left.currency or CURRENCY_CNY,
        pricing_source=source,
        credential_source=left.credential_source,
    )


def note_run_spend(
    sink: EventSink,
    *,
    run_id: str,
    agent_id: str,
    role: str,
    model: str,
    call_usage: TokenUsage,
    total_usage: TokenUsage,
    spent: Cost | None,
) -> Cost | None:
    """Price this call, add it to ``spent``, and emit the cumulative frame.

    No-op without a ``run_id`` (narrow internal loops). Returns the new total
    so the caller can keep it across rounds.
    """
    if not run_id:
        return spent
    if not (call_usage.input_tokens or call_usage.output_tokens):
        return spent
    call_cost = calculate_cost(model, call_usage)
    spent = call_cost if spent is None else _add_cost(spent, call_cost)
    sink.emit(
        run_spend(
            run_id,
            agent_id,
            role=role,
            model=model,
            usage=total_usage.as_dict(),
            cost=asdict(spent),
        )
    )
    return spent
