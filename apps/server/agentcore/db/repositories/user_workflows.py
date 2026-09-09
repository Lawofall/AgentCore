"""User workflow repository (账户级 CRUD + 一口钟)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.core.types import new_id
from agentcore.db.models.user_workflows import UserWorkflow
from agentcore.db.repositories._base import strip_nul
from agentcore.workflows.source import TURN_SOURCE_KIND


class UserWorkflowRepository:
    """Owner-scoped workflow CRUD. ``version`` bumps on every successful update.

    ``source`` 只在 :meth:`create` 写一次（服务端权威元数据，见
    :mod:`agentcore.workflows.source`）——:meth:`update` 不收这个参数，所以没有任何路径
    能在事后改掉一条工作流的来源。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        definition: dict,
        description: str | None = None,
        source: Mapping[str, Any] | None = None,
    ) -> UserWorkflow:
        row = UserWorkflow(
            id=new_id(),
            user_id=user_id,
            name=strip_nul(name) or "未命名工作流",
            description=strip_nul(description) if description else None,
            definition=dict(definition or {}),
            source=dict(source) if source else None,
            version=1,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def find_by_turn_source(
        self, *, user_id: str, conversation_id: str, message_id: str
    ) -> UserWorkflow | None:
        """同一轮已固化过的工作流，没有则 ``None``。

        走 ``ix_user_workflows_turn_source``。历史 ``kind=turn`` 行仍认（抽槽只认固化来源）；
        不是唯一索引，命中多条时取最近改过的那条。
        """
        result = await self._session.execute(
            select(UserWorkflow)
            .where(
                UserWorkflow.user_id == user_id,
                UserWorkflow.source["kind"].astext == TURN_SOURCE_KIND,
                UserWorkflow.source["conversation_id"].astext == conversation_id,
                UserWorkflow.source["message_id"].astext == message_id,
            )
            .order_by(UserWorkflow.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(
        self, workflow_id: str, *, user_id: str | None = None
    ) -> UserWorkflow | None:
        conditions = [UserWorkflow.id == workflow_id]
        if user_id is not None:
            conditions.append(UserWorkflow.user_id == user_id)
        result = await self._session.execute(select(UserWorkflow).where(*conditions))
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: str) -> Sequence[UserWorkflow]:
        result = await self._session.execute(
            select(UserWorkflow)
            .where(UserWorkflow.user_id == user_id)
            .order_by(UserWorkflow.updated_at.desc())
        )
        return result.scalars().all()

    async def update(
        self,
        workflow_id: str,
        *,
        user_id: str,
        name: str | None = None,
        description: str | None | object = ...,
        definition: dict | None = None,
    ) -> UserWorkflow | None:
        row = await self.get_by_id(workflow_id, user_id=user_id)
        if row is None:
            return None
        bumped = False
        if name is not None:
            row.name = strip_nul(name) or row.name
            bumped = True
        if description is not ...:
            if description is None:
                row.description = None
            else:
                row.description = strip_nul(str(description)) or None
            bumped = True
        if definition is not None:
            # 整份覆盖是 definition 的正常用法（画布保存）；``source`` 在列上，不受影响。
            row.definition = dict(definition)
            bumped = True
        if bumped:
            row.version = int(row.version or 1) + 1
            row.updated_at = datetime.now(UTC)
            await self._session.commit()
            await self._session.refresh(row)
        return row

    async def delete(self, workflow_id: str, *, user_id: str) -> bool:
        row = await self.get_by_id(workflow_id, user_id=user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def get_by_webhook_id(self, webhook_id: str) -> UserWorkflow | None:
        result = await self._session.execute(
            select(UserWorkflow).where(
                UserWorkflow.trigger_webhook_id == webhook_id,
                UserWorkflow.trigger_kind == "webhook",
            )
        )
        return result.scalar_one_or_none()

    async def replace_trigger(
        self,
        workflow_id: str,
        *,
        user_id: str,
        **fields: object,
    ) -> UserWorkflow | None:
        """Overwrite trigger columns. Does not bump ``version`` (canvas stays)."""
        row = await self.get_by_id(workflow_id, user_id=user_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def clear_trigger(self, workflow_id: str, *, user_id: str) -> UserWorkflow | None:
        return await self.replace_trigger(
            workflow_id,
            user_id=user_id,
            trigger_kind=None,
            trigger_enabled=False,
            trigger_folder_id=None,
            trigger_cron=None,
            trigger_webhook_id=None,
            trigger_webhook_secret_hash=None,
            trigger_next_run_at=None,
            trigger_last_run_at=None,
            last_trigger_error=None,
            trigger_lease_owner=None,
            trigger_lease_until=None,
        )

    async def set_last_trigger_error(self, workflow_id: str, *, error: str | None) -> None:
        cleaned = strip_nul(error)[:4000] if error else None
        await self._session.execute(
            update(UserWorkflow)
            .where(UserWorkflow.id == workflow_id)
            .values(last_trigger_error=cleaned)
        )
        await self._session.commit()

    async def advance_trigger_next_run(
        self, workflow_id: str, *, next_run_at: datetime
    ) -> None:
        await self._session.execute(
            update(UserWorkflow)
            .where(UserWorkflow.id == workflow_id)
            .values(trigger_next_run_at=next_run_at)
        )
        await self._session.commit()

    async def claim_due_triggers(
        self,
        *,
        now: datetime,
        owner: str,
        lease_seconds: int,
        limit: int = 10,
    ) -> list[UserWorkflow]:
        """Atomically claim up to ``limit`` due enabled schedule triggers."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        lease_until = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=UTC)
        candidates = (
            (
                await self._session.execute(
                    select(UserWorkflow.id)
                    .where(
                        UserWorkflow.trigger_kind == "schedule",
                        UserWorkflow.trigger_enabled.is_(True),
                        UserWorkflow.trigger_next_run_at.is_not(None),
                        UserWorkflow.trigger_next_run_at <= now,
                        or_(
                            UserWorkflow.trigger_lease_until.is_(None),
                            UserWorkflow.trigger_lease_until < now,
                        ),
                    )
                    .order_by(UserWorkflow.trigger_next_run_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[UserWorkflow] = []
        for workflow_id in candidates:
            result = await self._session.execute(
                update(UserWorkflow)
                .where(
                    UserWorkflow.id == workflow_id,
                    UserWorkflow.trigger_kind == "schedule",
                    UserWorkflow.trigger_enabled.is_(True),
                    UserWorkflow.trigger_next_run_at.is_not(None),
                    UserWorkflow.trigger_next_run_at <= now,
                    or_(
                        UserWorkflow.trigger_lease_until.is_(None),
                        UserWorkflow.trigger_lease_until < now,
                    ),
                )
                .values(
                    trigger_lease_owner=owner,
                    trigger_lease_until=lease_until,
                    trigger_last_run_at=now,
                )
                .returning(UserWorkflow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                claimed.append(row)
        await self._session.commit()
        return claimed

    async def claim_trigger_dispatch(
        self,
        workflow_id: str,
        *,
        owner: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> UserWorkflow | None:
        """Claim the trigger lease for webhook dispatch. ``None`` if still held."""
        if now is None:
            now = datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        lease_until = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=UTC)
        result = await self._session.execute(
            update(UserWorkflow)
            .where(
                UserWorkflow.id == workflow_id,
                or_(
                    UserWorkflow.trigger_lease_until.is_(None),
                    UserWorkflow.trigger_lease_until < now,
                ),
            )
            .values(
                trigger_lease_owner=owner,
                trigger_lease_until=lease_until,
                trigger_last_run_at=now,
            )
            .returning(UserWorkflow)
        )
        row = result.scalar_one_or_none()
        await self._session.commit()
        return row

    async def clear_trigger_lease(
        self, workflow_id: str, *, owner: str | None = None
    ) -> None:
        conditions = [UserWorkflow.id == workflow_id]
        if owner is not None:
            conditions.append(UserWorkflow.trigger_lease_owner == owner)
        await self._session.execute(
            update(UserWorkflow)
            .where(*conditions)
            .values(trigger_lease_owner=None, trigger_lease_until=None)
        )
        await self._session.commit()


def is_lease_free(*, lease_until: datetime | None, now: datetime) -> bool:
    """True when the trigger lease is absent or expired."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if lease_until is None:
        return True
    if lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=UTC)
    return lease_until < now


def is_trigger_claimable(
    *,
    enabled: bool,
    next_run_at: datetime | None,
    lease_until: datetime | None,
    now: datetime,
    trigger_kind: str | None = "schedule",
) -> bool:
    """Whether a schedule trigger may be claimed by the poll (lease anti-double-run)."""
    if trigger_kind != "schedule":
        return False
    if not enabled:
        return False
    if next_run_at is None:
        return False
    if next_run_at.tzinfo is None:
        next_run_at = next_run_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if next_run_at > now:
        return False
    return is_lease_free(lease_until=lease_until, now=now)
