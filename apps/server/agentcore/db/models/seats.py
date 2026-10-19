"""Paid-seat redemption codes (链动小铺卡密). Plaintext is never stored."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class RedemptionCode(Base):
    """One unused / redeemed / voided card secret.

    ``code_hash`` is SHA-256 of the normalized plaintext. ``code_prefix`` is the
    SKU head plus the first body group (admin roster). Batch plaintext is
    returned once at issue time and then forgotten.
    """

    __tablename__ = "redemption_codes"
    __table_args__ = (
        CheckConstraint(
            "sku in ('starter', 'plus', 'flagship')",
            name="ck_redemption_codes_sku",
        ),
        CheckConstraint(
            "status in ('unused', 'redeemed', 'void')",
            name="ck_redemption_codes_status",
        ),
        Index("ix_redemption_codes_status_sku", "status", "sku"),
        Index("ix_redemption_codes_batch_id", "batch_id"),
    )

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    code_prefix: Mapped[str] = mapped_column(String(16))
    sku: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20), default="unused", server_default=text("'unused'")
    )
    batch_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    created_by: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    redeemed_by: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
