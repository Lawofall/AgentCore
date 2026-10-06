"""Account assemblies (装配): tools, envelope, plugs, 交代, and the model.

A conversation pins this id only. Main / worker / background / vision,
``reasoning_effort``, and optional ``context_budget`` live on the row. Empty
worker / background follow main. NULL ``context_budget`` uses the model window.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class LlmModelProfile(Base):
    """One named assembly. Table ``assemblies``.

    Tool deny list, envelope omissions, enabled local MCP server ids, and the
    model columns live here. Account-level 交代 membership is ``assembly_skills``.
    ``kind=implicit`` rows are migration-era per-session overrides; ``kind=user``
    are user-authored assemblies.
    """

    __tablename__ = "assemblies"
    __table_args__ = (
        CheckConstraint(
            "kind in ('user', 'implicit')",
            name="ck_llm_model_profiles_kind",
        ),
        CheckConstraint(
            "recipe is null or recipe in ('chat', 'web', 'full')",
            name="ck_assemblies_recipe",
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

    disabled_tools: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    omitted_projections: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    enabled_mcp_server_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    omit_factory_catalog: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    # chat / web / full while the official recipe still owns tools and envelope.
    # Null = the stored deny lists are the record.
    recipe: Mapped[str | None] = mapped_column(String(16), nullable=True)
    omit_desk_rules: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
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

    reasoning_effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # NULL = this main model's catalog window. A positive step is a shorter ceiling.
    context_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )


class AssemblySkill(Base):
    """Which account-level document this assembly carries, and at which apply mode.

    Folder-scoped documents are not rows here; they stay on the desk.
    """

    __tablename__ = "assembly_skills"
    __table_args__ = (
        CheckConstraint(
            "apply_mode in ('always', 'on_demand', 'paths')",
            name="ck_assembly_skills_apply_mode",
        ),
        Index("ix_assembly_skills_document", "document_id"),
    )

    assembly_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("assemblies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    apply_mode: Mapped[str] = mapped_column(String(20), nullable=False)
