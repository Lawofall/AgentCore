"""skill store listings shelf_group

Revision ID: d1c8f4a6e2b9
Revises: b9e4c2a7f1d8
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1c8f4a6e2b9"
down_revision: str | None = "b9e4c2a7f1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GROUPS = "shelf_group in ('legal', 'writing', 'research', 'product', 'engineering', 'decision')"


def upgrade() -> None:
    op.add_column(
        "skill_store_listings",
        sa.Column("shelf_group", sa.String(length=32), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE skill_store_listings SET shelf_group = 'writing' WHERE shelf_group IS NULL"
        )
    )
    op.alter_column("skill_store_listings", "shelf_group", nullable=False)
    op.create_check_constraint(
        "ck_skill_store_listings_shelf_group",
        "skill_store_listings",
        _GROUPS,
    )
    op.create_index(
        "ix_skill_store_listings_status_group",
        "skill_store_listings",
        ["status", "shelf_group"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_store_listings_status_group", table_name="skill_store_listings")
    op.drop_constraint("ck_skill_store_listings_shelf_group", "skill_store_listings", type_="check")
    op.drop_column("skill_store_listings", "shelf_group")
