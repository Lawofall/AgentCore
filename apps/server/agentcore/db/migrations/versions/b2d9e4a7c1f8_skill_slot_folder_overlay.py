"""folder-scoped skill slot overlay (近的覆盖远的)

Revision ID: b2d9e4a7c1f8
Revises: a1c4e8b2d7f3
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2d9e4a7c1f8"
down_revision: str | None = "a1c4e8b2d7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skill_slot_replacements",
        sa.Column("folder_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.drop_constraint(
        "uq_skill_slot_replacements_user_slot",
        "skill_slot_replacements",
        type_="unique",
    )
    op.create_index(
        "uq_skill_slot_replacements_user_slot",
        "skill_slot_replacements",
        ["user_id", "slot_name"],
        unique=True,
        postgresql_where=sa.text("folder_id IS NULL"),
    )
    op.create_index(
        "uq_skill_slot_replacements_user_folder_slot",
        "skill_slot_replacements",
        ["user_id", "folder_id", "slot_name"],
        unique=True,
        postgresql_where=sa.text("folder_id IS NOT NULL"),
    )
    op.create_index(
        "ix_skill_slot_replacements_folder",
        "skill_slot_replacements",
        ["folder_id"],
    )

    op.add_column(
        "skill_slot_mutes",
        sa.Column("folder_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.drop_constraint(
        "uq_skill_slot_mutes_user_slot",
        "skill_slot_mutes",
        type_="unique",
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
        "ix_skill_slot_mutes_folder",
        "skill_slot_mutes",
        ["folder_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_slot_mutes_folder", table_name="skill_slot_mutes")
    op.drop_index(
        "uq_skill_slot_mutes_user_folder_slot", table_name="skill_slot_mutes"
    )
    op.drop_index("uq_skill_slot_mutes_user_slot", table_name="skill_slot_mutes")
    op.drop_column("skill_slot_mutes", "folder_id")
    op.create_unique_constraint(
        "uq_skill_slot_mutes_user_slot",
        "skill_slot_mutes",
        ["user_id", "slot_name"],
    )

    op.drop_index(
        "ix_skill_slot_replacements_folder", table_name="skill_slot_replacements"
    )
    op.drop_index(
        "uq_skill_slot_replacements_user_folder_slot",
        table_name="skill_slot_replacements",
    )
    op.drop_index(
        "uq_skill_slot_replacements_user_slot",
        table_name="skill_slot_replacements",
    )
    op.drop_column("skill_slot_replacements", "folder_id")
    op.create_unique_constraint(
        "uq_skill_slot_replacements_user_slot",
        "skill_slot_replacements",
        ["user_id", "slot_name"],
    )
