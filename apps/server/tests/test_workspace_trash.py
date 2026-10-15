"""Unit tests for GAgent/trash list + restore."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentcore.workspace.protocol import AlreadyExists, OutsideWorkspace, WorkspaceIOError
from agentcore.workspace.trash import (
    TrashExpiredError,
    TrashNotFound,
    list_trash_entries,
    restore_from_trash,
    soft_delete_expanding_trash_ancestor,
    soft_delete_to_trash,
)


def _write(path: Path, text: str = "hi") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _stamp_meta(entry_dir: Path, *, original: str, deleted_at: str, name: str) -> None:
    (entry_dir / "meta.json").write_text(
        json.dumps(
            {
                "original_path": original,
                "deleted_at": deleted_at,
                "is_dir": False,
                "name": name,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_soft_delete_list_restore_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "docs" / "a.md"
    _write(target, "body")
    entry_id = soft_delete_to_trash(
        root=tmp_path, target=target, original_rel="docs/a.md"
    )
    assert not target.exists()

    entries = list_trash_entries(root=tmp_path, retention_days=30)
    assert len(entries) == 1
    assert entries[0].entry_id == entry_id
    assert entries[0].original_path == "docs/a.md"
    assert entries[0].name == "a.md"
    assert entries[0].is_dir is False

    restored = restore_from_trash(
        root=tmp_path, entry_id=entry_id, retention_days=30
    )
    assert restored == "docs/a.md"
    assert target.read_text(encoding="utf-8") == "body"
    assert list_trash_entries(root=tmp_path, retention_days=30) == []


def test_restore_dir_tree(tmp_path: Path) -> None:
    d = tmp_path / "pkg"
    _write(d / "x.py", "x")
    _write(d / "y.py", "y")
    entry_id = soft_delete_to_trash(root=tmp_path, target=d, original_rel="pkg")
    assert restore_from_trash(root=tmp_path, entry_id=entry_id) == "pkg"
    assert (tmp_path / "pkg" / "x.py").read_text(encoding="utf-8") == "x"
    assert (tmp_path / "pkg" / "y.py").read_text(encoding="utf-8") == "y"


def test_restore_conflict_when_dest_exists(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    _write(target, "v1")
    entry_id = soft_delete_to_trash(root=tmp_path, target=target, original_rel="f.txt")
    _write(target, "occupied")
    with pytest.raises(AlreadyExists):
        restore_from_trash(root=tmp_path, entry_id=entry_id)
    assert target.read_text(encoding="utf-8") == "occupied"


def test_restore_unknown_id(tmp_path: Path) -> None:
    with pytest.raises(TrashNotFound):
        restore_from_trash(root=tmp_path, entry_id="nope")


def test_list_purges_expired(tmp_path: Path) -> None:
    target = tmp_path / "old.txt"
    _write(target)
    entry_id = soft_delete_to_trash(root=tmp_path, target=target, original_rel="old.txt")
    entry_dir = tmp_path / "GAgent" / "trash" / entry_id
    stale = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    _stamp_meta(entry_dir, original="old.txt", deleted_at=stale, name="old.txt")

    assert list_trash_entries(root=tmp_path, retention_days=30) == []
    assert not entry_dir.exists()


def test_restore_rejects_expired(tmp_path: Path) -> None:
    target = tmp_path / "old.txt"
    _write(target)
    entry_id = soft_delete_to_trash(root=tmp_path, target=target, original_rel="old.txt")
    entry_dir = tmp_path / "GAgent" / "trash" / entry_id
    stale = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    _stamp_meta(entry_dir, original="old.txt", deleted_at=stale, name="old.txt")
    with pytest.raises(TrashExpiredError):
        restore_from_trash(root=tmp_path, entry_id=entry_id, retention_days=30)


def test_restore_rejects_traversal_meta(tmp_path: Path) -> None:
    target = tmp_path / "safe.txt"
    _write(target)
    entry_id = soft_delete_to_trash(root=tmp_path, target=target, original_rel="safe.txt")
    entry_dir = tmp_path / "GAgent" / "trash" / entry_id
    _stamp_meta(
        entry_dir,
        original="../escape.txt",
        deleted_at=datetime.now(UTC).isoformat(),
        name="escape.txt",
    )
    with pytest.raises(OutsideWorkspace):
        restore_from_trash(root=tmp_path, entry_id=entry_id)


def test_list_newest_first(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    _write(a)
    id_a = soft_delete_to_trash(root=tmp_path, target=a, original_rel="a.txt")
    _write(b)
    id_b = soft_delete_to_trash(root=tmp_path, target=b, original_rel="b.txt")
    older = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    newer = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    for eid, ts, name in ((id_a, older, "a.txt"), (id_b, newer, "b.txt")):
        _stamp_meta(
            tmp_path / "GAgent" / "trash" / eid,
            original=name,
            deleted_at=ts,
            name=name,
        )
    entries = list_trash_entries(root=tmp_path, retention_days=30)
    assert [e.entry_id for e in entries] == [id_b, id_a]


def test_soft_delete_rejects_self_nest_under_agentcore(tmp_path: Path) -> None:
    """Mechanical guard: never shutil-move a trash ancestor into its own trash."""
    ac = tmp_path / "GAgent"
    _write(ac / "rules" / "r.md", "rule")
    (ac / "trash").mkdir(parents=True)
    with pytest.raises(WorkspaceIOError, match="自嵌套"):
        soft_delete_to_trash(root=tmp_path, target=ac, original_rel="GAgent")
    assert (ac / "rules" / "r.md").read_text(encoding="utf-8") == "rule"


def test_expand_delete_agentcore_soft_rules_hard_clears_zones(tmp_path: Path) -> None:
    ac = tmp_path / "GAgent"
    _write(ac / "rules" / "r.md", "rule-body")
    _write(ac / "index" / "cache.db", "db")
    _write(ac / "trash" / "stale" / "content", "old")
    (ac / "baselines").mkdir(parents=True)
    (ac / "baselines" / "snap.zip").write_bytes(b"PK")

    soft_delete_expanding_trash_ancestor(root=tmp_path, target=ac)

    assert not (ac / "rules").exists()
    assert not (ac / "index").exists()
    assert not (ac / "baselines").exists()
    # Soft-deletes recreate trash under GAgent — shell remains.
    assert (ac / "trash").is_dir()
    entries = list_trash_entries(root=tmp_path, retention_days=30)
    assert len(entries) == 1
    assert entries[0].original_path == "GAgent/rules"
    restored = restore_from_trash(
        root=tmp_path, entry_id=entries[0].entry_id, retention_days=30
    )
    assert restored == "GAgent/rules"
    assert (tmp_path / "GAgent" / "rules" / "r.md").read_text(encoding="utf-8") == (
        "rule-body"
    )
