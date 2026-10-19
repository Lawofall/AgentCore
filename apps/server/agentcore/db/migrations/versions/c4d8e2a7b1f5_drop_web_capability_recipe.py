"""Drop the 轻量 (web) official capability recipe.

Locked rows keep their stored deny lists as ordinary assemblies.
Conversations that pinned the virtual preset id are a dangling id.

Revision ID: c4d8e2a7b1f5
Revises: b2e7c4a9d1f6
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d8e2a7b1f5"
down_revision: str | None = "b2e7c4a9d1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE assemblies SET recipe = NULL WHERE recipe = 'web'")
    op.drop_constraint("ck_assemblies_recipe", "assemblies", type_="check")
    op.create_check_constraint(
        "ck_assemblies_recipe",
        "assemblies",
        "recipe is null or recipe in ('chat', 'full')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_assemblies_recipe", "assemblies", type_="check")
    op.create_check_constraint(
        "ck_assemblies_recipe",
        "assemblies",
        "recipe is null or recipe in ('chat', 'web', 'full')",
    )
