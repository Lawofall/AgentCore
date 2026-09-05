"""Admin console schemas: user management, usage/observability dashboards, replay.

The cross-user counterparts of the per-user usage schemas, plus user management
and 会话复盘. All admin-gated (管理员后台.md); reuses the per-user usage schemas.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .auth import SessionSummary
from .messages import AgentMention, RunsPayload, StoredAttachment
from .usage import (
    CostBreakdown,
    DailyCost,
    ModelCostLine,
    QuotaStatus,
    UsageWindow,
)

# --- Admin (用户管理: 平台管理员后台, admin-only) ---


class AdminUserResponse(BaseModel):
    """A platform account as seen by the admin console (full record + quota state).

    Richer than ``UserResponse`` (the self-view): adds ``status`` and the per-user
    quota overrides so the operator can manage accounts. Each quota override is
    nullable — NULL = inherit the global config threshold for that dimension
    (成本配额与计费.md §一, 决策④); a value (incl. 0 = unlimited) overrides it.
    ``registration_ip`` is the client IP captured at signup (加强可查; NULL for
    pre-column / seeded rows).
    """

    id: str
    username: str
    display_name: str
    email: str | None
    email_verified_at: datetime | None = None
    role: Literal["user", "admin"]
    status: Literal["active", "disabled"]
    is_unlimited: bool
    quota_daily_tokens: int | None
    quota_monthly_cost_cny: float | None
    quota_daily_cost_cny: float | None
    quota_daily_requests: int | None
    created_at: datetime
    # Client IP at registration (加强可查). NULL for pre-column / seeded rows.
    registration_ip: str | None = None
    # NULL for a live account; a timestamp marks a 注销 (self-service deleted +
    # anonymized) account. The roster hides these by default and renders them as
    # 「已注销」when surfaced — they're tombstones, not manageable accounts.
    deleted_at: datetime | None


class AdminUserListItem(AdminUserResponse):
    """A roster row: the account record + its all-time cumulative spend.

    Extends the account view with ``cost_total`` (all-time, integer nano-CNY) so the
    用户管理 roster can both **sort by** and **display** per-user lifetime cost without a
    second round-trip. Clients format ¥ as ``cost_total / 1e9``.
    """

    cost_total: int


class AdminUserListResponse(BaseModel):
    data: list[AdminUserListItem]
    total: int
    page: int
    page_size: int


class AdminUpdateUserRequest(BaseModel):
    """Partial update of a user's role / status / quota (admin console).

    Tri-state semantics key off which fields are *present* in the request body
    (Pydantic ``model_fields_set``), not their value:
    - field absent        → leave unchanged
    - quota field = null  → clear the override (inherit the global config)
    - quota field = value → set the override (0 = unlimited for that dimension)

    ``is_unlimited`` short-circuits all quota dimensions for trusted accounts.
    Sending ``role``/``status`` that targets the caller's own account in a way that
    would revoke their own admin access is refused at the service layer (no
    self-lockout — the platform always keeps ≥1 active admin).
    """

    role: Literal["user", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None
    is_unlimited: bool | None = None
    quota_daily_tokens: int | None = Field(None, ge=0)
    quota_monthly_cost_cny: float | None = Field(None, ge=0)
    quota_daily_cost_cny: float | None = Field(None, ge=0)
    quota_daily_requests: int | None = Field(None, ge=0)


class AdminResetPasswordResponse(BaseModel):
    """The one-off password minted by an admin reset (重置密码), returned exactly once.

    Plaintext is never persisted — only its hash. The admin hands this to the user,
    whose existing sessions are already revoked, so they must log in with it next.
    """

    temporary_password: str


class AdminSetPasswordRequest(BaseModel):
    """Admin-specified new password (设置密码) for a target account.

    Plaintext is never stored or echoed back — only its hash. Revokes the user's
    sessions on success (same as reset). ``force_change`` defaults true so the user
    must set their own password on next login unless the operator opts out.
    """

    new_password: str = Field(..., min_length=8)
    force_change: bool = True


# --- Admin: 全站用量看板 (P1) + 系统状态 (P2) ---
# The cross-user counterparts of the per-user usage schemas above, plus a
# read-only deployment-status snapshot. Both endpoints are admin-gated
# (管理员后台.md); they reuse UsageWindow / DailyCost / QuotaStatus defined above.


class AdminUserCostLine(BaseModel):
    """One account's spend over a window — the platform 工资单 by user (全站看板).

    Money is integer nano-CNY; clients format ¥ as ``cost_total / 1e9``.
    """

    user_id: str
    username: str
    display_name: str
    cost_total: int
    # Distinct assistant turns this account ran over the window.
    turns: int


class AdminUsageSummary(BaseModel):
    """Platform-wide usage dashboard (``GET /v1/admin/usage/summary``, admin-only).

    The cross-user counterpart of ``UsageSummary``: today's / this month's totals
    aggregated over *every* account, the Top spenders by user (工资单 by user), and
    the 7-day platform trend. ``billing_mode`` is surfaced so the client frames cost
    honestly — in "byok" these totals are the sum of each user's spend on their
    *own* DeepSeek key (not platform-paid).
    """

    today: UsageWindow
    month: UsageWindow
    # This month's spend split by user (工资单 by user), spend-desc, >0 only, capped.
    month_by_user: list[AdminUserCostLine]
    # This month's spend split by model across *every* account (from ``cost_calls``,
    # never ``cost_events.model``), spend-desc.
    month_by_model: list[ModelCostLine]
    # Last 7 UTC days incl today, oldest-first, zero-filled — the platform trend.
    recent_daily_cost: list[DailyCost]
    billing_mode: str


class AdminGoWindow(BaseModel):
    """One OpenCode Go-style window, summed from our platform-prepaid ledger.

    ``cost_total_nano`` is curated **nominal** nano-CNY. ``estimated_usd_nano``
    is a read-time public-list USD estimate (nano-USD) — not an upstream bill
    or balance. Empty / idle-reset 5h windows have ``started_at`` null; weekly
    and monthly always have both bounds.
    """

    cost_total_nano: int
    estimated_usd_nano: int
    calls: int
    started_at: datetime | None
    reset_at: datetime | None


class AdminGoCredentialWindows(BaseModel):
    """One pool member's Go windows (that account's own subscription day)."""

    platform_credential_id: str
    label: str
    enabled: bool
    subscription_day: int
    five_hour: AdminGoWindow
    weekly: AdminGoWindow
    monthly: AdminGoWindow


class AdminGoWindows(BaseModel):
    """``GET /v1/admin/usage/go-windows`` — calibration baseline, not a balance.

    Three windows follow Go's own reset rules (UTC week / subscription-day
    month / fixed 5h + idle zero). ``cost_basis`` labels the CNY column
    (curated nominal). ``estimate_*`` labels the separate public-list USD
    column — not an FX of the nominal, not an upstream bill.

    Top-level monthly uses ``PLATFORM_GO_SUBSCRIPTION_DAY`` (env-fallback /
    all-calls coarse total). When the pool has members, ``members`` repeats
    the three windows per account using that row's subscription day.
    """

    five_hour: AdminGoWindow
    weekly: AdminGoWindow
    monthly: AdminGoWindow
    # Echo of ``PLATFORM_GO_SUBSCRIPTION_DAY`` so the card can label the month.
    subscription_day: int
    cost_basis: Literal["nominal_nano_cny"]
    estimate_basis: Literal["opencode_public_list"]
    estimate_currency: Literal["USD"]
    estimate_price_as_of: date
    estimate_model: str
    as_of: datetime
    members: list[AdminGoCredentialWindows] = Field(default_factory=list)


class ToolSurfaceLimits(BaseModel):
    """Operator-declared upstream tool-surface caps on one pool member.

    Each field is independent. ``null`` / omitted = that dimension is unlimited.
    Values are filled by ops (subscription tiers differ); this schema does not
    encode any vendor's exact cap. Counting at assemble time is our OpenAI-format
    surface: tool count, and top-level ``function.parameters.properties`` keys.
    """

    model_config = {"extra": "forbid"}

    max_tools: int | None = Field(default=None, ge=0)
    max_properties_total: int | None = Field(default=None, ge=0)
    max_properties_per_tool: int | None = Field(default=None, ge=0)


class PlatformCredentialView(BaseModel):
    """Admin view of one platform-pool member — never the plaintext key.

    ``status`` / ``recovery_at`` / ``limit_name`` / ``source`` are live pool-state
    (Redis or process memory), not Postgres columns. Absence of a store record is
    healthy. ``picked`` is the fill-first member ``platform_llm_credentials`` would
    take right now. ``same_as_env`` is true when this row's ``(api_key, base_url)``
    matches ``PLATFORM_API_KEY`` / ``PLATFORM_BASE_URL``.
    ``tool_surface_limits`` is stored on the row; empty / all-null = unlimited.
    """

    id: str
    label: str
    base_url: str
    subscription_day: int = Field(ge=1, le=31)
    enabled: bool
    masked_key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    status: Literal["healthy", "cooling", "exhausted", "blocked"] = "healthy"
    recovery_at: datetime | None = None
    limit_name: str | None = None
    source: str | None = None
    same_as_env: bool = False
    picked: bool = False
    tool_surface_limits: ToolSurfaceLimits = Field(default_factory=ToolSurfaceLimits)


class PlatformCredentialListResponse(BaseModel):
    """``GET /v1/admin/platform-credentials``.

    ``fallback`` tells ops which key path ``platform_llm_credentials`` will take:
    ``pool`` = at least one enabled member; ``env`` = empty/disabled pool falls
    back to ``PLATFORM_API_KEY``; ``none`` = no usable platform key at all.
    """

    data: list[PlatformCredentialView]
    fallback: Literal["pool", "env", "none"]


class CreatePlatformCredentialRequest(BaseModel):
    """Add one platform-pool member. ``base_url`` is required and bound to this key."""

    label: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(
        ...,
        max_length=400,
        description="Plaintext API key (AES-256-GCM at rest; never returned).",
    )
    base_url: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="OpenAI-compatible endpoint bound to this key (e.g. Go /zen/go/v1).",
        examples=["https://opencode.ai/zen/go/v1"],
    )
    subscription_day: int = Field(..., ge=1, le=31)
    enabled: bool = True
    tool_surface_limits: ToolSurfaceLimits | None = None


class UpdatePlatformCredentialRequest(BaseModel):
    """Partial update. Omitted ``api_key`` keeps the stored ciphertext."""

    label: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = Field(default=None, max_length=400)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    subscription_day: int | None = Field(default=None, ge=1, le=31)
    enabled: bool | None = None
    tool_surface_limits: ToolSurfaceLimits | None = None


class AdminSystemStatus(BaseModel):
    """Read-only platform status for the admin console (``GET /v1/admin/system``).

    A deployment sanity-check at a glance (管理员后台 P2): the billing mode + global
    quota defaults (deploy-time ``config``, not editable here), database reachability,
    build provenance, and account tallies. Everything is read-only — config changes
    go through env + redeploy, not the console. API no longer ships an FX rate.
    """

    billing_mode: str
    # Global quota defaults (config); per-user overrides live on the user record.
    quota: QuotaStatus
    # A live ``SELECT 1`` round-trip succeeded within the probe timeout.
    database_ok: bool
    version: str
    git_sha: str
    built_at: str
    users_total: int
    users_active: int
    admins: int


# --- Admin: 操作审计 (audit trail) ---


class AdminAuditLogLine(BaseModel):
    """One privileged operator action, newest-first in the audit feed."""

    id: str
    actor_id: str
    actor_username: str
    action: str
    target_type: str
    target_id: str | None
    detail: dict[str, Any] | None
    created_at: datetime


class AdminAuditLogListResponse(BaseModel):
    data: list[AdminAuditLogLine]
    total: int
    page: int
    page_size: int


# --- Admin: 运营观测看板 (观测, P1) ---
# The operator-facing health view, sourced from turn_metrics (the per-turn
# telemetry DB sink) rather than the dev log firehose (logs/dev.jsonl). Admin-gated
# (管理员后台.md). Per-turn money/text are NOT duplicated here — a 会话复盘 (P2)
# joins cost_events + messages by trace_id.


class TurnHealthWindow(BaseModel):
    """Turn health aggregated over a time window (today / 近 7 日) — 全站健康.

    Rates are derived server-side from the raw counts (the client renders them as
    percentages); ``p95_duration_ms`` surfaces the latency tail the average hides.
    """

    turns: int
    errors: int
    # errors / turns in 0..1 (0 when the window has no turns).
    error_rate: float
    avg_duration_ms: int
    p95_duration_ms: int
    avg_rounds: float
    # Turns that delegated to ≥1 member (multi-agent), and its share of turns.
    delegated_turns: int
    delegated_rate: float
    input_tokens: int
    output_tokens: int
    # 协作质量 (学·度量 §2.5): first_plan_survival_rate = share of delegated turns whose opening
    # plan ran without a supervised boundary handing control back (首计划存活率); scope_signals /
    # revises / escalations are raw window sums (漂移 / 返工 / 升级). Default 0 so a window with
    # no delegated turns renders clean.
    first_plan_survival_rate: float = 0.0
    scope_signals: int = 0
    revises: int = 0
    escalations: int = 0


class DailyTurns(BaseModel):
    """One UTC day's turn + error counts — a point in the 观测 trend."""

    # ISO date (YYYY-MM-DD) of the UTC calendar day.
    date: str
    turns: int
    errors: int


