"""Official capability recipe on an assembly.

``recipe`` stays set while tools and envelope still follow 对话 / 上网 / 完整.
``omit_desk_rules`` is the 对话档 switch: this turn does not read folder rules.

Revision ID: f2a8c1e4b7d9
Revises: d7c2a9e4b1f8
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a8c1e4b7d9"
down_revision: str | None = "d7c2a9e4b1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assemblies",
        sa.Column("recipe", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "assemblies",
        sa.Column(
            "omit_desk_rules",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_assemblies_recipe",
        "assemblies",
        "recipe is null or recipe in ('chat', 'web', 'full')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_assemblies_recipe", "assemblies", type_="check")
    op.drop_column("assemblies", "omit_desk_rules")
    op.drop_column("assemblies", "recipe")
