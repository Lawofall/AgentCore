"""Assemblies replace model profiles and per-chat tool/envelope lists.

One row in ``assemblies`` is a named way of working. Conversations store
``assembly_id``. Account-level 交代 membership is ``assembly_skills``.

Revision ID: b3e8a1c6d4f2
Revises: c8e1a4f7b2d9
Create Date: 2026-10-04
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3e8a1c6d4f2"
down_revision: str | None = "c8e1a4f7b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_list() -> sa.Column:
    return sa.Column(
        "disabled_tools",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )


def upgrade() -> None:
    op.rename_table("llm_model_profiles", "assemblies")
    op.add_column("assemblies", _json_list())
    op.add_column(
        "assemblies",
        sa.Column(
            "omitted_projections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "assemblies",
        sa.Column(
            "enabled_mcp_server_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column(
        "users",
        "default_model_profile_id",
        new_column_name="default_assembly_id",
    )
    op.alter_column(
        "conversations",
        "model_profile_id",
        new_column_name="assembly_id",
    )
    op.execute(
        """
        UPDATE assemblies AS a
        SET disabled_tools = u.disabled_tools
        FROM users AS u
        WHERE u.user_id = a.user_id
        """
    )
    op.create_table(
        "assembly_skills",
        sa.Column("assembly_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("apply_mode", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "apply_mode in ('always', 'on_demand', 'paths')",
            name="ck_assembly_skills_apply_mode",
        ),
        sa.ForeignKeyConstraint(
            ["assembly_id"], ["assemblies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("assembly_id", "document_id"),
    )
    op.create_index(
        "ix_assembly_skills_document", "assembly_skills", ["document_id"]
    )
    bind = op.get_bind()
    _materialize_virtual_pins(bind)
    _point_orphans_at_star(bind)
    _clone_divergent_chats(bind)
    op.execute(
        """
        INSERT INTO assembly_skills (assembly_id, document_id, apply_mode)
        SELECT a.id, d.id, d.apply_mode
        FROM assemblies AS a
        JOIN documents AS d ON d.user_id = a.user_id
        WHERE d.folder_id IS NULL
          AND d.deleted_at IS NULL
          AND d.role = 'rule'
          AND d.ai_maintained = false
          AND d.kind = 'document'
        """
    )
    op.drop_column("conversations", "disabled_tools")
    op.drop_column("conversations", "omitted_projections")
    op.drop_column("users", "disabled_tools")


def _materialize_virtual_pins(bind: sa.Connection) -> None:
    """System preset ids are not rows. Give each user who pinned one a real assembly."""
    from agentcore.llm.model_profiles import system_presets
    from agentcore.llm.catalog import platform_model_label

    presets = system_presets()
    if not presets:
        return
    pins = bind.execute(
        sa.text(
            """
            SELECT user_id, pin FROM (
                SELECT user_id, default_assembly_id AS pin FROM users
                WHERE default_assembly_id IS NOT NULL
                UNION
                SELECT user_id, assembly_id AS pin FROM conversations
                WHERE assembly_id IS NOT NULL
            ) AS pins
            WHERE pin NOT IN (SELECT id FROM assemblies)
            """
        )
    ).fetchall()
    for user_id, pin in pins:
        model_id = presets.get(str(pin))
        if not model_id:
            continue
        new_id = str(uuid.uuid4())
        tools = bind.execute(
            sa.text("SELECT disabled_tools FROM users WHERE user_id = :uid"),
            {"uid": user_id},
        ).scalar()
        if not isinstance(tools, str):
            tools = json.dumps(tools or [])
        bind.execute(
            sa.text(
                """
                INSERT INTO assemblies (
                    id, user_id, name, kind,
                    main_origin, main_model,
                    disabled_tools, omitted_projections, enabled_mcp_server_ids
                ) VALUES (
                    :id, :user_id, :name, 'user',
                    'platform', :model,
                    CAST(:tools AS jsonb), '[]'::jsonb, '[]'::jsonb
                )
                """
            ),
            {
                "id": new_id,
                "user_id": user_id,
                "name": platform_model_label(model_id),
                "model": model_id,
                "tools": tools if isinstance(tools, str) else "[]",
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE users
                SET default_assembly_id = :new_id
                WHERE user_id = :user_id AND default_assembly_id = :pin
                """
            ),
            {"new_id": new_id, "user_id": user_id, "pin": pin},
        )
        bind.execute(
            sa.text(
                """
                UPDATE conversations
                SET assembly_id = :new_id
                WHERE user_id = :user_id AND assembly_id = :pin
                """
            ),
            {"new_id": new_id, "user_id": user_id, "pin": pin},
        )