class TurnMetricLine(BaseModel):
    """One turn's telemetry row — the 近期错误 feed + 会话复盘 entry point.

    Carries the join keys (``trace_id`` / ``conversation_id``) to drill from a
    failure into the full turn (logs + messages + spend). ``error`` is the
    truncated soft-error text (NULL on success). Built straight from the ORM row.
    """

    turn_id: str
    conversation_id: str
    user_id: str
    agent_id: str | None
    trace_id: str | None
    kind: str
    status: Literal["ok", "partial", "paused", "error"]
    finish_reason: str | None
    error: str | None
    rounds: int
    duration_ms: int
    delegated: bool
    workers: int
    input_tokens: int
    output_tokens: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminObservabilitySummary(BaseModel):
    """Platform-wide 运营观测看板 (``GET /v1/admin/observability/summary``, admin-only).

    The operator's health view, sourced from ``turn_metrics`` (not the dev log
    file): today's and the trailing 7-day window health, the 7-day daily trend, and
    the most recent errored turns. Aggregated over *every* account (admin is a
    cross-user surface). Per-turn money/text are NOT here — drill into a turn by
    ``trace_id`` (会话复盘, P2) to join cost_events + messages.
    """

    today: TurnHealthWindow
    week: TurnHealthWindow
    # Last 7 UTC days incl today, oldest-first, zero-filled — the trend bars.
    recent_daily: list[DailyTurns]
    # Most recent errored turns (newest-first, capped) — the 近期错误 feed.
    recent_errors: list[TurnMetricLine]


