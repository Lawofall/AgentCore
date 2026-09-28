"""conversation-owned rows cascade; drop leftover ledger lines

Revision ID: c2e8a4f1b7d9
Revises: a9f3c2e8b7d1
Create Date: 2026-09-29

Leases, run sessions, memory notices, external grants, and agent-audit rows
die with the conversation. Delete children whose conversation is already gone
before adding the constraint.

``cost_calls`` stays without a foreign key. Lines whose conversation row is
already gone are the leftover of hard-delete removing ``cost_events`` only;
drop those lines. Account-level rows (``conversation_id`` NULL) stay.

``downgrade`` drops the constraints. It does not restore deleted rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2e8a4f1b7d9"
down_revision: str | None = "a9f3c2e8b7d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CASCADE_FKS: tuple[tuple[str, str], ...] = (
    ("fk_turn_leases_conversation_id", "turn_leases"),
    ("fk_run_sessions_conversation_id", "run_sessions"),
    ("fk_memory_updates_conversation_id", "memory_updates"),
    ("fk_conversation_external_grants_conversation_id", "conversation_external_grants"),
    ("fk_agent_audit_events_conversation_id", "agent_audit_events"),
)


def upgrade() -> None:
    for _name, child in _CASCADE_FKS:
        op.execute(
            sa.text(
                f"DELETE FROM {child} AS child "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM conversations AS parent WHERE parent.id = child.conversation_id"
                ")"
            )
        )
    op.execute(
        sa.text(
            "DELETE FROM cost_calls AS child "
            "WHERE child.conversation_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM conversations AS parent WHERE parent.id = child.conversation_id"
            ")"
        )
    )
    for name, child in _CASCADE_FKS:
        op.execute(
            sa.text(
                f"ALTER TABLE {child} ADD CONSTRAINT {name} "
                "FOREIGN KEY (conversation_id) REFERENCES conversations (id) "
                "ON DELETE CASCADE NOT VALID"
            )
        )
        op.execute(sa.text(f"ALTER TABLE {child} VALIDATE CONSTRAINT {name}"))


def downgrade() -> None:
    for name, child in reversed(_CASCADE_FKS):
        op.drop_constraint(name, child, type_="foreignkey")
