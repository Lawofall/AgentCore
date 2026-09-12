"""add docs table (creation-tool 文档, folder-hung block body)

Revision ID: a7d3e9c1b5f2
Revises: e9c4b1a7d3f8
Create Date: 2026-09-12 17:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d3e9c1b5f2"
down_revision: str | None = "e9c4b1a7d3f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "docs",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("folder_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("title", sa.String(length=500), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "body",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{\"schemaVersion\": 1, \"blocks\": []}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_docs_user_id"), "docs", ["user_id"], unique=False)
    op.create_index(op.f("ix_docs_folder_id"), "docs", ["folder_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_docs_folder_id"), table_name="docs")
    op.drop_index(op.f("ix_docs_user_id"), table_name="docs")
    op.drop_table("docs")
