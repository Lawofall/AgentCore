"""Tests for the snapshot service (conversation → workspace → StorageProvider).

End-to-end over the filesystem backend: resolves a conversation's workspace via
``locate``, snapshots it, lists/reads/restores it. ``data_dir`` and the storage
backend are redirected to ``tmp_path`` so nothing touches the real ./data tree;
the lru-cached factory is cleared around each test so the redirect takes effect.
"""

from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.storage import SnapshotNotFound
from agentcore.storage.factory import build_storage_provider
from agentcore.workspace.locate import resolve_workspace_root
from agentcore.workspace.snapshots import (
    create_snapshot,
    list_snapshots,
    parse_snapshot_storage_key,
    purge_snapshots,
    read_snapshot,
    restore_snapshot,
    sweep_snapshot_retention,
)


@pytest.fixture
def fs_storage(tmp_path: Path, monkeypatch):
    """Redirect data_dir + force the filesystem backend, with a clean factory cache."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    # Unit FS tests have no DB pin rows; empty pins keep D+C prune assertions valid.
    async def _no_pins(**_kwargs):
        return set()

    monkeypatch.setattr(
        "agentcore.workspace.snapshots.collect_pinned_system_snapshot_ids",
        _no_pins,
    )
    build_storage_provider.cache_clear()
    try:
        yield
    finally:
        build_storage_provider.cache_clear()


async def test_create_then_list_and_download(fs_storage):
    root = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    (root / "report.md").write_text("done", encoding="utf-8")

    ref = await create_snapshot(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1")
    listed = await list_snapshots(user_id="u1", folder_id="f1", conversation_id="c1")
    assert [r.snapshot_id for r in listed] == [ref.snapshot_id]

    data = await read_snapshot(
        user_id="u1", folder_id="f1", conversation_id="c1", snapshot_id=ref.snapshot_id
    )
    assert data[:2] == b"PK"


async def test_restore_recovers_deleted_file(fs_storage):
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="c9")
    (root / "keep.txt").write_text("v1", encoding="utf-8")
    ref = await create_snapshot(user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="c9")

    # User (or agent) wipes the file after the snapshot.
    (root / "keep.txt").unlink()
    assert not (root / "keep.txt").exists()

    await restore_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="c9", snapshot_id=ref.snapshot_id
    )
    assert (root / "keep.txt").read_text(encoding="utf-8") == "v1"


async def test_project_snapshots_are_shared(fs_storage):
    """Sibling conversations in the same project share snapshot history."""
    root = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    (root / "shared.txt").write_text("x", encoding="utf-8")
    ref = await create_snapshot(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1")

    listed_c2 = await list_snapshots(user_id="u1", folder_id="f1", conversation_id="c2")
    assert ref.snapshot_id in {r.snapshot_id for r in listed_c2}


async def test_label_is_preserved(fs_storage):
    root = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    (root / "a.txt").write_text("x", encoding="utf-8")
    await create_snapshot(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", label="milestone")
    listed = await list_snapshots(user_id="u1", folder_id="f1", conversation_id="c1")
    assert listed[0].label == "milestone"


async def test_read_unknown_snapshot_raises(fs_storage):
    resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    with pytest.raises(SnapshotNotFound):
        await read_snapshot(
            user_id="u1", folder_id="f1", conversation_id="c1", snapshot_id="missing"
        )


async def test_auto_snapshot_cap_prunes_oldest(fs_storage, monkeypatch):
    monkeypatch.setattr(settings, "workspace_auto_snapshot_max", 3)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="cap")
    (root / "f.txt").write_text("x", encoding="utf-8")

    refs = [
        await create_snapshot(user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="cap") for _ in range(5)
    ]
    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="cap")
    # Only the 3 newest auto snapshots survive; the 2 oldest were pruned.
    assert {r.snapshot_id for r in listed} == {r.snapshot_id for r in refs[-3:]}


async def test_labeled_snapshots_survive_cap(fs_storage, monkeypatch):
    monkeypatch.setattr(settings, "workspace_auto_snapshot_max", 1)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="kept")
    (root / "f.txt").write_text("x", encoding="utf-8")

    kept = await create_snapshot(user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="kept", label="v1")
    # Several auto snapshots that would blow past the cap of 1.
    for _ in range(3):
        await create_snapshot(user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="kept")

    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="kept")
    labels = [r.snapshot_id for r in listed if r.label == "v1"]
    autos = [r for r in listed if not r.label]
    assert kept.snapshot_id in labels  # the kept version is never pruned
    assert len(autos) == 1  # autos still capped


async def test_system_baseline_cap_prunes_oldest(fs_storage, monkeypatch):
    monkeypatch.setattr(settings, "workspace_system_baseline_snapshot_max", 2)
    monkeypatch.setattr(settings, "workspace_system_other_snapshot_max", 10)
    monkeypatch.setattr(settings, "workspace_system_snapshot_retention_days", 30)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="base")
    (root / "f.txt").write_text("x", encoding="utf-8")

    refs = [
        await create_snapshot(
            user_id="u1",
            folder_id=None, folder_rel_path=None,
            conversation_id="base",
            label=f"turn-baseline:m{i}",
        )
        for i in range(4)
    ]
    pin = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="base", label="发版前"
    )
    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="base")
    ids = {r.snapshot_id for r in listed}
    assert pin.snapshot_id in ids
    assert refs[-1].snapshot_id in ids
    assert refs[-2].snapshot_id in ids
    assert refs[0].snapshot_id not in ids
    assert refs[1].snapshot_id not in ids


async def test_system_export_cap_prunes_oldest(fs_storage, monkeypatch):
    monkeypatch.setattr(settings, "workspace_system_baseline_snapshot_max", 5)
    monkeypatch.setattr(settings, "workspace_system_other_snapshot_max", 2)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="exp")
    (root / "f.txt").write_text("x", encoding="utf-8")

    a = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="exp", label="导出"
    )
    b = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="exp", label="导出到本地"
    )
    c = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="exp", label="合回到本机"
    )
    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="exp")
    ids = {r.snapshot_id for r in listed}
    assert b.snapshot_id in ids
    assert c.snapshot_id in ids
    assert a.snapshot_id not in ids


async def test_system_prune_keeps_pinned_over_cap(fs_storage, monkeypatch):
    """Pinned baseline / handoff ids survive D+C even when past the count cap."""
    monkeypatch.setattr(settings, "workspace_system_baseline_snapshot_max", 1)
    monkeypatch.setattr(settings, "workspace_system_other_snapshot_max", 1)

    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="pin")
    (root / "f.txt").write_text("x", encoding="utf-8")

    old_base = await create_snapshot(
        user_id="u1",
        folder_id=None, folder_rel_path=None,
        conversation_id="pin",
        label="turn-baseline:old",
    )
    old_handoff = await create_snapshot(
        user_id="u1",
        folder_id=None, folder_rel_path=None,
        conversation_id="pin",
        label="handoff:2026-01-01T00:00:00Z",
    )

    async def _pins(**_kwargs):
        return {old_base.snapshot_id, old_handoff.snapshot_id}

    monkeypatch.setattr(
        "agentcore.workspace.snapshots.collect_pinned_system_snapshot_ids",
        _pins,
    )

    await create_snapshot(
        user_id="u1",
        folder_id=None, folder_rel_path=None,
        conversation_id="pin",
        label="turn-baseline:new",
    )
    await create_snapshot(
        user_id="u1",
        folder_id=None, folder_rel_path=None,
        conversation_id="pin",
        label="handoff:2026-01-02T00:00:00Z",
    )

    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="pin")
    ids = {r.snapshot_id for r in listed}
    assert old_base.snapshot_id in ids
    assert old_handoff.snapshot_id in ids


async def test_byte_cap_prunes_oldest(fs_storage, monkeypatch):
    monkeypatch.setattr(settings, "workspace_auto_snapshot_max", 10)
    monkeypatch.setattr(settings, "workspace_system_baseline_snapshot_max", 10)
    monkeypatch.setattr(settings, "workspace_system_other_snapshot_max", 10)
    monkeypatch.setattr(settings, "workspace_snapshot_max_bytes", 10**12)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="bcap")
    (root / "f.txt").write_text("x", encoding="utf-8")

    first = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="bcap"
    )
    monkeypatch.setattr(
        settings, "workspace_snapshot_max_bytes", first.size_bytes * 2 + first.size_bytes // 2
    )
    second = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="bcap"
    )
    third = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="bcap"
    )
    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="bcap")
    ids = {r.snapshot_id for r in listed}
    assert first.snapshot_id not in ids
    assert second.snapshot_id in ids
    assert third.snapshot_id in ids


async def test_byte_cap_keeps_labeled(fs_storage, monkeypatch):
    monkeypatch.setattr(settings, "workspace_auto_snapshot_max", 10)
    monkeypatch.setattr(settings, "workspace_snapshot_max_bytes", 10**12)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="bkept")
    (root / "f.txt").write_text("x", encoding="utf-8")

    kept = await create_snapshot(
        user_id="u1",
        folder_id=None,
        folder_rel_path=None,
        conversation_id="bkept",
        label="v1",
    )
    monkeypatch.setattr(settings, "workspace_snapshot_max_bytes", kept.size_bytes)
    for _ in range(3):
        await create_snapshot(
            user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="bkept"
        )
    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="bkept")
    assert kept.snapshot_id in {r.snapshot_id for r in listed}
    assert any(r.snapshot_id == kept.snapshot_id and r.label == "v1" for r in listed)


async def test_byte_cap_keeps_pinned(fs_storage, monkeypatch):
    monkeypatch.setattr(settings, "workspace_auto_snapshot_max", 10)
    monkeypatch.setattr(settings, "workspace_system_baseline_snapshot_max", 10)
    monkeypatch.setattr(settings, "workspace_snapshot_max_bytes", 10**12)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="bpin")
    (root / "f.txt").write_text("x", encoding="utf-8")

    pinned = await create_snapshot(
        user_id="u1",
        folder_id=None,
        folder_rel_path=None,
        conversation_id="bpin",
        label="turn-baseline:old",
    )

    async def _pins(**_kwargs):
        return {pinned.snapshot_id}

    monkeypatch.setattr(
        "agentcore.workspace.snapshots.collect_pinned_system_snapshot_ids",
        _pins,
    )
    monkeypatch.setattr(
        settings, "workspace_snapshot_max_bytes", pinned.size_bytes * 2 + pinned.size_bytes // 2
    )
    mid = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="bpin"
    )
    newest = await create_snapshot(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="bpin"
    )
    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="bpin")
    ids = {r.snapshot_id for r in listed}
    assert pinned.snapshot_id in ids
    assert newest.snapshot_id in ids
    assert mid.snapshot_id not in ids


async def test_sweep_applies_baseline_cap_without_a_new_snapshot(fs_storage, monkeypatch):
    """Quiet keys are not waiting on the next create — the sweep enforces D+C."""
    monkeypatch.setattr(settings, "workspace_system_baseline_snapshot_max", 10)
    root = resolve_workspace_root(user_id="u1", folder_rel_path=None, conversation_id="sweep")
    (root / "f.txt").write_text("x", encoding="utf-8")
    refs = [
        await create_snapshot(
            user_id="u1",
            folder_id=None,
            folder_rel_path=None,
            conversation_id="sweep",
            label=f"turn-baseline:m{i}",
        )
        for i in range(4)
    ]
    monkeypatch.setattr(settings, "workspace_system_baseline_snapshot_max", 2)
    removed = await sweep_snapshot_retention()
    listed = await list_snapshots(user_id="u1", folder_id=None, conversation_id="sweep")
    ids = {r.snapshot_id for r in listed}
    assert removed == 2
    assert refs[-1].snapshot_id in ids
    assert refs[-2].snapshot_id in ids
    assert refs[0].snapshot_id not in ids


def test_parse_snapshot_storage_key_shapes():
    assert parse_snapshot_storage_key("workspaces/u1/folder-1") == ("u1", "folder-1", "")
    assert parse_snapshot_storage_key("workspaces/u1/conv/c9") == ("u1", None, "c9")
    assert parse_snapshot_storage_key("workspaces/u1") is None


async def test_purge_snapshots_clears_history(fs_storage):
    root = resolve_workspace_root(user_id="u1", folder_rel_path="f1", conversation_id="c1")
    (root / "a.txt").write_text("x", encoding="utf-8")
    await create_snapshot(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1")
    await create_snapshot(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", label="v1")

    await purge_snapshots(user_id="u1", folder_id="f1", conversation_id="c1")
    listed = await list_snapshots(user_id="u1", folder_id="f1", conversation_id="c1")
    assert listed == []
