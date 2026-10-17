"""One verified search slot per engine.

Each account keeps at most one 开析 row and one SearXNG row. ``connected`` means
the last probe succeeded. ``needs_reconnect`` means a later search failed; only
a successful connect clears it. Unverified and duplicate rows collapse here.

Revision ID: e6c2a9f4b1d8
Revises: a4c8e2b7d1f9
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6c2a9f4b1d8"
down_revision: str | None = "a4c8e2b7d1f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_search_providers",
        sa.Column("detail", sa.String(length=300), nullable=True),
    )
    _collapse_slots()
    op.drop_constraint(
        "ck_user_search_providers_status",
        "user_search_providers",
        type_="check",
    )
    op.execute(
        sa.text(
            """
            UPDATE user_search_providers
            SET detail = CASE
                    WHEN status = 'active' THEN NULL
                    ELSE '请更换密钥以完成连接'
                END,
                status = CASE
                    WHEN status = 'active' THEN 'connected'
                    ELSE 'needs_reconnect'
                END
            """
        )
    )
    op.alter_column(
        "user_search_providers",
        "status",
        server_default=sa.text("'connected'"),
    )
    op.create_check_constraint(
        "ck_user_search_providers_status",
        "user_search_providers",
        "status in ('connected', 'needs_reconnect')",
    )
    op.create_unique_constraint(
        "uq_user_search_providers_user_protocol",
        "user_search_providers",
        ["user_id", "protocol"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_user_search_providers_user_protocol",
        "user_search_providers",
        type_="unique",
    )
    op.drop_constraint(
        "ck_user_search_providers_status",
        "user_search_providers",
        type_="check",
    )
    op.execute(
        sa.text(
            """
            UPDATE user_search_providers
            SET status = CASE
                WHEN status = 'connected' THEN 'active'
                ELSE 'error'
            END
            """
        )
    )
    op.alter_column(
        "user_search_providers",
        "status",
        server_default=sa.text("'unchecked'"),
    )
    op.create_check_constraint(
        "ck_user_search_providers_status",
        "user_search_providers",
        "status in ('unchecked', 'active', 'error')",
    )
    op.drop_column("user_search_providers", "detail")


def _collapse_slots() -> None:
    """Keep one row per account and engine. Prefer the selected row, else the newest active."""
    conn = op.get_bind()
    rows = (
        conn.execute(
            sa.text(
                """
                SELECT p.id::text AS id,
                       p.user_id::text AS user_id,
                       p.protocol AS protocol,
                       p.status AS status,
                       p.updated_at AS updated_at,
                       u.search_provider_id::text AS selected_id
                FROM user_search_providers p
                LEFT JOIN users u ON u.user_id = p.user_id
                """
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault((row["user_id"], row["protocol"]), []).append(dict(row))
    for group in grouped.values():
        selected = next((item for item in group if item["id"] == item["selected_id"]), None)
        if selected is not None:
            keeper = selected
        else:
            active = [item for item in group if item["status"] == "active"]
            pool = active or group
            keeper = max(pool, key=_row_order)
        drop_ids = [item["id"] for item in group if item["id"] != keeper["id"]]
        for drop_id in drop_ids:
            conn.execute(
                sa.text("DELETE FROM user_search_providers WHERE id::text = :id"),
                {"id": drop_id},
            )
        selected_id = group[0]["selected_id"]
        if selected_id and selected_id in drop_ids:
            conn.execute(
                sa.text(
                    """
                    UPDATE users
                    SET search_provider_id = CAST(:keeper AS uuid)
                    WHERE user_id::text = :user_id
                    """
                ),
                {"keeper": keeper["id"], "user_id": group[0]["user_id"]},
            )


def _row_order(row: dict) -> tuple:
    updated = row["updated_at"]
    return (updated is not None, updated or "", row["id"])
