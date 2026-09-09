"""workflow store listings / versions / installs / reports

Revision ID: b9e4c2a7f1d8
Revises: e5b1c8d4a7f2
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b9e4c2a7f1d8"
down_revision: str | None = "e5b1c8d4a7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_store_listings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_workflow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'published'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('published', 'unpublished', 'taken_down')",
            name="ck_workflow_store_listings_status",
        ),
        sa.UniqueConstraint(
            "source_workflow_id", name="uq_workflow_store_listings_source_workflow"
        ),
    )
    op.create_index(
        "ix_workflow_store_listings_author",
        "workflow_store_listings",
        ["author_user_id"],
    )
    op.create_index(
        "ix_workflow_store_listings_status_created",
        "workflow_store_listings",
        ["status", "created_at"],
    )

    op.create_table(
        "workflow_store_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version_n", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "listing_id", "version_n", name="uq_workflow_store_versions_listing_n"
        ),
    )
    op.create_index(
        "ix_workflow_store_versions_listing",
        "workflow_store_versions",
        ["listing_id"],
    )

    op.create_table(
        "workflow_store_installs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "listing_id", name="uq_workflow_store_installs_user_listing"
        ),
    )
    op.create_index(
        "ix_workflow_store_installs_user", "workflow_store_installs", ["user_id"]
    )
    op.create_index(
        "ix_workflow_store_installs_listing",
        "workflow_store_installs",
        ["listing_id"],
    )
    op.create_index(
        "ix_workflow_store_installs_workflow",
        "workflow_store_installs",
        ["workflow_id"],
    )

    op.create_table(
        "workflow_store_reports",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "listing_id", name="uq_workflow_store_reports_user_listing"
        ),
    )
    op.create_index(
        "ix_workflow_store_reports_listing",
        "workflow_store_reports",
        ["listing_id"],
    )
    op.create_index(
        "ix_workflow_store_reports_created",
        "workflow_store_reports",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_store_reports_created", table_name="workflow_store_reports")
    op.drop_index("ix_workflow_store_reports_listing", table_name="workflow_store_reports")
    op.drop_table("workflow_store_reports")
    op.drop_index(
        "ix_workflow_store_installs_workflow", table_name="workflow_store_installs"
    )
    op.drop_index(
        "ix_workflow_store_installs_listing", table_name="workflow_store_installs"
    )
    op.drop_index("ix_workflow_store_installs_user", table_name="workflow_store_installs")
    op.drop_table("workflow_store_installs")
    op.drop_index(
        "ix_workflow_store_versions_listing", table_name="workflow_store_versions"
    )
    op.drop_table("workflow_store_versions")
    op.drop_index(
        "ix_workflow_store_listings_status_created",
        table_name="workflow_store_listings",
    )
    op.drop_index("ix_workflow_store_listings_author", table_name="workflow_store_listings")
    op.drop_table("workflow_store_listings")
