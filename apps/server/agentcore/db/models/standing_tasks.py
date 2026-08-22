"""Standing tasks (站立任务 / 定时自动化 L1 + L2a webhook).

``standing_tasks`` is the user's always-on brief; ``standing_task_runs``
are the personal inbox rows (success / failure / awaiting_user). Distinct from
``handoff_jobs`` (local→云交接) — no shared job table/state machine. Shared
runtime shell with handoff/workflows is only ``spawn_background``; credentials
align with workflows, not handoff. Pause truth stays in ``paused_turns``.

Trigger is mutually exclusive: ``schedule`` | ``webhook`` (no dual cron+webhook).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class StandingTask(Base):
    __tablename__ = "standing_tasks"
    __table_args__ = (
        CheckConstraint(
            "trigger_kind in ('schedule', 'webhook')",
            name="ck_standing_tasks_trigger_kind",
        ),
        # Scheduler poll: due enabled *schedule* tasks ordered by next_run_at.
        Index("ix_standing_tasks_due", "trigger_kind", "enabled", "next_run_at"),
        Index("ix_standing_tasks_user_created", "user_id", "created_at"),
        Index("ix_standing_tasks_webhook_id", "webhook_id", unique=True),
        # One installed row per system template per user (partial unique).
        Index(
            "uq_standing_tasks_user_template",
            "user_id",
            "template_key",
            unique=True,
            postgresql_where=text("template_key IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # Cloud folder only (L1). App-level FK; create route rejects local folders.
    folder_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Periodic / webhook user goal appended as a user message each fire.
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    # schedule | webhook — mutually exclusive; existing rows default schedule.
    trigger_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'schedule'")
    )
    # 5-field cron (min hour dom month dow); NULL when trigger_kind=webhook.
    cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Public webhook path id; NULL when schedule.
    webhook_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    # SHA-256 hex of secret; plaintext only returned once on create/rotate.
    webhook_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permission_axes: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "file_write": "session",
            "command": "auto",
            "host": "ask",
        },
        server_default=text(
            "'{\"file_write\":\"session\",\"command\":\"auto\",\"host\":\"ask\"}'::jsonb"
        ),
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # NULL for webhook tasks; schedule poll only scans trigger_kind=schedule.
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Pinned conversation (mode=standing); NULL until first fire creates it.
    conversation_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True, index=True
    )
    # Lease: owner id + expiry so multi-worker / overlapping polls cannot double-run.
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # System template id (e.g. daily_conversation_review); NULL = user-authored task.
    template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Template-specific knobs (scope / lookback); {} for plain tasks.
    template_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Optional bind to an account workflow; when set, fire uses direct-start (not CEO).
    workflow_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class StandingTaskRun(Base):
    """One fire of a standing task — the personal inbox row."""

    __tablename__ = "standing_task_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'succeeded', 'failed', 'awaiting_user')",
            name="ck_standing_task_runs_status",
        ),
        CheckConstraint(
            "trigger_source in ('schedule', 'webhook', 'manual')",
            name="ck_standing_task_runs_trigger_source",
        ),
        Index("ix_standing_task_runs_user_created", "user_id", "created_at"),
        Index("ix_standing_task_runs_task_created", "standing_task_id", "created_at"),
        Index("ix_standing_task_runs_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    standing_task_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    conversation_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    user_message_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'running'")
    )
    trigger_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'schedule'")
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Inbox ack: set when the user marks read / dismisses a failure card.
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
