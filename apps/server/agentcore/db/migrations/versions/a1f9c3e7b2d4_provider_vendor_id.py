"""Persist BYOK vendor identity for protocol dispatch.

Revision ID: a1f9c3e7b2d4
Revises: d4e8b2c7a1f5
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f9c3e7b2d4"
down_revision: str | None = "d4e8b2c7a1f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VENDOR_URLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("deepseek", ("https://api.deepseek.com", "https://api.deepseek.com/v1")),
    ("openai", ("https://api.openai.com/v1",)),
    ("moonshot", ("https://api.moonshot.cn/v1", "https://api.moonshot.ai/v1")),
    ("zhipu", ("https://open.bigmodel.cn/api/paas/v4",)),
    ("doubao", ("https://ark.cn-beijing.volces.com/api/v3",)),
    (
        "hy",
        (
            "https://tokenhub.tencentmaas.com/v1",
            "https://tokenhub.tencentmaas.cn/v1",
            "https://tokenhub-intl.tencentmaas.com/v1",
            "https://tokenhub-intl.tencentmaas.cn/v1",
        ),
    ),
    ("openrouter", ("https://openrouter.ai/api/v1",)),
    ("opencode_zen", ("https://opencode.ai/zen/v1",)),
    ("opencode_go", ("https://opencode.ai/zen/go/v1",)),
)


def upgrade() -> None:
    op.add_column(
        "user_llm_providers",
        sa.Column(
            "vendor_id",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'custom'"),
        ),
    )
    bind = op.get_bind()
    for vendor_id, urls in _VENDOR_URLS:
        for url in urls:
            bind.execute(
                sa.text(
                    "UPDATE user_llm_providers "
                    "SET vendor_id = :vendor "
                    "WHERE rtrim(lower(base_url), '/') = :url"
                ),
                {"vendor": vendor_id, "url": url},
            )


def downgrade() -> None:
    op.drop_column("user_llm_providers", "vendor_id")
