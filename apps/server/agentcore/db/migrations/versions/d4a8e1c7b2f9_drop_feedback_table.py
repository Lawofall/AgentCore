"""drop feedback table (退役应用内工单)

In-app ticket product is removed. Keep historical ``a1b2c3d4e5f6`` (create)
in the chain; this revision drops the table. ``downgrade`` rebuilds the
final shape but does not restore deleted rows.

Revision ID: d4a8e1c7b2f9
Revises: e7c2b9d4a1f6
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4a8e1c7b2f9"
down_revision: str | None = "e7c2b9d4a1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_feedback_status", table_name="feedback")
    op.drop_index("ix_feedback_user", table_name="feedback")
    op.drop_table("feedback")


def downgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("page_context", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'open'"), nullable=False),
        sa.Column("admin_reply", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category in ('bug', 'feature', 'improvement', 'other')",
            name="ck_feedback_category",
        ),
        sa.CheckConstraint(
            "status in ('open', 'acknowledged', 'resolved', 'closed')",
            name="ck_feedback_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_user", "feedback", ["user_id"])
    op.create_index("ix_feedback_status", "feedback", ["status"])
