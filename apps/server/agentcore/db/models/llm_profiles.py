"""LLM model combination profiles (模型组合 · main / worker / background / vision)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class LlmModelProfile(Base):
    """A named model combination: main (required) + optional worker / background / vision.

    Empty worker / background slots (model NULL) mean ``follow_main``. Empty vision
    (NULL) is not persisted follow_main; reader resolve may reuse main when that
    id accepts images, else platform ``VISION_*`` when ``billing_mode=platform``.
    System presets are virtual (not stored here).
    ``kind=implicit`` rows are migration-era per-session overrides; ``kind=user`` are
    user-authored combinations.
    """

    __tablename__ = "llm_model_profiles"
    __table_args__ = (
        CheckConstraint(
            "kind in ('user', 'implicit')",
            name="ck_llm_model_profiles_kind",
        ),
        CheckConstraint(
            "main_origin in ('platform', 'byok')",
            name="ck_llm_model_profiles_main_origin",
        ),
        CheckConstraint(
            "worker_origin is null or worker_origin in ('platform', 'byok')",
            name="ck_llm_model_profiles_worker_origin",
        ),
        CheckConstraint(
            "background_origin is null or background_origin in ('platform', 'byok')",
            name="ck_llm_model_profiles_background_origin",
        ),
        CheckConstraint(
            "vision_origin is null or vision_origin in ('platform', 'byok')",
            name="ck_llm_model_profiles_vision_origin",
        ),
        Index("ix_llm_model_profiles_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'user'")
    )

    main_origin: Mapped[str] = mapped_column(String(20), nullable=False)
    main_provider_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    main_model: Mapped[str] = mapped_column(String(200), nullable=False)

    worker_origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    worker_provider_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    worker_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    background_origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    background_provider_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    background_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    vision_origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vision_provider_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    vision_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
