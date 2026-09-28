"""composition and pointer foreign keys for tables, chat, skills, handoff

Revision ID: c4a9e1b7d2f8
Revises: f3a8c1e7b2d5
Create Date: 2026-09-29

Rows that have no meaning without their parent gain ``ON DELETE CASCADE``.
Pointers that should outlive the parent gain ``ON DELETE SET NULL``.

Orphan composition rows are deleted. Dangling pointers are nulled. Account
``user_id`` columns stay bare. ``documents.parent_id`` stays bare.

``downgrade`` drops the constraints and the indexes this revision added. It
restores ``skill_store_listings.source_document_id`` NOT NULL, which fails
if a pointer is already NULL, and it does not put deleted rows back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a9e1b7d2f8"
down_revision: str | None = "f3a8c1e7b2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_tables_active_view_id", "tables", "active_view_id"),
    ("ix_chat_members_last_read_message_id", "chat_members", "last_read_message_id"),
    ("ix_chat_messages_reply_to_message_id", "chat_messages", "reply_to_message_id"),
    ("ix_skill_store_listings_current_version_id", "skill_store_listings", "current_version_id"),
    ("ix_skill_store_installs_version_id", "skill_store_installs", "version_id"),
    ("ix_handoff_jobs_job_conversation_id", "handoff_jobs", "job_conversation_id"),
)

_FKS: tuple[tuple[str, str, str, str, str], ...] = (
    ("table_rows", "fk_table_rows_table_id", "table_id", "tables (id)", "CASCADE"),
    ("table_views", "fk_table_views_table_id", "table_id", "tables (id)", "CASCADE"),
    (
        "tables",
        "fk_tables_conversation_id",
        "conversation_id",
        "conversations (id)",
        "SET NULL",
    ),
    (
        "tables",
        "fk_tables_active_view_id",
        "active_view_id",
        "table_views (id)",
        "SET NULL",
    ),
    ("chat_members", "fk_chat_members_chat_id", "chat_id", "chats (id)", "CASCADE"),
    (
        "chat_messages",
        "fk_chat_messages_chat_id",
        "chat_id",
        "chats (id)",
        "CASCADE",
    ),
    (
        "chat_messages",
        "fk_chat_messages_reply_to_message_id",
        "reply_to_message_id",
        "chat_messages (id)",
        "SET NULL",
    ),
    (
        "chat_members",
        "fk_chat_members_last_read_message_id",
        "last_read_message_id",
        "chat_messages (id)",
        "SET NULL",
    ),
    (
        "skill_store_versions",
        "fk_skill_store_versions_listing_id",
        "listing_id",
        "skill_store_listings (id)",
        "CASCADE",
    ),
    (
        "skill_store_installs",
        "fk_skill_store_installs_listing_id",
        "listing_id",
        "skill_store_listings (id)",
        "CASCADE",
    ),
    (
        "skill_store_installs",
        "fk_skill_store_installs_version_id",
        "version_id",
        "skill_store_versions (id)",
        "CASCADE",
    ),
    (
        "skill_store_installs",
        "fk_skill_store_installs_document_id",
        "document_id",
        "documents (id)",
        "CASCADE",
    ),
    (
        "skill_store_reports",
        "fk_skill_store_reports_listing_id",
        "listing_id",
        "skill_store_listings (id)",
        "CASCADE",
    ),
    (
        "skill_store_listings",
        "fk_skill_store_listings_current_version_id",
        "current_version_id",
        "skill_store_versions (id)",
        "SET NULL",
    ),
    (
        "skill_store_listings",
        "fk_skill_store_listings_source_document_id",
        "source_document_id",
        "documents (id)",
        "SET NULL",
    ),
    (
        "handoff_jobs",
        "fk_handoff_jobs_source_conversation_id",
        "source_conversation_id",
        "conversations (id)",
        "CASCADE",
    ),
    (
        "handoff_jobs",
        "fk_handoff_jobs_job_conversation_id",
        "job_conversation_id",
        "conversations (id)",
        "CASCADE",
    ),
)


def _null_dangling(table: str, column: str, parent: str) -> None:
    op.execute(
        sa.text(
            f"UPDATE {table} AS child SET {column} = NULL "
            f"WHERE child.{column} IS NOT NULL AND NOT EXISTS ("
            f"SELECT 1 FROM {parent} AS parent WHERE parent.id = child.{column}"
            f")"
        )
    )


def _delete_dangling(table: str, column: str, parent: str) -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {table} AS child WHERE NOT EXISTS ("
            f"SELECT 1 FROM {parent} AS parent WHERE parent.id = child.{column}"
            f")"
        )
    )


def _add_fk(table: str, name: str, column: str, ref: str, ondelete: str) -> None:
    op.execute(
        sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES {ref} "
            f"ON DELETE {ondelete} NOT VALID"
        )
    )
    op.execute(sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}"))


def upgrade() -> None:
    for name, table, column in _NEW_INDEXES:
        op.create_index(name, table, [column])

    _null_dangling("tables", "conversation_id", "conversations")
    _null_dangling("tables", "active_view_id", "table_views")
    _null_dangling("chat_messages", "reply_to_message_id", "chat_messages")
    _null_dangling("chat_members", "last_read_message_id", "chat_messages")
    _null_dangling("skill_store_listings", "current_version_id", "skill_store_versions")

    op.alter_column("skill_store_listings", "source_document_id", nullable=True)
    _null_dangling("skill_store_listings", "source_document_id", "documents")

    _delete_dangling("table_rows", "table_id", "tables")
    _delete_dangling("table_views", "table_id", "tables")
    _delete_dangling("chat_members", "chat_id", "chats")
    _delete_dangling("chat_messages", "chat_id", "chats")
    _delete_dangling("skill_store_versions", "listing_id", "skill_store_listings")
    _delete_dangling("skill_store_reports", "listing_id", "skill_store_listings")
    op.execute(
        sa.text(
            "DELETE FROM skill_store_installs AS child "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM skill_store_listings AS parent WHERE parent.id = child.listing_id"
            ") OR NOT EXISTS ("
            "SELECT 1 FROM skill_store_versions AS parent WHERE parent.id = child.version_id"
            ") OR NOT EXISTS ("
            "SELECT 1 FROM documents AS parent WHERE parent.id = child.document_id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM handoff_jobs AS child "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM conversations AS parent "
            "WHERE parent.id = child.source_conversation_id"
            ") OR NOT EXISTS ("
            "SELECT 1 FROM conversations AS parent "
            "WHERE parent.id = child.job_conversation_id"
            ")"
        )
    )

    for table, name, column, ref, ondelete in _FKS:
        _add_fk(table, name, column, ref, ondelete)


def downgrade() -> None:
    for table, name, _column, _ref, _ondelete in reversed(_FKS):
        op.drop_constraint(name, table, type_="foreignkey")
    for name, table, _column in reversed(_NEW_INDEXES):
        op.drop_index(name, table_name=table)
    op.alter_column("skill_store_listings", "source_document_id", nullable=False)
