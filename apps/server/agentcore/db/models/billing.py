"""Cost ledger: per-call details (``CostCall``) + per-run aggregate (``CostEvent``).

``cost_calls`` is the authority for every LLM spend line; ``cost_events`` is the
per-run materialized view product surfaces (工资单 / 仪表盘 / 配额 SUM) read.
``persona`` holds the human-facing role label (调研员 / CEO / …) so payroll can
group beyond the structural captain/member bucket. Old rows may lack call
details or persona — read side tolerates missing fields (no backfill).

``conversation_id`` is nullable on all three tables: an **account-level** spend
line (AI 改写, ``role=assist``) belongs to no conversation. Such a row is real
money and must be visible, so it SUMs into the
account windows (用量页 / 仪表盘 / 配额) while every conversation-scoped read
filters it out by construction (see 成本配额与计费 §三).

``CostLedgerOutbox`` is the shared DB durable queue (G5 mid-term) drained with
``FOR UPDATE SKIP LOCKED`` so every API worker can self-drain.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid

_ROLE_CHECK = "role in ('captain', 'member', 'arena', 'title', 'memory', 'vision', 'assist')"
_OUTBOX_STATUS_CHECK = "status in ('pending', 'corrupt')"


class CostEvent(Base):
    __tablename__ = "cost_events"
    __table_args__ = (
        CheckConstraint(_ROLE_CHECK, name="ck_cost_events_role"),
        Index("ix_cost_events_user_created", "user_id", "created_at"),
        Index("ix_cost_events_message", "message_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # NULL for an account-level call that belongs to no conversation (AI 改写,
    # ``role=assist``). ``user_id`` stays the only mandatory
    # owner key — that is what the account windows and 配额 SUM aggregate on.
    conversation_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True, index=True
    )
    # The assistant turn this run belongs to (== the persisted Message.id), or
    # NULL for an off-turn background LLM call (标题生成 / 记忆整合, Gap C): those
    # belong to no turn, so they SUM into the account/conversation totals but stay
    # out of any single turn's per-message 工资单 (queried by message_id) and do
    # not inflate the「请求数」(COUNT(DISTINCT message_id) ignores NULL).
    message_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    # Idempotency: a retry of the same run must not double-bill, so run_id is
    # unique and the ledger write is an upsert-by-run_id.
    #
    # NOT a UUID: a delegated worker's id is namespaced ``del_<uuid>_N`` and a
    # revision's is ``<run>_rev2`` (same posture as RunSessionRow.run_id). A native
    # uuid column here silently broke billing — record_runs writes the turn as ONE
    # multi-row INSERT, so a single non-uuid member id aborted the whole batch
    # (captain row included), and the caller swallows it to a warning. Plain
    # strings, sized like RunSessionRow.
    run_id: Mapped[str] = mapped_column(String(128), unique=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(20))
    # Human-facing persona label (调研员 / CEO / …). NULL on legacy rows; read
    # side falls back to ``role`` for dashboard grouping.
    persona: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str] = mapped_column(String(50))
    # Token counts ({input, output, reasoning, cache_hit, cache_miss}).
    tokens: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Money is always integer nano-CNY (1 CNY = 1e9), never float.
    # cost = {input, cached, output, total}.
    cost: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Redundant scalar total so window SUMs run on an integer column (precise +
    # index-friendly), instead of digging into the JSONB each time.
    cost_total_nano: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    # BYOK / user-credential estimates — never summed into enforce_quota.
    cost_estimated_nano: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(String(8), default="CNY", server_default=text("'USD'"))
    rounds: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Correlation key to the turn's runtime logs: joins a spend row to its trace
    # (per-run `run_id` already correlates to worker logs; trace_id gives the
    # turn-level join). NULL on untraced (handoff) turns. See core/log_context.py.
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class CostCall(Base):
    """One LLM call's priced detail line — the billing authority.

    Multiple calls share a ``run_id``; ``cost_events`` materializes their SUM.
    Idempotent by ``call_id`` (UNIQUE + ON CONFLICT DO NOTHING).
    """

    __tablename__ = "cost_calls"
    __table_args__ = (
        CheckConstraint(_ROLE_CHECK, name="ck_cost_calls_role"),
        Index("ix_cost_calls_user_created", "user_id", "created_at"),
        Index("ix_cost_calls_message", "message_id"),
        Index("ix_cost_calls_run", "run_id"),
        Index(
            "ix_cost_calls_platform_credential_created",
            "platform_credential_id",
            "created_at",
            postgresql_where=text("platform_credential_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True, index=True
    )
    message_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    call_id: Mapped[str] = mapped_column(String(128), unique=True)
    run_id: Mapped[str] = mapped_column(String(128))
    parent_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(20))
    persona: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str] = mapped_column(String(50))
    tokens: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    cost: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    cost_total_nano: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    cost_estimated_nano: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(String(8), default="CNY", server_default=text("'USD'"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Platform-pool member (alias or stable hash). NULL on BYOK / vendor / pre-column rows.
    # Logs + this column only — never user-visible SSE error context.
    platform_credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class CostLedgerOutbox(Base):
    """Shared durable outbox for ``cost_calls`` / ``cost_events`` ledger writes.

    Enqueue inserts a ``pending`` row (telemetry pool). Drain claims with
    ``FOR UPDATE SKIP LOCKED``, writes the ledger sink, then deletes the row
    (at-least-once; ``call_id`` / ``run_id`` UNIQUE on the sink).
    """

    __tablename__ = "cost_ledger_outbox"
    __table_args__ = (
        CheckConstraint(_OUTBOX_STATUS_CHECK, name="ck_cost_ledger_outbox_status"),
        Index(
            "ix_cost_ledger_outbox_pending_created",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    message_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'turn'"))
    runs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    calls: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    materialize_runs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
