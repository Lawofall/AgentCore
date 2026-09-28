"""Agent collaboration audit events — append-only multi-agent behavior trail."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class AgentAuditEvent(Base):
    """One auditable step in a delegated multi-agent turn (append-only).

    Projected from the turn journal + narrow runtime hooks for operator /
    owner-facing queries. Not a second replay source — ``turn_journal`` remains
    the UI fold's single fact stream.
    """

    __tablename__ = "agent_audit_events"
    __table_args__ = (
        CheckConstraint(
            "category in ('orchestration', 'tool', 'approval', 'comm', 'state', "
            "'failure', 'permission')",
            name="ck_agent_audit_events_category",
        ),
        CheckConstraint(
            "outcome in ('ok', 'denied', 'failed', 'skipped')",
            name="ck_agent_audit_events_outcome",
        ),
        CheckConstraint(
            "actor_kind in ('captain', 'member', 'system')",
            name="ck_agent_audit_events_actor_kind",
        ),
        # At-least-once drain / retry dedupe (as-built: 安全权限 §八).
        UniqueConstraint("turn_id", "seq", name="uq_agent_audit_events_turn_seq"),
        Index("ix_agent_audit_events_conversation_created", "conversation_id", "created_at"),
        Index("ix_agent_audit_events_created_category_action", "created_at", "category", "action"),
        Index(
            "ix_agent_audit_events_target_file",
            "target_type",
            "target_ref",
            postgresql_where=text("target_type = 'file'"),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey(
            "conversations.id",
            name="fk_agent_audit_events_conversation_id",
            ondelete="CASCADE",
        ),
        index=True,
    )
    turn_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    actor_kind: Mapped[str] = mapped_column(String(16))
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), index=True
    )
