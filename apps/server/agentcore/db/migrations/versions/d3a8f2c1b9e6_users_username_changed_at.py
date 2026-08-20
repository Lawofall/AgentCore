"""users.username_changed_at (self-selected username cooldown)

Revision ID: d3a8f2c1b9e6
Revises: c2f9a1e4b7d8
Create Date: 2026-08-20

Tracks when a user last claimed/changed a self-selected username so PATCH /me
can enforce the 14-day cooldown after the first non-system handle.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a8f2c1b9e6"
down_revision: str | None = "c2f9a1e4b7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("username_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Login and occupancy are case-insensitive; store the canonical lowercase form.
    op.execute(sa.text("UPDATE users SET username = lower(username) WHERE username <> lower(username)"))


def downgrade() -> None:
    op.drop_column("users", "username_changed_at")
