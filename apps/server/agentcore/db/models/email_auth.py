"""Pending signup rows and email verification challenges.

Register is verify-then-create: credentials sit here until the inbox code
succeeds, so an attacker cannot squat ``users.email`` (unique) with an
unverified account. Challenge rows cover register / password-reset /
logged-in email verify; the code itself is stored as a hash only.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class PendingRegistration(Base):
    """Unverified signup payload. Email is the natural key (one pending per inbox)."""

    __tablename__ = "pending_registrations"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(200), server_default=text("''"))
    registration_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class EmailChallenge(Base):
    """One active 6-digit challenge per (purpose, email). Hash only; never plaintext."""

    __tablename__ = "email_challenges"
    __table_args__ = (
        CheckConstraint(
            "purpose in ('register', 'password_reset', 'email_verify')",
            name="ck_email_challenges_purpose",
        ),
        UniqueConstraint("purpose", "email", name="uq_email_challenges_purpose_email"),
        Index("ix_email_challenges_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    purpose: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    code_hash: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
