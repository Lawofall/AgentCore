"""GVisorSandbox 云桌 bind 落盘（mock runsc，Windows / 无 runsc 主机可跑）。

真 runsc 是 Linux-only；这里用假 runsc 二进制模拟 desk guest：
``run -d`` 立即成功；``exec``（测试 Loopback 仍带 ``--bundle=``）往
``/workspace`` rw-bind 真盘写产物。
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

import agentcore.tools.sandbox.gvisor as gvisor_mod
from agentcore.config import settings
from agentcore.core.errors import SandboxError
from agentcore.tools.sandbox.gvisor import GVisorSandbox, reset_desk_sessions_for_tests
from agentcore.tools.sandbox.limits import reset_execution_slots
from agentcore.tools.sandbox.protocol import ExecutionRequest
from agentcore.tools.sandbox.sandboxd import (
    SandboxdError,
    SandboxdRpcError,
    SandboxdUnavailableError,
    set_sandboxd_client_for_tests,
)
from tests.sandboxd_testutil import LoopbackRunscClient


@pytest.fixture(autouse=True)
def _fresh_slots_linux_and_egress(monkeypatch, tmp_path):
    reset_execution_slots()
    reset_desk_sessions_for_tests()
    monkeypatch.setattr(gvisor_mod, "_IS_LINUX", True)
    monkeypatch.setattr(settings, "gvisor_max_concurrent_executions", 2)
    monkeypatch.setattr(settings, "gvisor_slot_wait_seconds", 1.0)
    monkeypatch.setattr(settings, "gvisor_timeout_max_seconds", 30)
    monkeypatch.setattr(settings, "gvisor_memory_limit_mb", 256)

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
    yield
    reset_desk_sessions_for_tests()
    reset_execution_slots()


def _install_fake_runsc(
    tmp_path: Path,
    *,
    artifact_rel: str = "hello.txt",
    artifact_text: str = "from-sandbox",
    hang: bool = False,
    write_artifact: bool = True,
) -> str:
    """Install a cross-platform fake ``runsc``.

    ``-detach`` → exit 0 (desk start). Otherwise write ``artifact_rel``
    into the ``/workspace`` rw-bind (exec / one-shot).
    """
    impl = tmp_path / "fake_runsc_impl.py"
    impl.write_text(
        textwrap.dedent(
            f"""\
            import json
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            if "--version" in args or (args[:1] == ["--version"]):
                print("runsc version fake")
                raise SystemExit(0)
            if args[:1] in (["kill"], ["delete"]):
                raise SystemExit(0)
            if "-detach" in args or "--detach" in args:
                raise SystemExit(0)

            bundle = None
            for a in args:
                if a.startswith("--bundle="):
                    bundle = a.split("=", 1)[1]
            if not bundle:
                print("fake_runsc: missing --bundle=", file=sys.stderr)
                raise SystemExit(2)

            cfg = json.loads((Path(bundle) / "config.json").read_text(encoding="utf-8"))
            args_in = cfg.get("process", {{}}).get("args") or []
            if args_in == ["/bin/true"]:
                raise SystemExit(0)

            ws = None
            for m in cfg.get("mounts", []):
                if m.get("destination") == "/workspace":
                    if m.get("type") == "bind" and "rw" in (m.get("options") or []):
                        ws = Path(m["source"])
                        break
            if ws is None:
                print("fake_runsc: no /workspace rw-bind", file=sys.stderr)
                raise SystemExit(2)

            if {hang!r}:
                import time
                time.sleep(99999)

            if not {write_artifact!r}:
                raise SystemExit(0)

            rel = Path({artifact_rel!r})
            dest = ws / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text({artifact_text!r}, encoding="utf-8")
            print("wrote", rel.as_posix())
            raise SystemExit(0)
            """
        ),
        encoding="utf-8",
    )
    if sys.platform == "win32":
        wrapper = tmp_path / "fake_runsc.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n',
            encoding="utf-8",
        )
        return str(wrapper)
    wrapper = tmp_path / "fake_runsc"
    wrapper.write_text(
        f"#!/usr/bin/env python3\nimport runpy\nrunpy.run_path({str(impl)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


def _age(path: Path, seconds: float = 60.0) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def _bind_loopback(sandbox: GVisorSandbox) -> LoopbackRunscClient:
    """Drive fake ``runsc`` through sandboxd's test client (never production)."""
    client = LoopbackRunscClient(
        runsc_path=sandbox._runsc,  # noqa: SLF001
        runtime_root=sandbox._runtime_root,  # noqa: SLF001
    )
    set_sandboxd_client_for_tests(client)
    return client


