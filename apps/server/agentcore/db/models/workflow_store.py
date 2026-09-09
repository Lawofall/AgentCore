"""Workflow store: listing / version snapshot / install / report.

Source is ``user_workflows``, not documents. Official playbooks stay on
``/workflow-playbook-templates`` and are not written into these tables.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid

WORKFLOW_STORE_STATUSES = ("published", "unpublished", "taken_down")


class WorkflowStoreListing(Base):
    """One shelf item. One source workflow → one listing; republish = new version."""

    __tablename__ = "workflow_store_listings"
    __table_args__ = (
        CheckConstraint(
            "status in ('published', 'unpublished', 'taken_down')",
            name="ck_workflow_store_listings_status",
        ),
        UniqueConstraint(
            "source_workflow_id", name="uq_workflow_store_listings_source_workflow"
        ),
        Index("ix_workflow_store_listings_author", "author_user_id"),
        Index("ix_workflow_store_listings_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    author_user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    source_workflow_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=False), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'published'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.now,
        nullable=False,
    )


class WorkflowStoreVersion(Base):
    """Immutable snapshot of name / description / definition at publish time.

    Triggers are not snapshotted — schedule / webhook are the author's clock.
    """

    __tablename__ = "workflow_store_versions"
    __table_args__ = (
        UniqueConstraint("listing_id", "version_n", name="uq_workflow_store_versions_listing_n"),
        Index("ix_workflow_store_versions_listing", "listing_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    listing_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    version_n: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    definition: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class WorkflowStoreInstall(Base):
    """This account installed this listing; ``workflow_id`` is the local copy."""

    __tablename__ = "workflow_store_installs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "listing_id", name="uq_workflow_store_installs_user_listing"
        ),
        Index("ix_workflow_store_installs_user", "user_id"),
        Index("ix_workflow_store_installs_listing", "listing_id"),
        Index("ix_workflow_store_installs_workflow", "workflow_id"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    listing_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    version_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    workflow_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.now,
        nullable=False,
    )


class WorkflowStoreReport(Base):
    """One report per (user, listing). Open listing; no intent classifier."""

    __tablename__ = "workflow_store_reports"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "listing_id", name="uq_workflow_store_reports_user_listing"
        ),
        Index("ix_workflow_store_reports_listing", "listing_id"),
        Index("ix_workflow_store_reports_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    listing_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
