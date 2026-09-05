"""account-level skill slot replacements (换用 overlay)

Revision ID: f7a2c9e1b4d6
Revises: e3b7c9a1f4d2
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7a2c9e1b4d6"
down_revision: str | None = "e3b7c9a1f4d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_slot_replacements",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("slot_name", sa.String(length=100), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=False),
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
        sa.UniqueConstraint(
            "user_id", "slot_name", name="uq_skill_slot_replacements_user_slot"
        ),
    )
    op.create_index(
        "ix_skill_slot_replacements_document",
        "skill_slot_replacements",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_slot_replacements_document", table_name="skill_slot_replacements"
    )
    op.drop_table("skill_slot_replacements")
