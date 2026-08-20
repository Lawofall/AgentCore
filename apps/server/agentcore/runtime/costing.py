"""Per-call detail + per-run aggregate ledger shapes.

Value objects / ``run_cost_from_calls`` live in the leaf ``agentcore.costing``
(so ``db`` never imports this module). This file keeps RunState reshape builders
and re-exports the leaf symbols for the historical ``runtime.costing`` import path.

Money stays integer nano throughout, in the currency each row's price card was
written in (curated CNY / community USD — no FX); pricing happens exactly once via
:func:`agentcore.llm.pricing.calculate_cost`. This module only *reshapes*
priced states / usages into ledger rows — it never re-prices and never converts.
Ledger routing by ``credential_source`` (on the priced ``Cost`` / cost dict):
platform/vendor → ``cost_total_nano`` (quota / admin); user → ``cost_estimated_nano``
(``cost_total_nano`` stays 0 so BYOK estimates never pollute ``enforce_quota``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.costing import (
    COST_KEYS,
    PERSONA_CEO,
    PERSONA_DESCRIPTION,
    PERSONA_REWRITE,
    ROLE_ARENA,
    ROLE_ASSIST,
    ROLE_CAPTAIN,
    ROLE_MEMBER,
    ROLE_MEMORY,
    ROLE_TITLE,
    ROLE_VISION,
    USAGE_KEYS,
    CallCost,
    RunCost,
    run_cost_from_calls,
    split_cost,
)
from agentcore.llm.pricing import CURRENCY_CNY, CredentialSource, calculate_cost
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.citations import merge_citations
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState

logger = get_logger(__name__)

# Historical private aliases (call sites / tests may still reference these names).
_COST_KEYS = COST_KEYS
_USAGE_KEYS = USAGE_KEYS
_split_cost = split_cost

# Leaf re-exports kept for the historical ``from agentcore.runtime.costing import …`` path.
__all__ = [
    "COST_KEYS",
    "USAGE_KEYS",
    "PERSONA_CEO",
    "PERSONA_DESCRIPTION",
    "PERSONA_REWRITE",
    "ROLE_ARENA",
    "ROLE_ASSIST",
    "ROLE_CAPTAIN",
    "ROLE_MEMBER",
    "ROLE_MEMORY",
    "ROLE_TITLE",
    "ROLE_VISION",
    "CallCost",
    "RunCost",
    "WorkerResultAccumulator",
    "aggregate_cost",
    "aggregate_usage_tokens",
    "arena_run_cost",
    "captain_run_cost_from_state",
    "member_run_cost",
    "priced_call_cost",
    "resolve_run_models",
    "run_cost_from_calls",
    "split_cost",
    "usage_metadata",
    "vision_run_cost",
]


def usage_metadata(usage: Mapping[str, int]) -> dict[str, int]:
    """The ``metadata`` token block a non-terminal orchestration tool returns.
    Re-keys the short-key usage form ({input, ...}) to the engine's ``*_tokens``
    names ({input_tokens, ...}). ``delegate`` (this call's worker usage) and
    ``revise`` (the revision's usage) both report through this single seam so the
    shape can never drift between them.
    """
    return {f"{key}_tokens": int(usage.get(key, 0)) for key in _USAGE_KEYS}


def aggregate_usage_tokens(cost_runs: Sequence[dict]) -> dict[str, int]:
    """Sum per-run ``tokens`` into the long-key block persisted on ``messages.usage``.

    Same source as ``cost_events.tokens``. Interrupt close stamps this onto the
    assistant row so the bubble matches the ledger for the same ``message_id``.
    """
    totals: dict[str, int] = {key: 0 for key in _USAGE_KEYS}
    for row in cost_runs:
        tokens = row.get("tokens") or {}
        for key in _USAGE_KEYS:
            totals[key] += int(tokens.get(key, 0) or 0)
    return usage_metadata(totals)


def member_run_cost(
    spec: RunSpec,
    state: RunState,
    *,
    parent_run_id: str | None,
    role: str = ROLE_MEMBER,
) -> RunCost:
    """A child-run ledger row, read off its terminal :class:`RunState`.

    The executor already priced this run onto ``state.cost``; this only reshapes
    it into a ledger row (no re-pricing). ``parent_run_id`` is the delegating
    captain / moderator run id. ``persona`` is the human-facing role label from
    the plan (调研员 / 主持人 / …). Default ``role`` is ``member`` (组队
    ``delegate``); debate callers pass :data:`ROLE_ARENA`.
    """
    body, billed, estimated, currency = _split_cost(state.cost)
    persona = (spec.role or "").strip() or None
    return RunCost(
        run_id=spec.run_id,
        parent_run_id=parent_run_id,
        agent_id=spec.agent_id or spec.run_id,
        role=role,
        persona=persona,
        model=state.model,
        tokens=dict(state.usage),
        cost=body,
        cost_total_nano=billed,
        cost_estimated_nano=estimated,
        currency=currency,
        rounds=state.rounds,
        duration_ms=state.duration_ms,
    )


def arena_run_cost(spec: RunSpec, state: RunState, *, parent_run_id: str | None) -> RunCost:
    """Debate-path ledger row (``role=arena``) — same reshape as :func:`member_run_cost`."""
    return member_run_cost(spec, state, parent_run_id=parent_run_id, role=ROLE_ARENA)


def _priced_model_from_route(route_or_model: str) -> str:
    """Strip router prefixes (``platform/`` or BYOK ``provider_id/``) for ledger lookup.

    Vendor-prefixed ids (``doubao/...``) stay intact — they are curated pricing keys.
    """
    raw = (route_or_model or "").strip()
    if "/" not in raw:
        return raw
    prefix, _, rest = raw.partition("/")
    if not rest:
        return raw
    from agentcore.llm.pricing import _VENDOR_PREFIXES
    from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL

    if prefix == PLATFORM_PROVIDER_SENTINEL:
        return rest
    if prefix in _VENDOR_PREFIXES:
        return raw
    return rest


def resolve_run_models(
    profiles: Any,
    spec_model: str,
    *,
    cost_role: str,
) -> tuple[str, str]:
    """``(priced_model, request_model)`` for an agent / continuation run.

    ``arena`` (辩论) falls back to the turn **main** model — never Worker
    ``model_for("agent")``. Explicit ``spec.model`` (injected main, or Phase 3
    per-side route key) still wins for the request; pricing strips router prefixes.
    """
    if cost_role == ROLE_ARENA:
        request = spec_model or profiles.model
        return _priced_model_from_route(request), request
    if spec_model:
        return _priced_model_from_route(spec_model), spec_model
    priced = profiles.model_for("agent")
    request = profiles.route_model_for("agent")
    return priced, request


def captain_run_cost_from_state(run_id: str, state: RunState) -> RunCost:
    """The CEO root run's ledger row, read off its terminal :class:`RunState`.
    The captain is now a real Run node executed through the run executor (it owns
    the turn's reply and may ``delegate``), so its cost is priced exactly once —
    onto ``state.cost`` by the executor — and this only reshapes it into the
    captain ledger row (role=captain, no parent: it is the turn's root). The
    delegated workers get their own member rows via :func:`member_run_cost`.
    """
    body, billed, estimated, currency = _split_cost(state.cost)
    return RunCost(
        run_id=run_id,
        parent_run_id=None,
        agent_id=None,
        role=ROLE_CAPTAIN,
        persona=PERSONA_CEO,
        model=state.model,
        tokens=dict(state.usage),
        cost=body,
        cost_total_nano=billed,
        cost_estimated_nano=estimated,
        currency=currency,
        rounds=state.rounds,
        duration_ms=state.duration_ms,
    )


def priced_call_cost(
    *,
    model: str,
    usage: TokenUsage,
    role: str,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    agent_id: str | None = None,
    persona: str | None = None,
    call_id: str | None = None,
    duration_ms: int = 0,
    credential_source: CredentialSource | None = None,
    platform_credential_id: str | None = None,
) -> CallCost:
    """Price one LLM call into a ``cost_calls`` detail row (不变量 #2).
    Used by the inference proxy (sidecar path) and in-process cloud metering.
    ``call_id`` is the idempotency key; when omitted a fresh id is minted.
    ``run_id`` defaults to a fresh id when the caller has no run tree (title /
    memory / unattributed proxy call) — each such call is its own run aggregate.
    ``platform_credential_id`` is the platform-pool member (logs + ledger). When
    omitted, ambient log context is used — only stamped when the priced source
    is ``platform`` (BYOK stays NULL even if a prior extra left ambient state).
    """
    body, billed, estimated, currency = _split_cost(
        asdict(calculate_cost(model, usage, credential_source=credential_source))
    )
    rid = run_id or new_id()
    cid = (platform_credential_id or "").strip() or None
    if cid is None and str(body.get("credential_source") or "") == "platform":
        from agentcore.core.log_context import get_log_value

        cid = get_log_value("platform_credential_id") or None
    elif str(body.get("credential_source") or "") != "platform":
        cid = None
    return CallCost(
        call_id=call_id or f"call_{new_id()}",
        run_id=rid,
        parent_run_id=parent_run_id,
        agent_id=agent_id,
        role=role,
        persona=(persona or "").strip() or None,
        model=model,
        tokens=usage.as_dict(),
        cost=body,
        cost_total_nano=billed,
        cost_estimated_nano=estimated,
        currency=currency,
        duration_ms=duration_ms,
        platform_credential_id=cid,
    )


def vision_run_cost(
    model: str,
    usage: TokenUsage,
    *,
    parent_run_id: str | None,
    duration_ms: int = 0,
    credential_source: CredentialSource | None = None,
) -> RunCost:
    """A ledger row for a ``board_read`` vision sub-call (AI协作白板.md §九.4 Gap ②).
    A tool-layer sub-call to a SEPARATE vision model (qwen-vl ≠ the run's DeepSeek), so it
    cannot fold into the run's usage — that would misprice it at the run's tier. Priced
    here exactly once via the one ``calculate_cost`` (不变量 #2) under the dedicated
    ``vision`` role, then routed into the turn's ``cost_runs`` via ``ToolContext.cost_sink``
    so it lands on the turn's ``message_id`` (in-turn spend, unlike an off-turn background
    call whose ``message_id`` stays NULL). ``parent_run_id`` is the calling captain's run
    id, so the spend nests under the captain in the turn's run tree; ``rounds`` is 1 (one
    vision call). A unique ``vis_`` run id keeps the ledger's idempotent upsert-by-run_id
    honest.
    """
    body, billed, estimated, currency = _split_cost(
        asdict(calculate_cost(model, usage, credential_source=credential_source))
    )
    return RunCost(
        run_id=f"vis_{new_id()}",
        parent_run_id=parent_run_id,
        agent_id=None,
        role=ROLE_VISION,
        persona=None,
        model=model,
        tokens=usage.as_dict(),
        cost=body,
        cost_total_nano=billed,
        cost_estimated_nano=estimated,
        currency=currency,
        rounds=1,
        duration_ms=duration_ms,
    )


def _row_currency(row: Mapping[str, Any]) -> str:
    """A ledger row's currency — scalar column first, then the JSONB body."""
    cost = row.get("cost") or {}
    return str(row.get("currency") or cost.get("currency") or CURRENCY_CNY)


def aggregate_cost(cost_runs: Sequence[dict]) -> dict[str, int | str]:
    """Sum per-run cost rows into the turn total carried on ``message_end.cost``.

    Takes the ``asdict(RunCost)`` rows the pipeline builds (captain + members) and
    returns ``{input, cached, output, total, currency, estimated_total,
    estimated_currency, pricing_source}``. Never re-prices combined usage.

    Two money buckets, each self-consistent — mirroring the SQL rollup in
    ``db.repositories.billing._aggregate`` so the live turn and the replayed
    ledger agree:

    - **billed**: ``input``/``cached``/``output`` come only from rows that
      actually billed, so ``input + output == total`` holds. Folding every row's
      components in (as this used to) made a pure-BYOK turn report non-zero
      components against ``total == 0``, and — once BYOK estimates became USD —
      would have added dollars to yuan.
    - **estimated**: ``estimated_total`` is the BYOK SUM, labelled by
      ``estimated_currency``. A consumer picking this number must read that
      currency, not ``currency`` (which labels the billed side).

    Per-agent components stay available in full on ``GET /messages/{id}/cost``,
    which reads the ledger rows themselves.
    """
    agg: dict[str, int | str] = {
        "input": 0,
        "cached": 0,
        "output": 0,
        "total": 0,
        "estimated_total": 0,
        "currency": CURRENCY_CNY,
        "estimated_currency": CURRENCY_CNY,
        "pricing_source": "curated",
    }
    sources: set[str] = set()
    billed_currencies: list[str] = []
    estimated_currencies: list[str] = []
    for row in cost_runs:
        cost = row.get("cost") or {}
        billed = int(row.get("cost_total_nano", 0) or 0)
        estimated = int(row.get("cost_estimated_nano", 0) or 0)
        currency = _row_currency(row)
        if billed:
            agg["input"] = int(agg["input"]) + int(cost.get("input", 0))
            agg["cached"] = int(agg["cached"]) + int(cost.get("cached", 0))
            agg["output"] = int(agg["output"]) + int(cost.get("output", 0))
            agg["total"] = int(agg["total"]) + billed
            billed_currencies.append(currency)
        if estimated:
            agg["estimated_total"] = int(agg["estimated_total"]) + estimated
            estimated_currencies.append(currency)
        if cost.get("pricing_source"):
            sources.add(str(cost["pricing_source"]))
    agg["currency"] = _bucket_currency(billed_currencies, bucket="billed")
    agg["estimated_currency"] = _bucket_currency(estimated_currencies, bucket="estimated")
    if len(sources) == 1:
        agg["pricing_source"] = next(iter(sources))
    elif sources:
        agg["pricing_source"] = "estimated"
    return agg


def _bucket_currency(currencies: Sequence[str], *, bucket: str) -> str:
    """The single currency a money bucket is denominated in.

    Empty bucket → ``CNY`` (a zero needs a unit, and the billed ledger is CNY).
    Two currencies in one bucket cannot be summed without FX, which this product
    does not do — that only happens when a platform model ships without its
    curated CNY card (F4 漏配), so log it loudly and keep the first.
    """
    if not currencies:
        return CURRENCY_CNY
    first = currencies[0]
    distinct = set(currencies)
    if len(distinct) > 1:
        logger.warning(
            "cost.currency_mixed",
            bucket=bucket,
            currencies=sorted(distinct),
            kept=first,
        )
    return first


class WorkerResultAccumulator:
    """The shared「用量 + 账目 + 引用」roll-up for orchestration tools.
    ``delegate`` (cold workers) and ``revise`` (a recalled author) both spin up
    member runs whose results must fold back into the turn totals the pipeline
    reads: token ``usage`` (summed, cache split kept), a per-run cost ``run_ledger``
    (one row per metered run, 决策②), and the workers' ``citations`` (de-duped into
    the turn's shared source card). Both tools used to hand-roll these three
    identical pieces; they now share this accumulator so the fold logic lives once.
    All three collections are mutated in place — a tool exposes them read-only and
    the pipeline reads ``usage`` / ``run_ledger`` / ``citations`` after the loop.
    """

    def __init__(self) -> None:
        self.usage: dict[str, int] = {key: 0 for key in _USAGE_KEYS}
        self.run_ledger: list[RunCost] = []
        self.citations: list[dict[str, Any]] = []
        # 协作质量 tally (学·度量, docs/05-平台与运维/管理员后台.md §四): per-turn orchestration
        # signals rolled up the SAME parent/child path as usage (merge() below), so a nested
        # lead's sub-team folds in for free. ``boundary_yields`` = 受监督边界让出次数 (首计划存活:
        # a supervised bind/scope boundary handed control back to the captain mid-plan);
        # ``scope_signals`` = escalate kind=scope count (漂移); ``escalations`` = total
        # worker→captain escalations. The revise count (返工 的另一半) is read off the revise
        # tool's run_ledger, not here.
        # ``*_by_user`` 是同一批事件里「用户亲手促成的那部分」的**子集**计数，不是新指标：
        # 运营口径 (turn_metrics / admin / decision_spine) 照旧读总数，用户面的「队友互相
        # 把关」减掉它，才不会把用户自己点的操作说成队友互检。
        self.collab: dict[str, int] = {
            "boundary_yields": 0,
            "boundary_yields_by_user": 0,
            "scope_signals": 0,
            "escalations": 0,
        }
        # 续派次数 (turn_metrics.revises) 的承载处，与 collab 同口径：它必须活在【会被
        # merge 的对象】上。挂在 delegate 工具实例上时，``absorb_children`` 合并完账目就
        # ``_children.clear()``，lead 子团队的续派随子工具一起消失，revises 系统性少计。
        self.continuations: list[str] = []
        # 上面那批里由用户「立即改此人」促成的子集（redirect 热修）；队友续派不入。
        self.user_continuations: list[str] = []

    def add_usage(self, usage: Mapping[str, int]) -> None:
        """Fold one run's (or sub-team's) short-key token usage into the total."""
        for key in self.usage:
            self.usage[key] += usage.get(key, 0)

    def add_run_cost(
        self,
        spec: RunSpec,
        state: RunState,
        *,
        parent_run_id: str | None,
        role: str = ROLE_MEMBER,
    ) -> None:
        """Append a ledger row for a run that metered LLM usage.
        Runs that never hit the LLM (skipped / failed before any call) carry no
        usage and are not billed, mirroring the old delegate/revise guard.
        Debate callers pass ``role=ROLE_ARENA``;组队 ``delegate`` keeps default member.
        """
        if state.usage:
            self.run_ledger.append(
                member_run_cost(spec, state, parent_run_id=parent_run_id, role=role)
            )

    def add_citations(self, state: RunState) -> None:
        """Merge a COMPLETED run's web sources into the shared card (de-duped/capped).
        Only COMPLETED runs contribute — a hard-failed worker's output is discarded
        by the captain, so its sources must not back the answer.
        """
        if state.phase is RunPhase.COMPLETED and state.citations:
            merge_citations(self.citations, state.citations)

    def add_run(
        self,
        spec: RunSpec,
        state: RunState,
        *,
        parent_run_id: str | None,
        role: str = ROLE_MEMBER,
    ) -> None:
        """Fold one finished child run end-to-end: usage + ledger row + citations.
        The convenience the ``revise`` path uses (one run per call). ``delegate``
        folds a batch through the granular adders so it can also stage this call's
        usage for the result metadata. Debate passes ``role=ROLE_ARENA``.
        """
        self.add_usage(state.usage)
        self.add_run_cost(spec, state, parent_run_id=parent_run_id, role=role)
        self.add_citations(state)

    def merge(self, other: WorkerResultAccumulator) -> None:
        """Fold another accumulator into this one (a nested sub-team's roll-up).
        Used by ``delegate.nesting.absorb_children`` to roll a re-delegating worker's
        sub-team usage + ledger + sources up into this captain's totals.
        """
        self.add_usage(other.usage)
        self.run_ledger.extend(other.run_ledger)
        merge_citations(self.citations, other.citations)
        self.continuations.extend(other.continuations)
        self.user_continuations.extend(other.user_continuations)
        for key in self.collab:
            self.collab[key] += other.collab.get(key, 0)
