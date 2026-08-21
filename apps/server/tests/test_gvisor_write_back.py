"""GVisorSandbox 产物写回端到端（mock runsc，Windows / 无 runsc 主机可跑）。

真 runsc 是 Linux-only；这里用假 runsc 二进制模拟「容器内写文件」：
解析 ``--bundle=`` → 往 staging workspace 落产物 → 退出 0，让 copy-out 腿跑通。
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

import agentcore.tools.sandbox.gvisor as gvisor_mod
from agentcore.config import settings
from agentcore.tools.sandbox.gvisor import GVisorSandbox
from agentcore.tools.sandbox.limits import reset_execution_slots
from agentcore.tools.sandbox.protocol import ExecutionRequest
from agentcore.tools.sandbox.sandboxd import (
    SandboxdError,
    SandboxdUnavailable,
    set_sandboxd_client_for_tests,
)
from tests.sandboxd_testutil import LoopbackRunscClient


@pytest.fixture(autouse=True)
def _fresh_slots_and_linux(monkeypatch):
    reset_execution_slots()
    monkeypatch.setattr(gvisor_mod, "_IS_LINUX", True)
    monkeypatch.setattr(settings, "gvisor_max_concurrent_executions", 2)
    monkeypatch.setattr(settings, "gvisor_slot_wait_seconds", 1.0)
    monkeypatch.setattr(settings, "gvisor_timeout_max_seconds", 30)
    monkeypatch.setattr(settings, "gvisor_memory_limit_mb", 256)
    monkeypatch.setattr(settings, "gvisor_stage_max_bytes", 16 * 1024 * 1024)
    monkeypatch.setattr(settings, "gvisor_write_back_max_bytes", 8 * 1024 * 1024)
    monkeypatch.setattr(settings, "gvisor_write_back_max_files", 50)
    yield
    reset_execution_slots()


def _install_fake_runsc(
    tmp_path: Path,
    *,
    artifact_rel: str = "out/hello.txt",
    hang: bool = False,
    rematerialize: bool = False,
    rematerialize_edit: dict[str, str] | None = None,
) -> str:
    """Install a cross-platform fake ``runsc``.

    Default: write ``artifact_rel`` into the staging mount (legacy bind-write).
    ``rematerialize``: emit the production artifact trailer (whole-tree
    base64) so host ``write_bytes`` refreshes mtime — the read-only honesty path.
    """
    impl = tmp_path / "fake_runsc_impl.py"
    impl.write_text(
        textwrap.dedent(
            f"""\
            import base64
            import json
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            if "--version" in args or (args[:1] == ["--version"]):
                print("runsc version fake")
                raise SystemExit(0)
            if args[:1] in (["kill"], ["delete"]):
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
                if m.get("destination") in ("/workspace-seed", "/workspace-sync"):
                    ws = Path(m["source"])
                    break
            if ws is None:
                for m in cfg.get("mounts", []):
                    if m.get("destination") == "/workspace":
                        if m.get("type") == "bind" and "rw" in (m.get("options") or []):
                            ws = Path(m["source"])
                            break
            if ws is None:
                print("fake_runsc: no workspace staging mount", file=sys.stderr)
                raise SystemExit(2)

            if {hang!r}:
                import time
                time.sleep(99999)

            if {rematerialize!r}:
                files = {{}}
                for p in ws.rglob("*"):
                    if p.is_file():
                        files[p.relative_to(ws).as_posix()] = (
                            base64.b64encode(p.read_bytes()).decode("ascii")
                        )
                edits = {rematerialize_edit!r} or {{}}
                for rel, text in edits.items():
                    files[rel] = base64.b64encode(text.encode("utf-8")).decode("ascii")
                print("listed dist")
                print("__AGENTCORE_ARTIFACTS__" + json.dumps(files, separators=(",", ":")))
                raise SystemExit(0)

            rel = Path({artifact_rel!r})
            dest = ws / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("from-sandbox", encoding="utf-8")
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


