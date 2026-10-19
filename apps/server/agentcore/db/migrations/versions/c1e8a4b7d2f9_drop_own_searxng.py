"""Drop the own-SearXNG search slot.

Accounts that had selected own SearXNG fall back to platform SearXNG.
The protocol is not kept as an alias.

Revision ID: c1e8a4b7d2f9
Revises: d9c4e7a2b1f6
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1e8a4b7d2f9"
down_revision: str | None = "d9c4e7a2b1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE users
            SET search_provider_id = NULL
            WHERE search_provider_id IN (
                SELECT id FROM user_search_providers WHERE protocol = 'searxng'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM user_search_providers WHERE protocol = 'searxng'"))
    op.drop_constraint(
        "ck_user_search_providers_protocol",
        "user_search_providers",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_search_providers_protocol",
        "user_search_providers",
        "protocol in ('deepseek')",
    )


def downgrade() -> None:
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
