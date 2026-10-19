"""Factory boundary default: computer (这台电脑).

Revision ID: b9e2c4a7d1f8
Revises: a8f3c1e6b2d9
Create Date: 2026-10-19

New-row defaults only. Existing conversation.permission_axes and
users.autonomy_policy values stay as stored.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b9e2c4a7d1f8"
down_revision: str | None = "a8f3c1e6b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONV_NEW = "'{\"boundary\":\"computer\"}'::jsonb"
_CONV_OLD = "'{\"boundary\":\"folder\"}'::jsonb"


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN autonomy_policy SET DEFAULT 'computer'")
    op.execute(
        f"ALTER TABLE conversations ALTER COLUMN permission_axes SET DEFAULT {_CONV_NEW}"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN autonomy_policy SET DEFAULT 'folder'")
    op.execute(
        f"ALTER TABLE conversations ALTER COLUMN permission_axes SET DEFAULT {_CONV_OLD}"
    )
