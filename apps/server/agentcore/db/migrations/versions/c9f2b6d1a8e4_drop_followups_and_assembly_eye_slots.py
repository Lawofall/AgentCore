"""drop leftover followups column and assembly eye / background slots

``messages.followups`` chips mint is gone. Assembly ``background_*`` /
``vision_*`` were unused by live title / compaction / turns (images ride
main). Drop the columns and their origin checks.

Revision ID: c9f2b6d1a8e4
Revises: f1c4a8e2b7d9
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9f2b6d1a8e4"
down_revision: str | None = "f1c4a8e2b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("messages", "followups")
    op.drop_constraint(
        "ck_llm_model_profiles_background_origin",
        "assemblies",
        type_="check",
    )
    op.drop_constraint(
        "ck_llm_model_profiles_vision_origin",
        "assemblies",
        type_="check",
    )
    op.drop_column("assemblies", "background_origin")
    op.drop_column("assemblies", "background_provider_id")
    op.drop_column("assemblies", "background_model")
    op.drop_column("assemblies", "vision_origin")
    op.drop_column("assemblies", "vision_provider_id")
    op.drop_column("assemblies", "vision_model")


def downgrade() -> None:
    op.add_column(
        "assemblies",
        sa.Column("vision_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "assemblies",
        sa.Column(
            "vision_provider_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
    )
    op.add_column(
        "assemblies",
        sa.Column("vision_origin", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "assemblies",
        sa.Column("background_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "assemblies",
        sa.Column(
            "background_provider_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
    )
    op.add_column(
        "assemblies",
        sa.Column("background_origin", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "ck_llm_model_profiles_vision_origin",
        "assemblies",
        "vision_origin is null or vision_origin in ('platform', 'byok')",
    )
    op.create_check_constraint(
        "ck_llm_model_profiles_background_origin",
        "assemblies",
        "background_origin is null or background_origin in ('platform', 'byok')",
    )
    op.add_column(
        "messages",
        sa.Column(
            "followups",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
