"""Allow 基础版 (starter) paid seats.

Revision ID: e8a3c7b1d4f6
Revises: d6b1f4a8c2e9
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8a3c7b1d4f6"
down_revision: str | None = "d6b1f4a8c2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAID = "('starter', 'plus', 'flagship')"
_PAID_OLD = "('plus', 'flagship')"


def upgrade() -> None:
    op.drop_constraint("ck_users_seat_plan", "users", type_="check")
    op.create_check_constraint(
        "ck_users_seat_plan",
        "users",
        f"seat_plan is null or seat_plan in {_PAID}",
    )
    op.drop_constraint("ck_users_seat_pending_plan", "users", type_="check")
    op.create_check_constraint(
        "ck_users_seat_pending_plan",
        "users",
        f"seat_pending_plan is null or seat_pending_plan in {_PAID}",
    )
    op.drop_constraint("ck_redemption_codes_sku", "redemption_codes", type_="check")
    op.create_check_constraint(
        "ck_redemption_codes_sku",
        "redemption_codes",
        f"sku in {_PAID}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_seat_plan", "users", type_="check")
    op.create_check_constraint(
        "ck_users_seat_plan",
        "users",
        f"seat_plan is null or seat_plan in {_PAID_OLD}",
    )
    op.drop_constraint("ck_users_seat_pending_plan", "users", type_="check")
    op.create_check_constraint(
        "ck_users_seat_pending_plan",
        "users",
        f"seat_pending_plan is null or seat_pending_plan in {_PAID_OLD}",
    )
    op.drop_constraint("ck_redemption_codes_sku", "redemption_codes", type_="check")
    op.create_check_constraint(
        "ck_redemption_codes_sku",
        "redemption_codes",
        f"sku in {_PAID_OLD}",
    )
