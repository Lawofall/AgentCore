"""Retention cleanup for soft-deleted workspaces (决策⑦: 与软删除对齐).

项目即工作区: aged soft-deleted projects purge their shared ``folder:<id>`` space;
aged soft-deleted 裸聊 conversations purge ``conv:<id>`` scratch. Project-member
conversations never own an independent disk root — deleting the row does not
rmtree the shared project space.

Handoff cloud hosts (§7.6): apply/discard soft-delete immediately; open finished
jobs are soft-deleted only after ``workspace_retention_days`` from ``finished_at``
(so Diff stays available in-window — never early-delete on succeed). Physical
purge then follows the same soft-delete grace as any other conversation.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.errors import is_schema_error
from agentcore.db.models import Conversation
from agentcore.db.repositories import (
    ConversationRepository,
    DocumentRepository,
    FolderRepository,
    HandoffJobRepository,
)
from agentcore.folders.unbind import clear_folder_session_pointers
from agentcore.workspace.handoff_reclaim import soft_delete_job_host
from agentcore.workspace.indexing.registry import drop_index_registry
from agentcore.workspace.locate import (
    folder_tombstone_path,
    workspace_internal_root,
    workspace_root_path,
    workspace_storage_key,
)
from agentcore.workspace.locks import workspace_lock
from agentcore.workspace.snapshots import purge_snapshots
from agentcore.workspace.stage_dirs import INDEX_ZONE_NAME, internal_zone_path

logger = get_logger(__name__)


def retention_cutoff() -> datetime:
    """The instant a soft-delete becomes due for hard purge (``deleted_at <= cutoff``).

    Single source for「什么时候真没了」: the sweep below selects against it, and the
    project recycle bin uses it to refuse restores it could not honour. UTC-aware —
    the columns are ``TIMESTAMPTZ`` and asyncpg binds a naive datetime as UTC, so a
    naive local ``now()`` would shift the window by the dev box's offset.
    """
    return datetime.now(UTC) - timedelta(days=settings.workspace_retention_days)


async def purge_folder_space(
    *, user_id: str, folder_id: str, folder_rel_path: str | None
) -> None:
    """Delete a folder's directory, hidden zones and snapshots.

    The tombstone area and the hidden zones are keyed by the stable id, so they are
    always safe to remove. ``folder_rel_path`` is the visible-tree slot and must be
    passed **only for a folder that is still live** (彻底删除): once soft-deleted,
    that slot is released for reuse, and a stale one may well belong to a different
    folder by now.
    """
    key = workspace_storage_key(user_id=user_id, folder_id=folder_id, conversation_id="")
    index_dir = internal_zone_path(
        INDEX_ZONE_NAME,
        root=Path(),
        internal_root=workspace_internal_root(
            user_id=user_id, folder_id=folder_id, conversation_id=""
        ),
    )
    async with workspace_lock(key):
        # Release the BM25 handle first — Windows refuses to rmtree an open SQLite file.
        await drop_index_registry(index_dir)
        targets = [
            folder_tombstone_path(user_id=user_id, folder_id=folder_id),
            workspace_internal_root(
                user_id=user_id, folder_id=folder_id, conversation_id=""
            ),
        ]
        if folder_rel_path:
            targets.append(
                workspace_root_path(
                    user_id=user_id,
                    folder_rel_path=folder_rel_path,
                    conversation_id="",
                )
            )
        for target in targets:
            shutil.rmtree(target, ignore_errors=True)
        await purge_snapshots(user_id=user_id, folder_id=folder_id, conversation_id="")


async def _purge_conversation_space(
    *, user_id: str, conversation_id: str, folder_id: str | None
) -> None:
    """Delete a 裸聊's scratch; project members leave the shared folder space alone."""
    if folder_id:
        return
    key = workspace_storage_key(user_id=user_id, folder_id=None, conversation_id=conversation_id)
    internal_root = workspace_internal_root(
        user_id=user_id, folder_id=None, conversation_id=conversation_id
    )
    async with workspace_lock(key):
        await drop_index_registry(
            internal_zone_path(INDEX_ZONE_NAME, root=Path(), internal_root=internal_root)
        )
        shutil.rmtree(
            workspace_root_path(
                user_id=user_id, folder_rel_path=None, conversation_id=conversation_id
            ),
            ignore_errors=True,
        )
        shutil.rmtree(internal_root, ignore_errors=True)
        await purge_snapshots(user_id=user_id, folder_id=None, conversation_id=conversation_id)


