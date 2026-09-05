"""account-level skill slot mutes (藏官方 overlay)

Revision ID: a1c4e8b2d7f3
Revises: f7a2c9e1b4d6
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c4e8b2d7f3"
down_revision: str | None = "f7a2c9e1b4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_slot_mutes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("slot_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "slot_name", name="uq_skill_slot_mutes_user_slot"),
    )


def downgrade() -> None:
    op.drop_table("skill_slot_mutes")
