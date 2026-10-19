"""Paid seats + redemption codes.

Revision ID: e9b3c7d1a4f2
Revises: c2f8a1d6e4b9
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e9b3c7d1a4f2"
down_revision: str | None = "c2f8a1d6e4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("seat_plan", sa.String(length=20), nullable=True))
    op.add_column(
        "users",
        sa.Column("seat_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("seat_pending_plan", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("seat_reminded_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_seat_plan",
        "users",
        "seat_plan is null or seat_plan in ('plus', 'flagship')",
    )
    op.create_check_constraint(
        "ck_users_seat_pending_plan",
        "users",
        "seat_pending_plan is null or seat_pending_plan in ('plus', 'flagship')",
    )
    op.create_index("ix_users_seat_until", "users", ["seat_until"])

    op.create_table(
        "redemption_codes",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_prefix", sa.String(length=16), nullable=False),
        sa.Column("sku", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'unused'"),
            nullable=False,
        ),
        sa.Column("batch_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("redeemed_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("sku in ('plus', 'flagship')", name="ck_redemption_codes_sku"),
        sa.CheckConstraint(
            "status in ('unused', 'redeemed', 'void')",
            name="ck_redemption_codes_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index(
        "ix_redemption_codes_status_sku",
        "redemption_codes",
        ["status", "sku"],
    )
    op.create_index("ix_redemption_codes_batch_id", "redemption_codes", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_redemption_codes_batch_id", table_name="redemption_codes")
    op.drop_index("ix_redemption_codes_status_sku", table_name="redemption_codes")
    op.drop_table("redemption_codes")
    op.drop_index("ix_users_seat_until", table_name="users")
    op.drop_constraint("ck_users_seat_pending_plan", "users", type_="check")
    op.drop_constraint("ck_users_seat_plan", "users", type_="check")
    op.drop_column("users", "seat_reminded_until")
    op.drop_column("users", "seat_pending_plan")
    op.drop_column("users", "seat_until")
    op.drop_column("users", "seat_plan")
