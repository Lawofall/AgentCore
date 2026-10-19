"""conversations.delegate_mode (输入区「委派」)

Revision ID: c7a1e4b9d2f6
Revises: e7d2a9c4b1f8
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a1e4b9d2f6"
down_revision: str | None = "e7d2a9c4b1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "delegate_mode",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "delegate_mode")
