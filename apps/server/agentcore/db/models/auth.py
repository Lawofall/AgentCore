"""Auth credentials and tokens: Credentials, UserLlmProvider, RefreshToken."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid

# --- Credentials ---
# Local password auth, separated from the user profile. One row per user.


class Credentials(Base):
    __tablename__ = "credentials"

    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Brute-force lockout bookkeeping.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set by admin password reset; cleared after the user sets a new password on next login.
    password_must_change: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


# --- User LLM providers (BYOK · 多服务商列表) ---
# 用户自带的 OpenAI 兼容服务商配置。一人多行（多服务商），每行是一个独立端点：
# label（显示名）+ api_key_enc（AES-256-GCM 密文）+ base_url + default_model +
# supports_tools + 连通状态（各服务商各测各的）。明文 key 永不落库
# （security.KeyEncryptor 加密，解析见 llm/resolve.py）。账号级 chat/后台默认指针
# （(provider_id, model) 一对）落在 users 表；会话覆盖指针落在 conversations.model_provider_id。


class UserLlmProvider(Base):
    __tablename__ = "user_llm_providers"
    __table_args__ = (
        CheckConstraint(
            "status in ('unchecked', 'active', 'error')",
            name="ck_user_llm_providers_status",
        ),
        # The per-user provider list read + account-注销 cascade.
        Index("ix_user_llm_providers_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # Owning account (app-level FK → users; account注销 cascades these rows).
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    # Human-facing display name for this provider (e.g. "DeepSeek", "火山方舟").
    label: Mapped[str] = mapped_column(String(100), server_default=text("''"))
    # AES-256-GCM ciphertext (nonce ‖ ct+tag); never the plaintext key.
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # User-configured OpenAI-compatible endpoint (includes version prefix).
    base_url: Mapped[str] = mapped_column(
        String(500), server_default=text("'https://api.deepseek.com'")
    )
    # This provider's default model name (e.g. deepseek-v4-flash) — discovery seed +
    # sensible default when the account/conversation pointer omits a model.
    default_model: Mapped[str] = mapped_column(
        String(200), server_default=text("'deepseek-v4-flash'")
    )
    # Probe hint: whether the endpoint returned tool_calls on a dummy-tool completion.
    supports_tools: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Last connectivity-test outcome surfaced in 设置·模型配置 ('测试连接'):
    # 'unchecked' until tested, then 'active'/'error'. Reset to 'unchecked' on
    # every key change (a new key hasn't been verified yet).
    status: Mapped[str] = mapped_column(
        String(20), default="unchecked", server_default=text("'unchecked'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


# --- User Git credentials (G3 · 云私仓账户级 PAT) ---
# One row per account. Ciphertext via security.KeyEncryptor (same AES-256-GCM
# wire as BYOK). Plaintext never returned; tools never accept password params —
# clone/push load this row server-side when running on cloud workspaces.


class UserGitCredential(Base):
    __tablename__ = "user_git_credentials"

    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)
    # AES-256-GCM ciphertext (nonce ‖ ct+tag); never the plaintext PAT.
    token_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # HTTP basic-auth username for cloud http(s). Product always writes
    # ``x-access-token`` (GitHub PAT); not a public API field.
    username: Mapped[str] = mapped_column(String(200), server_default=text("'x-access-token'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


# --- Refresh Tokens ---


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    token_hash: Mapped[str] = mapped_column(String(255))
    token_family: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # Session audience bound at issuance (product vs admin); refresh inherits it.
    client_aud: Mapped[str] = mapped_column(
        String(20), default="product", server_default=text("'product'")
    )
    # Client surface (desktop/mobile/admin) at family start; refresh inherits.
    client_platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Raw User-Agent truncated at insert; display parsing is a frontend concern.
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Last successful refresh / issuance for this row (session activity).
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Absolute family ceiling anchor — set on first login of the family, inherited
    # on every rotation (refresh_family_max_days / admin_refresh_family_max_hours /
    # ephemeral_refresh_family_max_hours when persist_session is false).
    family_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # False = session-cookie / short-TTL family (login persist_session=false).
    # Inherited on every rotation so refresh keeps the same cookie/TTL policy.
    persist_session: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
