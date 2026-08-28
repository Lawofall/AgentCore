"""add turn_metrics.prompt_tokens (single-request fit-check watermark)

Revision ID: c7e2a9b4d1f8
Revises: b1c8e4f2a7d9
Create Date: 2026-08-28

``input_tokens`` stays the sum of every LLM round this turn (billing).
``prompt_tokens`` is the largest single-request prompt size — near-ceiling
compaction compares that number to this turn's model window. Default 0 so
legacy rows fall back to ``input_tokens`` on read.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a9b4d1f8"
down_revision: str | None = "b1c8e4f2a7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_metrics",
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("turn_metrics", "prompt_tokens")
