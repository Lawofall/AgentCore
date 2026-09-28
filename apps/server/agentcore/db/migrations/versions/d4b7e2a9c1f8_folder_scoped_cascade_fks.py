"""folder-scoped docs and documents cascade with the folder row

Revision ID: d4b7e2a9c1f8
Revises: c2e8a4f1b7d9
Create Date: 2026-09-29

Creation drafts (``docs``) and desk settings (``documents.folder_id``) die
when the folder row is removed. Account-level documents keep ``folder_id``
NULL and are not touched. Delete children whose folder is already gone
before adding the constraint.

Soft-delete leaves the folder row, so these constraints do not fire then.

``downgrade`` drops the constraints. It does not restore deleted rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b7e2a9c1f8"
down_revision: str | None = "c2e8a4f1b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CASCADE_FKS: tuple[tuple[str, str, str], ...] = (
    ("fk_docs_folder_id", "docs", "folder_id"),
    ("fk_documents_folder_id", "documents", "folder_id"),
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM docs AS child "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM folders AS parent WHERE parent.id = child.folder_id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM documents AS child "
            "WHERE child.folder_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM folders AS parent WHERE parent.id = child.folder_id"
            ")"
        )
    )
    for name, child, column in _CASCADE_FKS:
        op.execute(
            sa.text(
                f"ALTER TABLE {child} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({column}) REFERENCES folders (id) "
                "ON DELETE CASCADE NOT VALID"
            )
        )
        op.execute(sa.text(f"ALTER TABLE {child} VALIDATE CONSTRAINT {name}"))


def downgrade() -> None:
    for name, child, _column in reversed(_CASCADE_FKS):
        op.drop_constraint(name, child, type_="foreignkey")
