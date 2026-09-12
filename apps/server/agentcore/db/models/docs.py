"""Creation-tool 文档 (folder-hung block body).

Distinct from ``documents`` (记忆 / 规则 Markdown 树). A Doc hangs on a cloud
folder — collaboration-desk members see the same live draft. ``body`` is the
canonical block list (schemaVersion + blocks[]); ``version`` is the CAS counter
so two tabs cannot silently clobber (照 boards.scene).
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, text
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
    # Required cloud folder. App-level ref, no DB FK (核心接口 §6.2).
    folder_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    body: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{\"schemaVersion\": 1, \"blocks\": []}'::jsonb"),
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
    """Public read-only 文档 share: frozen block-list snapshot (所见即所享).

    Distinct from ``conversation_shares``. The row id is the unguessable
    ``/shared/<id>`` token. Public render reads ``snapshot``, never the live doc.
    """

    __tablename__ = "doc_shares"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    doc_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # Who minted the link (audit). Revoke is desk can_write, not this column.
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{\"schemaVersion\": 1, \"blocks\": []}'::jsonb"),
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
