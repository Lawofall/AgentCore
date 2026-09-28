"""Drop users.quota_daily_requests.

A daily turn-count cap is neither a spend quota nor a rate limit.
Spend caps and the sliding-window rate limiter remain; turn counts stay
a usage statistic.

Revision ID: a6d1e8c4b2f7
Revises: b7e2c9a4f1d8
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6d1e8c4b2f7"
down_revision: str | None = "b7e2c9a4f1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "quota_daily_requests")


def downgrade() -> None:
    op.add_column(
        "users", sa.Column("quota_daily_requests", sa.Integer(), nullable=True)
    )
