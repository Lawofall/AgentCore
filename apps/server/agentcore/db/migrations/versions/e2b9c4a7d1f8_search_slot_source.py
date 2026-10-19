"""DeepSeek search slot may use the official BYOK service key.

Revision ID: e2b9c4a7d1f8
Revises: c1e8a4b7d2f9
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b9c4a7d1f8"
down_revision: str | None = "c1e8a4b7d2f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_search_providers",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'key'"),
        ),
    )
    op.create_check_constraint(
        "ck_user_search_providers_source",
        "user_search_providers",
        "source in ('key', 'provider')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_search_providers_source",
        "user_search_providers",
        type_="check",
    )
    op.drop_column("user_search_providers", "source")
