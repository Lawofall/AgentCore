"""BYOK picker allowlist (opt-in roster).

Revision ID: c2f8a1d6e4b9
Revises: b8c4e1a9d2f7
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2f8a1d6e4b9"
down_revision: str | None = "b8c4e1a9d2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "enabled_model_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "enabled_model_refs")
