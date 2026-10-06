"""Per-conversation envelope projections (date, file index).

Empty list = both present. Fact lines are not stored here.

Revision ID: c8e1a4f7b2d9
Revises: e1b7c4a9d2f6
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8e1a4f7b2d9"
down_revision: str | None = "e1b7c4a9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "omitted_projections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "omitted_projections")