# --- Admin: 控制台概览 (landing dashboard) ---
# A curated one-call snapshot for the console home, stitched from the same
# aggregates as 用量 / 观测 / 系统 (one extra: distinct active users) so the
# headline numbers never drift from those drill-down pages.


class AdminOverview(BaseModel):
    """Landing dashboard (``GET /v1/admin/overview``, admin-only) — platform pulse.

    Today's vitals (active users / turn health / cost), account tallies, the 7-day
    cost + turn trends, deployment health, and the most recent errors (drillable
    into 会话复盘). Money is integer nano-CNY; ``CostBreakdown.cny_total`` is yuan.
    """

    # 今日 pulse: distinct users that took a turn, the turn-health rollup (turns /
    # errors / error_rate / p95 / 委派率 / tokens), and total spend today.
    active_users_today: int
    today: TurnHealthWindow
    cost_today: CostBreakdown
    # Account tallies (status-based, same source as 系统状态).
    users_total: int
    users_active: int
    admins: int
    # 7-day trends (oldest-first, zero-filled) — cost bars + turn/error bars.
    recent_daily_cost: list[DailyCost]
    recent_daily_turns: list[DailyTurns]
    # Deployment health + a short recent-errors feed (drill into 会话复盘 by id).
    database_ok: bool
    recent_errors: list[TurnMetricLine]
    billing_mode: str


