"""Account and conversation tool switches.

Empty list = every switchable tool is on. New conversations copy the account
list; a conversation then keeps its own.

Revision ID: e1b7c4a9d2f6
Revises: a6d1e8c4b2f7
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1b7c4a9d2f6"
down_revision: str | None = "a6d1e8c4b2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _column() -> sa.Column:
    return sa.Column(
        "disabled_tools",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )


def upgrade() -> None:
    op.add_column("users", _column())
    op.add_column("conversations", _column())


def downgrade() -> None:
    op.drop_column("conversations", "disabled_tools")
    op.drop_column("users", "disabled_tools")
