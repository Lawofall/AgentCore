"""Drop leftover memory_updates.kind='episodic' rows.

Revision ID: b1c8e4f2a7d9
Revises: c9e2a7f4b1d6
Create Date: 2026-08-28

Session digests stay in ``memory_episodes``; they no longer get a conversation-tail
card. ``MemoryUpdateKind`` is closed to ``semantic`` | ``quota`` — leftover
``episodic`` rows would hard-fail the latest-window read, so delete them rather
than filter on the read side. Irreversible: the card payload is not reconstructed
on downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c8e4f2a7d9"
down_revision: str | None = "c9e2a7f4b1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM memory_updates WHERE kind = 'episodic'"))


def downgrade() -> None:
    pass
