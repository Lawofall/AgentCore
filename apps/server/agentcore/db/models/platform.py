"""Platform LLM credential pool (operator-managed upstream keys)."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class PlatformCredential(Base):
    """One platform-pool member: ``(api_key, base_url)`` plus operator metadata.

    Key ciphertext uses the same AES-256-GCM ``KeyEncryptor`` / ``ENCRYPTION_KEY``
    as BYOK. ``id`` is the stable ``platform_credential_id`` stamped on logs and
    ``cost_calls``. ``subscription_day`` is this Go account's monthly-window
    anniversary (accounts are bought in batches, so anchors differ).
    ``tool_surface_limits`` is the operator-declared upstream tool-surface cap
    (empty object = unlimited).
    """

    __tablename__ = "platform_credentials"
    __table_args__ = (
        CheckConstraint(
            "subscription_day >= 1 AND subscription_day <= 31",
            name="ck_platform_credentials_subscription_day",
        ),
        CheckConstraint(
            "jsonb_typeof(tool_surface_limits) = 'object'",
            name="ck_platform_credentials_tool_surface_limits_object",
        ),
        Index("ix_platform_credentials_enabled_created", "enabled", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    label: Mapped[str] = mapped_column(String(100), server_default=text("''"))
    # AES-256-GCM ciphertext (nonce ‖ ct+tag); never the plaintext key.
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # Bound to this member — never fall back to global PLATFORM_BASE_URL.
    base_url: Mapped[str] = mapped_column(String(500))
    subscription_day: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    # Operator-declared upstream tool-surface caps. ``{}`` = unlimited.
    tool_surface_limits: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
