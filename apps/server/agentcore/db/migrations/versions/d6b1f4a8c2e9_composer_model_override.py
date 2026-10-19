"""Account last-used composer model snapshot.

Revision ID: d6b1f4a8c2e9
Revises: c4d8e2a7b1f5
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6b1f4a8c2e9"
down_revision: str | None = "c4d8e2a7b1f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "composer_model_override",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "composer_model_override")
