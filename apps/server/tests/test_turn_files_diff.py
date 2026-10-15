"""Tests for A1+ turn baseline / files diff (cloud + local)."""

import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.storage._archive import ArchiveLimitError, zip_dir
from agentcore.workspace.handoff_diff import diff_archives, read_archive_entries
from agentcore.workspace.turn_baseline import (
    LOCAL_BASELINE_MAX_FILES,
    local_baseline_path,
    local_baselines_root,
    maybe_capture_turn_baseline,
)
from agentcore.workspace.turn_diff import (
    _enrich,
    compute_local_turn_files_diff,
    compute_turn_files_diff,
    restore_local_turn_baseline,
)


@pytest.mark.asyncio
async def test_diff_archives_enrich_includes_base_content(tmp_path: Path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "a.txt").write_bytes(b"hello\n")
    (after / "a.txt").write_bytes(b"hello\nworld\n")
    (after / "b.txt").write_bytes(b"new\n")

    changes = diff_archives(zip_dir(before), zip_dir(after))
    enriched = _enrich(changes, read_archive_entries(zip_dir(before)))
    by_path = {c.path: c for c in enriched}
    assert by_path["a.txt"].change_type == "modified"
    assert by_path["a.txt"].base_content == "hello\n"
    assert by_path["a.txt"].content == "hello\nworld\n"
    assert by_path["b.txt"].change_type == "added"
    assert by_path["b.txt"].base_content is None


@pytest.mark.asyncio
async def test_compute_turn_files_diff_unavailable_without_baseline():
    result = await compute_turn_files_diff(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="m1",
        baseline_snapshot_id=None,
    )
    assert result.available is False
    assert result.changes == []


@pytest.mark.asyncio
async def test_local_baseline_capture_writes_zip(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    backend = SimpleNamespace(location="local")

    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="msg-abc",
        backend=backend,
        workspace_root=root,
    )
    assert sid == "msg-abc"
    zip_path = local_baseline_path(root, "msg-abc")
    assert zip_path.is_file()
    # AgentCore internal zones are pruned from the archive; visible dirs stay.
    entries = read_archive_entries(zip_path.read_bytes())
    assert "src/a.py" in entries
    assert not any(p.startswith("GAgent/baselines/") for p in entries)
    assert not any(p.startswith("GAgent/index/") for p in entries)
    assert not any(p.startswith("GAgent/trash/") for p in entries)


@pytest.mark.asyncio
async def test_local_baseline_skips_on_file_cap(tmp_path: Path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    for i in range(5):
        (root / f"f{i}.txt").write_text("x", encoding="utf-8")
    backend = SimpleNamespace(location="local")
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.LOCAL_BASELINE_MAX_FILES",
        2,
    )

    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="msg-cap",
        backend=backend,
        workspace_root=root,
    )
    assert sid is None
    assert not local_baseline_path(root, "msg-cap").exists()


