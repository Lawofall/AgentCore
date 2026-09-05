"""Skill overlay: 换用 bindings and 藏起 (muted) official skill slots.

Account layer: ``folder_id IS NULL``. Folder layer: one row per
``(user_id, folder_id, slot_name)`` — the desk owner's user_id, so members
read this desk's index and the owner's account overlay stays private.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class SkillSlotReplacement(Base):
    """One explicit 换用 binding: ``(user, folder_or_account, slot_name)`` → a user on-demand rule.

    Overlay only — system skill bodies stay in code.
    """

    __tablename__ = "skill_slot_replacements"
    __table_args__ = (
        Index(
            "uq_skill_slot_replacements_user_slot",
            "user_id",
            "slot_name",
            unique=True,
            postgresql_where=text("folder_id IS NULL"),
        ),
        Index(
            "uq_skill_slot_replacements_user_folder_slot",
            "user_id",
            "folder_id",
            "slot_name",
            unique=True,
            postgresql_where=text("folder_id IS NOT NULL"),
        ),
        Index("ix_skill_slot_replacements_document", "document_id"),
        Index("ix_skill_slot_replacements_folder", "folder_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    folder_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    slot_name: Mapped[str] = mapped_column(String(100), nullable=False)
    document_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.now,
        nullable=False,
    )


class SkillSlotMute(Base):
    """One explicit 藏起: this official slot leaves the model's on-demand catalog.

    Toolbox still shows the factory body so the user can put it back. Independent
    of 换用 — a muted replaced slot stays bound, just not advertised. Clearing this
    layer's mute inherits the outer layer (does not force-show).
    """

    __tablename__ = "skill_slot_mutes"
    __table_args__ = (
        Index(
            "uq_skill_slot_mutes_user_slot",
            "user_id",
            "slot_name",
            unique=True,
            postgresql_where=text("folder_id IS NULL"),
        ),
        Index(
            "uq_skill_slot_mutes_user_folder_slot",
            "user_id",
            "folder_id",
            "slot_name",
            unique=True,
            postgresql_where=text("folder_id IS NOT NULL"),
        ),
        Index("ix_skill_slot_mutes_folder", "folder_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    folder_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    slot_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
