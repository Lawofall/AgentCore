"""workflow trigger columns on user_workflows; drop standing_tasks

Revision ID: e7c2b9d4a1f6
Revises: c4e8b1a7d3f9
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7c2b9d4a1f6"
down_revision: str | None = "c4e8b1a7d3f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER_XOR = """
(
  trigger_kind IS NULL
  AND trigger_cron IS NULL
  AND trigger_webhook_id IS NULL
  AND trigger_folder_id IS NULL
)
OR (
  trigger_kind = 'schedule'
  AND trigger_cron IS NOT NULL
  AND trigger_webhook_id IS NULL
  AND trigger_folder_id IS NOT NULL
)
OR (
  trigger_kind = 'webhook'
  AND trigger_webhook_id IS NOT NULL
  AND trigger_cron IS NULL
  AND trigger_next_run_at IS NULL
  AND trigger_folder_id IS NOT NULL
)
"""


def upgrade() -> None:
    op.add_column(
        "user_workflows",
        sa.Column("trigger_kind", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column(
            "trigger_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_folder_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_cron", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_webhook_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_webhook_secret_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("last_trigger_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_lease_owner", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "user_workflows",
        sa.Column("trigger_lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_user_workflows_trigger_due",
        "user_workflows",
        ["trigger_kind", "trigger_enabled", "trigger_next_run_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_workflows_webhook_id",
        "user_workflows",
        ["trigger_webhook_id"],
        unique=True,
    )
    op.create_check_constraint(
        "ck_user_workflows_trigger_xor",
        "user_workflows",
        _TRIGGER_XOR,
    )

    op.drop_table("standing_task_runs")
    op.drop_table("standing_tasks")


def downgrade() -> None:
    raise NotImplementedError("standing_tasks product is retired; no downgrade")
