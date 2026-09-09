"""drop skill_slot_mutes

Revision ID: a3e9c7b1d4f8
Revises: b4e7a1c9d2f8
Create Date: 2026-09-08

Official HOW is no longer hidden from the consult directory. Keep historical
``a1c4e8b2d7f3`` (create) and ``b2d9e4a7c1f8`` (folder overlay) in the chain;
this revision drops the table. ``downgrade`` rebuilds the final shape but
does not restore rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3e9c7b1d4f8"
down_revision: str | None = "b4e7a1c9d2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_skill_slot_mutes_folder", table_name="skill_slot_mutes")
    op.drop_index("uq_skill_slot_mutes_user_folder_slot", table_name="skill_slot_mutes")
    op.drop_index("uq_skill_slot_mutes_user_slot", table_name="skill_slot_mutes")
    op.drop_table("skill_slot_mutes")


def downgrade() -> None:
    op.create_table(
        "skill_slot_mutes",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("slot_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_skill_slot_mutes_user_slot",
        "skill_slot_mutes",
        ["user_id", "slot_name"],
        unique=True,
        postgresql_where=sa.text("folder_id IS NULL"),
    )
    op.create_index(
        "uq_skill_slot_mutes_user_folder_slot",
        "skill_slot_mutes",
        ["user_id", "folder_id", "slot_name"],
        unique=True,
        postgresql_where=sa.text("folder_id IS NOT NULL"),
    )
    op.create_index(
        "ix_skill_slot_mutes_folder", "skill_slot_mutes", ["folder_id"], unique=False
    )
