"""capability store listings / versions / installs / reports

Revision ID: c8f3a1e6b4d9
Revises: b2d9e4a7c1f8
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8f3a1e6b4d9"
down_revision: str | None = "b2d9e4a7c1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_store_listings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=False), nullable=False),
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
            name="ck_skill_store_listings_status",
        ),
        sa.UniqueConstraint("source_document_id", name="uq_skill_store_listings_source_document"),
    )
    op.create_index(
        "ix_skill_store_listings_author",
        "skill_store_listings",
        ["author_user_id"],
    )
    op.create_index(
        "ix_skill_store_listings_status_created",
        "skill_store_listings",
        ["status", "created_at"],
    )

    op.create_table(
        "skill_store_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version_n", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("listing_id", "version_n", name="uq_skill_store_versions_listing_n"),
    )
    op.create_index(
        "ix_skill_store_versions_listing",
        "skill_store_versions",
        ["listing_id"],
    )

    op.create_table(
        "skill_store_installs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=False),
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
        sa.UniqueConstraint("user_id", "listing_id", name="uq_skill_store_installs_user_listing"),
    )
    op.create_index("ix_skill_store_installs_user", "skill_store_installs", ["user_id"])
    op.create_index("ix_skill_store_installs_listing", "skill_store_installs", ["listing_id"])
    op.create_index("ix_skill_store_installs_document", "skill_store_installs", ["document_id"])

    op.create_table(
        "skill_store_reports",
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
        sa.UniqueConstraint("user_id", "listing_id", name="uq_skill_store_reports_user_listing"),
    )
    op.create_index("ix_skill_store_reports_listing", "skill_store_reports", ["listing_id"])
    op.create_index("ix_skill_store_reports_created", "skill_store_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_skill_store_reports_created", table_name="skill_store_reports")
    op.drop_index("ix_skill_store_reports_listing", table_name="skill_store_reports")
    op.drop_table("skill_store_reports")
    op.drop_index("ix_skill_store_installs_document", table_name="skill_store_installs")
    op.drop_index("ix_skill_store_installs_listing", table_name="skill_store_installs")
    op.drop_index("ix_skill_store_installs_user", table_name="skill_store_installs")
    op.drop_table("skill_store_installs")
    op.drop_index("ix_skill_store_versions_listing", table_name="skill_store_versions")
    op.drop_table("skill_store_versions")
    op.drop_index("ix_skill_store_listings_status_created", table_name="skill_store_listings")
    op.drop_index("ix_skill_store_listings_author", table_name="skill_store_listings")
    op.drop_table("skill_store_listings")
