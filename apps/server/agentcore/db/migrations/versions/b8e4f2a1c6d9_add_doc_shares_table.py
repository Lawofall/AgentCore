"""add doc_shares table (frozen 文档 snapshot for public /shared/<id>)

Revision ID: b8e4f2a1c6d9
Revises: a7d3e9c1b5f2
Create Date: 2026-09-12 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8e4f2a1c6d9"
down_revision: str | None = "a7d3e9c1b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "doc_shares",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("doc_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("title", sa.String(length=500), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{\"schemaVersion\": 1, \"blocks\": []}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_doc_shares_doc_id"), "doc_shares", ["doc_id"], unique=False)
    op.create_index(op.f("ix_doc_shares_user_id"), "doc_shares", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_doc_shares_user_id"), table_name="doc_shares")
    op.drop_index(op.f("ix_doc_shares_doc_id"), table_name="doc_shares")
    op.drop_table("doc_shares")
