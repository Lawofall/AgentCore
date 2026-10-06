"""StorageProvider Protocol — workspace persistence (axis 3).

Deliberately separate from ``WorkspaceBackend`` (双模式工作区设计 §四, 护栏1):
the backend owns live "file + execution" on one platform; the StorageProvider
owns turn-level **persistence** — point-in-time snapshots of a workspace to
object storage, used for backup, kept versions, cross-device handoff, and
download. Cloud mode keeps files on the server disk in real time; storage only
holds snapshots (not every write).

One provider abstraction, two implementations behind a factory (project house
style — cf. ``LLMProvider`` / ``SandboxProvider``):
- ``FilesystemStorageProvider`` — snapshots under ``<data_dir>/snapshots`` (dev
  default; no external infra, fully testable).
- ``S3StorageProvider`` — any S3-compatible object store (Aliyun OSS in prod,
  MinIO in dev), so swapping vendors needs no code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


class StorageError(Exception):
    """Base for storage-provider failures."""


class SnapshotNotFound(StorageError):
    """The requested snapshot id does not exist under the given storage key."""


@dataclass(frozen=True)
class SnapshotRef:
    """A persisted point-in-time snapshot of one workspace.

    ``snapshot_id`` is time-sortable (UTC compact + short random suffix). A
    non-empty ``label`` is either a **kept version** (user-pinned name, never
    auto-pruned — including the per-key byte cap) or a **system** label
    (turn-baseline / handoff / export·merge — D+C capped + TTL'd, then the
    workspace byte cap; open handoff Diff bases and each conversation's recent
    baselines stay pinned). ``label is None`` marks an automatic
    post-turn backup.
    """

    snapshot_id: str
    label: str | None
    created_at: datetime
    size_bytes: int


class StorageProvider(Protocol):
    """Snapshot / version / restore / download for a workspace.

    ``storage_key`` is the logical location of the workspace in the object store
    (mirrors its on-disk path, e.g. ``workspaces/<user_id>/<folder_id>``); the
    caller derives it so this layer stays free of path policy. All methods are
    async; blocking I/O is offloaded to threads in the concrete providers.
    """

    async def snapshot(
        self, workspace_root: Path, storage_key: str, *, label: str | None = None
    ) -> SnapshotRef:
        """Archive ``workspace_root`` as a new snapshot under ``storage_key``."""
        ...

    async def list_snapshots(self, storage_key: str) -> list[SnapshotRef]:
        """Return snapshots under ``storage_key``, newest first."""
        ...

    async def restore(self, storage_key: str, snapshot_id: str, workspace_root: Path) -> None:
        """Extract a snapshot over ``workspace_root`` (raises ``SnapshotNotFound``)."""
        ...

    async def read_snapshot(self, storage_key: str, snapshot_id: str) -> bytes:
        """Return the snapshot archive (zip) bytes for download."""
        ...

    async def delete_snapshot(self, storage_key: str, snapshot_id: str) -> None:
        """Delete one snapshot (idempotent: missing id is not an error)."""
        ...

    async def purge(self, storage_key: str) -> None:
        """Delete every snapshot + the manifest under ``storage_key`` (idempotent).

        Used by retention cleanup (决策⑦) when a soft-deleted workspace's grace
        period ends — the whole snapshot history for that key goes, not one id.
        A missing key is not an error.
        """
        ...
