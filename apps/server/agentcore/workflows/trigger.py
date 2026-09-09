"""Fire a workflow clock / webhook via ``dispatch_workflow_run``.

One graph, one trigger. Each fire opens a new ``mode=workflow`` conversation
(account-default axes, slot defaults). Missed fires write ``last_trigger_error``
only — no inbox, no second job table.
"""

from __future__ import annotations

from uuid import uuid4

from agentcore.config import settings
from agentcore.core.errors import ConflictError
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import FolderRepository, UserWorkflowRepository
from agentcore.workflows.runner import dispatch_workflow_run

logger = get_logger(__name__)

_LEASE_BUSY_ERROR = "上一次代跑仍在进行中，本次触发未执行（未自动补跑）"
_DISPATCH_FAILED_ERROR = "本次定时代跑未能启动，本周期已跳过（未自动补跑）：{error}"


async def fire_workflow_trigger(
    *,
    workflow_id: str,
    user_id: str,
    note: str | None = None,
    lease_owner: str | None = None,
) -> str:
    """Claim the trigger lease (unless already held) and dispatch a new run.

    Returns the new conversation id. A held lease writes ``last_trigger_error``
    and raises :class:`ConflictError` (HTTP 409) — the event is dropped, not queued.
    """
    claimed_owner = lease_owner
    async with async_session_factory() as session:
        repo = UserWorkflowRepository(session)
        row = await repo.get_by_id(workflow_id, user_id=user_id)
        if row is None:
            raise LookupError("工作流不存在")
        kind = getattr(row, "trigger_kind", None)
        if not kind:
            raise LookupError("工作流未配置触发")
        if not row.trigger_enabled:
            raise ValueError("触发已停用")
        folder_id = row.trigger_folder_id
        if not folder_id:
            raise ValueError("触发未绑定工作区")
        folder = await FolderRepository(session).get_by_id(folder_id, user_id=user_id)
        if folder is None:
            raise LookupError("工作区不存在")
        if folder.local_root_id:
            raise ValueError("触发仅支持云工作区")
        definition = dict(row.definition or {})
        version = int(row.version or 1)
        name = row.name

        if claimed_owner is None:
            claimed_owner = f"dispatch-{uuid4().hex[:12]}"
            claimed = await repo.claim_trigger_dispatch(
                workflow_id,
                owner=claimed_owner,
                lease_seconds=settings.workflow_trigger_lease_seconds,
            )
            if claimed is None:
                await repo.set_last_trigger_error(workflow_id, error=_LEASE_BUSY_ERROR)
                logger.info(
                    "workflow.trigger.fire_skipped_busy",
                    workflow_id=workflow_id,
                )
                raise ConflictError("工作流正在执行中，请稍后再试")

    try:
        conv_id = await dispatch_workflow_run(
            user_id=user_id,
            workflow_id=workflow_id,
            workflow_version=version,
            definition=definition,
            folder_id=folder_id,
            note=note,
            conversation_id=None,
            workflow_name=name,
            permission_axes=None,
            slot_values=None,
            trigger_lease_owner=claimed_owner,
        )
    except Exception as e:
        async with async_session_factory() as session:
            repo = UserWorkflowRepository(session)
            await repo.set_last_trigger_error(
                workflow_id, error=_DISPATCH_FAILED_ERROR.format(error=e)
            )
            await repo.clear_trigger_lease(workflow_id, owner=claimed_owner)
        raise

    async with async_session_factory() as session:
        await UserWorkflowRepository(session).set_last_trigger_error(
            workflow_id, error=None
        )
    return conv_id
