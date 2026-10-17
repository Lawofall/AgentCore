"""Index of 带法 for workspace markdown files.

The row stores path + channel. The body stays on the workspace.

Revision ID: a4c8e2b7d1f9
Revises: c9a4e7b2d1f6
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4c8e2b7d1f9"
down_revision: str | None = "c9a4e7b2d1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_markdown_enrollments",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("apply_mode", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "patterns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "apply_mode in ('always', 'on_demand', 'paths')",
            name="ck_ws_md_enroll_apply",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name="fk_ws_md_enroll_folder",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("folder_id", "path", name="uq_ws_md_enroll_folder_path"),
    )
    op.create_index("ix_ws_md_enroll_folder", "workspace_markdown_enrollments", ["folder_id"])


def downgrade() -> None:
    op.drop_index("ix_ws_md_enroll_folder", table_name="workspace_markdown_enrollments")
    op.drop_table("workspace_markdown_enrollments")
