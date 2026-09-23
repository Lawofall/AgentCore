"""Cost & usage (团队工资单 + 账户仪表盘) schemas.

Money is integer nano (1 unit = 1e9) everywhere — never a float — denominated in
each breakdown's own ``currency``. Billed spend and BYOK display copy share the
curated CNY card; ``estimated_cost`` is the BYOK slice (not quota). **Nothing is
converted** (无 FX), so a client picks its symbol from ``currency``, never from
the field name.
Token fields use the ledger short keys (matching cost_events.tokens /
RunState.usage), distinct from message_end's legacy ``*_tokens`` SSE shape.
"""

from pydantic import BaseModel


class CostBreakdown(BaseModel):
    """A run's / turn's / window's cost in integer nano-money (canonical).

    Billed (``cost``) and BYOK (``estimated_cost``) stay two breakdowns so quota
    SUM never sees the display copy. Same CNY card; mixed turns add on the
    turn-total ``cost.total`` (bubble), not in the account window.
    """

    input: int
    cached: int
    output: int
    total: int
    currency: str = "CNY"
    # Major units = total / 1e9, in ``currency`` (元 for CNY, dollars for USD).
    # Legacy field name kept so clients don't break; it is not a CNY promise.
    cny_total: float
    # Which price layer produced these numbers (缺省 curated 兼容旧数据).
    pricing_source: str = "curated"


class UsageError(BaseModel):
    """Structured turn error persisted on the messages.usage JSON column.

    Pure-failure rows keep ``message.content`` empty and put the cause here (and on
    journal ``turn_end.error``). REST must project this so reload can paint a face
    even when the journal is sparse.
    """

    code: str
    message: str


class UsageBreakdown(BaseModel):
    """Token counts (cache_hit + cache_miss == input; reasoning ⊆ output).

    ``error`` is optional: present on failed / empty turns that stored a structured
    cause on the usage column. Token fields may be zeros when only ``error`` is set.
    ``last_prompt`` is the latest single-request prompt on this message/run
    (window fill). Omitted on window aggregates and old rows — not summed ``input``.
    """

    input: int
    output: int
    reasoning: int
    cache_hit: int
    cache_miss: int
    last_prompt: int | None = None
    error: UsageError | None = None


class AgentCostLine(BaseModel):
    """One participant's row in the team payroll (one Run = one Agent)."""

    run_id: str
    agent_id: str | None
    role: str
    model: str
    usage: UsageBreakdown
    cost: CostBreakdown
    estimated_cost: CostBreakdown | None = None
    duration_ms: int


class TurnCost(BaseModel):
    """A turn's cost + per-Agent payroll (``GET /messages/{id}/cost``).

    Rebuilt from the ``cost_events`` ledger by message_id, so it replays a past
    turn's payroll on reload. ``agents`` is empty when the turn has no ledger
    rows (e.g. unknown / non-owned message — never leaks existence).
    """

    message_id: str
    usage: UsageBreakdown
    cost: CostBreakdown
    estimated_cost: CostBreakdown | None = None
    rounds: int
    agents: list[AgentCostLine]


class ConversationCost(BaseModel):
    """A conversation's cumulative spend (``GET /conversations/{id}/cost``)."""

    conversation_id: str
    usage: UsageBreakdown
    cost: CostBreakdown
    estimated_cost: CostBreakdown | None = None
    turns: int


class UsageWindow(BaseModel):
    """Aggregated usage over a time window (today / month)."""

    usage: UsageBreakdown
    cost: CostBreakdown
    estimated_cost: CostBreakdown | None = None
    # Distinct assistant turns in the window (the quota's「请求」proxy).
    requests: int


class QuotaStatus(BaseModel):
    """Resolved quota limits (决策④ / F2); 0 = unlimited. Money is nano-CNY internally."""

    daily_tokens: int
    monthly_cost_nano: int
    # 单日成本 backstop (成本配额与计费 §〇·六 F2); 0 = unlimited.
    daily_cost_nano: int
    daily_requests: int


class ModelCostLine(BaseModel):
    """One model's call-level spend over a window — admin per-model payroll.

    Aggregated from ``cost_calls`` (``GROUP BY model``), never from
    ``cost_events.model`` (that column only records the run's first call; multi-model
    runs would mis-attribute). Money is integer nano-CNY; ``tokens_total`` is
    ``SUM(input + output + reasoning)``. Clients format ¥ as ``cost_total / 1e9``.
    """

    model: str
    # LLM call rows in the window (``cost_calls`` row count).
    calls: int
    tokens_total: int
    cost_total: int
    cost_estimated_total: int = 0


class DailyCost(BaseModel):
    """One UTC day's total spend — a point in the dashboard 7-day trend (§7.3D)."""

    # ISO date (YYYY-MM-DD) of the UTC calendar day.
    date: str
    cost_total: int


class UsageSummary(BaseModel):
    """Account dashboard payload (``GET /usage/summary``).

    Money fields are nano-CNY; ``CostBreakdown.cny_total`` is already yuan.
    API no longer ships an FX rate.
    """

    today: UsageWindow
    month: UsageWindow
    # Last 7 UTC days incl today, oldest-first, zero-filled — the trend sparkline.
    recent_daily_cost: list[DailyCost]
    quota: QuotaStatus
    # Billing mode (config.billing_mode). In "byok" the platform quota is dormant
    # (the turn runs on the user's own key), so the client reframes the quota meters
    # as「自带 Key 不限额」and presents cost as the user's own DeepSeek spend.
    billing_mode: str
