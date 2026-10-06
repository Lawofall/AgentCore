"""Assembly flag: leave the factory skill catalog off this copy.

Revision ID: d7c2a9e4b1f8
Revises: b3e8a1c6d4f2
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7c2a9e4b1f8"
down_revision: str | None = "b3e8a1c6d4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assemblies",
        sa.Column(
            "omit_factory_catalog",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("assemblies", "omit_factory_catalog")
