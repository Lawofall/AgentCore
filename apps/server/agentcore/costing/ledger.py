"""Per-call / per-run ledger shapes and call→run materialization (stdlib-only)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

# Structural role categories (mirror the DB CheckConstraint). A turn's run tree
# produces captain + member; ``title`` / ``memory`` tag the off-turn background
# LLM calls (标题生成 / 记忆整合) so their spend rolls into account/conversation
# totals without polluting the per-message team payroll. ``arena`` tags debate
# runs (主持人 / 辩手 / 取证员 / 证人续写) so spend shows as「辩论」and sidecar
# proxy keeps the turn main model (never Worker).
ROLE_CAPTAIN = "captain"
ROLE_MEMBER = "member"
ROLE_ARENA = "arena"
ROLE_TITLE = "title"
ROLE_MEMORY = "memory"
# ``vision`` tags a historical 读图 sub-call (retired eye). Native images ride
# the current main model and do not mint this role.
# tool-layer call to a SEPARATE vision model (qwen-vl ≠ the run's chat model). It is
# NOT a Run/Agent — it gets its own priced ledger row (one model = one row,
# 同跨档不复价) so its spend shows as its own line on the turn team payroll
# (``GET /messages/{id}/cost``).
ROLE_VISION = "vision"
# ``assist`` tags an **account-level** product-chrome call — AI 改写（划词改写）:
# real spend that belongs to no conversation at all, so its ledger row carries
# ``conversation_id = NULL`` (and ``message_id = NULL``) and only SUMs into the
# account windows / 配额. ``persona`` carries which chrome it was.
ROLE_ASSIST = "assist"
PERSONA_CEO = "CEO"
# Human-facing ``persona`` label for the account-level chrome path.
PERSONA_REWRITE = "AI 改写"
# The four money keys carried in cost_events.cost (integer nano-CNY). The Cost
# dataclass also exposes ``currency`` / ``pricing_source`` / ``credential_source``.
COST_KEYS = ("input", "cached", "output", "total")
# The five short-key token counts carried on RunState.usage / a tool's accumulated
# usage (cache_hit/cache_miss split kept so the folded total stays priceable).
USAGE_KEYS = ("input", "output", "reasoning", "cache_hit", "cache_miss")


@dataclass(frozen=True)
class RunCost:
    """One per-run ledger row (``cost_events`` materialized view).

    The user / conversation / message envelope is attached at persistence time by
    the conversation service (which owns the DB session), so this stays a pure
    value object the runtime can build without any DB awareness.
    """

    run_id: str
    parent_run_id: str | None
    agent_id: str | None
    role: str
    model: str
    tokens: dict[str, int]
    cost: dict[str, int | str]
    cost_total_nano: int
    currency: str
    rounds: int
    duration_ms: int
    persona: str | None = None
    cost_estimated_nano: int = 0


@dataclass(frozen=True)
class CallCost:
    """One per-call detail row (``cost_calls`` — billing authority)."""

    call_id: str
    run_id: str
    parent_run_id: str | None
    agent_id: str | None
    role: str
    model: str
    tokens: dict[str, int]
    cost: dict[str, int | str]
    cost_total_nano: int
    currency: str
    duration_ms: int
    persona: str | None = None
    cost_estimated_nano: int = 0
    # Platform-pool member that funded this call. None on BYOK / vendor / legacy.
    platform_credential_id: str | None = None


def split_cost(cost: dict) -> tuple[dict[str, int | str], int, int, str]:
    """Normalise a cost dict into (JSONB body, billed nano, estimated nano, currency).

    Accepts the ``asdict(Cost)`` shape. User-sourced money always lands in
    ``cost_estimated_nano`` with ``cost_total_nano == 0``; platform/vendor keep
    billed ``cost_total_nano``.

    ``currency`` comes off the priced ``Cost`` (curated CNY) and
    rides the row's scalar column — read it from there, not from the body.
    """
    body: dict[str, int | str] = {key: int(cost.get(key, 0)) for key in COST_KEYS}
    pricing_source = str(cost.get("pricing_source") or "curated")
    credential_source = str(cost.get("credential_source") or "platform")
    body["pricing_source"] = pricing_source
    body["credential_source"] = credential_source
    total = int(body["total"])
    currency = str(cost.get("currency", "CNY"))
    if credential_source == "user":
        return body, 0, total, currency
    return body, total, 0, currency


def run_cost_from_calls(calls: Sequence[CallCost | Mapping[str, Any]]) -> RunCost | None:
    """Materialize one per-run aggregate from a batch of call details.

    Sums tokens / cost / duration; ``rounds`` = call count. Attribution
    (role / persona / agent / parent) is taken from the first call. Returns
    ``None`` when ``calls`` is empty.
    """
    if not calls:
        return None
    first = calls[0]
    if isinstance(first, CallCost):
        first_map: Mapping[str, Any] = asdict(first)
    else:
        first_map = first
    tokens = {key: 0 for key in USAGE_KEYS}
    cost_body: dict[str, int | str] = {key: 0 for key in COST_KEYS}
    billed = 0
    estimated = 0
    duration = 0
    pricing_source = "curated"
    credential_source = "platform"
    for raw in calls:
        row = asdict(raw) if isinstance(raw, CallCost) else raw
        for key in USAGE_KEYS:
            tokens[key] += int((row.get("tokens") or {}).get(key, 0) or 0)
        c = row.get("cost") or {}
        for key in ("input", "cached", "output"):
            cost_body[key] = int(cost_body[key]) + int(c.get(key, 0) or 0)
        billed += int(row.get("cost_total_nano", 0) or 0)
        estimated += int(row.get("cost_estimated_nano", 0) or 0)
        duration += int(row.get("duration_ms", 0) or 0)
        if c.get("pricing_source"):
            pricing_source = str(c["pricing_source"])
        if c.get("credential_source"):
            credential_source = str(c["credential_source"])
    cost_body["total"] = billed + estimated
    cost_body["pricing_source"] = pricing_source
    cost_body["credential_source"] = credential_source
    return RunCost(
        run_id=str(first_map["run_id"]),
        parent_run_id=first_map.get("parent_run_id"),
        agent_id=first_map.get("agent_id"),
        role=str(first_map.get("role") or ROLE_MEMBER),
        persona=(str(first_map["persona"]).strip() if first_map.get("persona") else None),
        model=str(first_map.get("model") or ""),
        tokens=tokens,
        cost=cost_body,
        cost_total_nano=billed,
        cost_estimated_nano=estimated,
        currency=str(first_map.get("currency") or "CNY"),
        rounds=len(calls),
        duration_ms=duration,
    )
