"""add turn_metrics.error_code and error_type (investigation join)

Revision ID: e9c4b1a7d3f8
Revises: d1c8f4a6e2b9
Create Date: 2026-09-12

``error`` stays the truncated user-facing sentence. These two columns hold the
structured code / exception class already logged on ``engine.llm_failed_terminal``
and ``chat.local_turn_recorded``, so ``log_timeline`` can join them when sidecar
jsonl missed the terminal LLM event. Nullable; old rows stay NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9c4b1a7d3f8"
down_revision: str | None = "d1c8f4a6e2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_metrics",
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "turn_metrics",
        sa.Column("error_type", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("turn_metrics", "error_type")
    op.drop_column("turn_metrics", "error_code")
