"""Drop pasted DeepSeek search keys.

Search uses the official BYOK service key only. Accounts that had a pasted
search key fall back to platform SearXNG.

Revision ID: f3a1d8c6e2b4
Revises: e2b9c4a7d1f8
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a1d8c6e2b4"
down_revision: str | None = "e2b9c4a7d1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE users
            SET search_provider_id = NULL
            WHERE search_provider_id IN (
                SELECT id FROM user_search_providers WHERE source = 'key'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM user_search_providers WHERE source = 'key'"))
    op.drop_constraint(
        "ck_user_search_providers_source",
        "user_search_providers",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_search_providers_source",
        "user_search_providers",
        "source in ('provider')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_search_providers_source",
        "user_search_providers",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_search_providers_source",
        "user_search_providers",
        "source in ('key', 'provider')",
    )
