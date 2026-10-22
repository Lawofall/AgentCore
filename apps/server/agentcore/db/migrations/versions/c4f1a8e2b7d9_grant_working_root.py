"""Conversation external grant working_root (session cwd).

Revision ID: c4f1a8e2b7d9
Revises: b9e2c4a7d1f8
Create Date: 2026-10-20

At most one working-root grant per conversation. Does not change folder_id.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f1a8e2b7d9"
down_revision: str | None = "b9e2c4a7d1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_external_grants",
        sa.Column(
            "working_root",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "uq_conversation_external_grants_one_working_root",
        "conversation_external_grants",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("working_root IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_conversation_external_grants_one_working_root",
        table_name="conversation_external_grants",
    )
    op.drop_column("conversation_external_grants", "working_root")
