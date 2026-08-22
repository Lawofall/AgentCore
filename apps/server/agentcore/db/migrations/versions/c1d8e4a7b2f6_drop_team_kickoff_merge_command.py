"""Drop team_kickoff; merge command=kickoff in stored permission_axes.

Revision ID: c1d8e4a7b2f6
Revises: e9b2c4f1a7d3
Create Date: 2026-08-22 05:50:00.000000

conversations.permission_axes and standing_tasks.permission_axes:
- command=kickoff + file_write=session → command=auto
- command=kickoff + file_write=ask → command=ask
- drop team_kickoff key
- rewrite column defaults without the retired fields
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1d8e4a7b2f6"
down_revision: str | None = "e9b2c4f1a7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONV_DEFAULT = (
    '\'{"file_write":"session","command":"auto","host":"session"}\'::jsonb'
)
_TASK_DEFAULT = (
    '\'{"file_write":"session","command":"auto","host":"ask"}\'::jsonb'
)
_LEGACY_CONV_DEFAULT = (
    '\'{"file_write":"session","command":"auto",'
    '"team_kickoff":"rules","host":"session"}\'::jsonb'
)
_LEGACY_TASK_DEFAULT = (
    '\'{"file_write":"session","command":"auto",'
    '"team_kickoff":"skip","host":"ask"}\'::jsonb'
)

_MERGE_SQL = """
UPDATE {table}
SET permission_axes = jsonb_build_object(
    'file_write', COALESCE(permission_axes->>'file_write', 'session'),
    'command', CASE
        WHEN permission_axes->>'command' = 'kickoff' THEN
            CASE
                WHEN COALESCE(permission_axes->>'file_write', 'session') = 'ask'
                THEN 'ask'
                ELSE 'auto'
            END
        ELSE COALESCE(permission_axes->>'command', 'auto')
    END,
    'host', COALESCE(permission_axes->>'host', '{host_fallback}')
)
"""


def upgrade() -> None:
    op.execute(_MERGE_SQL.format(table="conversations", host_fallback="session"))
    op.execute(_MERGE_SQL.format(table="standing_tasks", host_fallback="ask"))
    op.execute(
        f"ALTER TABLE conversations ALTER COLUMN permission_axes SET DEFAULT {_CONV_DEFAULT}"
    )
    op.execute(
        f"ALTER TABLE standing_tasks ALTER COLUMN permission_axes SET DEFAULT {_TASK_DEFAULT}"
    )


def downgrade() -> None:
    # Restore retired keys on new-row defaults only; do not invent team_kickoff
    # for already-merged rows.
    op.execute(
        f"ALTER TABLE conversations ALTER COLUMN permission_axes "
        f"SET DEFAULT {_LEGACY_CONV_DEFAULT}"
    )
    op.execute(
        f"ALTER TABLE standing_tasks ALTER COLUMN permission_axes "
        f"SET DEFAULT {_LEGACY_TASK_DEFAULT}"
    )
