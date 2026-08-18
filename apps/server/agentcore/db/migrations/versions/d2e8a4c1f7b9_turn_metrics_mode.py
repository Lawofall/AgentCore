"""add turn_metrics.mode (engine location: cloud | local)

Revision ID: d2e8a4c1f7b9
Revises: f1a8c3e6b9d2
Create Date: 2026-08-19

Marks where the engine ran — the same fork as ``CloudStore.finalize(mode=)``.
Not ``chat.turn_start.location`` (workspace on user disk vs server) and not
``via`` (``cloud`` / ``sidecar``). Old rows are all cloud: local turns did not
write ``turn_metrics`` until after this column, so ``server_default='cloud'``
is a factual backfill, not an unknown-state guess.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e8a4c1f7b9"
down_revision: str | None = "f1a8c3e6b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_metrics",
        sa.Column(
            "mode",
            sa.String(length=16),
            server_default=sa.text("'cloud'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_turn_metrics_mode",
        "turn_metrics",
        "mode in ('cloud', 'local')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_turn_metrics_mode", "turn_metrics", type_="check")
    op.drop_column("turn_metrics", "mode")
