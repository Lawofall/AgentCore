"""IM messaging models (消息页 = 找人; 消息IM.md): Chat, ChatMember, ChatMessage.

A separate domain from the AI conversation tables: the 对话 page is "找 AI" (1 user
+ 1 agent_id, AI-shaped Message rows with reasoning/tool_calls/runs/usage), while the
消息 page is "找人" (human↔human + an official account). The two share the *frontend*
chat core, not these tables — folding 人↔人 into Message would bloat the AI hot-path
table with nullable columns and cannot hold many participants. Hence the dedicated
chats / chat_members / chat_messages set.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint("type in ('dm', 'group', 'official')", name="ck_chats_type"),
        # Per-user chat list ordering: chats a user belongs to (chat_members join),
        # ordered by recency. Index the sort key.
        Index("ix_chats_last_message_at", "last_message_at"),
        # Site-wide singleton official broadcast channel (product notices).
        Index(
            "uq_chats_official_singleton",
            "type",
            unique=True,
            postgresql_where=text("type = 'official'"),
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    type: Mapped[str] = mapped_column(String(20))
    # Group title; NULL for dm (client renders the peer's name) and official.
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Creator (NULL for system-owned official chats). Account pointer stays bare.
    created_by: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), index=True, nullable=True
    )
    # Canonical "min_id:max_id" pair key for dm chats, enforcing one DM per pair
    # and giving O(1) existing-DM lookup. NULL for group / official (Postgres
    # allows many NULLs under a unique index).
    dm_key: Mapped[str | None] = mapped_column(String(73), unique=True, nullable=True)
    # When true, every user is auto-joined to this chat: new users at registration
    # and existing users via a one-time backfill (the 内测全员群 mechanism — see
    # docs/05-平台与运维/消息IM.md · 官方号). Generalizes to an official broadcast
    # channel later. dm/regular group chats stay false.
    auto_join: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Denormalized list-row preview, refreshed on each message.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (
        CheckConstraint("role in ('owner', 'admin', 'member')", name="ck_chat_members_role"),
        # state=pending is the stranger "message request" gate: a DM from a
        # non-contact lands pending for the recipient until accepted.
        CheckConstraint("state in ('accepted', 'pending')", name="ck_chat_members_state"),
    )

    chat_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("chats.id", name="fk_chat_members_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Index for the hot "list my chats" query.
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="member", server_default=text("'member'"))
    state: Mapped[str] = mapped_column(
        String(20), default="accepted", server_default=text("'accepted'")
    )
    # Read cursor for unread counts (count messages created after this) and the
    # sender-side read receipt (last message id this member has seen).
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Read cursor. Removing that message nulls the pointer; the membership stays.
    last_read_message_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey(
            "chat_messages.id",
            name="fk_chat_members_last_read_message_id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )
    muted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Admin-imposed 禁言 (Stage 3 审核治理): a moderator silenced this member — they
    # can still read but a send is refused (403). Distinct from `muted` (the
    # member's own notification mute) so moderation and self-service stay separate.
    muted_by_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_type in ('user', 'official', 'agent')",
            name="ck_chat_messages_sender_type",
        ),
        CheckConstraint(
            "content_type in ('text', 'image', 'file', 'system_card')",
            name="ck_chat_messages_content_type",
        ),
        # Fetch a chat's messages in order (also covers chat_id prefix lookups).
        Index("ix_chat_messages_chat_created", "chat_id", "created_at"),
        # Idempotent send: a client retry with the same client_msg_id must not
        # duplicate. NULL client_msg_id (e.g. official pushes) is exempt.
        Index(
            "uq_chat_messages_client_msg",
            "chat_id",
            "sender_user_id",
            "client_msg_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    chat_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("chats.id", name="fk_chat_messages_chat_id", ondelete="CASCADE"),
    )
    # NULL = system/official sender. Account pointer stays bare.
    sender_user_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    sender_type: Mapped[str] = mapped_column(
        String(20), default="user", server_default=text("'user'")
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(
        String(20), default="text", server_default=text("'text'")
    )
    # Reuses the Message.attachments shape (list of {name, path, ...}).
    attachments: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # system_card deep-link payload (e.g. {kind, conversation_id}); NULL otherwise.
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Quote target. Removing that message nulls the pointer; ``reply_to`` stays.
    reply_to_message_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey(
            "chat_messages.id",
            name="fk_chat_messages_reply_to_message_id",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )
    # Frozen reply preview ({sender_user_id, sender_display_name, body_preview}).
    # Written at send time so S3 recall of the target still leaves a readable quote.
    reply_to: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Frozen @mentions list written at send time ([{kind:user,user_id}|{kind:everyone}]).
    mentions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Client-minted dedup key for retry-safe sends.
    client_msg_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Soft recall (S3): row kept for cursors/reply snapshots; body cleared.
    recalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recalled_by_user_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    # Edit stamp (S4): set when the sender rewrites plain-text content.
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
