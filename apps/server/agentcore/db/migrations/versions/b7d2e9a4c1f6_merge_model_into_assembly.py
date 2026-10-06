"""Put model columns back on assemblies.

Revision ID: b7d2e9a4c1f6
Revises: a8c4e2b7d1f9
Create Date: 2026-10-05

A conversation pins ``assembly_id`` only. When chats on one assembly were
using different slots, each extra signature becomes its own assembly named
「原名 · 主模型」. The starred assembly keeps its id and receives the starred
slot. Unused model-slot rows are dropped.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d2e9a4c1f6"
down_revision: str | None = "a8c4e2b7d1f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLATFORM_SLOT = str(uuid.uuid5(uuid.NAMESPACE_URL, "agentcore:model-slot:platform"))
_SIG_KEYS = (
    "main_origin",
    "main_provider_id",
    "main_model",
    "worker_origin",
    "worker_provider_id",
    "worker_model",
    "background_origin",
    "background_provider_id",
    "background_model",
    "vision_origin",
    "vision_provider_id",
    "vision_model",
    "reasoning_effort",
)


def _empty_sig(model: str) -> dict[str, str | None]:
    sig = {key: None for key in _SIG_KEYS}
    sig["main_origin"] = "platform"
    sig["main_model"] = model
    return sig


def _sig_from_mapping(row: object) -> dict[str, str | None]:
    data = dict(row)  # type: ignore[arg-type]
    return {key: data.get(key) for key in _SIG_KEYS}


def _sig_key(sig: dict[str, str | None]) -> tuple:
    return tuple(sig[key] for key in _SIG_KEYS)


def upgrade() -> None:
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

    bind = op.get_bind()
    from agentcore.llm.model_profiles import (
        _capability_main_model_id,
        system_presets,
    )
    from agentcore.llm.profiles import PLATFORM_MODEL_FLASH

    platform_model = _capability_main_model_id() or PLATFORM_MODEL_FLASH
    legacy = system_presets()
    platform_sig = _empty_sig(platform_model)

    slot_rows = bind.execute(sa.text("SELECT * FROM model_slots")).mappings().all()
    slot_by_id = {row["id"]: _sig_from_mapping(row) for row in slot_rows}

    def resolve(slot_id: str | None) -> dict[str, str | None]:
        if slot_id and slot_id in slot_by_id:
            return slot_by_id[slot_id]
        if slot_id == _PLATFORM_SLOT:
            return platform_sig
        if slot_id and slot_id in legacy:
            sig = _empty_sig(legacy[slot_id])
            return sig
        return platform_sig

    users = bind.execute(
        sa.text(
            "SELECT user_id, default_assembly_id, default_model_slot_id FROM users"
        )
    ).mappings().all()
    user_by_id = {row["user_id"]: row for row in users}

    convs = bind.execute(
        sa.text(
            """
            SELECT id, user_id, assembly_id, model_slot_id
            FROM conversations
            """
        )
    ).mappings().all()

    # (assembly_id, sig_key) -> conversation ids
    groups: dict[tuple[str, tuple], list[str]] = defaultdict(list)
    for conv in convs:
        user = user_by_id.get(conv["user_id"])
        assembly_id = conv["assembly_id"] or (
            user["default_assembly_id"] if user else None
        )
        if not assembly_id:
            continue
        raw_slot = conv["model_slot_id"]
        if not raw_slot and user is not None:
            raw_slot = user["default_model_slot_id"]
        sig = resolve(raw_slot)
        groups[(assembly_id, _sig_key(sig))].append(conv["id"])

    assemblies = bind.execute(
        sa.text("SELECT id, user_id, name FROM assemblies")
    ).mappings().all()
    assembly_by_id = {row["id"]: row for row in assemblies}

    def write_sig(assembly_id: str, sig: dict[str, str | None]) -> None:
        bind.execute(
            sa.text(
                """
                UPDATE assemblies SET
                    main_origin = :main_origin,
                    main_provider_id = :main_provider_id,
                    main_model = :main_model,
                    worker_origin = :worker_origin,
                    worker_provider_id = :worker_provider_id,
                    worker_model = :worker_model,
                    background_origin = :background_origin,
                    background_provider_id = :background_provider_id,
                    background_model = :background_model,
                    vision_origin = :vision_origin,
                    vision_provider_id = :vision_provider_id,
                    vision_model = :vision_model,
                    reasoning_effort = :reasoning_effort
                WHERE id = :id
                """
            ),
            {**sig, "id": assembly_id},
        )

    def clone(assembly_id: str, sig: dict[str, str | None], conv_ids: list[str]) -> None:
        src = assembly_by_id.get(assembly_id)
        if src is None or not conv_ids:
            return
        new_id = str(uuid.uuid4())
        base = (src["name"] or "装配").strip() or "装配"
        label = (sig.get("main_model") or "模型").strip() or "模型"
        name = f"{base} · {label}"[:200]
        bind.execute(
            sa.text(
                """
                INSERT INTO assemblies (
                    id, user_id, name, kind,
                    disabled_tools, omitted_projections, enabled_mcp_server_ids,
                    omit_factory_catalog, recipe, omit_desk_rules,
                    main_origin, main_provider_id, main_model,
                    worker_origin, worker_provider_id, worker_model,
                    background_origin, background_provider_id, background_model,
                    vision_origin, vision_provider_id, vision_model,
                    reasoning_effort, created_at, updated_at
                )
                SELECT
                    :new_id, user_id, :name, kind,
                    disabled_tools, omitted_projections, enabled_mcp_server_ids,
                    omit_factory_catalog, recipe, omit_desk_rules,
                    :main_origin, :main_provider_id, :main_model,
                    :worker_origin, :worker_provider_id, :worker_model,
                    :background_origin, :background_provider_id, :background_model,
                    :vision_origin, :vision_provider_id, :vision_model,
                    :reasoning_effort, now(), now()
                FROM assemblies
                WHERE id = :old_id
                """
            ),
            {**sig, "new_id": new_id, "name": name, "old_id": assembly_id},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO assembly_skills (assembly_id, document_id, apply_mode)
                SELECT :new_id, document_id, apply_mode
                FROM assembly_skills
                WHERE assembly_id = :old_id
                """
            ),
            {"new_id": new_id, "old_id": assembly_id},
        )
        for conv_id in conv_ids:
            bind.execute(
                sa.text(
                    "UPDATE conversations SET assembly_id = :new_id WHERE id = :cid"
                ),
                {"new_id": new_id, "cid": conv_id},
            )

    grouped_by_assembly: dict[str, list[tuple[tuple, list[str]]]] = defaultdict(list)
    for (assembly_id, key), conv_ids in groups.items():
        grouped_by_assembly[assembly_id].append((key, conv_ids))

    touched: set[str] = set()
    for assembly_id, buckets in grouped_by_assembly.items():
        src = assembly_by_id.get(assembly_id)
        if src is None:
            continue
        user = user_by_id.get(src["user_id"])
        star_assembly = user["default_assembly_id"] if user else None
        star_sig = (
            resolve(user["default_model_slot_id"]) if user is not None else platform_sig
        )
        if assembly_id == star_assembly:
            kept = _sig_key(star_sig)
            write_sig(assembly_id, star_sig)
        else:
            buckets_sorted = sorted(buckets, key=lambda item: (-len(item[1]), item[0]))
            kept = buckets_sorted[0][0]
            write_sig(assembly_id, dict(zip(_SIG_KEYS, kept, strict=True)))
        touched.add(assembly_id)
        for key, conv_ids in buckets:
            if key == kept:
                continue
            clone(assembly_id, dict(zip(_SIG_KEYS, key, strict=True)), conv_ids)

    for row in assemblies:
        if row["id"] in touched:
            continue
        user = user_by_id.get(row["user_id"])
        sig = (
            resolve(user["default_model_slot_id"])
            if user is not None and user["default_model_slot_id"]
            else platform_sig
        )
        write_sig(row["id"], sig)

    bind.execute(
        sa.text(
            """
            UPDATE assemblies
            SET main_origin = 'platform', main_model = :model
            WHERE main_model IS NULL OR main_origin IS NULL
            """
        ),
        {"model": platform_model},
    )
    op.alter_column("assemblies", "main_origin", nullable=False)
    op.alter_column("assemblies", "main_model", nullable=False)
    for name, sql in (
        (
            "ck_llm_model_profiles_main_origin",
            "main_origin in ('platform', 'byok')",
        ),
        (
            "ck_llm_model_profiles_worker_origin",
            "worker_origin is null or worker_origin in ('platform', 'byok')",
        ),
        (
            "ck_llm_model_profiles_background_origin",
            "background_origin is null or background_origin in ('platform', 'byok')",
        ),
        (
            "ck_llm_model_profiles_vision_origin",
            "vision_origin is null or vision_origin in ('platform', 'byok')",
        ),
    ):
        op.create_check_constraint(name, "assemblies", sql)

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


def downgrade() -> None:
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
    )
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
            SELECT
                id, user_id, name, kind,
                main_origin, main_provider_id, main_model,
                worker_origin, worker_provider_id, worker_model,
                background_origin, background_provider_id, background_model,
                vision_origin, vision_provider_id, vision_model,
                reasoning_effort, created_at, updated_at
            FROM assemblies
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
    op.execute(
        sa.text(
            """
            UPDATE conversations SET model_slot_id = assembly_id
            WHERE assembly_id IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE users SET default_model_slot_id = default_assembly_id
            WHERE default_assembly_id IS NOT NULL
            """
        )
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
