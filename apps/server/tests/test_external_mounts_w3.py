"""W3 session external mounts: routing, readonly refusal, grant store lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agentcore.workspace import grant_store
from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
from agentcore.workspace.external_mounts import (
    ExternalMount,
    external_env_var,
    external_ns,
    parse_external_path,
    sanitize_alias,
    uniquify_alias,
)
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.protocol import OutsideWorkspace, PathNotFound, WorkspaceIOError
from agentcore.workspace.server import ServerWorkspace


@pytest.fixture(autouse=True)
def _clear_grants():
    grant_store.clear_all_for_tests()
    yield
    grant_store.clear_all_for_tests()


def test_parse_external_path():
    assert parse_external_path("external/reports/a.xlsx") == ("reports", "a.xlsx")
    assert parse_external_path("external/reports") == ("reports", "")
    assert parse_external_path("attachments/x") is None
    assert parse_external_path("../evil") is None
    # Non-ASCII alias under reserved namespace is not parseable (must not fall through).
    assert parse_external_path("external/项目资料/a.txt") is None


def test_sanitize_and_uniquify_alias():
    from agentcore.workspace.external_mounts import alias_is_routable

    cn = sanitize_alias("6月报表")
    assert alias_is_routable(cn)
    assert cn.isascii()
    assert sanitize_alias("6月报表") == cn  # stable
    pure = sanitize_alias("项目资料")
    assert alias_is_routable(pure) and pure.startswith("ext_")
    assert sanitize_alias("reports") == "reports"
    a = uniquify_alias("reports", {"reports"})
    assert a == "reports_2"
    # uniquify must stay within alias rules even for long bases
    long_base = "a" * 64
    u = uniquify_alias(long_base, {sanitize_alias(long_base)})
    assert alias_is_routable(u)
    assert len(u) <= 64


@pytest.mark.asyncio
async def test_grant_chinese_label_produces_routable_alias():
    m = await grant_store.add_grant("c1", root_id="r1", label="项目资料")
    from agentcore.workspace.external_mounts import alias_is_routable

    assert alias_is_routable(m.alias)
    assert parse_external_path(f"external/{m.alias}/x.txt") == (m.alias, "x.txt")


@pytest.mark.asyncio
async def test_local_workspace_rejects_invalid_external_alias_no_workspace_fallback():
    """``external/<non-ascii>/…`` must PathNotFound — not write under primary ``external/``."""
    channel = AsyncMock(spec=WorkspaceChannel)
    channel.root_id = "primary"
    channel.conversation_id = "c1"
    channel.request = AsyncMock(return_value="should-not-run")
    ws = LocalWorkspace(channel)
    with pytest.raises(PathNotFound):
        await ws.read("external/项目资料/a.txt")
    channel.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_workspace_rejects_invalid_external_no_primary_fallback(tmp_path: Path):
    primary = tmp_path / "ws"
    primary.mkdir()

    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    ws = ServerWorkspace(primary, _Sandbox(), location="local")
    with pytest.raises(PathNotFound):
        await ws.write("external/项目资料/a.txt", "leaked")
    assert not (primary / "external").exists()


@pytest.mark.asyncio
async def test_grant_store_add_revoke_clear():
    m = await grant_store.add_grant("c1", root_id="r1", label="报表", alias_hint="reports")
    assert m.alias == "reports"
    assert (await grant_store.list_grants("c1"))[0].root_id == "r1"
    # Same root refreshes without duplicating
    m2 = await grant_store.add_grant("c1", root_id="r1", label="报表2")
    assert m2.alias == "reports"
    assert len(await grant_store.list_grants("c1")) == 1
    assert await grant_store.revoke_grant("c1", alias="reports")
    assert await grant_store.list_grants("c1") == []


@pytest.mark.asyncio
async def test_grant_store_mode_upgrade():
    m = await grant_store.add_grant(
        "c1", root_id="r1", label="桌面", alias_hint="desk", mode="readonly"
    )
    assert m.mode == "readonly"
    m2 = await grant_store.add_grant(
        "c1", root_id="r1", label="桌面", mode="organize"
    )
    assert m2.alias == m.alias
    assert m2.mode == "organize"


class _FakeGrantRow:
    def __init__(self, *, alias: str, root_id: str, label: str, mode: str = "readonly"):
        self.alias = alias
        self.root_id = root_id
        self.label = label
        self.mode = mode


class _FakeSessionCM:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_add_grant_retries_on_alias_integrity_error(monkeypatch):
    """Concurrent peer took the alias → IntegrityError → uniquify + retry succeeds."""
    from sqlalchemy.exc import IntegrityError

    import agentcore.workspace.grant_store as gs

    gs._memory = None
    gs._cache.clear()

    load_n = {"n": 0}

    async def fake_load(conversation_id: str):
        load_n["n"] += 1
        if load_n["n"] == 1:
            return {}
        # After conflict: peer already owns ``reports``.
        return {
            "reports": ExternalMount(
                alias="reports",
                root_id="peer-root",
                label="peer",
                mode="readonly",
            )
        }

    aliases_seen: list[str] = []

    class _Repo:
        def __init__(self, session):
            pass

        async def upsert(self, **kwargs):
            aliases_seen.append(kwargs["alias"])
            if len(aliases_seen) == 1:
                raise IntegrityError("INSERT", {}, Exception("uq_alias"))
            return _FakeGrantRow(
                alias=kwargs["alias"],
                root_id=kwargs["root_id"],
                label=kwargs["label"],
                mode=kwargs["mode"],
            )

    monkeypatch.setattr(gs, "_load_from_db", fake_load)
    monkeypatch.setattr(
        "agentcore.db.repositories.external_grants.ExternalGrantRepository",
        _Repo,
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _FakeSessionCM(),
    )

    m = await gs.add_grant(
        "c1",
        root_id="r-new",
        label="报表",
        alias_hint="reports",
        working_root=False,
    )
    assert aliases_seen == ["reports", "reports_2"]
    assert m.alias == "reports_2"
    assert m.root_id == "r-new"


@pytest.mark.asyncio
async def test_add_grant_retries_on_root_integrity_error(monkeypatch):
    """Concurrent peer inserted same root → IntegrityError → reload + refresh succeeds."""
    from sqlalchemy.exc import IntegrityError

    import agentcore.workspace.grant_store as gs

    gs._memory = None
    gs._cache.clear()

    load_n = {"n": 0}

    async def fake_load(conversation_id: str):
        load_n["n"] += 1
        if load_n["n"] == 1:
            return {}
        return {
            "desk": ExternalMount(
                alias="desk",
                root_id="r1",
                label="桌面",
                mode="readonly",
            )
        }

    upsert_n = {"n": 0}

    class _Repo:
        def __init__(self, session):
            pass

        async def upsert(self, **kwargs):
            upsert_n["n"] += 1
            if upsert_n["n"] == 1:
                raise IntegrityError("INSERT", {}, Exception("uq_root"))
            # Same-root refresh path: keep peer's alias.
            assert kwargs["alias"] == "desk"
            assert kwargs["root_id"] == "r1"
            return _FakeGrantRow(
                alias="desk",
                root_id="r1",
                label=kwargs["label"],
                mode=kwargs["mode"],
            )

    monkeypatch.setattr(gs, "_load_from_db", fake_load)
    monkeypatch.setattr(
        "agentcore.db.repositories.external_grants.ExternalGrantRepository",
        _Repo,
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _FakeSessionCM(),
    )

    m = await gs.add_grant(
        "c1", root_id="r1", label="桌面新", alias_hint="desk", working_root=False
    )
    assert upsert_n["n"] == 2
    assert m.alias == "desk"
    assert m.label == "桌面新"


@pytest.mark.asyncio
async def test_add_grant_exhausts_retries_raises(monkeypatch):
    """Persistent unique conflicts surface a clear error after limited retries."""
    from sqlalchemy.exc import IntegrityError

    import agentcore.workspace.grant_store as gs

    gs._memory = None
    gs._cache.clear()

    async def fake_load(conversation_id: str):
        return {}

    class _Repo:
        def __init__(self, session):
            pass

        async def upsert(self, **kwargs):
            raise IntegrityError("INSERT", {}, Exception("uq"))

    monkeypatch.setattr(gs, "_load_from_db", fake_load)
    monkeypatch.setattr(
        "agentcore.db.repositories.external_grants.ExternalGrantRepository",
        _Repo,
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _FakeSessionCM(),
    )

    with pytest.raises(RuntimeError, match="conflict after retries"):
        await gs.add_grant(
            "c1", root_id="r1", label="x", alias_hint="desk", working_root=False
        )


@pytest.mark.asyncio
async def test_build_turn_backend_attaches_external_channel_for_cloud_grants(tmp_path, monkeypatch):
    """Cloud turn with grants wires attach_external_channel (root_id-only mounts)."""
    from agentcore.config import settings
    from agentcore.conversation.turn_backend import build_turn_backend
    from agentcore.runtime.events.sink import EventSink

    await grant_store.add_grant("conv-cloud", root_id="r-ext", label="桌面", alias_hint="desk")
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    backend = await build_turn_backend(
        user_id="u1",
        conversation_id="conv-cloud",
        folder_id=None,
        sink=EventSink(),
        local_binding=None,
    )
    assert isinstance(backend, ServerWorkspace)
    assert backend.location == "server"
    assert backend._external_bridge is not None  # noqa: SLF001
    assert "desk" in backend._mounts  # noqa: SLF001
    assert backend._mounts["desk"].root_id == "r-ext"  # noqa: SLF001
    assert backend._mounts["desk"].abs_path is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_local_workspace_routes_external_read_and_rejects_write():
    channel = AsyncMock(spec=WorkspaceChannel)
    channel.root_id = "primary"
    channel.conversation_id = "c1"
    channel.request = AsyncMock(return_value="ok-content")
    ws = LocalWorkspace(channel)
    ws.attach_external_mounts(
        {
            "reports": ExternalMount(
                alias="reports", root_id="ext-r1", label="报表", mode="readonly"
            )
        }
    )
    out = await ws.read("external/reports/a.txt")
    assert out == "ok-content"
    channel.request.assert_awaited()
    call = channel.request.await_args
    assert call.args[0] == WorkspaceOp.READ
    assert call.args[1]["path"] == "a.txt"
    assert call.kwargs.get("root_id") == "ext-r1"

    with pytest.raises(OutsideWorkspace, match="只读"):
        await ws.write("external/reports/a.txt", "nope")


@pytest.mark.asyncio
async def test_local_workspace_unknown_external_alias():
    channel = AsyncMock(spec=WorkspaceChannel)
    channel.root_id = "primary"
    channel.conversation_id = "c1"
    ws = LocalWorkspace(channel)
    with pytest.raises(PathNotFound):
        await ws.read("external/missing/x.txt")


@pytest.mark.asyncio
async def test_server_workspace_external_readonly(tmp_path: Path):
    primary = tmp_path / "ws"
    primary.mkdir()
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "note.txt").write_text("hello", encoding="utf-8")

    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    ws = ServerWorkspace(primary, _Sandbox(), location="local")
    ws.attach_external_mounts(
        {
            "ext": ExternalMount(
                alias="ext",
                root_id="r",
                label="ext",
                abs_path=str(ext),
                mode="readonly",
            )
        }
    )
    assert await ws.read("external/ext/note.txt") == "hello"
    with pytest.raises(OutsideWorkspace, match="只读"):
        await ws.write("external/ext/note.txt", "x")
    entries = await ws.list("external/ext", "*")
    assert any(
        e.path.replace("\\", "/").endswith("note.txt") for e in entries
    )
    assert not any("external/" in e.path.replace("\\", "/") for e in entries)


@pytest.mark.asyncio
async def test_server_workspace_model_path_no_dotdot_leak(tmp_path: Path):
    """list_tree / _model_path must not emit ``../`` when abs is under an external mount."""
    primary = tmp_path / "ws"
    primary.mkdir()
    # Host prefix often contains ``tmp`` (Linux CI). Ignore rules must not
    # treat that segment as workspace noise and hide the grant.
    ext = tmp_path / "tmp" / "ext"
    ext.mkdir(parents=True)
    (ext / "a.txt").write_text("x", encoding="utf-8")

    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    ws = ServerWorkspace(primary, _Sandbox(), location="local")
    ws.attach_external_mounts(
        {
            "ext": ExternalMount(
                alias="ext",
                root_id="r",
                label="ext",
                abs_path=str(ext),
                mode="readonly",
            )
        }
    )
    # Reverse-lookup without logical (the dangerous fallback path).
    mapped = ws._model_path(ext / "a.txt")
    assert mapped.replace("\\", "/").endswith("a.txt")
    assert ".." not in mapped.replace("\\", "/")
    assert "external/" not in mapped.replace("\\", "/")

    tree = await ws.list_tree("external/ext", max_depth=2)
    assert tree.entries
    for e in tree.entries:
        posix = e.path.replace("\\", "/")
        assert posix.endswith("a.txt") or posix.endswith("ext")
        assert ".." not in posix
        assert "external/" not in posix


def test_external_env_var_name():
    assert external_env_var("my-reports") == "AGENTCORE_EXTERNAL_MY_REPORTS"
    assert external_ns("a", "b/c") == "external/a/b/c"


@pytest.mark.asyncio
async def test_local_organize_allows_move_rejects_write_and_permanent():
    channel = AsyncMock(spec=WorkspaceChannel)
    channel.root_id = "primary"
    channel.conversation_id = "c1"
    channel.request = AsyncMock(return_value=None)
    ws = LocalWorkspace(channel)
    ws.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk", root_id="ext-r1", label="桌面", mode="organize"
            )
        }
    )
    await ws.mkdir("external/desk/Docs")
    channel.request.assert_awaited()
    with pytest.raises(OutsideWorkspace, match="整理授权|不允许"):
        await ws.write("external/desk/a.txt", "nope")
    with pytest.raises(OutsideWorkspace, match="永久删除"):
        await ws.delete("external/desk/a.txt", permanent=True)


@pytest.mark.asyncio
async def test_server_organize_move_and_env_skips_organize(tmp_path: Path):
    primary = tmp_path / "ws"
    primary.mkdir()
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "a.txt").write_text("hello", encoding="utf-8")

    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    ws = ServerWorkspace(primary, _Sandbox(), location="local")
    ws.attach_external_mounts(
        {
            "ext": ExternalMount(
                alias="ext",
                root_id="r",
                label="ext",
                abs_path=str(ext),
                mode="organize",
            )
        }
    )
    await ws.mkdir("external/ext/Docs")
    await ws.move("external/ext/a.txt", "external/ext/Docs/a.txt")
    assert (ext / "Docs" / "a.txt").read_text(encoding="utf-8") == "hello"
    from agentcore.workspace.external_mounts import build_external_env

    assert build_external_env(ws._mounts) == {}


@pytest.mark.asyncio
async def test_cloud_server_external_via_channel_read_and_organize():
    """Cloud ServerWorkspace (no abs_path) routes external ops via per-op root_id."""
    primary = Path(".")  # unused for external channel path

    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    channel = AsyncMock(spec=WorkspaceChannel)
    channel.root_id = ""
    channel.conversation_id = "c-cloud"
    channel.request = AsyncMock(return_value="hello-from-desktop")

    ws = ServerWorkspace(primary, _Sandbox(), location="server")
    assert ws.location == "server"  # worker_gate must stay off
    ws.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk", root_id="ext-desk", label="桌面", mode="organize"
            )
        }
    )
    ws.attach_external_channel(channel)

    out = await ws.read("external/desk/a.txt")
    assert out == "hello-from-desktop"
    call = channel.request.await_args
    assert call.args[0] == WorkspaceOp.READ
    assert call.args[1]["path"] == "a.txt"
    assert call.kwargs.get("root_id") == "ext-desk"

    channel.request = AsyncMock(return_value=None)
    await ws.mkdir("external/desk/Docs")
    mkdir_call = channel.request.await_args
    assert mkdir_call.args[0] == WorkspaceOp.MKDIR
    assert mkdir_call.kwargs.get("root_id") == "ext-desk"
    assert ws.dirty

    with pytest.raises(OutsideWorkspace, match="整理授权|不允许"):
        await ws.write("external/desk/a.txt", "nope")
    with pytest.raises(OutsideWorkspace, match="永久删除"):
        await ws.delete("external/desk/a.txt", permanent=True)


@pytest.mark.asyncio
async def test_cloud_server_external_without_channel_still_hard_rejects(tmp_path: Path):
    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    ws = ServerWorkspace(tmp_path, _Sandbox(), location="server")
    ws.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk", root_id="ext-desk", label="桌面", mode="readonly"
            )
        }
    )
    with pytest.raises(WorkspaceIOError, match="本机引擎外不可直读"):
        await ws.read("external/desk/a.txt")


@pytest.mark.asyncio
async def test_cloud_server_unknown_and_primary_paths(tmp_path: Path):
    """Non-external primary still Path-I/O; unknown external alias still PathNotFound."""

    class _Sandbox:
        async def execute(self, req):
            from agentcore.tools.sandbox.protocol import ExecutionResult

            return ExecutionResult(
                success=True, stdout="", stderr="", exit_code=0, duration_ms=0
            )

    (tmp_path / "note.txt").write_text("primary", encoding="utf-8")
    channel = AsyncMock(spec=WorkspaceChannel)
    channel.root_id = ""
    channel.conversation_id = "c1"
    channel.request = AsyncMock(return_value="should-not-be-called")

    ws = ServerWorkspace(tmp_path, _Sandbox(), location="server")
    ws.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk", root_id="ext-desk", label="桌面", mode="readonly"
            )
        }
    )
    ws.attach_external_channel(channel)

    assert await ws.read("note.txt") == "primary"
    channel.request.assert_not_awaited()

    with pytest.raises(PathNotFound):
        await ws.read("external/missing/x.txt")
    channel.request.assert_not_awaited()
