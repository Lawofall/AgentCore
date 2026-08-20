"""turn_journal PK (turn_id, seq) → (turn_id, band, seq)

Revision ID: e9b2c4f1a7d3
Revises: d3a8f2c1b9e6
Create Date: 2026-08-20

Live prefix occupancy and post-seal overflow used to share one integer seq axis,
split at 1_000_000. That made ``seq`` ≠ emission order (overflow sorted after every
later live fact), polluted ``MAX(seq)``, and leaked the split into the db layer.

``band ∈ {live, overflow}`` is the occupancy namespace; ``seq`` is band-local.
Existing rows with ``seq >= 1000000`` become ``band='overflow'`` with seq restored
into the band (``seq - 1000000``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9b2c4f1a7d3"
down_revision: str | None = "d3a8f2c1b9e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turn_journal",
        sa.Column(
            "band",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'live'"),
        ),
    )
    # Drop the old PK before rewriting overflow seqs: live seq 0 and overflow
    # seq 1000000→0 would collide on (turn_id, seq) while that constraint lives.
    op.drop_constraint("turn_journal_pkey", "turn_journal", type_="primary")
    op.execute(
        sa.text(
            "UPDATE turn_journal "
            "SET band = 'overflow', seq = seq - 1000000 "
            "WHERE seq >= 1000000"
        )
    )
    op.create_primary_key(
        "turn_journal_pkey",
        "turn_journal",
        ["turn_id", "band", "seq"],
    )
    op.create_check_constraint(
        "ck_turn_journal_band",
        "turn_journal",
        "band in ('live', 'overflow')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_turn_journal_band", "turn_journal", type_="check")
    op.drop_constraint("turn_journal_pkey", "turn_journal", type_="primary")
    op.execute(
        sa.text(
            "UPDATE turn_journal "
            "SET seq = seq + 1000000 "
            "WHERE band = 'overflow'"
        )
    )
    op.drop_column("turn_journal", "band")
    op.create_primary_key(
        "turn_journal_pkey",
        "turn_journal",
        ["turn_id", "seq"],
    )
