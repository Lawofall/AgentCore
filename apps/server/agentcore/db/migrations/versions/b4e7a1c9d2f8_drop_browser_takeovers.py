"""drop browser_takeovers

Revision ID: b4e7a1c9d2f8
Revises: c8f3a1e6b4d9
Create Date: 2026-09-06

Anytime-takeover product entity is removed. Keep historical ``d3b8c1f0a2e6``
(create) and ``e5b2a8c1d4f7`` (session_id) in the chain; this revision drops
the table. ``downgrade`` rebuilds the final shape but does not restore rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4e7a1c9d2f8"
down_revision: str | None = "c8f3a1e6b4d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_browser_takeovers_session_id", table_name="browser_takeovers")
    op.drop_index("ix_browser_takeovers_conversation_started", table_name="browser_takeovers")
    op.drop_index("ix_browser_takeovers_user_id", table_name="browser_takeovers")
    op.drop_table("browser_takeovers")


def downgrade() -> None:
    op.create_table(
        "browser_takeovers",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_browser_takeovers_user_id", "browser_takeovers", ["user_id"], unique=False
    )
    op.create_index(
        "ix_browser_takeovers_conversation_started",
        "browser_takeovers",
        ["conversation_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_browser_takeovers_session_id",
        "browser_takeovers",
        ["session_id"],
        unique=False,
    )
