"""Undo snapshot for a user context cut (方案切点).

Revision ID: c3a9e7b1d4f8
Revises: f2a8c1e4b7d9
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a9e7b1d4f8"
down_revision: str | None = "f2a8c1e4b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "context_cut_undo",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "context_cut_undo")
