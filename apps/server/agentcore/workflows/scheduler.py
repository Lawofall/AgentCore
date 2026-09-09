"""In-process workflow-trigger scheduler (lifespan DB poll + lease)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.errors import is_schema_error
from agentcore.db.repositories.user_workflows import UserWorkflowRepository
from agentcore.workflows.schedule import next_run_after
from agentcore.workflows.trigger import fire_workflow_trigger

logger = get_logger(__name__)

_DISPATCH_FAILED_ERROR = "本次定时代跑未能启动，本周期已跳过（未自动补跑）：{error}"


async def poll_due_workflow_triggers(*, owner: str | None = None) -> int:
    """Claim due schedule triggers and spawn runs. Returns the number claimed."""
    owner_id = owner or f"sched-{uuid4().hex[:12]}"
    now = datetime.now(UTC)
    lease_seconds = settings.workflow_trigger_lease_seconds
    limit = settings.workflow_trigger_poll_batch_limit

    async with async_session_factory() as session:
        claimed = await UserWorkflowRepository(session).claim_due_triggers(
            now=now,
            owner=owner_id,
            lease_seconds=lease_seconds,
            limit=limit,
        )

    spawned = 0
    for row in claimed:
        try:
            try:
                if not row.trigger_cron:
                    raise ValueError("schedule trigger missing cron")
                nxt = next_run_after(row.trigger_cron, now)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "workflow.trigger.schedule_advance_failed",
                    workflow_id=row.id,
                    error=str(e),
                )
                nxt = None
            if nxt is not None:
                async with async_session_factory() as session:
                    await UserWorkflowRepository(session).advance_trigger_next_run(
                        row.id, next_run_at=nxt
                    )
            await fire_workflow_trigger(
                workflow_id=row.id,
                user_id=row.user_id,
                lease_owner=owner_id,
            )
            spawned += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not stall the poll
            logger.error(
                "workflow.trigger.dispatch_failed",
                workflow_id=row.id,
                error=str(e),
                exc_info=True,
            )
            async with async_session_factory() as session:
                repo = UserWorkflowRepository(session)
                await repo.clear_trigger_lease(row.id, owner=owner_id)
                await repo.set_last_trigger_error(
                    row.id, error=_DISPATCH_FAILED_ERROR.format(error=e)
                )
    return spawned


async def workflow_trigger_scheduler_loop() -> None:
    """Poll ``trigger_next_run_at`` forever on the configured interval."""
    interval = settings.workflow_trigger_poll_interval_seconds
    while True:
        try:
            n = await poll_due_workflow_triggers()
            if n:
                logger.info("workflow.trigger.poll_spawned", count=n)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if is_schema_error(e):
                logger.error("workflow.trigger.poll_failed", error=str(e))
            else:
                logger.warning("workflow.trigger.poll_failed", error=str(e))
        await asyncio.sleep(interval)
