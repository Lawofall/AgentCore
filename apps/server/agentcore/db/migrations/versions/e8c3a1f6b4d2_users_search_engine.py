"""users.search_engine (account web_search index)

Revision ID: e8c3a1f6b4d2
Revises: c6e1a4d8b2f9
Create Date: 2026-09-29

Default searxng so existing accounts keep the current index. cleversee is
opt-in from 设置 · 通用 and only selectable when CLEVERSEE_API_KEY is set.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8c3a1f6b4d2"
down_revision: str | None = "c6e1a4d8b2f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "search_engine",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'searxng'"),
        ),
    )
    op.create_check_constraint(
        "ck_users_search_engine",
        "users",
        "search_engine in ('searxng', 'cleversee')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_search_engine", "users", type_="check")
    op.drop_column("users", "search_engine")
