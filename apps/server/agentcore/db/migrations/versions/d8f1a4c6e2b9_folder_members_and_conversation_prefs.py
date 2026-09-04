"""folder_members + conversation_preferences (协作桌 §八)

Revision ID: d8f1a4c6e2b9
Revises: c7e2a9b4d1f8
Create Date: 2026-09-04

Collaboration-desk membership lives on folder_id (not IM chat_members).
Per-user pin/archive so two members cannot stomp Conversation.pinned/archived.
Existing flags migrate onto the desk-owner preference row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8f1a4c6e2b9"
down_revision: str | None = "c7e2a9b4d1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folder_members",
        sa.Column("folder_id", postgresql.UUID(as_uuid=False), primary_key=True),
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
            "role in ('editor', 'viewer')",
            name="ck_folder_members_role",
        ),
        sa.CheckConstraint(
            "state in ('accepted', 'pending')",
            name="ck_folder_members_state",
        ),
    )
    op.create_index("ix_folder_members_user_id", "folder_members", ["user_id"])

    op.create_table(
        "conversation_preferences",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "pinned",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_conversation_preferences_user_id",
        "conversation_preferences",
        ["user_id"],
    )

    op.execute(
        sa.text(
            """
            INSERT INTO conversation_preferences (conversation_id, user_id, pinned, archived)
            SELECT c.id,
                   COALESCE(f.user_id, c.user_id),
                   COALESCE(c.pinned, false),
                   COALESCE(c.archived, false)
            FROM conversations c
            LEFT JOIN folders f ON f.id = c.folder_id
            WHERE c.pinned IS TRUE OR c.archived IS TRUE
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_preferences_user_id",
        table_name="conversation_preferences",
    )
    op.drop_table("conversation_preferences")
    op.drop_index("ix_folder_members_user_id", table_name="folder_members")
    op.drop_table("folder_members")
