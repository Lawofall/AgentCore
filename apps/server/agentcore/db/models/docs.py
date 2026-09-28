"""Creation-tool 文档 (folder-hung markdown body).

Distinct from ``documents`` (记忆 / 规则 Markdown 树). A Doc hangs on a cloud
folder — collaboration-desk members see the same live draft. ``body`` is
``{"markdown": str}``; ``version`` is the CAS counter so two tabs cannot
silently clobber (CAS ``version``).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class Doc(Base):
    __tablename__ = "docs"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # Creator (audit). Access is via the folder desk, not this column.
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # Required cloud folder. Removed with the folder row
    # (``fk_docs_folder_id``). Soft-delete keeps the row, so drafts stay for restore.
    folder_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("folders.id", name="fk_docs_folder_id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    body: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{\"markdown\": \"\"}'::jsonb"),
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocShare(Base):
    """Public read-only 文档 page: last *published* markdown snapshot.

    Distinct from ``conversation_shares`` (those stay freeze-on-mint). The row
    id is the stable ``/shared/<id>`` token. Public render reads ``snapshot``,
    never the live doc. Republish overwrites ``snapshot`` in place (same URL);
    unpublished live edits do not leak. Removing the doc nulls ``doc_id``
    (``fk_doc_shares_doc_id``) and leaves the row.
    """

    __tablename__ = "doc_shares"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # Parent doc. NULL after that row is removed
    # (``fk_doc_shares_doc_id``, ON DELETE SET NULL). Revoke stays a service write.
    doc_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("docs.id", name="fk_doc_shares_doc_id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # Who minted the link (audit). Revoke is desk can_write, not this column.
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{\"markdown\": \"\"}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
