"""In-process cloud LLM call metering → shared :mod:`cost_ledger_queue`.

Only active when the ledger drain is running (API server lifespan). Sidecar
never starts the drain; its spend is recorded exclusively by the cloud
inference proxy. Call details are authoritative; per-run aggregates are
upserted from those details (``materialize_runs=True``, isomorphic with the
proxy path). Turn finalize still reconciles orphans (e.g. vision) and re-reads
the product view for ``cost.recorded`` / ``messages.cost``.

Pricing / attribution field assembly is shared with ``proxy_spend`` via
:mod:`agentcore.billing.ledger_call` — this module only decides *whether* to
enqueue and stamps ``source=inprocess_call``.
"""

from __future__ import annotations

from dataclasses import asdict

from agentcore.billing.ledger_call import assemble_ledger_call
from agentcore.core.log_context import get_log_value
from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import TokenUsage

logger = get_logger(__name__)

# Proxy-forwarded unary calls still emit ``llm.call`` (latency obs) via the
# ``observe_provider`` fence around ``build_provider``, but billing must be
# ``proxy_spend`` only — otherwise one physical upstream call lands two
# ``cost_calls`` rows.
PROXY_LLM_SCENARIO = "inference.proxy"

# Vision 读图: ``log_llm_call`` is observability-only; callers price a separate
# ``role=vision`` ``cost_runs`` row (different model tier). Metering here would
# mis-attribute tokens onto the parent run_id and double-bill when finalize also
# records the vision orphan.
_VISION_SCENARIO_PREFIX = "vision."


def maybe_enqueue_inprocess_call(
    *,
    model: str,
    usage: TokenUsage,
    duration_ms: int = 0,
    scenario: str | None = None,
    credential_source: str | None = None,
) -> str | None:
    """Enqueue one ``cost_calls`` row (+ materialize its run) when drain is live.

    ``user_id`` is the only envelope key required: an account-level chrome call
    (AI 改写, ``cost_role=assist``) belongs to no conversation
    and lands as a ``conversation_id = NULL`` row — real spend stays visible in
    the account windows instead of being dropped (成本配额与计费 §三).

    Skips when: drain not running (sidecar / unit tests), no bound ``user_id``
    (evals / 测连 probes have no account to charge), zero usage, ``scenario`` is
    the inference proxy marker (proxy already records via ``proxy_spend``), or a
    vision read (billed only via the turn ``cost_runs`` vision row).
    """
    if scenario == PROXY_LLM_SCENARIO:
        return None
    if scenario and scenario.startswith(_VISION_SCENARIO_PREFIX):
        return None

    from agentcore.billing.cost_ledger_queue import get_cost_ledger_queue

    queue = get_cost_ledger_queue()
    if not queue.running:
        return None

    user_id = get_log_value("user_id")
    if not user_id:
        return None
    conversation_id = get_log_value("conversation_id") or None

    call = assemble_ledger_call(
        model=model,
        usage=usage,
        role=get_log_value("cost_role") or None,
        run_id=get_log_value("run_id") or None,
        parent_run_id=get_log_value("parent_run_id") or None,
        agent_id=get_log_value("agent_id") or None,
        persona=get_log_value("persona") or None,
        duration_ms=duration_ms,
        credential_source=credential_source,
    )
    record_id = queue.enqueue_calls(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=get_log_value("message_id") or None,
        trace_id=get_log_value("trace_id") or None,
        calls=[asdict(call)],
        source="inprocess_call",
        materialize_runs=True,
    )
    if record_id is None:
        logger.warning(
            "cost.inprocess_call_enqueue_failed",
            conversation_id=conversation_id,
            run_id=call.run_id,
        )
    return record_id
