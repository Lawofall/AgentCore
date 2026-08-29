"""Idle cloud-desk reap: kill guest (no freeze), keep disk, lazy-create next use."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

import agentcore.runtime.browser.registry as breg
import agentcore.tools.sandbox.desk_process as desk_process_mod
import agentcore.tools.sandbox.gvisor as gvisor_mod
from agentcore.config import settings
from agentcore.core.errors import SandboxError
from agentcore.runtime.browser.registry import BrowserSessionRegistry, _Entry
from agentcore.tools.sandbox.desk_process import (
    PROCESS_NOT_REGISTERED,
    DeskProcessError,
    _Record,
    _require_record,
)
from agentcore.tools.sandbox.gvisor import (
    GVisorSandbox,
    attach_workspace_desk,
    reap_idle_desks,
    reset_desk_sessions_for_tests,
)
from agentcore.tools.sandbox.limits import reset_execution_slots
from agentcore.tools.sandbox.protocol import ExecutionRequest
from agentcore.tools.sandbox.sandboxd import SandboxdError, set_sandboxd_client_for_tests


class _TrackingClient:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, str]] = []
        self.kills: list[str] = []
        self.deletes: list[str] = []
        self.delete_forced: list[bool] = []
        self.exec_timeouts: list[float] = []

    async def ping(self) -> None:
        return None

    async def health(self, shape: str) -> tuple[bool, str]:
        return True, ""

    async def start_detach(
        self, *, bundle_dir: str, container_id: str, netns_path: str, **_kwargs: object
    ) -> None:
        self.start_calls.append(
            {
                "bundle_dir": bundle_dir,
                "container_id": container_id,
                "netns_path": netns_path,
            }
        )

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        self.kills.append(container_id)

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        self.deletes.append(container_id)
        self.delete_forced.append(force)

    async def exec_wait(self, **kwargs: object) -> tuple[int, str, str]:
        timeout = kwargs.get("timeout_seconds")
        if timeout is not None:
            self.exec_timeouts.append(float(timeout))
        return 0, "", ""


class _LiveBrowser:
    def __init__(self, desk_container_id: str) -> None:
        self.alive = True
        self.desk_container_id = desk_container_id
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        self.alive = False


@pytest.fixture
def _desk_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    reset_execution_slots()
    reset_desk_sessions_for_tests()
    clock = {"now": 1_000.0}
    monkeypatch.setattr(gvisor_mod, "_IS_LINUX", True)
    monkeypatch.setattr(gvisor_mod, "_now", lambda: clock["now"])
    monkeypatch.setattr(settings, "gvisor_desk_idle_ttl_seconds", 10.0)
    monkeypatch.setattr(settings, "gvisor_max_concurrent_executions", 2)
    monkeypatch.setattr(settings, "gvisor_slot_wait_seconds", 1.0)

    async def _fake_egress(*, cache_bucket=None):  # noqa: ANN001, ARG001
        class _S:
            netns_path = "/var/run/netns/fake"
            cache_host_dir = tmp_path / "pkg-cache" / "b"
            proxy_url = "http://10.0.0.1:8898"
            host_ip = "10.0.0.1"

            async def close(self):
                return None

        _S.cache_host_dir.mkdir(parents=True, exist_ok=True)
        return _S()

    monkeypatch.setattr(
        "agentcore.tools.sandbox.egress.open_package_egress",
        _fake_egress,
    )
    client = _TrackingClient()
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    ws = tmp_path / "workspace"
    ws.mkdir()
    yield clock, client, sandbox, ws
    reset_desk_sessions_for_tests()
    reset_execution_slots()


def test_compat_modules_and_fields_are_gone():
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agentcore.tools.sandbox.staging")
    from agentcore.tools.sandbox.browser import netns
    from agentcore.tools.sandbox.protocol import ExecutionRequest

    assert not hasattr(netns, "browser_netns_health")
    assert not hasattr(netns, "probe_browser_netns_at_startup")
    assert not hasattr(netns, "SessionNetns")
    assert not hasattr(ExecutionRequest, "registry_egress")
    from agentcore.tools.sandbox.protocol import ExecutionResult

    assert not hasattr(ExecutionResult, "write_back_skipped")
    src = inspect.getsource(gvisor_mod._DeskSession.close)
    assert "freeze" not in src.lower()
    assert "pause" not in src.lower()
    assert "kill" in src
    assert "delete" in src


@pytest.mark.asyncio
async def test_idle_desk_is_reaped(_desk_env):
    clock, client, sandbox, ws = _desk_env
    await sandbox.ensure_workspace_desk(str(ws))
    assert sandbox.host_scratch_dir(str(ws)) is not None
    assert len(client.start_calls) == 1
    first_bundle = client.start_calls[0]["bundle_dir"]
    first_id = client.start_calls[0]["container_id"]

    clock["now"] = 1_011.0
    closed = await reap_idle_desks()
    assert closed == 1
    assert sandbox.host_scratch_dir(str(ws)) is None
    assert first_id in client.kills
    assert first_id in client.deletes
    assert first_bundle not in {c["bundle_dir"] for c in client.start_calls[1:]}


@pytest.mark.asyncio
async def test_busy_inflight_desk_is_not_reaped(_desk_env):
    clock, client, sandbox, ws = _desk_env
    await sandbox._ensure_desk(str(ws), cache_bucket=None, pin=True)
    clock["now"] = 1_011.0
    assert await reap_idle_desks() == 0
    assert sandbox.host_scratch_dir(str(ws)) is not None
    assert client.kills == []
    await gvisor_mod._unpin_desk(str(ws))


@pytest.mark.asyncio
async def test_running_desk_process_blocks_reap(_desk_env):
    clock, _client, sandbox, ws = _desk_env
    await sandbox.ensure_workspace_desk(str(ws))
    key = str(ws.resolve())
    desk_process_mod._records["tp-old"] = _Record(
        process_id="tp-old",
        conversation_id="c1",
        desk_key=key,
        command="pnpm dev",
        name="vite",
        cwd="",
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        exit_code=None,
        host_dir=ws / "proc",
        guest_dir="/scratch/proc/c1/tp-old",
    )
    clock["now"] = 1_011.0
    assert await reap_idle_desks() == 0
    assert sandbox.host_scratch_dir(str(ws)) is not None
    assert "tp-old" in desk_process_mod._records


@pytest.mark.asyncio
async def test_live_sandbox_browser_blocks_reap(monkeypatch, _desk_env):
    clock, _client, sandbox, ws = _desk_env
    await sandbox.ensure_workspace_desk(str(ws))
    desk = next(iter(gvisor_mod._desks.values()))
    reg = BrowserSessionRegistry()
    monkeypatch.setattr(breg, "_registry", reg)
    live = _LiveBrowser(desk.container_id)
    reg._entries["s1"] = _Entry(
        session_id="s1",
        conversation_id="c1",
        session=live,  # type: ignore[arg-type]
        host_kind="sandbox",
    )
    clock["now"] = 1_011.0
    assert await reap_idle_desks() == 0
    assert sandbox.host_scratch_dir(str(ws)) is not None
    assert live.closed is False


@pytest.mark.asyncio
async def test_reap_drops_ledger_old_process_id_fails_and_reattach_is_new_guest(
    _desk_env,
):
    clock, client, sandbox, ws = _desk_env
    await sandbox.ensure_workspace_desk(str(ws))
    key = str(ws.resolve())
    desk_process_mod._records["tp-old"] = _Record(
        process_id="tp-old",
        conversation_id="c1",
        desk_key=key,
        command="sleep 999",
        name=None,
        cwd="",
        status="exited",
        started_at=datetime.now(UTC).isoformat(),
        exit_code=0,
        host_dir=ws / "proc",
        guest_dir="/scratch/proc/c1/tp-old",
    )
    first = client.start_calls[0]
    clock["now"] = 1_011.0
    assert await reap_idle_desks() == 1
    assert desk_process_mod._records == {}
    with pytest.raises(DeskProcessError) as lost:
        await _require_record("c1", "tp-old")
    assert lost.value.code == PROCESS_NOT_REGISTERED

    attach = await attach_workspace_desk(
        str(ws), runtime_root=sandbox._runtime_root
    )
    assert len(client.start_calls) == 2
    assert client.start_calls[1]["bundle_dir"] != first["bundle_dir"]
    assert attach.container_id == first["container_id"]
    assert sandbox.host_scratch_dir(str(ws)) is not None


@pytest.mark.asyncio
async def test_reap_closes_sandbox_browsers_not_local_bridge(monkeypatch, _desk_env):
    clock, _client, sandbox, ws = _desk_env
    await sandbox.ensure_workspace_desk(str(ws))
    desk = next(iter(gvisor_mod._desks.values()))
    reg = BrowserSessionRegistry()
    monkeypatch.setattr(breg, "_registry", reg)
    leftover = _LiveBrowser(desk.container_id)
    leftover.alive = False
    local = _LiveBrowser(desk.container_id)
    reg._entries["sbx"] = _Entry(
        session_id="sbx",
        conversation_id="c1",
        session=leftover,  # type: ignore[arg-type]
        host_kind="sandbox",
    )
    reg._entries["loc"] = _Entry(
        session_id="loc",
        conversation_id="c2",
        session=local,  # type: ignore[arg-type]
        host_kind="local",
    )
    clock["now"] = 1_011.0
    assert await reap_idle_desks() == 1
    assert leftover.closed is True
    assert "sbx" not in reg._entries
    assert "loc" in reg._entries
    assert local.closed is False


@pytest.mark.asyncio
async def test_empty_desk_map_is_noop_for_bridge(_desk_env):
    _clock, client, _sandbox, _ws = _desk_env
    assert await reap_idle_desks() == 0
    assert client.kills == []
    assert client.deletes == []
    assert client.start_calls == []


class _StaleThenOk(_TrackingClient):
    def __init__(self, stderr: str) -> None:
        super().__init__()
        self._stderr = stderr

    async def start_detach(
        self, *, bundle_dir: str, container_id: str, netns_path: str, **_kwargs: object
    ) -> None:
        await super().start_detach(
            bundle_dir=bundle_dir, container_id=container_id, netns_path=netns_path
        )
        if len(self.start_calls) == 1:
            raise SandboxdError(self._stderr)


class _AlwaysStale(_TrackingClient):
    async def start_detach(
        self, *, bundle_dir: str, container_id: str, netns_path: str, **_kwargs: object
    ) -> None:
        await super().start_detach(
            bundle_dir=bundle_dir, container_id=container_id, netns_path=netns_path
        )
        raise SandboxdError("container already exists")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stderr",
    [
        "container already exists",
        "cannot lock container metadata",
    ],
)
async def test_stale_desk_delete_force_retries_start_once(_desk_env, stderr: str):
    _clock, _old, sandbox, ws = _desk_env
    client = _StaleThenOk(stderr)
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    result = await sandbox.execute(
        ExecutionRequest(
            code="print(1)",
            language="python",
            cwd=str(ws),
            timeout_seconds=9,
        )
    )
    assert result.success is True
    assert len(client.start_calls) == 2
    assert len(client.deletes) == 1
    assert client.delete_forced == [True]
    assert client.start_calls[0]["container_id"] == client.deletes[0]
    assert client.exec_timeouts == [9.0]
    assert sandbox.host_scratch_dir(str(ws)) is not None


@pytest.mark.asyncio
async def test_stale_desk_retry_still_fails_is_start_failure_and_stops(_desk_env):
    _clock, _old, sandbox, ws = _desk_env
    client = _AlwaysStale()
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    with pytest.raises(SandboxError, match="执行环境启动失败") as failed:
        await sandbox.execute(
            ExecutionRequest(
                code="print(1)",
                language="python",
                cwd=str(ws),
                timeout_seconds=15,
            )
        )
    assert "forced stop after" not in str(failed.value)
    assert len(client.start_calls) == 2
    assert len(client.deletes) == 1
    assert client.exec_timeouts == []


@pytest.mark.asyncio
async def test_exec_wait_uses_request_timeout_not_boot_rpc(_desk_env):
    _clock, client, sandbox, ws = _desk_env
    result = await sandbox.execute(
        ExecutionRequest(
            code="print(1)",
            language="python",
            cwd=str(ws),
            timeout_seconds=12,
        )
    )
    assert result.success is True
    assert client.exec_timeouts == [12.0]
    assert len(client.start_calls) == 1
