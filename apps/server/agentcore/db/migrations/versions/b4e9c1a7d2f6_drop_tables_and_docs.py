"""drop tables and docs (创作表格 / 文档活稿硬切)

内测无兼容层：人侧 /tables /docs、/v1/tables /v1/docs、table_* 产品与
CSV 灌活表一并卸。用户规则 documents 与 docs_export 不动。

Revision ID: b4e9c1a7d2f6
Revises: c9f2b6d1a8e4
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4e9c1a7d2f6"
down_revision: str | None = "c9f2b6d1a8e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS table_rows, table_views, tables, doc_shares, docs CASCADE"
    )
    op.execute("ALTER TABLE turn_queue_items DROP COLUMN IF EXISTS table_selection")


def downgrade() -> None:
    raise NotImplementedError("内测硬切，不回滚 tables/docs")
