"""drop shared_spaces / shared_space_members / shared_space_events

Revision ID: e3b7c9a1f4d2
Revises: d8f1a4c6e2b9
Create Date: 2026-09-04

Independent shared-space product is removed (双模式工作区 §八). Keep historical
``a5c8e2f1b4d7`` (create) in the chain; this revision drops the three tables.
``downgrade`` rebuilds the final shape but does not restore deleted rows.
On-disk ``workspaces/shared/`` is left in place (no auto-migrate, no rmtree).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3b7c9a1f4d2"
down_revision: str | None = "d8f1a4c6e2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_shared_space_events_space_created", table_name="shared_space_events"
    )
    op.drop_table("shared_space_events")
    op.drop_index("ix_shared_space_members_user_id", table_name="shared_space_members")
    op.drop_table("shared_space_members")
    op.drop_index("ix_shared_spaces_owner_user_id", table_name="shared_spaces")
    op.drop_table("shared_spaces")


def downgrade() -> None:
    op.create_table(
        "shared_spaces",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
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
    )
    op.create_index("ix_shared_spaces_owner_user_id", "shared_spaces", ["owner_user_id"])

    op.create_table(
        "shared_space_members",
        sa.Column("space_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "state",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("invited_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role in ('owner', 'editor', 'viewer')",
            name="ck_shared_space_members_role",
        ),
        sa.CheckConstraint(
            "state in ('accepted', 'pending')",
            name="ck_shared_space_members_state",
        ),
    )
    op.create_index(
        "ix_shared_space_members_user_id", "shared_space_members", ["user_id"]
    )

    op.create_table(
        "shared_space_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("space_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "actor_via",
            sa.String(length=20),
            server_default=sa.text("'user'"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_shared_space_events_space_created",
        "shared_space_events",
        ["space_id", "created_at"],
    )
