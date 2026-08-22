"""Drop users.memory_enabled and users.conversation_history_access.

Revision ID: e2f9a6c3d8b1
Revises: c1d8e4a7b2f6
Create Date: 2026-08-22 07:10:00.000000

Long-term memory and conversation-log access are product-always-on.
The leftover columns are unread; drop them so ORM and schema stay aligned.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f9a6c3d8b1"
down_revision: str | None = "c1d8e4a7b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "memory_enabled")
    op.drop_column("users", "conversation_history_access")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "conversation_history_access",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
