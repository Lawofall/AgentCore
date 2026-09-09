"""User workflows (账户级可保存的团队拆法定义 + 一口钟)."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class UserWorkflow(Base):
    """Account-scoped workflow definition (画布 JSON + version + optional trigger)."""

    __tablename__ = "user_workflows"
    __table_args__ = (
        Index("ix_user_workflows_user_created", "user_id", "created_at"),
        # 「同一轮再点一次保存」的幂等查询（原本是拉用户全部工作流再内存扫）。
        Index(
            "ix_user_workflows_turn_source",
            "user_id",
            text("(source ->> 'conversation_id')"),
            text("(source ->> 'message_id')"),
            postgresql_where=text("source ->> 'kind' = 'turn'"),
        ),
        # Schedule XOR webhook XOR none — see migration e7c2b9d4a1f6.
        CheckConstraint(
            """
            (
              trigger_kind IS NULL
              AND trigger_cron IS NULL
              AND trigger_webhook_id IS NULL
              AND trigger_folder_id IS NULL
            )
            OR (
              trigger_kind = 'schedule'
              AND trigger_cron IS NOT NULL
              AND trigger_webhook_id IS NULL
              AND trigger_folder_id IS NOT NULL
            )
            OR (
              trigger_kind = 'webhook'
              AND trigger_webhook_id IS NOT NULL
              AND trigger_cron IS NULL
              AND trigger_next_run_at IS NULL
              AND trigger_folder_id IS NOT NULL
            )
            """,
            name="ck_user_workflows_trigger_xor",
        ),
        Index(
            "ix_user_workflows_trigger_due",
            "trigger_kind",
            "trigger_enabled",
            "trigger_next_run_at",
        ),
        Index("ix_user_workflows_webhook_id", "trigger_webhook_id", unique=True),
    )

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用户的画布内容：客户端整份覆盖，服务端只校验不重建（agentcore.workflows.definition）。
    definition: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # 服务端权威的来源标记，创建时写一次、之后不改；客户端只读
    # （为什么不放 definition 里 → agentcore.workflows.source）。
    source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    # Clock: schedule XOR webhook XOR none. Changing these does not pin a conversation.
    trigger_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trigger_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    trigger_folder_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    trigger_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_webhook_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), nullable=True
    )
    trigger_webhook_secret_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    trigger_next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trigger_last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_trigger_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