# --- Admin: 用户详情下钻 (用户管理 P0 drill-down) ---
# One account's at-a-glance profile: the full record + its *own* usage (the
# per-user counterpart of the platform 用量看板) + its recent conversations and
# turn activity (each drillable into 会话复盘). Admin cross-user, read-only.


class AdminConversationLine(BaseModel):
    """One of a user's conversations in the 用户详情 roster (compact row).

    id/title/timestamps + message count, newest-activity first. Links to the
    existing 会话复盘 (``GET /v1/admin/observability/conversations/{id}``) for the
    full merged timeline. ``title`` is NULL for an untitled conversation.
    """

    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    # Total messages in the conversation (user + assistant).
    messages: int


class AdminConversationListItem(BaseModel):
    """One row in the platform-wide 对话 roster (``GET /v1/admin/conversations``).

    Cross-user conversation index for ops: owner identity, housekeeping flags,
    message/turn/error rollups, and all-time spend (nano-CNY). Soft-deleted
    conversations and tombstone owners are surfaced when requested. Clients format
    ¥ as ``cost_total / 1e9``. Drill into 会话复盘 by ``id``.
    """

    id: str
    title: str | None
    user_id: str
    username: str | None
    display_name: str | None
    # Set when the owning account was soft-deleted (注销).
    user_deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    archived: bool
    messages: int
    turns: int
    errors: int
    cost_total: int
    # Multi-agent rollup: turns that delegated (≥1 worker) + max workers across those turns.
    # ``delegated_turns > 0`` drives the roster「多 Agent」badge / ``has_delegated`` filter.
    delegated_turns: int = 0
    workers: int = 0