@pytest.mark.asyncio
async def test_zip_dir_raises_archive_limit(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello")
    (root / "b.txt").write_bytes(b"world")
    with pytest.raises(ArchiveLimitError) as ei:
        zip_dir(root, max_files=1)
    assert ei.value.reason == "max_files"


@pytest.mark.asyncio
async def test_local_diff_and_restore(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("old\n", encoding="utf-8")
    backend = SimpleNamespace(location="local")
    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="m-diff",
        backend=backend,
        workspace_root=root,
    )
    assert sid == "m-diff"

    (root / "a.txt").write_text("new\n", encoding="utf-8")
    (root / "b.txt").write_text("added\n", encoding="utf-8")

    diff = await compute_local_turn_files_diff(workspace_root=root, message_id="m-diff")
    assert diff.available is True
    assert diff.baseline_snapshot_id == "m-diff"
    by_path = {c.path: c for c in diff.changes}
    assert by_path["a.txt"].change_type == "modified"
    assert by_path["a.txt"].base_content is not None
    assert by_path["a.txt"].base_content.replace("\r\n", "\n") == "old\n"
    assert by_path["a.txt"].content is not None
    assert by_path["a.txt"].content.replace("\r\n", "\n") == "new\n"
    assert by_path["b.txt"].change_type == "added"

    await restore_local_turn_baseline(workspace_root=root, snapshot_id="m-diff")
    assert (root / "a.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "old\n"
    # Overlay restore does not delete post-baseline adds (same as cloud unzip).
    assert (root / "b.txt").is_file()


@pytest.mark.asyncio
async def test_local_diff_unavailable_without_zip(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    diff = await compute_local_turn_files_diff(workspace_root=root, message_id="missing")
    assert diff.available is False
    assert diff.changes == []


@pytest.mark.asyncio
async def test_cloud_location_still_skips_without_snapshot_setting(tmp_path: Path, monkeypatch):
    """Server location without snapshot feature → None (unchanged cloud gate)."""
    from agentcore.config import settings

    monkeypatch.setattr(settings, "workspace_snapshot_enabled", False)
    backend = SimpleNamespace(location="server")
    sid = await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id="m1",
        backend=backend,
        workspace_root=tmp_path,
    )
    assert sid is None


def test_local_baseline_max_files_aligned_with_desktop_gate():
    # Keep in sync with apps/desktop/.../fs/constants.ts ARCHIVE_MAX_FILES.
    assert LOCAL_BASELINE_MAX_FILES == 20_000


def test_local_baseline_retention_aligned_with_desktop_mirror():
    """Desktop main cannot read settings — it mirrors these two in constants.ts.

    Keep in sync with ``BASELINE_KEEP_MAX`` / ``BASELINE_MAX_AGE_MS``.
    """
    from agentcore.config.workspace import WorkspaceSettings

    fields = WorkspaceSettings.model_fields
    assert fields["workspace_local_baseline_max"].default == 20
    assert fields["workspace_local_baseline_retention_days"].default == 30


def _make_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    return root


def _stub_baseline(root: Path, snapshot_id: str, *, age_minutes: float) -> Path:
    """A stand-in baseline zip with a controlled mtime (prune orders by mtime)."""
    path = local_baseline_path(root, snapshot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04stub")
    stamp = time.time() - age_minutes * 60
    os.utime(path, (stamp, stamp))
    return path


async def _capture(root: Path, message_id: str) -> str | None:
    return await maybe_capture_turn_baseline(
        user_id="u1",
        folder_id=None,
        conversation_id="c1",
        message_id=message_id,
        backend=SimpleNamespace(location="local"),
        workspace_root=root,
    )


def _baseline_ids(root: Path) -> set[str]:
    return {p.stem for p in local_baselines_root(root).glob("*.zip")}


@pytest.mark.asyncio
async def test_local_baseline_capture_prunes_beyond_count(tmp_path: Path, monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "workspace_local_baseline_max", 3)
    root = _make_workspace(tmp_path)
    for i in range(5):
        _stub_baseline(root, f"old-{i}", age_minutes=60 - i)

    assert await _capture(root, "msg-new") == "msg-new"
    # Newest three survive: this turn's baseline plus the two youngest olds.
    assert _baseline_ids(root) == {"msg-new", "old-4", "old-3"}


@pytest.mark.asyncio
async def test_local_baseline_capture_prunes_expired(tmp_path: Path, monkeypatch):
    from agentcore.config import settings

    # Count cap wide open so only the TTL leg can delete anything.
    monkeypatch.setattr(settings, "workspace_local_baseline_max", 100)
    monkeypatch.setattr(settings, "workspace_local_baseline_retention_days", 30)
    root = _make_workspace(tmp_path)
    _stub_baseline(root, "stale", age_minutes=31 * 24 * 60)
    _stub_baseline(root, "fresh", age_minutes=29 * 24 * 60)

    assert await _capture(root, "msg-new") == "msg-new"
    assert _baseline_ids(root) == {"msg-new", "fresh"}


@pytest.mark.asyncio
async def test_local_baseline_capture_keeps_this_turn_under_clock_skew(tmp_path: Path, monkeypatch):
    """A future-dated zip must not push this turn's own baseline over the cap.

    Restored backups / clock skew can leave an mtime ahead of now, which would
    otherwise sort above the fresh capture and make the current turn the one
    that gets pruned — i.e. exactly the turn the user can still roll back.
    """
    from agentcore.config import settings

    monkeypatch.setattr(settings, "workspace_local_baseline_max", 1)
    root = _make_workspace(tmp_path)
    _stub_baseline(root, "future", age_minutes=-24 * 60)

    assert await _capture(root, "msg-new") == "msg-new"
    assert "msg-new" in _baseline_ids(root)


@pytest.mark.asyncio
async def test_local_baseline_prune_never_touches_named_versions(tmp_path: Path, monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "workspace_local_baseline_max", 1)
    monkeypatch.setattr(settings, "workspace_local_baseline_retention_days", 1)
    root = _make_workspace(tmp_path)
    _stub_baseline(root, "old", age_minutes=90 * 24 * 60)
    version_dir = root / "GAgent" / "versions" / "20250101T000000Z-abcd1234"
    version_dir.mkdir(parents=True)
    (version_dir / "content.zip").write_bytes(b"PK\x03\x04version")
    (version_dir / "meta.json").write_text("{}", encoding="utf-8")
    ancient = time.time() - 400 * 24 * 3600
    for name in ("content.zip", "meta.json"):
        os.utime(version_dir / name, (ancient, ancient))

    assert await _capture(root, "msg-new") == "msg-new"
    assert _baseline_ids(root) == {"msg-new"}
    # User-named versions are never auto-pruned, however old they are.
    assert (version_dir / "content.zip").is_file()
    assert (version_dir / "meta.json").is_file()


@pytest.mark.asyncio
async def test_local_baseline_prune_failure_never_blocks_the_turn(tmp_path: Path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("baselines zone unreadable")

    monkeypatch.setattr("agentcore.workspace.turn_baseline.prune_local_baselines", boom)
    root = _make_workspace(tmp_path)

    assert await _capture(root, "msg-new") == "msg-new"
    assert local_baseline_path(root, "msg-new").is_file()


@pytest.mark.asyncio
async def test_get_turn_files_diff_route_passes_folder_id():
    """S6 regression: route must use `_get_owned_conversation` (returns row), not the
    void ownership guard — otherwise ``conv.folder_id`` raises AttributeError → 500.
    """
    from agentcore.api.routes.conversations.turn_files_diff import get_turn_files_diff

    user = SimpleNamespace(user_id="u1")
    conv = SimpleNamespace(id="c1", folder_id="folder-xyz")
    msg = SimpleNamespace(role="assistant", baseline_snapshot_id="snap-1")
    conv_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=conv))
    msg_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=msg))
    fake = SimpleNamespace(baseline_snapshot_id="snap-1", available=True, changes=[])

    with patch(
        "agentcore.api.routes.conversations.turn_files_diff.compute_turn_files_diff",
        new=AsyncMock(return_value=fake),
    ) as compute:
        resp = await get_turn_files_diff(
            conversation_id="c1",
            message_id="m1",
            user=user,
            conv_repo=conv_repo,
            msg_repo=msg_repo,
        )

    compute.assert_awaited_once()
    assert compute.await_args.kwargs["folder_id"] == "folder-xyz"
    assert compute.await_args.kwargs["conversation_id"] == "c1"
    assert compute.await_args.kwargs["message_id"] == "m1"
    assert compute.await_args.kwargs["baseline_snapshot_id"] == "snap-1"
    assert resp.available is True
    assert resp.total == 0
