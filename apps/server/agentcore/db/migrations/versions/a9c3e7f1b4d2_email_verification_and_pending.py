"""users.email_verified_at + pending_registrations + email_challenges

Revision ID: a9c3e7f1b4d2
Revises: d2e8a4c1f7b9
Create Date: 2026-08-19

Verify-then-create signup: credentials live on pending_registrations until
the inbox code succeeds, so ``users.email`` (unique) cannot be squatted by an
unverified account. Challenge rows store only a hash of the 6-digit code.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c3e7f1b4d2"
down_revision: str | None = "d2e8a4c1f7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "pending_registrations",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "display_name",
            sa.String(length=200),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("registration_ip", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("email"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "email_challenges",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose in ('register', 'password_reset', 'email_verify')",
            name="ck_email_challenges_purpose",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("purpose", "email", name="uq_email_challenges_purpose_email"),
    )
    op.create_index("ix_email_challenges_user_id", "email_challenges", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_email_challenges_user_id", table_name="email_challenges")
    op.drop_table("email_challenges")
    op.drop_table("pending_registrations")
    op.drop_column("users", "email_verified_at")