def _bind_loopback(sandbox: GVisorSandbox) -> LoopbackRunscClient:
    """Drive fake ``runsc`` through sandboxd's test client (never production)."""
    client = LoopbackRunscClient(
        runsc_path=sandbox._runsc,  # noqa: SLF001
        runtime_root=sandbox._runtime_root,  # noqa: SLF001
    )
    set_sandboxd_client_for_tests(client)
    return client


async def test_gvisor_write_back_lands_artifact_in_real_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "seed.txt").write_text("keep", encoding="utf-8")

    runsc = _install_fake_runsc(tmp_path)
    sandbox = GVisorSandbox(
        runsc_path=runsc,
        runtime_root=str(tmp_path / "rt"),
    )
    _bind_loopback(sandbox)

    result = await sandbox.execute(
        ExecutionRequest(
            code="print('ignored-by-fake')",
            language="python",
            cwd=str(ws),
            timeout_seconds=10,
        )
    )

    assert result.success is True
    assert result.written_files == ["out/hello.txt"]
    assert result.write_back_skipped == 0
    assert (ws / "out" / "hello.txt").read_text(encoding="utf-8") == "from-sandbox"
    assert (ws / "seed.txt").read_text(encoding="utf-8") == "keep"


async def test_gvisor_readonly_script_does_not_claim_written_files(tmp_path: Path):
    """Cloud sandbox rematerializes every seed path; same bytes must not be a delivery."""
    ws = tmp_path / "workspace"
    (ws / "dist").mkdir(parents=True)
    seed = ws / "seed.txt"
    app = ws / "dist" / "app.js"
    vendor = ws / "dist" / "vendor.js"
    seed.write_text("keep", encoding="utf-8")
    app.write_text("bundle-a", encoding="utf-8")
    vendor.write_text("bundle-b", encoding="utf-8")
    seed_mtime = seed.stat().st_mtime_ns
    app_mtime = app.stat().st_mtime_ns

    sandbox = GVisorSandbox(
        runsc_path=_install_fake_runsc(tmp_path, rematerialize=True),
        runtime_root=str(tmp_path / "rt"),
    )
    _bind_loopback(sandbox)
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
    assert result.write_back_skipped == 0
    assert seed.read_text(encoding="utf-8") == "keep"
    assert app.read_text(encoding="utf-8") == "bundle-a"
    assert seed.stat().st_mtime_ns == seed_mtime
    assert app.stat().st_mtime_ns == app_mtime


async def test_gvisor_rematerialize_reports_only_actual_content_change(tmp_path: Path):
    """Whole-tree artifact payload + one real edit → only the edited path is delivered."""
    ws = tmp_path / "workspace"
    (ws / "dist").mkdir(parents=True)
    (ws / "seed.txt").write_text("keep", encoding="utf-8")
    (ws / "dist" / "app.js").write_text("bundle-a", encoding="utf-8")

    sandbox = GVisorSandbox(
        runsc_path=_install_fake_runsc(
            tmp_path,
            rematerialize=True,
            rematerialize_edit={"seed.txt": "changed-in-sandbox"},
        ),
        runtime_root=str(tmp_path / "rt"),
    )
    _bind_loopback(sandbox)
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
    assert (ws / "dist" / "app.js").read_text(encoding="utf-8") == "bundle-a"


async def test_gvisor_timeout_skips_write_back(tmp_path: Path):
    """Timeout path must not persist half-written artifacts (copy-out skipped)."""
    import time

    ws = tmp_path / "workspace"
    ws.mkdir()
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))

    class _TimeoutClient(LoopbackRunscClient):
        async def run_wait(self, **kwargs):  # noqa: ANN003
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

    start = time.monotonic()
    result = await sandbox._execute_in_slot(  # noqa: SLF001
        ExecutionRequest(code="x", language="python", cwd=str(ws), timeout_seconds=1),
        start,
    )

    assert result.success is False
    assert "Timeout" in result.stderr
    assert "未写回" in result.stderr
    assert not (ws / "out").exists()
    assert result.written_files is None


