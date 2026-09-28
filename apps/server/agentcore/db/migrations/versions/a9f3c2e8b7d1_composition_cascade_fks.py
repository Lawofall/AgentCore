"""composition foreign keys ON DELETE CASCADE

Revision ID: a9f3c2e8b7d1
Revises: e8c3a1f6b4d2
Create Date: 2026-09-29

Child rows whose parent is already gone cannot be reached. Delete those
before adding the constraint so upgrade does not fail on leftover ids.
``turn_queue_items.conversation_id`` is varchar today; cast it to uuid
after dropping values that are not a conversation id.

``downgrade`` drops the constraints and restores the varchar column. It
does not put the deleted orphan rows back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9f3c2e8b7d1"
down_revision: str | None = "e8c3a1f6b4d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID_RE = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# (constraint, child table, child column, parent table)
_CASCADE_FKS: tuple[tuple[str, str, str, str], ...] = (
    ("fk_messages_conversation_id", "messages", "conversation_id", "conversations"),
    (
        "fk_conversation_preferences_conversation_id",
        "conversation_preferences",
        "conversation_id",
        "conversations",
    ),
    ("fk_folder_members_folder_id", "folder_members", "folder_id", "folders"),
    (
        "fk_turn_queue_items_conversation_id",
        "turn_queue_items",
        "conversation_id",
        "conversations",
    ),
    ("fk_turn_journal_conversation_id", "turn_journal", "conversation_id", "conversations"),
    ("fk_paused_turns_conversation_id", "paused_turns", "conversation_id", "conversations"),
    (
        "fk_paused_turn_outcomes_conversation_id",
        "paused_turn_outcomes",
        "conversation_id",
        "conversations",
    ),
)


def _drop_orphans(child: str, column: str, parent: str) -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {child} AS child "
            f"WHERE NOT EXISTS ("
            f"SELECT 1 FROM {parent} AS parent WHERE parent.id = child.{column}"
            f")"
        )
    )


def upgrade() -> None:
    for _name, child, column, parent in _CASCADE_FKS:
        if child == "turn_queue_items":
            continue
        _drop_orphans(child, column, parent)

    op.execute(
        sa.text(
            "DELETE FROM turn_queue_items "
            f"WHERE conversation_id !~* '{_UUID_RE}'"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM turn_queue_items AS child "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM conversations AS parent "
            "WHERE parent.id = child.conversation_id::uuid"
            ")"
        )
    )
    op.alter_column(
        "turn_queue_items",
        "conversation_id",
        existing_type=sa.String(length=64),
        type_=postgresql.UUID(as_uuid=False),
        postgresql_using="conversation_id::uuid",
        existing_nullable=False,
    )
    op.create_index(
        "ix_turn_queue_items_conversation",
        "turn_queue_items",
        ["conversation_id"],
        unique=False,
    )

    for name, child, column, parent in _CASCADE_FKS:
        op.execute(
            sa.text(
                f"ALTER TABLE {child} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({column}) REFERENCES {parent} (id) "
                f"ON DELETE CASCADE NOT VALID"
            )
        )
        op.execute(sa.text(f"ALTER TABLE {child} VALIDATE CONSTRAINT {name}"))


def downgrade() -> None:
    for name, child, _column, _parent in reversed(_CASCADE_FKS):
        op.drop_constraint(name, child, type_="foreignkey")
    op.drop_index("ix_turn_queue_items_conversation", table_name="turn_queue_items")
    op.alter_column(
        "turn_queue_items",
        "conversation_id",
        existing_type=postgresql.UUID(as_uuid=False),
        type_=sa.String(length=64),
        postgresql_using="conversation_id::text",
        existing_nullable=False,
    )
