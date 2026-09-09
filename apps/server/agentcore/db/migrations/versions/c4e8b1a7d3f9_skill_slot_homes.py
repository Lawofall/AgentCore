"""account-level skill slot homes (人侧夹 overlay)

Revision ID: c4e8b1a7d3f9
Revises: a3e9c7b1d4f8
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e8b1a7d3f9"
down_revision: str | None = "a3e9c7b1d4f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_slot_homes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("slot_name", sa.String(length=100), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_skill_slot_homes_user_slot",
        "skill_slot_homes",
        ["user_id", "slot_name"],
        unique=True,
        postgresql_where=sa.text("folder_id IS NULL"),
    )
    op.create_index(
        "uq_skill_slot_homes_user_folder_slot",
        "skill_slot_homes",
        ["user_id", "folder_id", "slot_name"],
        unique=True,
        postgresql_where=sa.text("folder_id IS NOT NULL"),
    )
    op.create_index("ix_skill_slot_homes_parent", "skill_slot_homes", ["parent_id"])
    op.create_index("ix_skill_slot_homes_folder", "skill_slot_homes", ["folder_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_slot_homes_folder", table_name="skill_slot_homes")
    op.drop_index("ix_skill_slot_homes_parent", table_name="skill_slot_homes")
    op.drop_index(
        "uq_skill_slot_homes_user_folder_slot", table_name="skill_slot_homes"
    )
    op.drop_index("uq_skill_slot_homes_user_slot", table_name="skill_slot_homes")
    op.drop_table("skill_slot_homes")
