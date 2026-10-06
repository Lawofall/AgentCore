"""Workspace snapshot service — conversation → workspace → StorageProvider.

Thin orchestration over ``locate.py`` (path policy) and ``storage`` (the
configured provider). Both the post-turn auto-backup (``conversation/service``)
and the snapshot API routes go through here, so "which conversation → which
storage key / root" lives in exactly one place. Path policy never leaks into the
storage layer; provider choice never leaks into the routes.

A′: ``create_snapshot`` / ``restore_snapshot`` / ``restore_into_workspace`` hold
``workspace_lock`` once at this sink — callers must not nest the same key.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.storage import SnapshotRef, StorageProvider, build_storage_provider
from agentcore.storage.filesystem import FilesystemStorageProvider
from agentcore.workspace.layout import CONV_SEGMENT, WORKSPACES_SEGMENT
from agentcore.workspace.locate import resolve_workspace_root, workspace_storage_key
from agentcore.workspace.locks import workspace_lock
from agentcore.workspace.snapshot_kinds import (
    byte_cap_prune_ids,
    recent_baseline_pin_ids,
    system_prune_ids,
)

logger = get_logger(__name__)


def _resolve_root(
    *, user_id: str, folder_rel_path: str | None, conversation_id: str
) -> Path:
    """The workspace directory to snapshot / restore into.

    Note the asymmetry, and that it is the point: the storage **key** below stays
    id-derived so a rename never orphans snapshot history, while the *directory*
    follows ``folders.rel_path``. Callers pass the placement in (routes resolve it
    via ``folders.placement``) rather than having this module query for it — a
    snapshot service that needs a database to find a path is a snapshot service
    that cannot be unit-tested.
    """
    return resolve_workspace_root(
        user_id=user_id,
        folder_rel_path=folder_rel_path,
        conversation_id=conversation_id,
    )

# Open handoff jobs still need Diff (§7.6): not applied / not discarded.
# Includes ``succeeded`` — Diff window must keep ``base_snapshot_id`` (do not unpin on succeed).
_OPEN_HANDOFF_STATUSES = ("pending", "running", "succeeded", "failed")


async def collect_pinned_system_snapshot_ids(
    *, user_id: str, folder_id: str | None, conversation_id: str
) -> set[str]:
    """Snapshot ids that must survive D+C and the byte cap for this storage key.

    Folder conversations share one key — collect across all members of the folder.
    Pins: open ``handoff_jobs.base_snapshot_id``, plus each conversation's newest
    baselines that are still inside the system TTL (count cap ∧ age). Older
    ``messages.baseline_snapshot_id`` values are not pins.
    """
    from agentcore.db.base import async_session_factory
    from agentcore.db.models import Conversation, HandoffJob, Message

    async with async_session_factory() as session:
        if folder_id:
            conv_rows = await session.execute(
                select(Conversation.id).where(
                    Conversation.user_id == user_id,
                    Conversation.folder_id == folder_id,
                )
            )
            conv_ids = list(conv_rows.scalars().all())
        else:
            conv_ids = [conversation_id]
        if not conv_ids:
            return set()

        pinned: set[str] = set()
        handoff_rows = await session.execute(
            select(HandoffJob.base_snapshot_id).where(
                HandoffJob.user_id == user_id,
                HandoffJob.source_conversation_id.in_(conv_ids),
                HandoffJob.status.in_(_OPEN_HANDOFF_STATUSES),
            )
        )
        pinned.update(handoff_rows.scalars().all())

        baseline_rows = await session.execute(
            select(
                Message.conversation_id,
                Message.baseline_snapshot_id,
                Message.created_at,
            ).where(
                Message.conversation_id.in_(conv_ids),
                Message.baseline_snapshot_id.is_not(None),
            )
        )
        pinned.update(
            recent_baseline_pin_ids(
                [
                    (conversation_id, snapshot_id, created_at)
                    for conversation_id, snapshot_id, created_at in baseline_rows.all()
                    if snapshot_id
                ],
                baseline_max=settings.workspace_system_baseline_snapshot_max,
                max_age=timedelta(days=settings.workspace_system_snapshot_retention_days),
            )
        )
        return pinned


async def _enforce_auto_cap(provider: StorageProvider, key: str, keep: int) -> None:
    """Prune the oldest automatic (unlabeled) snapshots beyond ``keep`` (决策⑥).

    Only auto backups are capped here; system labels use :func:`_enforce_system_caps`,
    and user-named kept versions are never pruned by either path.
    ``list_snapshots`` returns newest-first, so the autos past ``keep`` are the
    tail. Best-effort: a prune failure must never fail the snapshot it follows.
    """
    if keep <= 0:
        return
    autos = [s for s in await provider.list_snapshots(key) if not s.label]
    for stale in autos[keep:]:
        await provider.delete_snapshot(key, stale.snapshot_id)


async def _enforce_system_caps(
    provider: StorageProvider,
    key: str,
    *,
    user_id: str,
    folder_id: str | None,
    conversation_id: str,
) -> None:
    """Prune system snapshots by count cap ∧ TTL (D+C); never touch user pins.

    Skips ids still referenced by open handoff Diff / turn baselines (storage-key
    scoped). Best-effort: failures log and must not fail the create that triggered prune.
    """
    try:
        pinned = await collect_pinned_system_snapshot_ids(
            user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
        )
        refs = await provider.list_snapshots(key)
        stale_ids = system_prune_ids(
            refs,
            baseline_max=settings.workspace_system_baseline_snapshot_max,
            other_max=settings.workspace_system_other_snapshot_max,
            max_age=timedelta(days=settings.workspace_system_snapshot_retention_days),
            pinned_ids=pinned,
        )
        for snapshot_id in stale_ids:
            await provider.delete_snapshot(key, snapshot_id)
    except Exception as e:
        logger.warning(
            "workspace.system_snapshot_prune_failed",
            storage_key=key,
            error=str(e),
        )


async def _enforce_byte_cap(
    provider: StorageProvider,
    key: str,
    *,
    user_id: str,
    folder_id: str | None,
    conversation_id: str,
) -> None:
    """Prune oldest evictable snapshots until total zip bytes fit the cap.

    Superimposed on count + TTL; never touches user-named kept versions or
    ids pinned by open handoff Diff / turn baselines. Best-effort: failures
    log and must not fail the create that triggered prune.
    """
    try:
        pinned = await collect_pinned_system_snapshot_ids(
            user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
        )
        refs = await provider.list_snapshots(key)
        stale_ids = byte_cap_prune_ids(
            refs,
            max_bytes=settings.workspace_snapshot_max_bytes,
            pinned_ids=pinned,
        )
        for snapshot_id in stale_ids:
            await provider.delete_snapshot(key, snapshot_id)
    except Exception as e:
        logger.warning(
            "workspace.system_snapshot_prune_failed",
            storage_key=key,
            error=str(e),
        )


async def create_snapshot(
    *,
    user_id: str,
    folder_id: str | None,
    folder_rel_path: str | None,
    conversation_id: str,
    label: str | None = None,
) -> SnapshotRef:
    """Snapshot a conversation's workspace. A ``label`` marks a kept version.

    Auto snapshots (no ``label``) are capped to ``workspace_auto_snapshot_max``
    (决策⑥). User-named kept versions are never pruned. System labels
    (turn-baseline / handoff / export·merge) are capped + TTL'd (D+C). Pins that
    survive that pass are open handoff Diff bases and each conversation's recent
    baselines (same count ∧ TTL). After those, a per-key
    ``workspace_snapshot_max_bytes`` cap evicts oldest evictable leftovers
    (same kept / pin exemptions).

    Holds ``workspace_lock`` for the manifest RMW (A′ sink).
    """
    key = workspace_storage_key(
        user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
    )
    root = _resolve_root(
        user_id=user_id,
        folder_rel_path=folder_rel_path,
        conversation_id=conversation_id,
    )
    provider = build_storage_provider()
    async with workspace_lock(key):
        ref = await provider.snapshot(root, key, label=label)
        if label is None:
            await _enforce_auto_cap(provider, key, settings.workspace_auto_snapshot_max)
        await _enforce_system_caps(
            provider,
            key,
            user_id=user_id,
            folder_id=folder_id,
            conversation_id=conversation_id,
        )
        await _enforce_byte_cap(
            provider,
            key,
            user_id=user_id,
            folder_id=folder_id,
            conversation_id=conversation_id,
        )
        return ref


def parse_snapshot_storage_key(key: str) -> tuple[str, str | None, str] | None:
    """Split a snapshot storage key into ``(user_id, folder_id, conversation_id)``.

    Folder keys are ``workspaces/<user>/<folder>`` (conversation id empty).
    Bare-chat keys are ``workspaces/<user>/conv/<conversation>``. Anything else
    is not a workspace snapshot key.
    """
    parts = [part for part in key.split("/") if part]
    if len(parts) == 3 and parts[0] == WORKSPACES_SEGMENT and parts[2] != CONV_SEGMENT:
        return parts[1], parts[2], ""
    if len(parts) == 4 and parts[0] == WORKSPACES_SEGMENT and parts[2] == CONV_SEGMENT:
        return parts[1], None, parts[3]
    return None


async def sweep_snapshot_retention() -> int:
    """Apply auto / system / byte caps to every filesystem snapshot key.

    ``create_snapshot`` only prunes the key it just wrote. Quiet workspaces
    would otherwise keep every historical turn zip. Returns how many snapshot
    ids were removed. Non-filesystem providers are skipped (no key listing).
    """
    provider = build_storage_provider()
    if not isinstance(provider, FilesystemStorageProvider):
        return 0
    keys = await asyncio.to_thread(provider.list_storage_keys)
    removed = 0
    for key in keys:
        parsed = parse_snapshot_storage_key(key)
        if parsed is None:
            continue
        user_id, folder_id, conversation_id = parsed
        try:
            async with workspace_lock(key):
                before = {ref.snapshot_id for ref in await provider.list_snapshots(key)}
                await _enforce_auto_cap(provider, key, settings.workspace_auto_snapshot_max)
                await _enforce_system_caps(
                    provider,
                    key,
                    user_id=user_id,
                    folder_id=folder_id,
                    conversation_id=conversation_id,
                )
                await _enforce_byte_cap(
                    provider,
                    key,
                    user_id=user_id,
                    folder_id=folder_id,
                    conversation_id=conversation_id,
                )
                after = {ref.snapshot_id for ref in await provider.list_snapshots(key)}
                removed += len(before - after)
        except Exception as e:
            logger.warning(
                "retention.snapshot_caps_key_failed",
                storage_key=key,
                error=str(e),
            )
    if removed:
        logger.info(
            "retention.snapshot_caps_pruned",
            removed=removed,
            keys=len(keys),
        )
    return removed


async def purge_snapshots(*, user_id: str, folder_id: str | None, conversation_id: str) -> None:
    """Delete the entire snapshot history for a conversation's workspace (决策⑦).

    Caller must already hold ``workspace_lock`` for ``key`` when purging together
    with an on-disk rmtree (retention) — this path does not re-acquire.
    """
    key = workspace_storage_key(
        user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
    )
    await build_storage_provider().purge(key)


async def list_snapshots(
    *, user_id: str, folder_id: str | None, conversation_id: str
) -> list[SnapshotRef]:
    """List a conversation's workspace snapshots, newest first."""
    key = workspace_storage_key(
        user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
    )
    return await build_storage_provider().list_snapshots(key)


