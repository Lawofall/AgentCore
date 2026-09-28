"""share parent pointers null when the parent row is removed

Revision ID: f3a8c1e7b2d5
Revises: e6a1c9d4b7f2
Create Date: 2026-09-29

Revoked share rows stay. The public page reads ``snapshot``. When the
conversation or doc row disappears, ``conversation_id`` / ``doc_id`` become
NULL (``ON DELETE SET NULL``). Revoke (``revoked_at``) stays a service write.

Null pointers whose parent is already gone, then drop NOT NULL, then add the
constraints.

``downgrade`` drops the constraints and restores NOT NULL. It fails if a
pointer is already NULL, and it does not invent a parent id.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a8c1e7b2d5"
down_revision: str | None = "e6a1c9d4b7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("conversation_shares", "conversation_id", nullable=True)
    op.alter_column("doc_shares", "doc_id", nullable=True)
    op.execute(
        sa.text(
            "UPDATE conversation_shares AS child SET conversation_id = NULL "
            "WHERE child.conversation_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM conversations AS parent WHERE parent.id = child.conversation_id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE doc_shares AS child SET doc_id = NULL "
            "WHERE child.doc_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM docs AS parent WHERE parent.id = child.doc_id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE conversation_shares "
            "ADD CONSTRAINT fk_conversation_shares_conversation_id "
            "FOREIGN KEY (conversation_id) REFERENCES conversations (id) "
            "ON DELETE SET NULL NOT VALID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE conversation_shares "
            "VALIDATE CONSTRAINT fk_conversation_shares_conversation_id"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE doc_shares ADD CONSTRAINT fk_doc_shares_doc_id "
            "FOREIGN KEY (doc_id) REFERENCES docs (id) "
            "ON DELETE SET NULL NOT VALID"
        )
    )
    op.execute(sa.text("ALTER TABLE doc_shares VALIDATE CONSTRAINT fk_doc_shares_doc_id"))


def downgrade() -> None:
    op.drop_constraint(
        "fk_doc_shares_doc_id", "doc_shares", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_conversation_shares_conversation_id",
        "conversation_shares",
        type_="foreignkey",
    )
    op.alter_column("doc_shares", "doc_id", nullable=False)
    op.alter_column("conversation_shares", "conversation_id", nullable=False)
