"""Durable FIFO of turns that have not started.

The process-local deque is what a live engine drains. This table is the copy
that survives a process restart. It stores the run payload and the order, never
the enqueue-time API key — drain resolves credentials again.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base


class TurnQueueItem(Base):
    """One queued user turn waiting for its conversation slot."""

    __tablename__ = "turn_queue_items"
    __table_args__ = (
        Index(
            "ix_turn_queue_items_holder_order",
            "engine",
            "local_root_id",
            "user_id",
            "position",
        ),
        Index("ix_turn_queue_items_conversation", "conversation_id"),
    )

    queue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey(
            "conversations.id",
            name="fk_turn_queue_items_conversation_id",
            ondelete="CASCADE",
        ),
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    # 1-based FIFO position. Reorder rewrites these; created_at is only a tiebreak.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    attachments: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    agent_mentions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    table_selection: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    requires_tools: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    x_client_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    origin_device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interjection_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Sidecar drain needs the desktop-minted assistant id and trace. Cloud leaves them null.
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ``cloud`` rows are restored by the API process. ``sidecar`` rows by the
    # desktop engine that owns ``local_root_id``.
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    local_root_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
