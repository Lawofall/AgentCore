"""Account web-search providers and the platform search-count ledger.

Platform SearXNG is the default index and is metered by ``platform_search_uses``.
A row in ``user_search_providers`` is the account's own CleverSee key or own
SearXNG URL; those calls do not write the ledger.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class UserSearchProvider(Base):
    __tablename__ = "user_search_providers"
    __table_args__ = (
        CheckConstraint(
            "protocol in ('searxng', 'cleversee')",
            name="ck_user_search_providers_protocol",
        ),
        CheckConstraint(
            "status in ('unchecked', 'active', 'error')",
            name="ck_user_search_providers_status",
        ),
        Index("ix_user_search_providers_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    label: Mapped[str] = mapped_column(String(100), server_default=text("''"))
    protocol: Mapped[str] = mapped_column(String(16))
    base_url: Mapped[str] = mapped_column(String(500))
    # AES-256-GCM ciphertext. NULL when a SearXNG instance has no key.
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="unchecked", server_default=text("'unchecked'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class PlatformSearchUse(Base):
    """One platform SearXNG request that left the process.

    Append-only count for the search quota. Cache hits and rejected queries
    never insert a row. Own-provider searches never insert a row.
    """

    __tablename__ = "platform_search_uses"
    __table_args__ = (
        Index("ix_platform_search_uses_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
