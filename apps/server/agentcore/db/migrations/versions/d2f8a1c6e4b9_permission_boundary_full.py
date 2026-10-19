"""Allow conversation boundary full (完全权限).

Revision ID: d2f8a1c6e4b9
Revises: c7a1e4b9d2f6
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d2f8a1c6e4b9"
down_revision: str | None = "c7a1e4b9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_users_autonomy_policy", "users", type_="check")
    op.create_check_constraint(
        "ck_users_autonomy_policy",
        "users",
        "autonomy_policy in ('read', 'folder', 'computer', 'full')",
    )


def downgrade() -> None:
    op.execute("UPDATE users SET autonomy_policy = 'folder' WHERE autonomy_policy = 'full'")
    op.drop_constraint("ck_users_autonomy_policy", "users", type_="check")
    op.create_check_constraint(
        "ck_users_autonomy_policy",
        "users",
        "autonomy_policy in ('read', 'folder', 'computer')",
    )
