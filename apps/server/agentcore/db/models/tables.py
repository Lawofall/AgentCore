"""Creation-tool 多维表格 (account-scoped typed grid).

A table is owned by ``user_id`` (照白板，不挂文件夹). Schema (columns) is JSONB
with ``schema_version`` CAS; rows and named views are sibling tables so two
editors can last-write-win different cells without a whole-table CAS.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class Table(Base):
    __tablename__ = "tables"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # Dedicated AI conversation, lazily bound on first sendTableTurn.
    # The grid outlives that chat: hard-delete nulls the pointer.
    conversation_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey(
            "conversations.id",
            name="fk_tables_conversation_id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    # DB column ``schema``; attribute is not ``schema`` (that is SQLAlchemy's
    # PostgreSQL-schema slot on the mapper).
    columns_schema: Mapped[dict] = mapped_column(
        "schema",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{\"columns\": []}'::jsonb"),
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # Current view. Hard-deleting that view nulls the pointer; the grid stays.
    active_view_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey(
            "table_views.id",
            name="fk_tables_active_view_id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )
    # Last reversible batch: {id, inverses, row_stamps}. NULL = nothing to undo.
    undo_batch: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # csv 灌数 upsert key（账号级表仍不挂 folder_id）。NULL = 人点新建。
    source_workspace_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_tables_source_live",
            "user_id",
            "source_workspace_key",
            "source_path",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND source_path IS NOT NULL"),
        ),
    )


class TableRow(Base):
    __tablename__ = "table_rows"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    table_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("tables.id", name="fk_table_rows_table_id", ondelete="CASCADE"),
        index=True,
    )
    cells: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    position: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1000"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TableView(Base):
    __tablename__ = "table_views"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    table_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("tables.id", name="fk_table_views_table_id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"))
    display_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'table'")
    )
    # filters, sort, group_by, hidden_column_ids, column_widths, density, mode_config
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