class AdminConversationListResponse(BaseModel):
    """Paginated platform conversation roster (admin-only)."""

    data: list[AdminConversationListItem]
    total: int
    page: int
    page_size: int


class AdminTurnListItem(TurnMetricLine):
    """One turn in the platform-wide 回合 feed — TurnMetricLine + list context.

    Carries the owning conversation title and account display identity so an
    operator can triage without opening 复盘 first. ``models`` /
    ``credential_source`` come from ``cost_calls`` joined by ``trace_id``
    (``turn_metrics.turn_id`` ≠ assistant ``message_id`` — never join on turn_id).
    """

    conversation_title: str | None = None
    username: str | None = None
    display_name: str | None = None
    conversation_deleted_at: datetime | None = None
    # Distinct model ids from ``cost_calls`` for this turn's ``trace_id`` (deduped,
    # first-seen order). Empty when the turn left no call ledger rows.
    models: list[str] = []
    # From ``cost_calls.cost`` JSONB ``credential_source``. No ledger → null.
    # Rows present but key missing → ``platform`` (matches ledger ``split_cost``).
    # Mixed sources → ``user`` if any call is user, else ``platform`` (vendor→platform).
    credential_source: Literal["user", "platform"] | None = None


class AdminTurnListResponse(BaseModel):
    """Paginated platform turn feed (admin-only)."""

    data: list[AdminTurnListItem]
    total: int
    page: int
    page_size: int


class AdminUserDetail(BaseModel):
    """One account's drill-down (``GET /v1/admin/users/{id}/detail``, admin-only).

    Stitches the per-user views an operator needs to understand an account: the
    full record (``user``), the account BYOK default chat/background model names +
    provider count (from the ``users`` pointers / ``user_llm_providers`` — never the
    API key), this account's usage (today/month/
    trend/by-model — the per-user counterpart of ``AdminUsageSummary``),
    its recent conversations, and its recent turn activity (``turn_metrics``, each
    drillable into 会话复盘), plus active login ``sessions`` (refresh-token families,
    same shape as ``GET /v1/auth/sessions``). Money is integer nano-CNY;
    ``CostBreakdown.cny_total`` is yuan. ``billing_mode`` frames cost honestly
    (byok = own-key spend).
    """

    user: AdminUserResponse
    # Account BYOK default model pointers (names only; null when unset). 多服务商列表:
    # the chat / background default model, plus how many providers are configured.
    default_model: str | None = None
    background_model: str | None = None
    provider_count: int = 0
    today: UsageWindow
    month: UsageWindow
    # Last 30 days' call-level spend split by model (from ``cost_calls``), spend-desc.
    recent_by_model: list[ModelCostLine]
    # Last 7 UTC days incl today, oldest-first, zero-filled — the trend sparkline.
    recent_daily_cost: list[DailyCost]
    # Recent conversations (newest-activity first, capped).
    conversations: list[AdminConversationLine]
    # Recent turns (newest-first, capped) — each drillable into 会话复盘.
    recent_turns: list[TurnMetricLine]
    # Active login devices (refresh-token families) — same shape as ``GET /v1/auth/sessions``.
    # Read-only for ops 加强可查; ``current`` is always false (admin is not in-session).
    sessions: list[SessionSummary] = []
    billing_mode: str


