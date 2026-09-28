"""User search providers + platform search-count ledger.

Replaces the account enum ``users.search_engine`` (platform SearXNG vs the
deployment CleverSee key). Platform search stays the default (null pointer).
Own CleverSee keys and own SearXNG URLs live in ``user_search_providers``.
Accounts that had selected the deployment CleverSee key fall back to platform
SearXNG — that key was never theirs.

Revision ID: b7e2c9a4f1d8
Revises: c4a9e1b7d2f8
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e2c9a4f1d8"
down_revision: str | None = "c4a9e1b7d2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_search_providers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("label", sa.String(length=100), server_default=sa.text("''"), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'unchecked'"),
            nullable=False,
        ),
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
            "protocol in ('searxng', 'cleversee')",
            name="ck_user_search_providers_protocol",
        ),
        sa.CheckConstraint(
            "status in ('unchecked', 'active', 'error')",
            name="ck_user_search_providers_status",
        ),
    )
    op.create_index(
        "ix_user_search_providers_user",
        "user_search_providers",
        ["user_id"],
    )
    op.create_table(
        "platform_search_uses",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_platform_search_uses_user_created",
        "platform_search_uses",
        ["user_id", "created_at"],
    )
    op.add_column(
        "users",
        sa.Column("search_provider_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("quota_search_daily", sa.Integer(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("quota_search_monthly", sa.Integer(), nullable=True),
    )
    op.drop_constraint("ck_users_search_engine", "users", type_="check")
    op.drop_column("users", "search_engine")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "search_engine",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'searxng'"),
        ),
    )
    op.create_check_constraint(
        "ck_users_search_engine",
        "users",
        "search_engine in ('searxng', 'cleversee')",
    )
    op.drop_column("users", "quota_search_monthly")
    op.drop_column("users", "quota_search_daily")
    op.drop_column("users", "search_provider_id")
    op.drop_index("ix_platform_search_uses_user_created", table_name="platform_search_uses")
    op.drop_table("platform_search_uses")
    op.drop_index("ix_user_search_providers_user", table_name="user_search_providers")
    op.drop_table("user_search_providers")