def test_oci_workspace_mount_is_rw_when_staged(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cfg = sandbox._build_oci_config(  # noqa: SLF001
        ExecutionRequest(code="x", language="python"),
        script_name="main.py",
        workspace=str(tmp_path / "staged"),
        scratch_dir=str(tmp_path / "scratch"),
        workspace_writable=True,
        memory_limit_mb=256,
    )
    mounts = {m["destination"]: m for m in cfg["mounts"]}
    assert mounts["/workspace"]["type"] == "tmpfs"
    assert mounts["/workspace-seed"]["type"] == "bind"
    assert mounts["/scratch"]["options"] == ["ro", "bind", "nosuid", "nodev"]
    # Memory ceiling comes from the guardrail knob, not the request default.
    assert cfg["linux"]["resources"]["memory"]["limit"] == 256 * 1024 * 1024


def test_oci_config_json_roundtrip_shape(tmp_path: Path):
    """config.json must be JSON-serializable for runsc (regression for Path/set leaks)."""
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cfg = sandbox._build_oci_config(  # noqa: SLF001
        ExecutionRequest(code="print(1)", language="python", network_mode="none"),
        script_name="main.py",
        workspace=str(tmp_path),
        scratch_dir=str(tmp_path / "scratch"),
        workspace_writable=False,
    )
    dumped = json.dumps(cfg)
    assert '"cwd": "/workspace"' in dumped
    assert '"network"' not in dumped  # offline posture


def test_runsc_run_cmd_global_flags_before_run(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cmd = sandbox._build_run_cmd(  # noqa: SLF001
        bundle_dir="/tmp/bundle",
        container_id="agentcore-test",
        network_mode="none",
    )
    assert cmd[0] == sandbox._runsc  # noqa: SLF001
    run_idx = cmd.index("run")
    assert "--rootless" in cmd[:run_idx]
    assert "--network=none" in cmd[:run_idx]
    assert f"--root={tmp_path / 'rt'}" in cmd[:run_idx]
    assert cmd[run_idx + 1] == "--bundle=/tmp/bundle"
    assert cmd[run_idx + 2] == "agentcore-test"


def test_runsc_run_cmd_restricted_uses_network_host(tmp_path: Path):
    """Rootless runsc requires an explicit network flag; restricted → host."""
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cmd = sandbox._build_run_cmd(  # noqa: SLF001
        bundle_dir="/tmp/bundle",
        container_id="agentcore-test",
        network_mode="restricted",
    )
    run_idx = cmd.index("run")
    assert "--network=host" in cmd[:run_idx]
    assert "--network=none" not in cmd[:run_idx]
    assert "--rootless" in cmd[:run_idx]


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


@pytest.mark.asyncio
async def test_health_check_sandboxd_unavailable(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))

    class _Down(LoopbackRunscClient):
        async def health(self, shape):  # noqa: ANN001
            raise SandboxdUnavailable("no daemon")

    set_sandboxd_client_for_tests(
        _Down(
            runsc_path=sandbox._runsc,  # noqa: SLF001
            runtime_root=sandbox._runtime_root,  # noqa: SLF001
        )
    )
    assert await sandbox.health_check() is False
    assert sandbox.last_health_failure is not None
    assert sandbox.last_health_failure[0] == "sandboxd_unavailable"


@pytest.mark.asyncio
async def test_health_check_probes_code_shape_only(tmp_path: Path):
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
    assert shapes == ["code"]


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

    assert WorkspaceSettings.model_fields["gvisor_runtime_root"].default == "./data/sandbox"
    assert WorkspaceSettings.model_fields["gvisor_runtime_root"].default != "/tmp/agentcore-sandbox"