class ReplaySpan(BaseModel):
    """One execution span inside a turn — a tool call or an LLM call — projected
    compactly from ``turn_journal`` for the 复盘 drill-down.

    Deliberately summary-only: NOT the heavy/sensitive replay payload (system
    prompts, full tool results), just what triages a turn — what ran, in which
    round/run, ok?, finish_reason, tokens, and a short preview. Per-span latency is
    omitted: the execution facts don't reliably carry a per-span timestamp yet.
    """

    kind: str  # "tool" | "llm"
    run_id: str | None = None
    round_idx: int | None = None
    # Tool spans:
    name: str | None = None
    success: bool | None = None
    args_preview: str | None = None
    result_preview: str | None = None
    # LLM spans:
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ReplayRun(BaseModel):
    """One agent run in a turn's multi-agent tree (会话复盘 · 协作树节点).

    Lightweight triage projection — NOT the full ``RunsPayload.events``. Sourced
    from ``turn_journal`` via the existing display fold
    (``runs_from_entries`` → ``project_turn``) plus ``message_final`` for full
    worker text. ``spans`` stay on the parent ``ReplayMessage`` and are grouped
    client-side by ``run_id``.
    """

    run_id: str
    agent_id: str
    role: str | None = None
    kind: str = "agent"  # agent | captain | …
    task: str = ""
    status: str = "pending"
    parent_run_id: str | None = None
    depends_on: list[str] = []
    # Worker deliverable body (message_final full text); None when absent.
    content: str | None = None
    # Structured handoff brief {summary/key_points/assumptions/next_steps}, or None.
    debrief: dict | None = None
    output_summary: str | None = None
    error: str | None = None


class ReplayMessage(BaseModel):
    """One message in a 会话复盘 timeline (the thread + per-turn overlays).

    An assistant message carries its turn's telemetry (``metrics``, joined by
    trace_id), spend (``cost_total``, summed from cost_events by message_id), and the
    turn's execution spans (``spans``, projected from turn_journal); user messages
    have none. ``content`` is the raw message text (the prompt / reply) — the
    substance of the post-mortem. Multi-agent turns also carry ``runs`` (lightweight
    tree nodes for triage — not the desktop team canvas).

    The conversation list is deliberately summary-sized: compressed ``spans`` /
    lightweight ``runs`` / ``metrics`` / ``cost_total`` travel with every row.
    The user-end final-state pair (``runs_payload`` + ``projected``) does **not** —
    it is fetched per assistant turn via
    ``GET .../messages/{id}/final-state`` when ``has_final_state`` is true.
    Those two fields stay on this model so the admin client can merge the
    on-demand pair onto the same row; on the list endpoint they are always null.

    ``models`` / ``credential_source`` come from ``cost_calls`` (call authority):
    message rows join by ``message_id``; bare text-less turn markers join by
    ``trace_id``. No ledger → empty models + null source.

    User rows also carry ``attachments`` / ``agent_mentions`` in the same shapes as
    ``MessageDetail`` (metadata chips only — no extracted file text).
    """

    id: str
    role: str
    content: str | None
    # DERIVED thinking stream (``messages.reasoning_content``); null on user rows
    # and bare turn markers. Not projected from turn_journal.
    reasoning_content: str | None = None
    created_at: datetime
    trace_id: str | None
    metrics: TurnMetricLine | None = None
    # Per-turn spend (integer nano-CNY); clients format ¥ as ``cost_total / 1e9``.
    cost_total: int = 0
    # Distinct model ids from ``cost_calls`` (deduped, first-seen order). Empty when
    # the turn left no call ledger rows.
    models: list[str] = []
    # From ``cost_calls.cost`` JSONB ``credential_source``. No ledger → null.
    # Rows present but key missing → ``platform`` (matches ledger ``split_cost``).
    # Mixed sources → ``user`` if any call is user, else ``platform`` (vendor→platform).
    credential_source: Literal["user", "platform"] | None = None
    # Message provenance from ``messages.usage.origin`` (e.g. ``execution_harvest`` for
    # system closing-turn synthetic user rows). Parity with MessageDetail.origin —
    # admin timeline must not paint these as ordinary user prompts. null otherwise.
    origin: str | None = None
    # From ``messages.usage.harvest_kind`` when origin is execution_harvest
    # (success / failure / cancelled). null for ordinary rows.
    harvest_kind: str | None = None
    # The turn's tool/LLM spans (turn_journal projection); empty for user prompts and
    # for turns that journaled nothing (a plain single-agent chat with no tools).
    spans: list[ReplaySpan] = []
    # Multi-agent run tree (empty for plain chat / user prompts).
    runs: list[ReplayRun] = []
    # Always null on GET conversation. Hydrate via the per-turn final-state
    # endpoint — the pair is the heavy DURABLE replay (full events / projected).
    runs_payload: RunsPayload | None = None
    projected: dict[str, Any] | None = None
    # True when ``runs_from_entries`` would return a payload (process-only
    # included). False for user rows and plain chat with nothing to replay.
    has_final_state: bool = False
    # Same as MessageDetail: persisted attachment chips (no extracted ``text``).
    attachments: list[StoredAttachment] = Field(default_factory=list)
    # Same as MessageDetail: conversation-page @Agent chips. Empty on assistant /
    # pre-feature / bare-turn-marker rows.
    agent_mentions: list[AgentMention] = Field(default_factory=list, max_length=10)

    @field_validator("agent_mentions", mode="before")
    @classmethod
    def _agent_mentions_from_row(cls, v: object) -> object:
        from agentcore.conversation.mentions import to_stored_agent_mentions

        if v is None:
            return []
        if isinstance(v, list):
            return to_stored_agent_mentions(
                [item if isinstance(item, dict) else {} for item in v]
            )
        return []