def _point_orphans_at_star(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE conversations AS c
            SET assembly_id = u.default_assembly_id
            FROM users AS u
            WHERE c.user_id = u.user_id
              AND u.default_assembly_id IS NOT NULL
              AND (
                c.assembly_id IS NULL
                OR c.assembly_id NOT IN (SELECT id FROM assemblies)
              )
            """
        )
    )


def _clone_divergent_chats(bind: sa.Connection) -> None:
    """A chat whose tool or envelope list differs gets its own assembly."""
    bind.execute(
        sa.text(
            """
            WITH divergent AS (
                SELECT
                    c.id AS conv_id,
                    gen_random_uuid() AS new_id,
                    a.user_id,
                    a.name,
                    a.main_origin,
                    a.main_provider_id,
                    a.main_model,
                    a.worker_origin,
                    a.worker_provider_id,
                    a.worker_model,
                    a.background_origin,
                    a.background_provider_id,
                    a.background_model,
                    a.vision_origin,
                    a.vision_provider_id,
                    a.vision_model,
                    a.reasoning_effort,
                    c.disabled_tools,
                    c.omitted_projections
                FROM conversations AS c
                JOIN assemblies AS a ON a.id = c.assembly_id
                WHERE c.disabled_tools IS DISTINCT FROM a.disabled_tools
                   OR c.omitted_projections IS DISTINCT FROM '[]'::jsonb
            ),
            inserted AS (
                INSERT INTO assemblies (
                    id, user_id, name, kind,
                    main_origin, main_provider_id, main_model,
                    worker_origin, worker_provider_id, worker_model,
                    background_origin, background_provider_id, background_model,
                    vision_origin, vision_provider_id, vision_model,
                    reasoning_effort,
                    disabled_tools, omitted_projections, enabled_mcp_server_ids
                )
                SELECT
                    new_id, user_id, name || ' · 这场', 'user',
                    main_origin, main_provider_id, main_model,
                    worker_origin, worker_provider_id, worker_model,
                    background_origin, background_provider_id, background_model,
                    vision_origin, vision_provider_id, vision_model,
                    reasoning_effort,
                    disabled_tools, omitted_projections, '[]'::jsonb
                FROM divergent
                RETURNING id
            )
            UPDATE conversations AS c
            SET assembly_id = d.new_id
            FROM divergent AS d
            WHERE c.id = d.conv_id
            """
        )
    )


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "disabled_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "disabled_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "omitted_projections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE conversations AS c
        SET disabled_tools = a.disabled_tools,
            omitted_projections = a.omitted_projections
        FROM assemblies AS a
        WHERE a.id = c.assembly_id
        """
    )
    op.execute(
        """
        UPDATE users AS u
        SET disabled_tools = a.disabled_tools
        FROM assemblies AS a
        WHERE a.id = u.default_assembly_id
        """
    )
    op.drop_index("ix_assembly_skills_document", table_name="assembly_skills")
    op.drop_table("assembly_skills")
    op.drop_column("assemblies", "enabled_mcp_server_ids")
    op.drop_column("assemblies", "omitted_projections")
    op.drop_column("assemblies", "disabled_tools")
    op.alter_column(
        "conversations", "assembly_id", new_column_name="model_profile_id"
    )
    op.alter_column(
        "users", "default_assembly_id", new_column_name="default_model_profile_id"
    )
    op.rename_table("assemblies", "llm_model_profiles")
