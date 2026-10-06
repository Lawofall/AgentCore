"""Split model slots off assemblies.

Revision ID: a8c4e2b7d1f9
Revises: c3a9e7b1d4f8
Create Date: 2026-10-05

One assembly is a recipe (tools, envelope, prompts, plugs). Model slots are
their own rows. Identical slot signatures on one account collapse to one row.
Conversations keep ``assembly_id`` and gain ``model_slot_id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8c4e2b7d1f9"
down_revision: str | None = "c3a9e7b1d4f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLOT_EQ = """
    s.user_id = a.user_id
    AND s.main_origin = a.main_origin
    AND s.main_model = a.main_model
    AND s.main_provider_id IS NOT DISTINCT FROM a.main_provider_id
    AND s.worker_origin IS NOT DISTINCT FROM a.worker_origin
    AND s.worker_model IS NOT DISTINCT FROM a.worker_model
    AND s.worker_provider_id IS NOT DISTINCT FROM a.worker_provider_id
    AND s.background_origin IS NOT DISTINCT FROM a.background_origin
    AND s.background_model IS NOT DISTINCT FROM a.background_model
    AND s.background_provider_id IS NOT DISTINCT FROM a.background_provider_id
    AND s.vision_origin IS NOT DISTINCT FROM a.vision_origin
    AND s.vision_model IS NOT DISTINCT FROM a.vision_model
    AND s.vision_provider_id IS NOT DISTINCT FROM a.vision_provider_id
    AND s.reasoning_effort IS NOT DISTINCT FROM a.reasoning_effort
