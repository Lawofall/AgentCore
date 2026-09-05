"""Cloud process kernel (slice 2): desk-guest ledger, not WorkspaceChannel.

Local ``process_*`` shapes stay in ``test_terminal_tool.py``. These tests never
talk to a real runsc: short exec is faked so Windows CI can prove start does not
hold a gVisor slot or emit ``WorkspaceOp``.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import agentcore.tools.sandbox.desk_process as desk_process_mod
import agentcore.tools.sandbox.gvisor as gvisor_mod
from agentcore.config import settings
from agentcore.tools.builtin.run_process import process_manage
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.desk_process import (
    PREVIEW_PORT_NOT_READY,
    PROCESS_NOT_RUNNING,
    CloudPreview,
    DeskProcessError,
    ensure_cloud_preview,
    lookup_cloud_preview,
)
from agentcore.tools.sandbox.gvisor import (
    GVisorSandbox,
    close_all_desk_sessions,
    desk_preview_upstream,
    reset_desk_sessions_for_tests,
)
from agentcore.tools.sandbox.limits import reset_execution_slots, try_acquire_execution_slot
from agentcore.tools.sandbox.sandboxd import (
    reset_sandboxd_client_for_tests,
    set_sandboxd_client_for_tests,
)
from agentcore.tools.sandbox.sandboxd.argv import EXEC_BINS
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.channel import WorkspaceChannel
from agentcore.workspace.server import ServerWorkspace
from tests.client_tool_fulfill_testutil import DELIVERED_EVENTS

pytestmark = pytest.mark.anyio

CONV_A = "conv-cloud-a"
CONV_B = "conv-cloud-b"


@pytest.fixture(autouse=True)
def _fresh_desk_ledger(monkeypatch: pytest.MonkeyPatch):
    reset_execution_slots()
    reset_desk_sessions_for_tests()
    reset_sandboxd_client_for_tests()
    monkeypatch.setattr(settings, "gvisor_max_concurrent_executions", 1)
    monkeypatch.setattr(settings, "gvisor_slot_wait_seconds", 0.2)
    yield
    reset_desk_sessions_for_tests()
    reset_execution_slots()
    reset_sandboxd_client_for_tests()
    DELIVERED_EVENTS.clear()


class _FakeDesk(GVisorSandbox):
    """In-process stand-in: short exec writes pid/log on host scratch and returns."""

    def __init__(self, scratch: Path, *, auto_log: str = "ok\n") -> None:
        super().__init__(
            workspace_root=str(scratch.parent / "ws"),
            runtime_root=str(scratch.parent / "rt"),
        )
        self._scratch = scratch
        self.auto_log = auto_log
        self.execs: list[str] = []
        self.last_log: Path | None = None

    async def ensure_workspace_desk(
        self, workspace: str, *, cache_bucket: str | None = None
    ) -> None:
        del cache_bucket
        self._scratch.mkdir(parents=True, exist_ok=True)
        key = gvisor_mod._desk_key(workspace)

        async def _close() -> None:
            return None

        gvisor_mod._desks[key] = SimpleNamespace(
            key=key,
            close=_close,
            container_id="desk-fake",
            scratch_dir=self._scratch,
            egress=SimpleNamespace(sbx_ip="10.202.1.2"),
        )

    def host_scratch_dir(self, workspace: str) -> Path | None:
        del workspace
        return self._scratch

    async def short_exec_script(
        self,
        workspace: str,
        *,
        guest_script: str,
        timeout_seconds: float = 15.0,
        cache_bucket: str | None = None,
    ) -> tuple[int, str, str]:
        del workspace, timeout_seconds, cache_bucket
        if not guest_script.startswith("/scratch/") or ".." in guest_script:
            raise AssertionError(f"short exec must stay under /scratch: {guest_script}")
        release = await try_acquire_execution_slot()
        if release is None:
            return 1, "", "slot busy"
        try:
            self.execs.append(guest_script)
            host_script = self._scratch / guest_script.removeprefix("/scratch/")
            name = host_script.name
            parent = host_script.parent
            parent.mkdir(parents=True, exist_ok=True)
            if name == "launch.sh":
                (parent / "pid").write_text("4242\n", encoding="utf-8")
                (parent / "log").write_text(self.auto_log, encoding="utf-8")
                self.last_log = parent / "log"
            elif name == "stop.sh":
                pidf = parent / "pid"
                if pidf.exists():
                    pidf.write_text("", encoding="utf-8")
            elif name == "alive.sh":
                pidf = parent / "pid"
                token = (
                    "alive"
                    if pidf.is_file() and pidf.read_text(encoding="utf-8").strip()
                    else "dead"
                )
                return 0, f"{token}\n", ""
            elif name == "bridge_stop_all.sh":
                for pidf in parent.glob("bridge-*.pid"):
                    pidf.write_text("", encoding="utf-8")
            elif name.startswith("bridge_launch_") and name.endswith(".sh"):
                port = name.removeprefix("bridge_launch_").removesuffix(".sh")
                if port.isdigit():
                    (parent / f"bridge-{port}.pid").write_text("4343\n", encoding="utf-8")
            elif name.startswith("bridge_alive_") and name.endswith(".sh"):
                port = name.removeprefix("bridge_alive_").removesuffix(".sh")
                pidf = parent / f"bridge-{port}.pid"
                token = (
                    "alive"
                    if pidf.is_file() and pidf.read_text(encoding="utf-8").strip()
                    else "dead"
                )
                return 0, f"{token}\n", ""
            return 0, "", ""
        finally:
            release()


def _backend(tmp_path: Path, sandbox: GVisorSandbox | SubprocessSandbox) -> ServerWorkspace:
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    return ServerWorkspace(root=root, sandbox=sandbox, location="server")


def _ctx(
    backend: ServerWorkspace,
    *,
    conversation_id: str = CONV_A,
    channel: WorkspaceChannel | None = None,
) -> ToolContext:
    return ToolContext.create(
        execution_id="e-cloud",
        run_id="r-cloud",
        agent_id="w-cloud",
        backend=backend,
        user_id="u-cloud",
        conversation_id=conversation_id,
        workspace_channel=channel,
    )


def _channel() -> WorkspaceChannel:
    from agentcore.runtime.interaction import InteractionRegistry

    return WorkspaceChannel(
        user_id="u-cloud",
        conversation_id=CONV_A,
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        root_id="root-cloud",
    )


def test_sandboxd_exec_allowlist_unchanged():
    assert frozenset({"python3", "node", "bash"}) == EXEC_BINS


async def test_cloud_start_does_not_emit_workspace_op(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch")
    backend = _backend(tmp_path, sandbox)
    execute_calls: list[Any] = []

    async def _spy_execute(req: Any) -> Any:
        execute_calls.append(req)
        raise AssertionError("cloud terminal must not call WorkspaceBackend.execute")

    backend.execute = _spy_execute  # type: ignore[method-assign]
    channel = _channel()
    DELIVERED_EVENTS.clear()
    result = await process_manage(
        {"subcommand": "start", "command": "echo hi"},
        _ctx(backend, channel=channel),
    )
    assert result.success
    assert result.display["subcommand"] == "start"
    assert str(result.display["process_id"]).startswith("tp-")
    assert not DELIVERED_EVENTS
    assert execute_calls == []
    assert any(script.endswith("/launch.sh") for script in sandbox.execs)


async def test_cloud_start_releases_exec_slot_before_wait_for(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="")
    backend = _backend(tmp_path, sandbox)
    task = asyncio.create_task(
        process_manage(
            {
                "subcommand": "start",
                "command": "pnpm dev",
                "wait_for": "READY",
                "wait_timeout_seconds": 3,
            },
            _ctx(backend),
        )
    )
    log_path: Path | None = None
    for _ in range(50):
        if sandbox.last_log is not None:
            log_path = sandbox.last_log
            break
        await asyncio.sleep(0.02)
    assert log_path is not None
    release = await try_acquire_execution_slot(wait_seconds=0.5)
    assert release is not None, "wait_for 轮询不得占住 gVisor 执行位"
    release()
    log_path.write_text("READY listening\n", encoding="utf-8")
    result = await task
    assert result.success
    assert result.display.get("matched") is True
    assert "READY" in (result.display.get("output") or "")


async def test_conversations_do_not_share_a_process_list(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch")
    backend = _backend(tmp_path, sandbox)
    started = await process_manage(
        {"subcommand": "start", "command": "echo a"},
        _ctx(backend, conversation_id=CONV_A),
    )
    assert started.success
    listed_b = await process_manage(
        {"subcommand": "list"},
        _ctx(backend, conversation_id=CONV_B),
    )
    listed_a = await process_manage(
        {"subcommand": "list"},
        _ctx(backend, conversation_id=CONV_A),
    )
    assert "无后台进程" in listed_b.output
    assert started.display["process_id"] in listed_a.output


async def test_restart_honesty_does_not_rebuild_from_leftover_files(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch")
    backend = _backend(tmp_path, sandbox)
    started = await process_manage(
        {"subcommand": "start", "command": "echo hi"},
        _ctx(backend),
    )
    process_id = str(started.display["process_id"])
    leftover = list((tmp_path / "scratch" / "proc").rglob("pid"))
    assert leftover, "pid 应落在 host scratch，不是工作区"
    assert not any(
        str(path).startswith(str((tmp_path / "ws").resolve())) for path in leftover
    )
    from agentcore.tools.sandbox.desk_process import reset_desk_processes_for_tests

    reset_desk_processes_for_tests()
    listed = await process_manage({"subcommand": "list"}, _ctx(backend))
    assert "无后台进程" in listed.output
    read = await process_manage(
        {"subcommand": "read", "process_id": process_id},
        _ctx(backend),
    )
    assert read.success is False
    assert read.metadata.get("code") == "process_not_registered"
    stopped = await process_manage(
        {"subcommand": "stop", "process_id": process_id},
        _ctx(backend),
    )
    assert stopped.success is False
    assert stopped.metadata.get("code") == "process_not_registered"


async def test_close_all_desk_sessions_drops_ledger(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch")
    backend = _backend(tmp_path, sandbox)
    started = await process_manage(
        {"subcommand": "start", "command": "echo hi"},
        _ctx(backend),
    )
    assert started.success
    await close_all_desk_sessions()
    listed = await process_manage({"subcommand": "list"}, _ctx(backend))
    assert "无后台进程" in listed.output


async def test_cloud_long_running_starts_without_default_wait(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="Local: http://localhost:5173/\n")
    backend = _backend(tmp_path, sandbox)
    result = await process_manage(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(backend),
    )
    assert result.success is True
    assert sandbox.execs != []
    assert "wait_for 已命中" not in result.output
    assert "请 read" not in result.output
    assert result.display.get("http_ports") == [5173]
    assert result.display.get("preview_available") is True
    assert "打开预览" in result.output
    assert "http://" not in result.output.split("【就绪判定】")[-1]


async def test_subprocess_sandbox_start_is_cloud_desk_required(tmp_path: Path):
    backend = _backend(tmp_path, SubprocessSandbox())
    channel = _channel()
    DELIVERED_EVENTS.clear()
    result = await process_manage(
        {"subcommand": "start", "command": "echo hi"},
        _ctx(backend, channel=channel),
    )
    assert result.success is False
    assert result.metadata.get("code") == "cloud_desk_required"
    assert not DELIVERED_EVENTS


async def test_cloud_read_and_stop_round_trip(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="Listening on :3000\n")
    backend = _backend(tmp_path, sandbox)
    started = await process_manage(
        {
            "subcommand": "start",
            "command": "pnpm dev",
            "wait_for": "Listening",
        },
        _ctx(backend),
    )
    assert started.success
    assert started.display.get("matched") is True
    assert started.display.get("http_ports") == [3000]
    assert started.display.get("preview_available") is True
    process_id = started.display["process_id"]
    read = await process_manage(
        {"subcommand": "read", "process_id": process_id, "tail_lines": 10},
        _ctx(backend),
    )
    assert read.success
    assert "Listening" in (read.display.get("output") or "")
    stopped = await process_manage(
        {"subcommand": "stop", "process_id": process_id},
        _ctx(backend),
    )
    assert stopped.success
    assert stopped.display["status"] == "exited"
    assert any(script.endswith("/stop.sh") for script in sandbox.execs)


class _PreviewClient:
    def __init__(self) -> None:
        self.registers: list[tuple[str, str, dict[str, Any]]] = []
        self.unregisters: list[tuple[str, str]] = []

    async def preview_register(
        self,
        conversation_id: str,
        process_id: str,
        *,
        upstream_ip: str,
        upstream_port: int,
        app_port: int,
    ) -> None:
        self.registers.append(
            (
                conversation_id,
                process_id,
                {
                    "upstream_ip": upstream_ip,
                    "upstream_port": upstream_port,
                    "app_port": app_port,
                },
            )
        )

    async def preview_unregister(self, conversation_id: str, process_id: str) -> None:
        self.unregisters.append((conversation_id, process_id))


def _expected_bridge_port(process_id: str, app_port: int) -> int:
    digest = hashlib.sha256(f"{process_id}:{int(app_port)}".encode()).digest()
    return 28000 + int.from_bytes(digest, "big") % 20000


def _bridge_launches(sandbox: _FakeDesk) -> list[str]:
    return [
        script
        for script in sandbox.execs
        if script.rsplit("/", 1)[-1].startswith("bridge_launch_")
    ]


async def test_lookup_cloud_preview_is_conversation_scoped(tmp_path: Path):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="Local: http://localhost:5173/\n")
    backend = _backend(tmp_path, sandbox)
    started = await process_manage(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(backend, conversation_id=CONV_A),
    )
    assert started.success
    process_id = str(started.display["process_id"])
    found = lookup_cloud_preview(CONV_A, process_id)
    assert found == CloudPreview(
        conversation_id=CONV_A,
        process_id=process_id,
        status="running",
        http_ports=(5173,),
    )
    assert lookup_cloud_preview(CONV_B, process_id) is None
    assert lookup_cloud_preview(CONV_A, "tp-missing") is None


async def test_ensure_cloud_preview_rejects_unparsed_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="Local: http://localhost:5173/\n")
    backend = _backend(tmp_path, sandbox)
    monkeypatch.setattr(desk_process_mod, "_gvisor_sandbox", lambda: sandbox)
    started = await process_manage(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(backend),
    )
    assert started.success
    process_id = str(started.display["process_id"])
    with pytest.raises(DeskProcessError) as rejected:
        await ensure_cloud_preview(
            str(backend.root.resolve()), CONV_A, process_id, 9999
        )
    assert rejected.value.code == PREVIEW_PORT_NOT_READY
    assert sandbox.execs
    assert not _bridge_launches(sandbox)


async def test_ensure_cloud_preview_registers_bridge_and_stop_unregisters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="Local: http://localhost:5173/\n")
    backend = _backend(tmp_path, sandbox)
    monkeypatch.setattr(desk_process_mod, "_gvisor_sandbox", lambda: sandbox)
    client = _PreviewClient()
    set_sandboxd_client_for_tests(client)
    started = await process_manage(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(backend),
    )
    assert started.success
    process_id = str(started.display["process_id"])
    workspace = str(backend.root.resolve())
    preview = await ensure_cloud_preview(workspace, CONV_A, process_id, 5173)
    assert preview.status == "running"
    assert preview.http_ports == (5173,)
    bridge_py = sandbox._scratch / "preview_bridge.py"
    assert bridge_py.is_file()
    source = bridge_py.read_text(encoding="utf-8")
    assert "0.0.0.0" in source
    assert "127.0.0.1" in source
    assert "preview_url" not in started.display
    assert client.registers == [
        (
            CONV_A,
            process_id,
            {
                "upstream_ip": "10.202.1.2",
                "upstream_port": _expected_bridge_port(process_id, 5173),
                "app_port": 5173,
            },
        )
    ]
    launches = _bridge_launches(sandbox)
    assert len(launches) == 1
    assert launches[0].endswith("/bridge_launch_5173.sh")
    again = await ensure_cloud_preview(workspace, CONV_A, process_id, 5173)
    assert again.http_ports == (5173,)
    assert len(_bridge_launches(sandbox)) == 1
    assert len(client.registers) == 2
    assert desk_preview_upstream(workspace) == ("10.202.1.2", "desk-fake")
    stopped = await process_manage(
        {"subcommand": "stop", "process_id": process_id},
        _ctx(backend),
    )
    assert stopped.success
    assert client.unregisters == [(CONV_A, process_id)]
    assert any(script.endswith("/bridge_stop_all.sh") for script in sandbox.execs)


async def test_ensure_cloud_preview_two_ports_two_bridges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sandbox = _FakeDesk(
        tmp_path / "scratch",
        auto_log="Local: http://localhost:5173/\nStorybook: http://localhost:6006/\n",
    )
    backend = _backend(tmp_path, sandbox)
    monkeypatch.setattr(desk_process_mod, "_gvisor_sandbox", lambda: sandbox)
    client = _PreviewClient()
    set_sandboxd_client_for_tests(client)
    started = await process_manage(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(backend),
    )
    process_id = str(started.display["process_id"])
    assert started.display["http_ports"] == [5173, 6006]
    workspace = str(backend.root.resolve())
    first = await ensure_cloud_preview(workspace, CONV_A, process_id, 5173)
    second = await ensure_cloud_preview(workspace, CONV_A, process_id, 6006)
    assert first.http_ports == (5173, 6006)
    assert second.http_ports == (5173, 6006)
    vite_port = _expected_bridge_port(process_id, 5173)
    story_port = _expected_bridge_port(process_id, 6006)
    assert vite_port != story_port
    assert [entry[2] for entry in client.registers] == [
        {"upstream_ip": "10.202.1.2", "upstream_port": vite_port, "app_port": 5173},
        {"upstream_ip": "10.202.1.2", "upstream_port": story_port, "app_port": 6006},
    ]
    launches = _bridge_launches(sandbox)
    assert len(launches) == 2
    names = {script.rsplit("/", 1)[-1] for script in launches}
    assert names == {"bridge_launch_5173.sh", "bridge_launch_6006.sh"}
    await ensure_cloud_preview(workspace, CONV_A, process_id, 5173)
    assert len(_bridge_launches(sandbox)) == 2
    stopped = await process_manage(
        {"subcommand": "stop", "process_id": process_id},
        _ctx(backend),
    )
    assert stopped.success
    assert client.unregisters == [(CONV_A, process_id)]
    assert any(script.endswith("/bridge_stop_all.sh") for script in sandbox.execs)


async def test_ensure_cloud_preview_requires_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="Local: http://localhost:5173/\n")
    backend = _backend(tmp_path, sandbox)
    monkeypatch.setattr(desk_process_mod, "_gvisor_sandbox", lambda: sandbox)
    started = await process_manage(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(backend),
    )
    process_id = str(started.display["process_id"])
    await process_manage({"subcommand": "stop", "process_id": process_id}, _ctx(backend))
    with pytest.raises(DeskProcessError) as rejected:
        await ensure_cloud_preview(
            str(backend.root.resolve()), CONV_A, process_id, 5173
        )
    assert rejected.value.code == PROCESS_NOT_RUNNING


async def test_close_all_desk_sessions_unregisters_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sandbox = _FakeDesk(tmp_path / "scratch", auto_log="Local: http://localhost:5173/\n")
    backend = _backend(tmp_path, sandbox)
    monkeypatch.setattr(desk_process_mod, "_gvisor_sandbox", lambda: sandbox)
    client = _PreviewClient()
    set_sandboxd_client_for_tests(client)
    started = await process_manage(
        {"subcommand": "start", "command": "npm run dev"},
        _ctx(backend),
    )
    process_id = str(started.display["process_id"])
    await close_all_desk_sessions()
    assert client.unregisters == [(CONV_A, process_id)]
    listed = await process_manage({"subcommand": "list"}, _ctx(backend))
    assert "无后台进程" in listed.output
