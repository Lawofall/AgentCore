"""Replace the 开析 search slot with DeepSeek official search.

Existing 开析 rows are removed. Their keys are not DeepSeek keys, and the
protocol is not kept as an alias. Accounts that had selected 开析 fall back
to platform SearXNG.

Revision ID: d9c4e7a2b1f6
Revises: a3c7e1b9d4f2
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9c4e7a2b1f6"
down_revision: str | None = "a3c7e1b9d4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE users
            SET search_provider_id = NULL
            WHERE search_provider_id IN (
                SELECT id FROM user_search_providers WHERE protocol = 'cleversee'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM user_search_providers WHERE protocol = 'cleversee'"))
    op.drop_constraint(
        "ck_user_search_providers_protocol",
        "user_search_providers",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_search_providers_protocol",
        "user_search_providers",
        "protocol in ('searxng', 'deepseek')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE users
            SET search_provider_id = NULL
            WHERE search_provider_id IN (
                SELECT id FROM user_search_providers WHERE protocol = 'deepseek'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM user_search_providers WHERE protocol = 'deepseek'"))
    op.drop_constraint(
        "ck_user_search_providers_protocol",
        "user_search_providers",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_search_providers_protocol",
        "user_search_providers",
        "protocol in ('searxng', 'cleversee')",
    )
