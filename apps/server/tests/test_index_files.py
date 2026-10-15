"""Tests for ``ServerWorkspace.index_files`` — the @ mention flat file index.

Filesystem-backed but hermetic: each test builds a throwaway tree under
``tmp_path``. This is the cloud counterpart to the desktop ``fsApi.listFiles``
that indexes local roots (文件中枢统一 F4), so it must behave the same: a flat,
files-only, POSIX path list with noise dirs pruned and a hard cap.
"""

import os
import threading
from pathlib import Path

import pytest

from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace import server as server_mod
from agentcore.workspace.server import ServerWorkspace


def _ws(root: Path) -> ServerWorkspace:
    return ServerWorkspace(root=root, sandbox=SubprocessSandbox())


def _seed(root: Path) -> None:
    (root / "README.md").write_text("r", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    (src / "app.ts").write_text("a", encoding="utf-8")
    (src / "util.ts").write_text("u", encoding="utf-8")
    # Noise dirs that must be pruned (mirrors local indexing).
    nm = root / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x", encoding="utf-8")
    git = root / ".git"
    git.mkdir()
    (git / "config").write_text("g", encoding="utf-8")
    ac = root / "AgentCore" / "index"
    ac.mkdir(parents=True)
    (ac / "cache.db").write_bytes(b"db")
    (root / "local.db").write_bytes(b"db")


async def test_index_lists_files_only_with_posix_paths(tmp_path: Path):
    _seed(tmp_path)
    paths, truncated = await _ws(tmp_path).index_files()
    # Files only (no dir entries), forward-slash separators, sorted.
    assert paths == ["README.md", "src/app.ts", "src/util.ts"]
    assert truncated is False


async def test_index_prunes_ignored_dirs(tmp_path: Path):
    _seed(tmp_path)
    paths, _ = await _ws(tmp_path).index_files()
    joined = "\n".join(paths)
    assert "node_modules" not in joined
    assert ".git" not in joined
    assert "GAgent/index" not in joined
    assert ".db" not in joined


async def test_index_caps_and_flags_truncation(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    paths, truncated = await _ws(tmp_path).index_files(cap=3)
    assert len(paths) == 3
    assert truncated is True


async def test_index_empty_workspace(tmp_path: Path):
    paths, truncated = await _ws(tmp_path).index_files()
    assert paths == []
    assert truncated is False


async def test_index_recent_order_is_newest_first(tmp_path: Path):
    # order="recent" returns newest-first by mtime (the worker manifest's relevance
    # budget), independent of alphabetical order; default order stays the @ view.
    for n in ["a_old.txt", "b_new.txt", "c_mid.txt"]:
        (tmp_path / n).write_text("x", encoding="utf-8")
    os.utime(tmp_path / "a_old.txt", (100, 100))  # oldest
    os.utime(tmp_path / "c_mid.txt", (200, 200))  # middle
    os.utime(tmp_path / "b_new.txt", (300, 300))  # newest
    recent, _ = await _ws(tmp_path).index_files(order="recent")
    assert recent == ["b_new.txt", "c_mid.txt", "a_old.txt"]
    alpha, _ = await _ws(tmp_path).index_files()  # default = alphabetical, unaffected
    assert alpha == ["a_old.txt", "b_new.txt", "c_mid.txt"]


async def test_index_entries_carry_fingerprint(tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    st = target.stat()
    result = await _ws(tmp_path).index_files()
    assert result.paths == ["note.txt"]
    assert result.truncated is False
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.path == "note.txt"
    assert entry.mtime_ms == st.st_mtime_ns // 1_000_000
    assert entry.size_bytes == st.st_size
    assert result.fingerprints() == {
        "note.txt": (st.st_mtime_ns // 1_000_000, st.st_size)
    }


async def test_index_disk_scan_runs_off_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    loop_tid = threading.get_ident()
    seen: list[int] = []
    real = server_mod._collect_index_files_sync

    def tracking_collect(*args, **kwargs):  # noqa: ANN002, ANN003
        seen.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(server_mod, "_collect_index_files_sync", tracking_collect)
    paths, truncated = await _ws(tmp_path).index_files()
    assert paths == ["a.txt"]
    assert truncated is False
    assert seen, "sync collect must run"
    assert all(tid != loop_tid for tid in seen)