async def _age_open_handoff_hosts(*, before: datetime, limit: int) -> int:
    """Soft-delete open (unapplied/undiscarded) handoff hosts past the Diff window."""
    async with async_session_factory() as session:
        jobs = await HandoffJobRepository(session).list_open_past_retention(
            before=before, limit=limit
        )
    aged = 0
    for job in jobs:
        try:
            if await soft_delete_job_host(
                user_id=job.user_id, job_conversation_id=job.job_conversation_id
            ):
                aged += 1
                logger.info(
                    "retention.handoff_host_aged",
                    job_id=job.id,
                    job_conversation_id=job.job_conversation_id,
                )
        except Exception as e:
            logger.warning(
                "retention.handoff_host_age_failed",
                job_id=job.id,
                error=str(e),
            )
    return aged


async def run_retention_sweep() -> dict[str, int]:
    """Purge soft-deleted folders/conversations past the retention period once."""
    if not settings.workspace_retention_enabled:
        return {"folders": 0, "conversations": 0, "handoff_hosts_aged": 0}

    before = retention_cutoff()
    limit = settings.workspace_retention_batch_limit

    # Open handoff hosts first: soft-delete so the conversation sweep below (or
    # a later pass) can hard-purge after the usual grace. Status stays
    # succeeded/failed — aging ≠ user discard; Diff may still work until purge.
    handoff_aged = await _age_open_handoff_hosts(before=before, limit=limit)

    async with async_session_factory() as session:
        folders = await FolderRepository(session).list_purgeable(before=before, limit=limit)
    purged_folders = 0
    for folder in folders:
        try:
            # ``folder_rel_path=None`` on purpose: these rows are all soft-deleted,
            # so their directory已经搬进墓碑区，而 ``rel_path`` 留的是删除那一刻的槽位。
            # 那个槽位在删除后立刻被释放，30 天里很可能已经被用户新建的同名文件夹占
            # 走——照着它 rmtree 等于删掉一个活着的文件夹。
            await purge_folder_space(
                user_id=folder.user_id,
                folder_id=folder.id,
                folder_rel_path=None,
            )
        except Exception as e:
            logger.warning("retention.folder_purge_failed", folder_id=folder.id, error=str(e))
            continue
        async with async_session_factory() as session:
            # Clear membership on any remaining (archived) conversations before
            # the folder row disappears. Soft-pointers (auto desk / boards) via
            # the shared fan-out — no user scope (global sweep). The archive
            # provenance flag goes with it: nothing can restore this project now,
            # so a lingering「因项目删除而归档」mark would name a folder that is gone.
            # ``updated_at`` self-assigns — housekeeping must not call ``touch_activity``
            # and scramble the「已归档」recency order.
            await session.execute(
                update(Conversation)
                .where(Conversation.folder_id == folder.id)
                .values(
                    folder_id=None,
                    archived_by_folder_delete=False,
                    updated_at=Conversation.updated_at,
                )
            )
            await clear_folder_session_pointers(session, folder_id=folder.id)
            await DocumentRepository(session).hard_delete_for_folders(
                folder.user_id, [folder.id], commit=False
            )
            await session.commit()
            await FolderRepository(session).hard_delete(folder.id)
        purged_folders += 1

    async with async_session_factory() as session:
        conversations = await ConversationRepository(session).list_purgeable(
            before=before, limit=limit
        )
    purged_convs = 0
    for conv in conversations:
        try:
            await _purge_conversation_space(
                user_id=conv.user_id,
                conversation_id=conv.id,
                folder_id=conv.folder_id,
            )
        except Exception as e:
            logger.warning(
                "retention.conversation_purge_failed",
                conversation_id=conv.id,
                error=str(e),
            )
            continue
        async with async_session_factory() as session:
            await ConversationRepository(session).hard_delete(conv.id)
        purged_convs += 1

    return {
        "folders": purged_folders,
        "conversations": purged_convs,
        "handoff_hosts_aged": handoff_aged,
    }


async def retention_loop() -> None:
    """Run :func:`run_retention_sweep` forever on the configured interval."""
    interval = settings.workspace_retention_sweep_interval_seconds
    while True:
        try:
            result = await run_retention_sweep()
            if result["folders"] or result["conversations"] or result["handoff_hosts_aged"]:
                logger.info("retention.sweep_purged", **result)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log = logger.error if is_schema_error(e) else logger.warning
            log("retention.sweep_failed", error=str(e))
        await asyncio.sleep(interval)
