"""User identity and social settings: User, UserBlock, UserDirectorySettings,
friendships / friend_requests (消息IM.md §九).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid

# --- Users ---
# Primary key is user_id. Other tables keep a bare ``user_id``: 注销 leaves
# this row in place, so a user foreign key would not delete the account.


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role in ('user', 'admin')", name="ck_users_role"),
        CheckConstraint("status in ('active', 'disabled')", name="ck_users_status"),
        CheckConstraint(
            "autonomy_policy in ('read', 'folder', 'computer')",
            name="ck_users_autonomy_policy",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    # Public handle (IM / roster). Unique, required. Must not contain ``@`` —
    # login treats a ``username`` field containing ``@`` as an email.
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), server_default=text("''"))
    # Unique when set. New signups prove inbox ownership before the users row
    # is created (pending_registrations); a timestamp here is that proof.
    # NULL = never verified (legacy accounts still log in unless the
    # require_email_verified setting is on).
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Object-storage key of the user's avatar (头像), e.g.
    # ``avatars/<user_id>/<hash>.webp``; NULL = no avatar (UI shows the initial).
    # Stores the storage key, not a URL — the served URL is derived at the API edge
    # (UserResponse / IM PersonPublic.avatar_url) so the backend stays agnostic of
    # its public origin.
    # The bytes live in object storage (storage/assets.py), never in the row.
    avatar_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user", server_default=text("'user'"))
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default=text("'active'")
    )
    # --- Per-user quota overrides (成本配额与计费.md §一) ---
    # `is_unlimited` short-circuits all quota checks (operator/trusted accounts).
    # Override columns are NULL = inherit the global config threshold for that
    # dimension; a non-null value (including 0 = unlimited) overrides it. Monthly /
    # daily cost mirror the config unit (float CNY), converted to nano-CNY at check
    # time. Resolved by `QuotaLimits.for_user`.
    is_unlimited: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    quota_daily_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quota_monthly_cost_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 日成本 backstop override. NULL = inherit config; 0 = unlimited for this user.
    # CNY like quota_monthly_cost_cny (→ nano at check time).
    quota_daily_cost_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Platform web_search count caps. NULL = inherit config; 0 = unlimited.
    # Orthogonal to the ¥ / token caps. Own search providers do not use them.
    quota_search_daily: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_search_monthly: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Default boundary for new conversations (安全权限与治理).
    # read | folder (default) | computer — seeds conversation.permission_axes.
    autonomy_policy: Mapped[str] = mapped_column(
        String(32), default="folder", server_default=text("'folder'")
    )
    # Account web_search selection. NULL = platform SearXNG (metered). A uuid
    # points at user_search_providers (own CleverSee or own SearXNG; not metered).
    search_provider_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    # Starred assembly. New conversations snapshot this id. NULL until the
    # first assembly is seeded. Changing the star does not move old chats.
    default_assembly_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    # Official recipes removed from the toolbox tray (chat / web / full).
    # The recipe stays in code. A row the user already materialized is separate.
    hidden_capability_recipes: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    # Client IP captured at registration (加强可查). NULL for pre-column rows /
    # seeded accounts. Same width as refresh_tokens.ip; written via get_client_ip.
    registration_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Last self-selected username claim/change — enforces 14-day cooldown after the
    # first non-system handle. NULL = never self-selected or still on a system handle.
    username_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Self-service account deletion (注销账户). NULL = live account; a timestamp marks
    # a user-initiated deletion. On delete the row is soft-deleted + anonymized
    # (username → "deleted_<id>", email → NULL) so the unique identifiers free up for
    # re-registration, while the append-only cost ledger (不变量①) stays intact.
    # Distinct from `status='disabled'` (admin-disabled, recoverable): a deleted
    # account is terminal. `get_current_user` already refuses non-active users, so a
    # deletion also sets status='disabled' to kill live tokens on the next request.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserBlock(Base):
    __tablename__ = "user_blocks"

    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    blocked_user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class UserDirectorySettings(Base):
    __tablename__ = "user_directory_settings"
    __table_args__ = (
        CheckConstraint(
            "who_can_dm in ('anyone', 'friends')",
            name="ck_user_directory_who_can_dm",
        ),
        CheckConstraint(
            "who_can_friend in ('anyone', 'group_members', 'nobody')",
            name="ck_user_directory_who_can_friend",
        ),
    )

    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    # Open search is the product default (任意搜人); users may opt out per-axis.
    discoverable: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    who_can_dm: Mapped[str] = mapped_column(
        String(20), default="anyone", server_default=text("'anyone'")
    )
    # Who may send a friend request: anyone (default) / shared group / nobody.
    who_can_friend: Mapped[str] = mapped_column(
        String(20), default="anyone", server_default=text("'anyone'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class Friendship(Base):
    """Bidirectional accepted friendship (canonical order user_a_id < user_b_id)."""

    __tablename__ = "friendships"
    __table_args__ = (
        CheckConstraint("user_a_id < user_b_id", name="ck_friendships_canonical_order"),
        Index("ix_friendships_user_b_id", "user_b_id"),
    )

    user_a_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    user_b_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class FriendRequest(Base):
    """Friend request: pending → accepted / rejected / cancelled (消息IM.md §9.2)."""

    __tablename__ = "friend_requests"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'accepted', 'rejected', 'cancelled')",
            name="ck_friend_requests_status",
        ),
        CheckConstraint(
            "from_user_id <> to_user_id",
            name="ck_friend_requests_not_self",
        ),
        Index("ix_friend_requests_to_status", "to_user_id", "status"),
        Index("ix_friend_requests_from_status", "from_user_id", "status"),
        # At most one pending request between a pair (either direction).
        Index(
            "uq_friend_requests_pending_pair",
            "pair_key",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    from_user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    to_user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # Canonical ``min:max`` of the two user ids — uniqueness of pending pairs.
    pair_key: Mapped[str] = mapped_column(String(80))
    message: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
