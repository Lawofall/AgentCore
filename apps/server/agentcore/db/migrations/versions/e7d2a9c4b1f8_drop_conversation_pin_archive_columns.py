"""drop conversations.pinned / archived (prefs + folder-delete 硬切)

内测无兼容层：置顶/归档只走 conversation_preferences；项目软删只打
archived_by_folder_delete。conversations 行上的双写列卸掉。

Revision ID: e7d2a9c4b1f8
Revises: b4e9c1a7d2f6
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7d2a9c4b1f8"
down_revision: str | None = "b4e9c1a7d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS pinned")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS archived")


def downgrade() -> None:
    raise NotImplementedError("内测硬切，不回滚 conversations.pinned/archived")
