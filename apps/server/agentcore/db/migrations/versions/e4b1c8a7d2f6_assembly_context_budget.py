"""assemblies.context_budget — optional shorter context ceiling.

Revision ID: e4b1c8a7d2f6
Revises: d8a2c4e7b1f9
Create Date: 2026-10-05

NULL = use the model's own window. A positive value is one of the fixed
steps below that window (128K / 256K / 512K).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b1c8a7d2f6"
down_revision: str | None = "d8a2c4e7b1f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assemblies",
        sa.Column("context_budget", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assemblies", "context_budget")
