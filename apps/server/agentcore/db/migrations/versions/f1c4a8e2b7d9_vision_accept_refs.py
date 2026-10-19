"""BYOK catalog refs declared as native-multimodal.

Revision ID: f1c4a8e2b7d9
Revises: e8a3c7b1d4f6
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1c4a8e2b7d9"
down_revision: str | None = "e8a3c7b1d4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "vision_accept_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "vision_accept_refs")