async def restore_snapshot(
    *,
    user_id: str,
    folder_id: str | None,
    folder_rel_path: str | None,
    conversation_id: str,
    snapshot_id: str,
) -> None:
    """Extract a snapshot over the conversation's workspace (``SnapshotNotFound``).

    Holds ``workspace_lock`` for the duration (A′ sink).
    """
    key = workspace_storage_key(
        user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
    )
    root = _resolve_root(
        user_id=user_id,
        folder_rel_path=folder_rel_path,
        conversation_id=conversation_id,
    )
    async with workspace_lock(key):
        await build_storage_provider().restore(key, snapshot_id, root)


async def read_snapshot(
    *, user_id: str, folder_id: str | None, conversation_id: str, snapshot_id: str
) -> bytes:
    """Return a snapshot archive (zip) bytes for download (``SnapshotNotFound``)."""
    key = workspace_storage_key(
        user_id=user_id, folder_id=folder_id, conversation_id=conversation_id
    )
    return await build_storage_provider().read_snapshot(key, snapshot_id)


async def restore_into_workspace(
    *,
    source_user_id: str,
    source_folder_id: str | None,
    source_conversation_id: str,
    snapshot_id: str,
    dest_user_id: str,
    dest_folder_id: str | None,
    dest_folder_rel_path: str | None,
    dest_conversation_id: str,
) -> None:
    """Restore one conversation's snapshot into *another* conversation's workspace.

    The seeding step of a local→云 handoff (双模式工作区 P2e / e2): the source
    (local) conversation's base snapshot is extracted into the destination (hidden
    cloud job) conversation's freshly-resolved workspace root, so the cloud team
    runs on the user's real files. Distinct from :func:`restore_snapshot`, which
    restores a conversation over its *own* root. Raises ``SnapshotNotFound`` if the
    snapshot id is missing under the source key.

    Holds ``workspace_lock`` on the **destination** key (A′ sink).
    """
    source_key = workspace_storage_key(
        user_id=source_user_id,
        folder_id=source_folder_id,
        conversation_id=source_conversation_id,
    )
    dest_key = workspace_storage_key(
        user_id=dest_user_id,
        folder_id=dest_folder_id,
        conversation_id=dest_conversation_id,
    )
    dest_root = _resolve_root(
        user_id=dest_user_id,
        folder_rel_path=dest_folder_rel_path,
        conversation_id=dest_conversation_id,
    )
    async with workspace_lock(dest_key):
        await build_storage_provider().restore(source_key, snapshot_id, dest_root)
