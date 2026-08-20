"""platform_credentials.tool_surface_limits (declared upstream caps)

Revision ID: c2f9a1e4b7d8
Revises: a9c3e7f1b4d2
Create Date: 2026-08-19

Per-member declaration of the upstream tool-surface the credential can hold.
Empty object = unlimited. Values are operator-filled; this migration does not
seed any vendor-specific numbers.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2f9a1e4b7d8"
down_revision: str | None = "a9c3e7f1b4d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_credentials",
        sa.Column(
            "tool_surface_limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_platform_credentials_tool_surface_limits_object",
        "platform_credentials",
        "jsonb_typeof(tool_surface_limits) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_platform_credentials_tool_surface_limits_object",
        "platform_credentials",
        type_="check",
    )
    op.drop_column("platform_credentials", "tool_surface_limits")