class ReplayConversation(BaseModel):
    """The conversation header for a 复盘 (owner identity + title + model profile)."""

    id: str
    title: str | None
    user_id: str
    username: str | None
    display_name: str | None
    created_at: datetime
    # Session pin into ``llm_model_profiles`` (or system preset id); null = follow
    # account default. Display name always comes from expand (effective combo).
    model_profile_id: str | None = None
    model_profile_name: str | None = None
    # Soft-delete stamp. Roster includes tombstones by default; replay must match
    # (null = live). Not an owner-scoped recycle-bin field.
    deleted_at: datetime | None = None


class AdminConversationReplay(BaseModel):
    """One conversation's 复盘 timeline (``GET /v1/admin/observability/conversations/{id}``).

    Merges the three turn sources by trace_id / message_id: the message thread
    (bodies, from ``messages``), per-turn outcome/quality (``turn_metrics``), and
    per-turn spend (``cost_events``). Admin-only, cross-user — the drill-down target
    of the 观测看板's 近期错误 feed (opens a failed turn in full context).

    ``messages`` is the **latest** window (newest-first fetch, returned chronological)
    so a long thread keeps the recent side ops need. ``has_more_before`` is true
    when older rows exist past the cap.

    Assistant-row ``runs_payload`` / ``projected`` are always null here; fetch
    ``AdminReplayTurnFinalState`` per turn instead of paging the list.
    """

    conversation: ReplayConversation
    messages: list[ReplayMessage]
    # Conversation rollup over its traced turns.
    turns: int
    errors: int
    # Total turn spend (integer nano-CNY); clients format ¥ as ``cost_total / 1e9``.
    cost_total: int
    # True when the latest-window cap dropped older messages (scroll-up remains later).
    has_more_before: bool = False


class AdminReplayTurnFinalState(BaseModel):
    """On-demand user-end final state for one 会话复盘 assistant turn.

    ``GET /v1/admin/observability/conversations/{id}/messages/{message_id}/final-state``.

    Same pair the user client reads on reload (no third projection):
    ``runs_payload`` is ``runs_from_entries`` verbatim (same as ``MessageDetail.runs``);
    ``projected`` is ``project_turn(runs_payload.events)`` — the same oracle as
    conformance golden, fed the journal's display events (not a reconstructed live
    vector). ``projected`` is null when there are no foldable display events
    (plain chat, or process-only single-agent — process then lives on
    ``runs_payload.process``). Terminal ``finish_reason`` / process / captain
    context stay on ``runs_payload`` (journal ``turn_end`` is not a ``message_end``
    event — same as GET /messages). Both null when the turn journaled nothing
    replayable. Not a live-stream rebuild, and not a truncated/summarized pair.
    """

    message_id: str
    runs_payload: RunsPayload | None = None
    projected: dict[str, Any] | None = None