def _desk_oci(sandbox: GVisorSandbox, tmp_path: Path, **kwargs):
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    ws = Path(kwargs.get("workspace", tmp_path / "ws"))
    ws.mkdir(exist_ok=True)
    scratch = Path(kwargs.get("scratch", tmp_path / "scratch"))
    scratch.mkdir(exist_ok=True)
    return sandbox._build_desk_oci(  # noqa: SLF001
        workspace=str(ws),
        scratch_dir=str(scratch),
        netns_path=str(kwargs.get("netns_path", "/var/run/netns/acpkg1")),
        cache_host_dir=str(cache),
        proxy_url="http://10.0.0.1:8898",
        memory_limit_mb=kwargs.get("memory_limit_mb"),
    )


async def test_gvisor_write_back_lands_artifact_in_real_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "seed.txt").write_text("keep", encoding="utf-8")
    _age(ws / "seed.txt")

    runsc = _install_fake_runsc(tmp_path)
    sandbox = GVisorSandbox(
        runsc_path=runsc,
        runtime_root=str(tmp_path / "rt"),
    )
    _bind_loopback(sandbox)
    await sandbox.ensure_workspace_desk(str(ws))

    result = await sandbox.execute(
        ExecutionRequest(
            code="print('ignored-by-fake')",
            language="python",
            cwd=str(ws),
            timeout_seconds=10,
        )
    )

    assert result.success is True
    assert result.written_files == ["hello.txt"]
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "from-sandbox"
    assert (ws / "seed.txt").read_text(encoding="utf-8") == "keep"


async def test_gvisor_readonly_script_does_not_claim_written_files(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    seed = ws / "seed.txt"
    seed.write_text("keep", encoding="utf-8")
    _age(seed)
    seed_mtime = seed.stat().st_mtime_ns

    sandbox = GVisorSandbox(
        runsc_path=_install_fake_runsc(tmp_path, write_artifact=False),
        runtime_root=str(tmp_path / "rt"),
    )
    _bind_loopback(sandbox)
    await sandbox.ensure_workspace_desk(str(ws))
    result = await sandbox.execute(
        ExecutionRequest(
            code="print('ignored-by-fake')",
            language="python",
            cwd=str(ws),
            timeout_seconds=10,
        )
    )

    assert result.success is True
    assert result.written_files == []
    assert seed.read_text(encoding="utf-8") == "keep"
    assert seed.stat().st_mtime_ns == seed_mtime


async def test_gvisor_bind_reports_only_actual_content_change(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "seed.txt").write_text("keep", encoding="utf-8")
    (ws / "other.txt").write_text("untouched", encoding="utf-8")
    _age(ws / "seed.txt")
    _age(ws / "other.txt")

    sandbox = GVisorSandbox(
        runsc_path=_install_fake_runsc(
            tmp_path,
            artifact_rel="seed.txt",
            artifact_text="changed-in-sandbox",
        ),
        runtime_root=str(tmp_path / "rt"),
    )
    _bind_loopback(sandbox)
    await sandbox.ensure_workspace_desk(str(ws))
    result = await sandbox.execute(
        ExecutionRequest(
            code="print('ignored-by-fake')",
            language="python",
            cwd=str(ws),
            timeout_seconds=10,
        )
    )

    assert result.success is True
    assert result.written_files == ["seed.txt"]
    assert (ws / "seed.txt").read_text(encoding="utf-8") == "changed-in-sandbox"
    assert (ws / "other.txt").read_text(encoding="utf-8") == "untouched"


async def test_gvisor_timeout_does_not_claim_copy_out(tmp_path: Path):
    import time

    ws = tmp_path / "workspace"
    ws.mkdir()
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))

    class _TimeoutClient(LoopbackRunscClient):
        async def start_detach(self, **kwargs):  # noqa: ANN003, ARG002
            return None

        async def exec_wait(self, **kwargs):  # noqa: ANN003, ARG002
            raise SandboxdError("loopback runsc timeout", code="sandboxd_timeout")

        async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
            return None

        async def delete(self, container_id: str, *, force: bool = True) -> None:
            return None

    set_sandboxd_client_for_tests(
        _TimeoutClient(
            runsc_path=sandbox._runsc,  # noqa: SLF001
            runtime_root=sandbox._runtime_root,  # noqa: SLF001
        )
    )

    await sandbox.ensure_workspace_desk(str(ws))
    start = time.monotonic()
    result = await sandbox._execute_in_slot(  # noqa: SLF001
        ExecutionRequest(code="x", language="python", cwd=str(ws), timeout_seconds=1),
        start,
    )

    assert result.success is False
    assert "Timeout" in result.stderr or "超时" in result.stderr or "中断" in result.stderr
    assert "未写回" not in result.stderr
    assert not (ws / "hello.txt").exists()
    assert result.written_files is None


