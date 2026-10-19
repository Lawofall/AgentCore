"""Account-level remote MCP servers (Streamable HTTP + OAuth tokens)."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class UserMcpServer(Base):
    """One remote MCP Server per row; stdio configs stay on the desktop."""

    __tablename__ = "user_mcp_servers"
    __table_args__ = (
        CheckConstraint(
            "auth_kind in ('none', 'oauth', 'bearer')",
            name="ck_user_mcp_servers_auth_kind",
        ),
        CheckConstraint(
            "status in ('unchecked', 'ready', 'failed', 'needs_auth')",
            name="ck_user_mcp_servers_status",
        ),
        Index("ix_user_mcp_servers_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    name: Mapped[str] = mapped_column(String(100), server_default=text("''"))
    url: Mapped[str] = mapped_column(String(2000))
    auth_kind: Mapped[str] = mapped_column(
        String(20), default="none", server_default=text("'none'")
    )
    # AES-256-GCM JSON blob (tokens / optional client_secret). Never returned.
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    oauth_client_id: Mapped[str] = mapped_column(String(200), server_default=text("''"))
    oauth_metadata: Mapped[str] = mapped_column(Text, server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(20), default="unchecked", server_default=text("'unchecked'")
    )
    last_error: Mapped[str] = mapped_column(String(2000), server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class UserMcpOauthPending(Base):
    """In-flight PKCE login for a remote MCP Server."""

    __tablename__ = "user_mcp_oauth_pending"
    __table_args__ = (Index("ix_user_mcp_oauth_pending_user", "user_id"),)

    state: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    server_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    code_verifier: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    token_endpoint: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(200), server_default=text("''"))
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    return_origin: Mapped[str] = mapped_column(String(500), server_default=text("''"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
