"""conversation folder references: restrict affiliation, null the auto desk

Revision ID: e6a1c9d4b7f2
Revises: d4b7e2a9c1f8
Create Date: 2026-09-29

``conversations.folder_id`` is affiliation, not composition. The folder row
cannot disappear while a conversation still names it (``ON DELETE RESTRICT``).
Retention unfiles first; permanent wipe hard-deletes member chats first.

``conversations.auto_desk_folder_id`` is a pointer. Deleting the folder row
clears it (``ON DELETE SET NULL``) and leaves the conversation. Soft-delete
keeps the folder row, so that path still clears the pointer in the service.

Null any pointer whose folder is already gone before adding the constraints.
``downgrade`` drops the constraints and the auto-desk index. It does not
restore nulled pointers.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a1c9d4b7f2"
down_revision: str | None = "d4b7e2a9c1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE conversations AS child SET folder_id = NULL "
            "WHERE child.folder_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM folders AS parent WHERE parent.id = child.folder_id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE conversations AS child SET auto_desk_folder_id = NULL "
            "WHERE child.auto_desk_folder_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM folders AS parent WHERE parent.id = child.auto_desk_folder_id"
            ")"
        )
    )
    op.create_index(
        "ix_conversations_auto_desk_folder_id",
        "conversations",
        ["auto_desk_folder_id"],
    )
    op.execute(
        sa.text(
            "ALTER TABLE conversations ADD CONSTRAINT fk_conversations_folder_id "
            "FOREIGN KEY (folder_id) REFERENCES folders (id) "
            "ON DELETE RESTRICT NOT VALID"
        )
    )
    op.execute(sa.text("ALTER TABLE conversations VALIDATE CONSTRAINT fk_conversations_folder_id"))
    op.execute(
        sa.text(
            "ALTER TABLE conversations ADD CONSTRAINT fk_conversations_auto_desk_folder_id "
            "FOREIGN KEY (auto_desk_folder_id) REFERENCES folders (id) "
            "ON DELETE SET NULL NOT VALID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE conversations VALIDATE CONSTRAINT fk_conversations_auto_desk_folder_id"
        )
    )


def downgrade() -> None:
    op.drop_constraint("fk_conversations_auto_desk_folder_id", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_folder_id", "conversations", type_="foreignkey")
    op.drop_index("ix_conversations_auto_desk_folder_id", table_name="conversations")
