"""Tests for code_search indexing and tool."""

from pathlib import Path

import pytest

from agentcore.tools.builtin.code_search import CodeSearchTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.indexing.chunker import chunk_file, detect_language
from agentcore.workspace.indexing.manager import IndexManager
from agentcore.workspace.server import ServerWorkspace


@pytest.fixture
def sample_py(tmp_path: Path) -> Path:
    src = tmp_path / "pkg" / "sample.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        '''"""Sample module."""

class ApprovalGate:
    """Gate tool approvals."""

    async def check(self, tool_name: str) -> bool:
        """Check whether a tool may run."""
        return True


def helper_function():
  return "noop"
''',
        encoding="utf-8",
    )
    return tmp_path


def test_detect_language_python():
    assert detect_language("apps/foo/bar.py") == "python"
    assert detect_language("component.tsx") == "tsx"


@pytest.mark.asyncio
async def test_chunk_file_python_symbols():
    content = '''class Foo:
    def bar(self):
        pass

def baz():
    return 1
'''
    chunks = await chunk_file("mod.py", content, "python")
    assert chunks
    symbols = {c.symbol for c in chunks if c.symbol}
    assert "Foo" in symbols or "bar" in symbols or "baz" in symbols


@pytest.mark.asyncio
async def test_index_manager_build_and_search(sample_py: Path):
    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    manager = IndexManager.for_workspace_root(str(sample_py))

    updated = await manager.ensure_index(ws)
    assert updated is True

    result = await manager.search("approval gate check", max_results=5)
    assert result.chunks
    assert result.scores
    assert len(result.chunks) == len(result.scores)
    paths = {c.path for c in result.chunks}
    assert any("sample.py" in p for p in paths)

    db_path = sample_py / "AgentCore" / "index" / "code_search.db"
    assert db_path.is_file()


def test_code_search_schema_is_short_trigger_not_index_cookbook():
    schema = CodeSearchTool().schema
    desc = schema.description
    assert "grep" in desc
    assert "概念" in desc or "意图" in desc
    for token in ("building", "stale", "尚无快照", "勿空等", "两工具并存", "file_read"):
        assert token not in desc
    query = schema.parameters["properties"]["query"]["description"]
    assert "grep" not in query


@pytest.mark.asyncio
async def test_code_search_tool_end_to_end(sample_py: Path):
    from unittest.mock import MagicMock

    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    await ws.ensure_code_index()
    kick = MagicMock(wraps=ws.start_code_index_maintenance)
    ws.start_code_index_maintenance = kick  # type: ignore[method-assign]
    tool = CodeSearchTool()
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="a1",
        backend=ws,
        user_id="u1",
    )
    result = await tool.execute({"query": "ApprovalGate check"}, ctx)
    assert result.success
    assert "sample.py" in result.output
    assert "score=" in result.output
    assert result.metadata["index_status"] == "ready"
    kick.assert_not_called()


@pytest.mark.asyncio
async def test_code_search_tool_is_query_only_when_ensure_is_slow(
    sample_py: Path, monkeypatch: pytest.MonkeyPatch
):
    """Tool must return while background ensure is still running (no sync wait)."""
    import asyncio
    import contextlib
    import time

    from agentcore.workspace.indexing.manager import IndexManager

    async def slow_ensure(self, backend, *, force=False):  # noqa: ANN001
        await asyncio.sleep(5)
        return False

    monkeypatch.setattr(IndexManager, "ensure_index", slow_ensure)

    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    tool = CodeSearchTool()
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="a1",
        backend=ws,
        user_id="u1",
    )
    t0 = time.monotonic()
    try:
        result = await tool.execute({"query": "ApprovalGate"}, ctx)
    finally:
        maintainer = getattr(ws, "_index_maintainer", None)
        task = getattr(maintainer, "_task", None) if maintainer else None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    elapsed = time.monotonic() - t0
    assert result.success
    assert elapsed < 2.0, f"tool blocked on ensure ({elapsed:.2f}s)"
    # No snapshot yet and kick runs after search → stale (not building-in-flight).
    assert result.metadata["index_status"] == "stale"
    assert getattr(ws, "_index_maintainer", None) is not None  # non-ready kicked
    assert "grep" in result.output.lower() or "索引" in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["building", "stale"],
)
async def test_code_search_kicks_when_index_not_ready(status: str):
    """No-snapshot / dirty still schedule background maintenance."""
    from unittest.mock import AsyncMock, MagicMock

    from agentcore.workspace.protocol import CodeIndexStatus, CodeSearchResult

    index_status = CodeIndexStatus(status)
    backend = MagicMock()
    backend.start_code_index_maintenance = MagicMock()
    mgr = MagicMock()
    mgr.needs_background_ensure.return_value = True
    backend._get_index_manager = MagicMock(return_value=mgr)
    backend.code_search = AsyncMock(
        return_value=CodeSearchResult(index_status=index_status, index_stale=True)
    )
    tool = CodeSearchTool()
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="a1",
        backend=backend,
        user_id="u1",
    )
    result = await tool.execute({"query": "ApprovalGate"}, ctx)
    assert result.success
    assert result.metadata["index_status"] == status
    assert "grep" in result.output.lower()
    backend.start_code_index_maintenance.assert_called_once()
    backend.code_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_code_search_skips_kick_when_truncated_only_stale():
    """Truncated snapshot is STALE for UX but must not re-kick ensure."""
    from unittest.mock import AsyncMock, MagicMock

    from agentcore.workspace.protocol import CodeIndexStatus, CodeSearchResult

    backend = MagicMock()
    backend.start_code_index_maintenance = MagicMock()
    mgr = MagicMock()
    mgr.needs_background_ensure.return_value = False
    backend._get_index_manager = MagicMock(return_value=mgr)
    backend.code_search = AsyncMock(
        return_value=CodeSearchResult(
            index_status=CodeIndexStatus.STALE, index_stale=True
        )
    )
    tool = CodeSearchTool()
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="a1",
        backend=backend,
        user_id="u1",
    )
    result = await tool.execute({"query": "ApprovalGate"}, ctx)
    assert result.success
    assert result.metadata["index_status"] == "stale"
    backend.start_code_index_maintenance.assert_not_called()


@pytest.mark.asyncio
async def test_code_search_skips_kick_when_index_ready():
    """Ready snapshots must not kick maintenance on every query."""
    from unittest.mock import AsyncMock, MagicMock

    from agentcore.workspace.protocol import (
        CodeChunk,
        CodeIndexStatus,
        CodeSearchResult,
    )

    backend = MagicMock()
    backend.start_code_index_maintenance = MagicMock()
    mgr = MagicMock()
    mgr.needs_background_ensure.return_value = False
    backend._get_index_manager = MagicMock(return_value=mgr)
    backend.code_search = AsyncMock(
        return_value=CodeSearchResult(
            chunks=[
                CodeChunk(
                    path="pkg/sample.py",
                    symbol="ApprovalGate",
                    symbol_type="class",
                    start_line=1,
                    end_line=3,
                    language="python",
                    snippet="class ApprovalGate: ...",
                )
            ],
            scores=[1.0],
            index_status=CodeIndexStatus.READY,
            index_stale=False,
        )
    )
    tool = CodeSearchTool()
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="a1",
        backend=backend,
        user_id="u1",
    )
    result = await tool.execute({"query": "ApprovalGate"}, ctx)
    assert result.success
    assert result.metadata["index_status"] == "ready"
    backend.start_code_index_maintenance.assert_not_called()
    backend.code_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_maintainer_builds_in_background(sample_py: Path):
    import asyncio

    from agentcore.workspace.indexing.maintainer import IndexMaintainer
    from agentcore.workspace.indexing.manager import IndexManager
    from agentcore.workspace.protocol import CodeIndexStatus

    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    manager = IndexManager.for_workspace_root(str(sample_py))
    maintainer = IndexMaintainer(manager, ws)
    maintainer.schedule()
    assert manager.building or maintainer.building
    for _ in range(50):
        if not maintainer.building and manager.index_status() == CodeIndexStatus.READY:
            break
        await asyncio.sleep(0.05)
    assert manager.index_status() == CodeIndexStatus.READY
    result = await manager.search("ApprovalGate", max_results=5)
    assert result.chunks
    assert result.index_status == CodeIndexStatus.READY


@pytest.mark.asyncio
async def test_ensure_code_index_is_incremental(sample_py: Path):
    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    manager = IndexManager.for_workspace_root(str(sample_py))

    assert await manager.ensure_index(ws) is True
    assert await manager.ensure_index(ws) is False

    py_file = sample_py / "pkg" / "sample.py"
    py_file.write_text(py_file.read_text() + "\n# touch\n", encoding="utf-8")
    assert await manager.ensure_index(ws) is True


@pytest.mark.asyncio
async def test_ensure_index_skips_read_when_fingerprint_unchanged(
    sample_py: Path, tmp_path: Path, monkeypatch
):
    """Fingerprint match → no backend.read; fingerprint change → read + reindex."""
    from agentcore.config import settings
    from agentcore.workspace.channel import WorkspaceOp
    from agentcore.workspace.local import LocalWorkspace
    from agentcore.workspace.protocol import IndexFileEntry

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))

    body = (sample_py / "pkg" / "sample.py").read_text(encoding="utf-8")
    files = {"pkg/sample.py": body}
    fingerprint = {"pkg/sample.py": (1_700_000_000_000, len(body.encode("utf-8")))}
    reads: list[str] = []

    class _FakeChannel:
        root_id = "root-fp-test"

        async def request(self, op, args, *, timeout=None, root_id=None):
            _ = (timeout, root_id)
            if op == WorkspaceOp.INDEX_FILES:
                entries = [
                    IndexFileEntry(
                        path=p, mtime_ms=fp[0], size_bytes=fp[1]
                    )
                    for p, fp in fingerprint.items()
                ]
                # Simulate desktop wire shape; LocalWorkspace parses the dict.
                return {
                    "entries": [
                        {
                            "path": e.path,
                            "mtime_ms": e.mtime_ms,
                            "size_bytes": e.size_bytes,
                        }
                        for e in entries
                    ],
                    "truncated": False,
                }
            if op == WorkspaceOp.READ:
                path = str(args["path"]).replace("\\", "/").lstrip("./")
                reads.append(path)
                if path not in files:
                    from agentcore.workspace.protocol import PathNotFound

                    raise PathNotFound(path)
                return files[path]
            raise AssertionError(f"unexpected op {op}")

    ws = LocalWorkspace(_FakeChannel(), root_label="proj")
    assert await ws.ensure_code_index() is True
    assert reads == ["pkg/sample.py"]

    reads.clear()
    assert await ws.ensure_code_index() is False
    assert reads == [], "unchanged fingerprint must not channel-read"

    # Touch fingerprint (mtime) without changing content → must read, content hash
    # matches so upsert is skipped (ensure returns False) but fingerprint is stored.
    fingerprint["pkg/sample.py"] = (
        fingerprint["pkg/sample.py"][0] + 1,
        fingerprint["pkg/sample.py"][1],
    )
    reads.clear()
    assert await ws.ensure_code_index() is False
    assert reads == ["pkg/sample.py"]

    reads.clear()
    assert await ws.ensure_code_index() is False
    assert reads == []

    # Content + fingerprint change → reindex.
    files["pkg/sample.py"] = body + "\n# changed\n"
    fingerprint["pkg/sample.py"] = (
        fingerprint["pkg/sample.py"][0] + 1,
        len(files["pkg/sample.py"].encode("utf-8")),
    )
    reads.clear()
    assert await ws.ensure_code_index() is True
    assert reads == ["pkg/sample.py"]


@pytest.mark.asyncio
async def test_ensure_index_skips_ai_noise_suffixes(tmp_path: Path):
    """Noise suffixes (e.g. .parquet) must not trigger backend.read."""
    from agentcore.workspace.indexing.manager import IndexManager, _should_skip_path
    from agentcore.workspace.protocol import IndexFileEntry, IndexFilesResult

    assert _should_skip_path("data/huge.parquet")
    assert _should_skip_path("weights.npy")
    assert _should_skip_path("model.pkl")
    assert not _should_skip_path("src/app.py")

    reads: list[str] = []

    class _Backend:
        async def index_files(self, cap=None, *, order="path"):  # noqa: ANN001
            _ = (cap, order)
            return IndexFilesResult(
                paths=["src/app.py", "data/huge.parquet", "weights.npy"],
                truncated=False,
                entries=(
                    IndexFileEntry(path="src/app.py", mtime_ms=1, size_bytes=10),
                    IndexFileEntry(path="data/huge.parquet", mtime_ms=1, size_bytes=99),
                    IndexFileEntry(path="weights.npy", mtime_ms=1, size_bytes=99),
                ),
            )

        async def read(self, path: str) -> str:
            reads.append(path)
            return "def ok():\n    return 1\n"

    manager = IndexManager(str(tmp_path / "idx"))
    assert await manager.ensure_index(_Backend()) is True
    assert reads == ["src/app.py"]


@pytest.mark.asyncio
async def test_ensure_index_aborts_after_consecutive_liveness_timeouts(tmp_path: Path):
    """Two consecutive channel/liveness timeouts abort the round (no per-file 60s burn)."""
    from agentcore.workspace.indexing.manager import IndexManager
    from agentcore.workspace.protocol import (
        CodeIndexStatus,
        IndexFileEntry,
        IndexFilesResult,
        WorkspaceIOError,
    )

    reads: list[str] = []

    class _Backend:
        async def index_files(self, cap=None, *, order="path"):  # noqa: ANN001
            _ = (cap, order)
            return IndexFilesResult(
                paths=["a.py", "b.py", "c.py"],
                truncated=False,
                entries=(
                    IndexFileEntry(path="a.py", mtime_ms=1, size_bytes=1),
                    IndexFileEntry(path="b.py", mtime_ms=1, size_bytes=1),
                    IndexFileEntry(path="c.py", mtime_ms=1, size_bytes=1),
                ),
            )

        async def read(self, path: str) -> str:
            reads.append(path)
            raise WorkspaceIOError("local workspace op 'read' timed out（活性挂起）")

    manager = IndexManager(str(tmp_path / "idx"))
    updated = await manager.ensure_index(_Backend())
    assert updated is False
    assert reads == ["a.py", "b.py"], "must abort before reading remaining paths"
    assert manager.index_status() == CodeIndexStatus.STALE


@pytest.mark.asyncio
async def test_local_workspace_code_search_via_channel(sample_py: Path, tmp_path: Path, monkeypatch):
    """Cloud→desktop LocalWorkspace indexes via channel reads (not a disk stub)."""
    from agentcore.config import settings
    from agentcore.workspace.channel import WorkspaceOp
    from agentcore.workspace.local import LocalWorkspace

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))

    files = {
        "pkg/sample.py": (sample_py / "pkg" / "sample.py").read_text(encoding="utf-8"),
    }

    class _FakeChannel:
        root_id = "root-local-test"

        async def request(self, op, args, *, timeout=None, root_id=None):
            _ = (timeout, root_id)
            if op == WorkspaceOp.INDEX_FILES:
                return {"paths": list(files), "truncated": False}
            if op == WorkspaceOp.READ:
                path = str(args["path"]).replace("\\", "/").lstrip("./")
                if path not in files:
                    from agentcore.workspace.protocol import PathNotFound

                    raise PathNotFound(path)
                return files[path]
            raise AssertionError(f"unexpected op {op}")

    ws = LocalWorkspace(_FakeChannel(), root_label="proj")
    assert await ws.ensure_code_index() is True
    result = await ws.code_search("ApprovalGate check", max_results=5)
    assert result.chunks
    assert any("sample.py" in c.path for c in result.chunks)
    assert result.index_stale is False
    assert result.index_status.value == "ready"


@pytest.mark.asyncio
async def test_code_search_requires_query(sample_py: Path):
    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    tool = CodeSearchTool()
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="a1",
        backend=ws,
        user_id="u1",
    )
    result = await tool.execute({"query": ""}, ctx)
    assert not result.success
    assert "query" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_code_search_empty_is_success_with_next_steps(sample_py: Path):
    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    await ws.ensure_code_index()
    tool = CodeSearchTool()
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="a1",
        backend=ws,
        user_id="u1",
    )
    result = await tool.execute({"query": "zzz_definitely_missing_symbol"}, ctx)
    assert result.success
    assert result.metadata["match_count"] == 0
    assert "可执行下一步" in result.output
    assert "grep" in result.output
    assert "zzz_definitely_missing_symbol" in result.output


def test_tokenize_query_keeps_identifiers_and_splits_cjk_latin():
    from agentcore.workspace.indexing.bm25 import tokenize_query

    assert "check_approval" in tokenize_query("check_approval")
    assert "ApprovalGate" in tokenize_query("ApprovalGate")
    mixed = tokenize_query("审批门控ApprovalGate")
    assert "ApprovalGate" in mixed
    assert any("审批" in t or t.startswith("审") for t in mixed)
    # Adjacent CJK+Latin must not glue into one token.
    assert "审批门控ApprovalGate" not in mixed


@pytest.mark.asyncio
async def test_file_hashes_legacy_db_missing_fingerprint_columns(tmp_path: Path):
    """Old file_hashes without mtime/size columns migrate; fingerprint reads as None."""
    import sqlite3

    from agentcore.workspace.indexing.bm25 import BM25Index
    from agentcore.workspace.indexing.chunker import RawChunk

    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE file_hashes (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                indexed_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO file_hashes(path, content_hash, indexed_at) VALUES (?, ?, ?)",
            ("old.py", "abc", 1.0),
        )
        conn.commit()

    index = BM25Index(str(db))
    assert await index.get_file_fingerprint("old.py") is None
    await index.upsert_file(
        "old.py",
        "x = 1\n",
        [
            RawChunk(
                path="old.py",
                symbol=None,
                symbol_type=None,
                start_line=1,
                end_line=1,
                language="python",
                content="x = 1\n",
            )
        ],
        mtime_ms=42,
        size_bytes=6,
    )
    assert await index.get_file_fingerprint("old.py") == (42, 6)


@pytest.mark.asyncio
async def test_symbol_column_ranks_above_body_only_hit(tmp_path: Path):
    """Symbol-field hits should outrank content-only mentions of the same token."""
    from agentcore.workspace.indexing.bm25 import BM25Index
    from agentcore.workspace.indexing.chunker import RawChunk

    db = tmp_path / "idx.db"
    index = BM25Index(str(db))
    await index.upsert_file(
        "sym.py",
        "class ApprovalGate:\n    pass\n",
        [
            RawChunk(
                path="sym.py",
                symbol="ApprovalGate",
                symbol_type="class",
                start_line=1,
                end_line=2,
                language="python",
                content="class ApprovalGate:\n    pass\n",
            )
        ],
    )
    await index.upsert_file(
        "body.py",
        "# see ApprovalGate elsewhere\n",
        [
            RawChunk(
                path="body.py",
                symbol="helper",
                symbol_type="function",
                start_line=1,
                end_line=1,
                language="python",
                content="# see ApprovalGate elsewhere\n",
            )
        ],
    )
    hits = await index.search("ApprovalGate", limit=5)
    assert hits
    assert hits[0][0].path == "sym.py"
    assert hits[0][0].symbol == "ApprovalGate"


@pytest.mark.asyncio
async def test_index_status_hydrates_across_manager_rounds(sample_py: Path):
    """Committed meta survives a new IndexManager; status is READY before ensure."""
    from agentcore.workspace.protocol import CodeIndexStatus

    ws = ServerWorkspace(root=sample_py, sandbox=SubprocessSandbox())
    first = IndexManager.for_workspace_root(str(sample_py))
    assert await first.ensure_index(ws) is True
    assert first.index_status() == CodeIndexStatus.READY

    second = IndexManager.for_workspace_root(str(sample_py))
    assert second.index_status() == CodeIndexStatus.READY
    second.set_building(True)
    assert second.index_status() == CodeIndexStatus.READY


@pytest.mark.asyncio
async def test_index_status_building_only_without_snapshot(tmp_path: Path):
    """Empty DB + building → BUILDING; snapshot + building → not BUILDING."""
    from agentcore.workspace.protocol import (
        CodeIndexStatus,
        IndexFileEntry,
        IndexFilesResult,
    )

    empty = IndexManager(str(tmp_path / "empty"))
    assert empty.index_status() == CodeIndexStatus.STALE
    empty.set_building(True)
    assert empty.index_status() == CodeIndexStatus.BUILDING

    class _Backend:
        async def index_files(self, cap=None, *, order="path"):  # noqa: ANN001
            _ = (cap, order)
            return IndexFilesResult(
                paths=["a.py"],
                truncated=False,
                entries=(IndexFileEntry(path="a.py", mtime_ms=1, size_bytes=8),),
            )

        async def read(self, path: str) -> str:
            _ = path
            return "def a():\n    return 1\n"

    built = IndexManager(str(tmp_path / "built"))
    assert await built.ensure_index(_Backend()) is True
    assert built.index_status() == CodeIndexStatus.READY
    built.set_building(True)
    assert built.index_status() == CodeIndexStatus.READY


@pytest.mark.asyncio
async def test_needs_background_ensure_ignores_truncated(tmp_path: Path):
    """Truncated snapshot with no dirty flag must not request background ensure."""
    from agentcore.workspace.indexing.bm25 import BM25Index
    from agentcore.workspace.indexing.manager import IndexManager
    from agentcore.workspace.protocol import CodeIndexStatus
    from agentcore.workspace.stage_dirs import INDEX_REL

    index_dir = tmp_path / Path(*INDEX_REL.split("/"))
    index_dir.mkdir(parents=True)
    bm25 = BM25Index(str(index_dir / "code_search.db"))
    await bm25.upsert_file("a.py", "def a():\n  pass\n", [])
    await bm25.commit_meta(truncated=True)

    manager = IndexManager(str(index_dir))
    assert manager.index_status() == CodeIndexStatus.STALE
    assert manager.needs_background_ensure() is False


@pytest.mark.asyncio
async def test_index_status_truncated_ensure_is_stale_snapshot(tmp_path: Path):
    """Truncated ensure still commits a snapshot → STALE, never BUILDING."""
    from agentcore.workspace.indexing.manager import IndexManager
    from agentcore.workspace.protocol import (
        CodeIndexStatus,
        IndexFileEntry,
        IndexFilesResult,
    )

    class _Backend:
        async def index_files(self, cap=None, *, order="path"):  # noqa: ANN001
            _ = (cap, order)
            return IndexFilesResult(
                paths=["a.py"],
                truncated=True,
                entries=(IndexFileEntry(path="a.py", mtime_ms=1, size_bytes=8),),
            )

        async def read(self, path: str) -> str:
            _ = path
            return "def a():\n    return 1\n"

    manager = IndexManager(str(tmp_path / "trunc"))
    assert await manager.ensure_index(_Backend()) is True
    assert manager.index_status() == CodeIndexStatus.STALE
    assert manager.needs_background_ensure() is False
    manager.set_building(True)
    assert manager.index_status() == CodeIndexStatus.STALE

    # New manager hydrates truncated meta → still STALE (has snapshot).
    again = IndexManager(str(tmp_path / "trunc"))
    assert again.index_status() == CodeIndexStatus.STALE
    assert again.needs_background_ensure() is False
    again.set_building(True)
    assert again.index_status() == CodeIndexStatus.STALE


@pytest.mark.asyncio
async def test_index_abort_preserves_committed_meta(tmp_path: Path):
    """Timeout abort must not wipe a previously committed snapshot meta."""
    from agentcore.workspace.indexing.bm25 import BM25Index
    from agentcore.workspace.protocol import (
        CodeIndexStatus,
        IndexFileEntry,
        IndexFilesResult,
        WorkspaceIOError,
    )

    class _OkBackend:
        async def index_files(self, cap=None, *, order="path"):  # noqa: ANN001
            _ = (cap, order)
            return IndexFilesResult(
                paths=["a.py"],
                truncated=False,
                entries=(IndexFileEntry(path="a.py", mtime_ms=1, size_bytes=8),),
            )

        async def read(self, path: str) -> str:
            _ = path
            return "def a():\n    return 1\n"

    class _TimeoutBackend:
        async def index_files(self, cap=None, *, order="path"):  # noqa: ANN001
            _ = (cap, order)
            return IndexFilesResult(
                paths=["a.py", "b.py", "c.py"],
                truncated=False,
                entries=(
                    IndexFileEntry(path="a.py", mtime_ms=2, size_bytes=8),
                    IndexFileEntry(path="b.py", mtime_ms=1, size_bytes=1),
                    IndexFileEntry(path="c.py", mtime_ms=1, size_bytes=1),
                ),
            )

        async def read(self, path: str) -> str:
            if path == "a.py":
                return "def a():\n    return 2\n"
            raise WorkspaceIOError("local workspace op 'read' timed out（活性挂起）")

    idx_dir = tmp_path / "abort-meta"
    manager = IndexManager(str(idx_dir))
    assert await manager.ensure_index(_OkBackend()) is True
    meta_before = await BM25Index(str(idx_dir / "code_search.db")).read_meta()
    assert meta_before is not None
    assert meta_before.truncated is False
    assert meta_before.dirty is False

    updated = await manager.ensure_index(_TimeoutBackend())
    assert updated is True  # a.py re-indexed before abort
    meta_after = await BM25Index(str(idx_dir / "code_search.db")).read_meta()
    assert meta_after is not None
    assert meta_after.generation == meta_before.generation
    assert meta_after.last_complete_at == meta_before.last_complete_at
    assert meta_after.dirty is True
    assert manager.index_status() == CodeIndexStatus.STALE
    manager.set_building(True)
    assert manager.index_status() == CodeIndexStatus.STALE

    # Dirty must survive a fresh IndexManager (new turn / backend).
    again = IndexManager(str(idx_dir))
    assert again.index_status() == CodeIndexStatus.STALE


@pytest.mark.asyncio
async def test_legacy_db_without_meta_counts_as_snapshot(tmp_path: Path):
    """Pre-meta DB with file_hashes is queryable but STALE until commit_meta."""
    import sqlite3

    from agentcore.workspace.protocol import CodeIndexStatus

    idx_dir = tmp_path / "legacy"
    idx_dir.mkdir()
    db = idx_dir / "code_search.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE VIRTUAL TABLE chunks USING fts5(
                path UNINDEXED, symbol, symbol_type, language UNINDEXED,
                content, start_line UNINDEXED, end_line UNINDEXED,
                tokenize='unicode61'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE file_hashes (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                indexed_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO file_hashes(path, content_hash, indexed_at) VALUES (?, ?, ?)",
            ("old.py", "abc", 1.0),
        )
        conn.commit()

    manager = IndexManager(str(idx_dir))
    assert manager.index_status() == CodeIndexStatus.STALE
    manager.set_building(True)
    # Has rows ⇒ snapshot exists ⇒ refresh must not flip to BUILDING.
    assert manager.index_status() == CodeIndexStatus.STALE
