"""Conversation-level model override.

Revision ID: a3c7e1b9d4f2
Revises: f7d3a1c8e4b2
Create Date: 2026-10-17

A conversation may pin its own model slots. Null means inherit the
assembly. Switching assembly does not clear this override.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c7e1b9d4f2"
down_revision: str | None = "f7d3a1c8e4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "model_override",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "model_override")
