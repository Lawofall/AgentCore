"""FilesystemStorageProvider — snapshots on the local disk (dev default).

Stores each snapshot as ``<base>/<storage_key>/<snapshot_id>.zip`` with a
sibling ``manifest.json``. No external infra, so it is the zero-config dev
default and the target for hermetic tests. Blocking file I/O is offloaded to a
thread to honor the async contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from agentcore.storage._archive import (
    MANIFEST_NAME,
    manifest_from_bytes,
    manifest_to_bytes,
    new_snapshot_id,
    unzip_into,
    zip_dir,
)
from agentcore.storage.protocol import SnapshotNotFound, SnapshotRef


class FilesystemStorageProvider:
    """Snapshot store backed by a local directory tree."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _key_dir(self, storage_key: str) -> Path:
        return self._base / storage_key

    @staticmethod
    def _read(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Write ``data`` via temp file + ``os.replace`` so readers never see a partial manifest."""
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_manifest_", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    async def snapshot(
        self, workspace_root: Path, storage_key: str, *, label: str | None = None
    ) -> SnapshotRef:
        return await asyncio.to_thread(self._snapshot_sync, workspace_root, storage_key, label)

    def _snapshot_sync(
        self, workspace_root: Path, storage_key: str, label: str | None
    ) -> SnapshotRef:
        key_dir = self._key_dir(storage_key)
        key_dir.mkdir(parents=True, exist_ok=True)
        data = zip_dir(workspace_root)
        snapshot_id = new_snapshot_id()
        (key_dir / f"{snapshot_id}.zip").write_bytes(data)
        ref = SnapshotRef(
            snapshot_id=snapshot_id,
            label=label,
            created_at=datetime.now(UTC),
            size_bytes=len(data),
        )
        refs = manifest_from_bytes(self._read(key_dir / MANIFEST_NAME))
        refs.insert(0, ref)
        self._atomic_write(key_dir / MANIFEST_NAME, manifest_to_bytes(refs))
        return ref

    async def list_snapshots(self, storage_key: str) -> list[SnapshotRef]:
        return await asyncio.to_thread(self._list_sync, storage_key)

    def _list_sync(self, storage_key: str) -> list[SnapshotRef]:
        refs = manifest_from_bytes(self._read(self._key_dir(storage_key) / MANIFEST_NAME))
        return sorted(refs, key=lambda r: r.created_at, reverse=True)

    async def restore(self, storage_key: str, snapshot_id: str, workspace_root: Path) -> None:
        await asyncio.to_thread(self._restore_sync, storage_key, snapshot_id, workspace_root)

    def _restore_sync(self, storage_key: str, snapshot_id: str, workspace_root: Path) -> None:
        data = self._read(self._key_dir(storage_key) / f"{snapshot_id}.zip")
        if data is None:
            raise SnapshotNotFound(snapshot_id)
        unzip_into(data, workspace_root)

    async def read_snapshot(self, storage_key: str, snapshot_id: str) -> bytes:
        return await asyncio.to_thread(self._read_snapshot_sync, storage_key, snapshot_id)

    def _read_snapshot_sync(self, storage_key: str, snapshot_id: str) -> bytes:
        data = self._read(self._key_dir(storage_key) / f"{snapshot_id}.zip")
        if data is None:
            raise SnapshotNotFound(snapshot_id)
        return data

    async def delete_snapshot(self, storage_key: str, snapshot_id: str) -> None:
        await asyncio.to_thread(self._delete_sync, storage_key, snapshot_id)

    def _delete_sync(self, storage_key: str, snapshot_id: str) -> None:
        key_dir = self._key_dir(storage_key)
        (key_dir / f"{snapshot_id}.zip").unlink(missing_ok=True)
        refs = manifest_from_bytes(self._read(key_dir / MANIFEST_NAME))
        kept = [r for r in refs if r.snapshot_id != snapshot_id]
        if len(kept) != len(refs):
            self._atomic_write(key_dir / MANIFEST_NAME, manifest_to_bytes(kept))

    async def purge(self, storage_key: str) -> None:
        await asyncio.to_thread(self._purge_sync, storage_key)

    def _purge_sync(self, storage_key: str) -> None:
        # Remove the whole key directory (all .zip snapshots + manifest). The
        # storage tree mirrors the workspace key, so this drops exactly one
        # workspace's snapshot history. ignore_errors keeps it idempotent.
        shutil.rmtree(self._key_dir(storage_key), ignore_errors=True)

    def list_storage_keys(self) -> list[str]:
        """Storage keys that have a manifest, relative to the snapshot root.

        Quiet keys are never visited by ``create_snapshot``, so retention has to
        enumerate them. Keys are POSIX (``workspaces/<user>/<folder>``).
        """
        if not self._base.is_dir():
            return []
        keys: list[str] = []
        for manifest in self._base.rglob(MANIFEST_NAME):
            if not manifest.is_file():
                continue
            keys.append(manifest.parent.relative_to(self._base).as_posix())
        return keys
