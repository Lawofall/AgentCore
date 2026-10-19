"""Replace the factory catalog flag with a per-skill omit list.

Revision ID: b2e7c4a9d1f6
Revises: a1f9c3e7b2d4
Create Date: 2026-10-19
"""

from collections.abc import Sequence
from json import dumps

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2e7c4a9d1f6"
down_revision: str | None = "a1f9c3e7b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Snapshot of registry names at this revision. Recipe-locked 极简 still
# expands to whatever the live catalog is; loosened rows keep this list.
_FACTORY_SKILLS = (
    "prompts",
    "data_file_landing",
    "page_ui",
    "product_help",
)


def upgrade() -> None:
    op.add_column(
        "assemblies",
        sa.Column(
            "omitted_factory_skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE assemblies SET omitted_factory_skills = CAST(:payload AS jsonb) "
            "WHERE omit_factory_catalog IS TRUE"
        ).bindparams(payload=dumps(list(_FACTORY_SKILLS)))
    )
    op.drop_column("assemblies", "omit_factory_catalog")


def downgrade() -> None:
    op.add_column(
        "assemblies",
        sa.Column(
            "omit_factory_catalog",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE assemblies SET omit_factory_catalog = TRUE "
            "WHERE omitted_factory_skills <> '[]'::jsonb"
        )
    )
    op.drop_column("assemblies", "omitted_factory_skills")