def test_desk_oci_rw_binds_workspace_with_app_uid(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cfg = _desk_oci(sandbox, tmp_path, memory_limit_mb=256)
    mounts = {m["destination"]: m for m in cfg["mounts"]}
    assert mounts["/workspace"]["type"] == "bind"
    assert "rw" in mounts["/workspace"]["options"]
    assert "/workspace-seed" not in mounts
    assert mounts["/workspace"]["type"] != "tmpfs"
    user = cfg["process"]["user"]
    assert user["uid"] != 65534
    assert user["gid"] != 65534
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        assert user["uid"] == int(getuid())
    assert cfg["linux"]["resources"]["memory"]["limit"] == 256 * 1024 * 1024
    net = [n for n in cfg["linux"]["namespaces"] if n["type"] == "network"]
    assert len(net) == 1
    assert net[0].get("path")


def test_oci_config_json_roundtrip_shape(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cfg = _desk_oci(sandbox, tmp_path)
    dumped = json.dumps(cfg)
    assert '"cwd": "/workspace"' in dumped
    assert cfg["process"]["args"] == ["sleep", "infinity"]


def test_runsc_run_cmd_is_shape_net_detach(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cmd = sandbox._build_run_cmd(  # noqa: SLF001
        bundle_dir="/tmp/bundle",
        container_id="agentcore-test",
        network_mode="none",
    )
    assert cmd[0] == sandbox._runsc  # noqa: SLF001
    run_idx = cmd.index("run")
    assert "--rootless" not in cmd[:run_idx]
    assert "--platform=systrap" in cmd[:run_idx]
    assert "--network=sandbox" in cmd[:run_idx]
    assert "--ignore-cgroups" in cmd[:run_idx]
    assert cmd[run_idx + 1] == "-detach"
    assert cmd[run_idx + 2] == "--bundle=/tmp/bundle"
    assert cmd[run_idx + 3] == "agentcore-test"


def test_runsc_run_cmd_ignores_legacy_network_mode(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cmd = sandbox._build_run_cmd(  # noqa: SLF001
        bundle_dir="/tmp/bundle",
        container_id="agentcore-test",
        network_mode="restricted",
    )
    run_idx = cmd.index("run")
    assert "--network=sandbox" in cmd[:run_idx]
    assert "--network=host" not in cmd[:run_idx]
    assert "--rootless" not in cmd[:run_idx]


async def test_health_check_smoke_run(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    _bind_loopback(sandbox)
    assert await sandbox.health_check() is True
    assert sandbox.last_health_failure is None


@pytest.mark.asyncio
async def test_health_check_not_linux_sets_failure_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(gvisor_mod, "_IS_LINUX", False)
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    assert await sandbox.health_check() is False
    assert sandbox.last_health_failure is not None
    assert sandbox.last_health_failure[0] == "not_linux"
    assert sandbox.last_health_failure[1] and "platform=" in sandbox.last_health_failure[1]
    assert sandbox.last_health_failure_code == "exec_env_not_linux"
    assert "not_linux" in sandbox.last_health_evidence


@pytest.mark.asyncio
async def test_health_check_sandboxd_unavailable(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))

    class _Down(LoopbackRunscClient):
        async def health(self, shape):  # noqa: ANN001
            raise SandboxdUnavailableError("no daemon")

    set_sandboxd_client_for_tests(
        _Down(
            runsc_path=sandbox._runsc,  # noqa: SLF001
            runtime_root=sandbox._runtime_root,  # noqa: SLF001
        )
    )
    assert await sandbox.health_check() is False
    assert sandbox.last_health_failure is not None
    assert sandbox.last_health_failure[0] == "sandboxd_unavailable"
    assert sandbox.last_health_failure_code == "exec_env_sandbox_unavailable"


@pytest.mark.asyncio
async def test_health_check_probes_net_shape_only(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    shapes: list[str] = []

    class _Probe(LoopbackRunscClient):
        async def health(self, shape):  # noqa: ANN001
            shapes.append(shape)
            return True, ""

    set_sandboxd_client_for_tests(
        _Probe(
            runsc_path=sandbox._runsc,  # noqa: SLF001
            runtime_root=sandbox._runtime_root,  # noqa: SLF001
        )
    )
    assert await sandbox.health_check() is True
    assert shapes == ["net"]


def test_resolve_runtime_root_uses_settings_default(monkeypatch, tmp_path: Path):
    """Default is under data_dir — no /tmp legacy redirect."""
    safe = str(tmp_path / "data" / "sandbox")
    monkeypatch.setattr(settings, "gvisor_runtime_root", safe)
    assert gvisor_mod._resolve_runtime_root(None) == safe  # noqa: SLF001
    assert "/tmp/agentcore-sandbox" not in gvisor_mod._resolve_runtime_root(None)  # noqa: SLF001


def test_resolve_runtime_root_keeps_explicit_override(tmp_path: Path):
    explicit = str(tmp_path / "custom-rt")
    assert gvisor_mod._resolve_runtime_root(explicit) == explicit  # noqa: SLF001


def test_gvisor_runtime_root_settings_default_not_tmp_legacy():
    """Class default must land on the data volume path, not /tmp legacy."""
    from agentcore.config.workspace import WorkspaceSettings

    assert "tmp" not in WorkspaceSettings().gvisor_runtime_root.replace("\\", "/")


def test_desk_oci_merges_browser_resources(tmp_path: Path, monkeypatch):
    browsers = tmp_path / "ms-playwright"
    browsers.mkdir()
    monkeypatch.setattr(settings, "browser_playwright_browsers_path", str(browsers))
    monkeypatch.setattr(settings, "gvisor_memory_limit_mb", 512)
    monkeypatch.setattr(settings, "browser_sandbox_memory_limit_mb", 2048)
    monkeypatch.setattr(settings, "browser_sandbox_pids_limit", 512)
    monkeypatch.setattr(settings, "browser_sandbox_cpu_limit", 2.0)
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cfg = _desk_oci(sandbox, tmp_path)
    mounts = {m["destination"]: m for m in cfg["mounts"]}
    tmp_opts = mounts["/tmp"]["options"]
    assert "size=512m" in tmp_opts
    assert "mode=1777" in tmp_opts
    assert str(browsers) in mounts
    assert "ro" in mounts[str(browsers)]["options"]
    assert cfg["linux"]["resources"]["memory"]["limit"] == 2048 * 1024 * 1024
    assert cfg["linux"]["resources"]["pids"]["limit"] == 512
    assert cfg["linux"]["resources"]["cpu"]["quota"] == 200000
    assert cfg["process"]["user"]["uid"] != 65534


def test_desk_oci_memory_override_still_wins(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cfg = _desk_oci(sandbox, tmp_path, memory_limit_mb=256)
    assert cfg["linux"]["resources"]["memory"]["limit"] == 256 * 1024 * 1024
    assert cfg["process"]["user"]["uid"] != 65534


def test_start_detach_rpc_budget_is_minutes_not_exec_cap():
    from agentcore.tools.sandbox.sandboxd.client import (
        _RPC_TIMEOUT,
        _START_DETACH_RPC_TIMEOUT,
    )

    assert _RPC_TIMEOUT == 30.0
    assert _START_DETACH_RPC_TIMEOUT > 60.0
    assert _START_DETACH_RPC_TIMEOUT < 1200.0


async def test_execute_without_desk_does_not_start_detach(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    starts: list[int] = []

    class _MustNotBoot(LoopbackRunscClient):
        async def start_detach(self, **kwargs):  # noqa: ANN003, ARG002
            starts.append(1)
            raise AssertionError("execute must not start_detach")

        async def exec_wait(self, **kwargs):  # noqa: ANN003, ARG002
            raise AssertionError("exec_wait must not run without a desk")

        async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
            return None

        async def delete(self, container_id: str, *, force: bool = True) -> None:
            return None

    set_sandboxd_client_for_tests(
        _MustNotBoot(
            runsc_path=sandbox._runsc,  # noqa: SLF001
            runtime_root=sandbox._runtime_root,  # noqa: SLF001
        )
    )

    with pytest.raises(SandboxError, match="云端隔离执行环境当前不可用") as failed:
        await sandbox._execute_in_slot(  # noqa: SLF001
            ExecutionRequest(
                code="x", language="python", cwd=str(ws), timeout_seconds=15
            ),
            time.monotonic(),
        )
    assert starts == []
    assert "forced stop after" not in str(failed.value)
    assert failed.value.details.get("code") == "exec_env_sandbox_unavailable"


@pytest.mark.parametrize("rpc_code", ["sandboxd_start_timeout", "sandboxd_timeout"])
@pytest.mark.asyncio
async def test_desk_start_rpc_timeout_closes_host_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rpc_code: str
):
    from agentcore.tools.sandbox.cloud_health import (
        cloud_sandbox_health,
        set_cloud_sandbox_health_for_tests,
    )
    from agentcore.tools.sandbox.exec_env import EXEC_ENV_SANDBOX_UNAVAILABLE_CODE

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)

    ws = tmp_path / "workspace"
    ws.mkdir()
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    starts: list[int] = []

    class _BootRpcTimeout(LoopbackRunscClient):
        async def start_detach(self, **kwargs):  # noqa: ANN003, ARG002
            starts.append(1)
            raise SandboxdRpcError("start-detach timed out", code=rpc_code)

        async def exec_wait(self, **kwargs):  # noqa: ANN003, ARG002
            raise AssertionError("exec_wait must not run before desk is ready")

        async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
            return None

        async def delete(self, container_id: str, *, force: bool = True) -> None:
            return None

    set_sandboxd_client_for_tests(
        _BootRpcTimeout(
            runsc_path=sandbox._runsc,  # noqa: SLF001
            runtime_root=sandbox._runtime_root,  # noqa: SLF001
        )
    )
    with pytest.raises(SandboxError, match="云端隔离执行环境当前不可用") as failed:
        await sandbox.ensure_workspace_desk(str(ws))
    assert failed.value.details.get("code") == EXEC_ENV_SANDBOX_UNAVAILABLE_CODE
    assert cloud_sandbox_health() is False
    assert starts == [1]
    with pytest.raises(SandboxError, match="云端隔离执行环境当前不可用"):
        await sandbox.ensure_workspace_desk(str(ws))
    assert starts == [1]


async def test_exec_sandboxd_error_is_not_desk_start_failure(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    sandbox = GVisorSandbox(
        runsc_path=_install_fake_runsc(tmp_path, write_artifact=False),
        runtime_root=str(tmp_path / "rt"),
    )
    inner = _bind_loopback(sandbox)
    await sandbox.ensure_workspace_desk(str(ws))

    async def _boom(**kwargs):  # noqa: ANN003
        raise SandboxdError("exec blew up")

    inner.exec_wait = _boom  # type: ignore[method-assign]
    with pytest.raises(SandboxError, match="云桌执行失败") as failed:
        await sandbox.execute(
            ExecutionRequest(
                code="x", language="python", cwd=str(ws), timeout_seconds=10
            )
        )
    assert "启动失败" not in str(failed.value)
    assert failed.value.details.get("code") != "exec_env_sandbox_unavailable"


async def test_dead_guest_is_dropped_and_exec_does_not_recreate(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    sandbox = GVisorSandbox(
        runsc_path=_install_fake_runsc(tmp_path, write_artifact=False),
        runtime_root=str(tmp_path / "rt"),
    )
    inner = _bind_loopback(sandbox)
    await sandbox.ensure_workspace_desk(str(ws))
    calls = {"n": 0}

    async def _dead(**kwargs):  # noqa: ANN003
        calls["n"] += 1
        raise SandboxdError("container not found")

    inner.exec_wait = _dead  # type: ignore[method-assign]
    with pytest.raises(SandboxError, match="云端隔离执行环境当前不可用"):
        await sandbox.execute(
            ExecutionRequest(
                code="print(1)", language="python", cwd=str(ws), timeout_seconds=10
            )
        )
    assert calls["n"] == 1
    assert sandbox.host_scratch_dir(str(ws)) is None


@pytest.mark.asyncio
async def test_ensure_desk_fails_fast_when_cloud_health_false(tmp_path: Path):
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests
    from agentcore.tools.sandbox.exec_env import EXEC_ENV_SANDBOX_UNAVAILABLE_CODE

    ws = tmp_path / "workspace"
    ws.mkdir()
    sandbox = GVisorSandbox(
        runsc_path=_install_fake_runsc(tmp_path, write_artifact=False),
        runtime_root=str(tmp_path / "rt"),
    )
    set_cloud_sandbox_health_for_tests(False)
    starts: list[int] = []

    class _NoStart:
        async def start_detach(self, **kwargs):  # noqa: ANN003, ARG002
            starts.append(1)
            raise AssertionError("must not start_detach when host is known unhealthy")

    set_sandboxd_client_for_tests(_NoStart())  # type: ignore[arg-type]
    with pytest.raises(SandboxError, match="云端隔离执行环境当前不可用") as failed:
        await sandbox.ensure_workspace_desk(str(ws))
    assert starts == []
    assert failed.value.details.get("code") == EXEC_ENV_SANDBOX_UNAVAILABLE_CODE