"""


def upgrade() -> None:
    op.create_table(
        "model_slots",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="user"),
        sa.Column("main_origin", sa.String(20), nullable=False),
        sa.Column("main_provider_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("main_model", sa.String(200), nullable=False),
        sa.Column("worker_origin", sa.String(20), nullable=True),
        sa.Column("worker_provider_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("worker_model", sa.String(200), nullable=True),
        sa.Column("background_origin", sa.String(20), nullable=True),
        sa.Column(
            "background_provider_id", postgresql.UUID(as_uuid=False), nullable=True
        ),
        sa.Column("background_model", sa.String(200), nullable=True),
        sa.Column("vision_origin", sa.String(20), nullable=True),
        sa.Column("vision_provider_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("vision_model", sa.String(200), nullable=True),
        sa.Column("reasoning_effort", sa.String(32), nullable=True),
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
            "kind in ('user', 'implicit')", name="ck_model_slots_kind"
        ),
        sa.CheckConstraint(
            "main_origin in ('platform', 'byok')", name="ck_model_slots_main_origin"
        ),
        sa.CheckConstraint(
            "worker_origin is null or worker_origin in ('platform', 'byok')",
            name="ck_model_slots_worker_origin",
        ),
        sa.CheckConstraint(
            "background_origin is null or background_origin in ('platform', 'byok')",
            name="ck_model_slots_background_origin",
        ),
        sa.CheckConstraint(
            "vision_origin is null or vision_origin in ('platform', 'byok')",
            name="ck_model_slots_vision_origin",
        ),
    )
    op.create_index("ix_model_slots_user", "model_slots", ["user_id"])

    op.execute(
        sa.text(
            """
            INSERT INTO model_slots (
                id, user_id, name, kind,
                main_origin, main_provider_id, main_model,
                worker_origin, worker_provider_id, worker_model,
                background_origin, background_provider_id, background_model,
                vision_origin, vision_provider_id, vision_model,
                reasoning_effort, created_at, updated_at
            )
            SELECT DISTINCT ON (
                assemblies.user_id,
                assemblies.main_origin,
                assemblies.main_provider_id,
                assemblies.main_model,
                assemblies.worker_origin,
                assemblies.worker_provider_id,
                assemblies.worker_model,
                assemblies.background_origin,
                assemblies.background_provider_id,
                assemblies.background_model,
                assemblies.vision_origin,
                assemblies.vision_provider_id,
                assemblies.vision_model,
                assemblies.reasoning_effort
            )
                gen_random_uuid(),
                assemblies.user_id,
                CASE
                    WHEN assemblies.name NOT IN ('对话', '上网', '完整')
                        THEN assemblies.name
                    ELSE assemblies.main_model
                END,
                CASE
                    WHEN assemblies.kind = 'implicit' THEN 'implicit'
                    ELSE 'user'
                END,
                assemblies.main_origin,
                assemblies.main_provider_id,
                assemblies.main_model,
                assemblies.worker_origin,
                assemblies.worker_provider_id,
                assemblies.worker_model,
                assemblies.background_origin,
                assemblies.background_provider_id,
                assemblies.background_model,
                assemblies.vision_origin,
                assemblies.vision_provider_id,
                assemblies.vision_model,
                assemblies.reasoning_effort,
                now(), now()
            FROM assemblies
            ORDER BY
                assemblies.user_id,
                assemblies.main_origin,
                assemblies.main_provider_id,
                assemblies.main_model,
                assemblies.worker_origin,
                assemblies.worker_provider_id,
                assemblies.worker_model,
                assemblies.background_origin,
                assemblies.background_provider_id,
                assemblies.background_model,
                assemblies.vision_origin,
                assemblies.vision_provider_id,
                assemblies.vision_model,
                assemblies.reasoning_effort,
                CASE WHEN assemblies.kind = 'user' THEN 0 ELSE 1 END,
                CASE
                    WHEN assemblies.name NOT IN ('对话', '上网', '完整') THEN 0
                    ELSE 1
                END,
                assemblies.created_at
            """
        )
    )

    op.add_column(
        "conversations",
        sa.Column("model_slot_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "default_model_slot_id", postgresql.UUID(as_uuid=False), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_conversations_model_slot_id",
        "conversations",
        "model_slots",
        ["model_slot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_users_default_model_slot_id",
        "users",
        "model_slots",
        ["default_model_slot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        sa.text(
            f"""
            UPDATE conversations AS c
            SET model_slot_id = s.id
            FROM assemblies AS a
            JOIN model_slots AS s ON {_SLOT_EQ}
            WHERE c.assembly_id = a.id
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE users AS u
            SET default_model_slot_id = s.id
            FROM assemblies AS a
            JOIN model_slots AS s ON {_SLOT_EQ}
            WHERE u.default_assembly_id = a.id
            """
        )
    )

    for name in (
        "ck_llm_model_profiles_main_origin",
        "ck_llm_model_profiles_worker_origin",
        "ck_llm_model_profiles_background_origin",
        "ck_llm_model_profiles_vision_origin",
    ):
        op.drop_constraint(name, "assemblies", type_="check")
    for column in (
        "reasoning_effort",
        "vision_model",
        "vision_provider_id",
        "vision_origin",
        "background_model",
        "background_provider_id",
        "background_origin",
        "worker_model",
        "worker_provider_id",
        "worker_origin",
        "main_model",
        "main_provider_id",
        "main_origin",
    ):
        op.drop_column("assemblies", column)


def downgrade() -> None:
    for column, type_ in (
        ("main_origin", sa.String(20)),
        ("main_provider_id", postgresql.UUID(as_uuid=False)),
        ("main_model", sa.String(200)),
        ("worker_origin", sa.String(20)),
        ("worker_provider_id", postgresql.UUID(as_uuid=False)),
        ("worker_model", sa.String(200)),
        ("background_origin", sa.String(20)),
        ("background_provider_id", postgresql.UUID(as_uuid=False)),
        ("background_model", sa.String(200)),
        ("vision_origin", sa.String(20)),
        ("vision_provider_id", postgresql.UUID(as_uuid=False)),
        ("vision_model", sa.String(200)),
        ("reasoning_effort", sa.String(32)),
    ):
        op.add_column("assemblies", sa.Column(column, type_, nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE assemblies AS a
            SET
                main_origin = s.main_origin,
                main_provider_id = s.main_provider_id,
                main_model = s.main_model,
                worker_origin = s.worker_origin,
                worker_provider_id = s.worker_provider_id,
                worker_model = s.worker_model,
                background_origin = s.background_origin,
                background_provider_id = s.background_provider_id,
                background_model = s.background_model,
                vision_origin = s.vision_origin,
                vision_provider_id = s.vision_provider_id,
                vision_model = s.vision_model,
                reasoning_effort = s.reasoning_effort
            FROM conversations AS c
            JOIN model_slots AS s ON s.id = c.model_slot_id
            WHERE c.assembly_id = a.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE assemblies AS a
            SET
                main_origin = COALESCE(a.main_origin, 'platform'),
                main_model = COALESCE(a.main_model, 'glm-5.2')
            WHERE a.main_model IS NULL
            """
        )
    )
    op.alter_column("assemblies", "main_origin", nullable=False)
    op.alter_column("assemblies", "main_model", nullable=False)
    op.drop_constraint(
        "fk_users_default_model_slot_id", "users", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_conversations_model_slot_id", "conversations", type_="foreignkey"
    )
    op.drop_column("users", "default_model_slot_id")
    op.drop_column("conversations", "model_slot_id")
    op.drop_index("ix_model_slots_user", table_name="model_slots")
    op.drop_table("model_slots")
