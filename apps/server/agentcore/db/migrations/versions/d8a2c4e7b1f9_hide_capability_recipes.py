"""Remember which official recipes left the toolbox tray.

Revision ID: d8a2c4e7b1f9
Revises: b7d2e9a4c1f6
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8a2c4e7b1f9"
down_revision: str | None = "b7d2e9a4c1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "hidden_capability_recipes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "hidden_capability_recipes")
