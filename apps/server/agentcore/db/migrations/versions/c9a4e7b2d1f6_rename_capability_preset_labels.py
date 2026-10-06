"""Rename locked preset labels: 对话 → 极简, 上网 → 轻量.

Rows that still carry the recipe and still use the old label fold back into
the preset chip. A row the user already renamed stays as they left it.

Revision ID: c9a4e7b2d1f6
Revises: e4b1c8a7d2f6
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9a4e7b2d1f6"
down_revision: str | None = "e4b1c8a7d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE assemblies SET name = '极简' WHERE recipe = 'chat' AND name = '对话'"
    )
    op.execute(
        "UPDATE assemblies SET name = '轻量' WHERE recipe = 'web' AND name = '上网'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE assemblies SET name = '对话' WHERE recipe = 'chat' AND name = '极简'"
    )
    op.execute(
        "UPDATE assemblies SET name = '上网' WHERE recipe = 'web' AND name = '轻量'"
    )
