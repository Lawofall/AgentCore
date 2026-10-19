"""Account-level remote MCP servers.

Revision ID: a8f3c1e6b2d9
Revises: d2f8a1c6e4b9
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8f3c1e6b2d9"
down_revision: str | None = "d2f8a1c6e4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(100), server_default=sa.text("''"), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column(
            "auth_kind",
            sa.String(20),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        sa.Column("token_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "oauth_client_id",
            sa.String(200),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("oauth_metadata", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'unchecked'"),
            nullable=False,
        ),
        sa.Column(
            "last_error",
            sa.String(2000),
            server_default=sa.text("''"),
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
            "auth_kind in ('none', 'oauth', 'bearer')",
            name="ck_user_mcp_servers_auth_kind",
        ),
        sa.CheckConstraint(
            "status in ('unchecked', 'ready', 'failed', 'needs_auth')",
            name="ck_user_mcp_servers_status",
        ),
    )
    op.create_index("ix_user_mcp_servers_user", "user_mcp_servers", ["user_id"])
    op.create_table(
        "user_mcp_oauth_pending",
        sa.Column("state", sa.String(80), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("server_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=False),
        sa.Column("token_endpoint", sa.String(500), nullable=False),
        sa.Column(
            "client_id",
            sa.String(200),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("client_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "return_origin",
            sa.String(500),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_mcp_oauth_pending_user", "user_mcp_oauth_pending", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_mcp_oauth_pending_user", table_name="user_mcp_oauth_pending")
    op.drop_table("user_mcp_oauth_pending")
    op.drop_index("ix_user_mcp_servers_user", table_name="user_mcp_servers")
    op.drop_table("user_mcp_servers")
