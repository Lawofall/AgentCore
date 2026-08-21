"""sandboxd Unix RPC: allowlisted runsc/ip, peercred, health shapes A/B."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from agentcore.tools.sandbox.sandboxd import server as server_mod
from agentcore.tools.sandbox.sandboxd.argv import build_runsc_cmd
from agentcore.tools.sandbox.sandboxd.client import UnixSandboxdClient
from agentcore.tools.sandbox.sandboxd.errors import SandboxdRpcError, SandboxdUnavailable
from agentcore.tools.sandbox.sandboxd.netns_ops import PROBE_NETNS_NAME, spec_for
from agentcore.tools.sandbox.sandboxd.server import (
    SandboxdServer,
    lookup_user_uid,
    peer_allowed,
    peer_uid,
)

_REAL_EXEC = asyncio.create_subprocess_exec

_WAIT_SCRIPT = "import sys;sys.stdout.write('out-chunk');sys.stderr.write('err-chunk');"

_STDIO_SCRIPT = (
    "import sys\n"
    "data = sys.stdin.buffer.readline()\n"
    "sys.stdout.buffer.write(data)\n"
    "sys.stdout.buffer.flush()\n"
)

_HANG_SCRIPT = "import time; time.sleep(60)"


def _run_argv(captured: list[list[str]]) -> list[str]:
    return next(a for a in captured if "run" in a and any(x.startswith("--bundle=") for x in a))


@asynccontextmanager
async def _running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_script: str = _WAIT_SCRIPT,
) -> AsyncIterator[tuple[SandboxdServer, UnixSandboxdClient, list[list[str]], list[dict]]]:
    runtime_root = tmp_path / "rt"
    netns_dir = tmp_path / "netns"
    runtime_root.mkdir()
    netns_dir.mkdir()
    captured: list[list[str]] = []
    bundles: list[dict] = []

    async def _fake_exec(*argv: object, **kwargs: object) -> asyncio.subprocess.Process:
        argv_s = [str(a) for a in argv]
        captured.append(argv_s)
        bundle = next((a.split("=", 1)[1] for a in argv_s if a.startswith("--bundle=")), None)
        if bundle:
            cfg_path = Path(bundle) / "config.json"
            if cfg_path.is_file():
                bundles.append(json.loads(cfg_path.read_text(encoding="utf-8")))
        script = run_script if "run" in argv_s else "raise SystemExit(0)"
        return await _REAL_EXEC(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    port_box: dict[str, int] = {}
    if not hasattr(asyncio, "start_unix_server"):

        async def _start_unix_server(handler, path=None):  # noqa: ARG001
            srv = await asyncio.start_server(handler, host="127.0.0.1", port=0)
            socks = srv.sockets or []
            port_box["port"] = int(socks[0].getsockname()[1])
            return srv

        monkeypatch.setattr(asyncio, "start_unix_server", _start_unix_server, raising=False)

    sock = tmp_path / "s.sock"
    server = SandboxdServer(
        socket_path=str(sock),
        runsc_path="runsc",
        runtime_root=str(runtime_root),
        netns_run_dir=str(netns_dir),
    )
    try:
        await server.start()
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"unix socket unavailable: {exc}")
    client = UnixSandboxdClient(str(sock))
    if port_box:

        async def _connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            return await asyncio.open_connection("127.0.0.1", port_box["port"])

        client._connect = _connect  # type: ignore[method-assign]
    try:
        yield server, client, captured, bundles
    finally:
        await server.close()


def test_spec_for_browser_and_package_names():
    br = spec_for("browser", 3, "10.201")
    assert br.name == "acbrw3"
    assert br.path.endswith("/acbrw3") or br.path.endswith("\\acbrw3")
    assert br.host_ip == "10.201.3.1"
    assert br.sbx_ip == "10.201.3.2"
    pkg = spec_for("package", 1, "10.202")
    assert pkg.name == "acpkg1"
    assert pkg.veth_host == "acpkgh1"


def test_peer_allowed_self_or_app():
    self_uid = 1000
    if sys.platform != "linux":
        assert peer_uid(None) is not None
        assert peer_allowed(None, self_uid=peer_uid(None) or 0, app_uid=999) is True
        return
    assert peer_allowed(None, self_uid=self_uid, app_uid=999) is False
    assert lookup_user_uid("no-such-user-sandboxd") is None


@pytest.mark.asyncio
async def test_ping_and_run_wait_uses_shape_argv(tmp_path, monkeypatch):
    async with _running(tmp_path, monkeypatch) as (server, client, captured, _b):
        await client.ping()
        bundle = Path(server._runtime_root) / "b"
        bundle.mkdir()
        _code, stdout, stderr = await client.run_wait(
            shape="code",
            bundle_dir=str(bundle),
            container_id="agentcore-code1",
            timeout_seconds=5,
        )
        assert "out-chunk" in stdout
        assert "err-chunk" in stderr
        # UnixSandboxdClient maps JSON 0 through ``code or 1``; pin argv instead.
        run = _run_argv(captured)
        assert run == build_runsc_cmd(
            runsc_path="runsc",
            runtime_root=server._runtime_root,
            bundle_dir=str(bundle.resolve()),
            container_id="agentcore-code1",
            shape="code",
            network_mode="none",
        )


@pytest.mark.asyncio
async def test_run_rejects_bad_container_id_and_bundle(tmp_path, monkeypatch):
    async with _running(tmp_path, monkeypatch) as (server, client, _c, _b):
        bundle = Path(server._runtime_root) / "b"
        bundle.mkdir()
        with pytest.raises(SandboxdRpcError) as bad_id:
            await client.run_wait(
                shape="code",
                bundle_dir=str(bundle),
                container_id="evil",
                timeout_seconds=5,
            )
        assert bad_id.value.code == "sandboxd_denied"
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(SandboxdRpcError) as bad_bundle:
            await client.run_wait(
                shape="code",
                bundle_dir=str(outside),
                container_id="agentcore-x",
                timeout_seconds=5,
            )
        assert bad_bundle.value.code == "sandboxd_denied"


@pytest.mark.asyncio
async def test_health_code_is_true_oci_shape_a(tmp_path, monkeypatch):
    async with _running(tmp_path, monkeypatch) as (_s, client, captured, bundles):
        ok, detail = await client.health("code")
        assert ok is True
        assert detail == ""
        run = _run_argv(captured)
        run_idx = run.index("run")
        assert "--rootless" in run[:run_idx]
        assert "--network=none" in run[:run_idx]
        assert "--network=sandbox" not in run[:run_idx]
        assert bundles
        cfg = bundles[-1]
        assert cfg["process"]["args"] == ["/bin/true"]
        ns_types = [n.get("type") for n in cfg.get("linux", {}).get("namespaces", [])]
        assert "network" not in ns_types


@pytest.mark.asyncio
async def test_health_net_is_probe_plus_shape_b(tmp_path, monkeypatch):
    async with _running(tmp_path, monkeypatch) as (_s, client, captured, bundles):
        ok, _detail = await client.health("net")
        assert ok is True
        ip_calls = [a[1:] for a in captured if a and a[0] == "ip"]
        assert any(c[:3] == ["netns", "add", PROBE_NETNS_NAME] for c in ip_calls)
        run = _run_argv(captured)
        run_idx = run.index("run")
        assert run[:4] == [
            "runsc",
            "--platform=systrap",
            "--network=sandbox",
            "--ignore-cgroups",
        ]
        assert "--rootless" not in run[:run_idx]
        assert bundles
        cfg = bundles[-1]
        assert cfg["process"]["args"] == ["/bin/true"]
        blob = json.dumps(cfg)
        assert "playwright" not in blob.lower()
        assert "chromium" not in blob.lower()
        net = next(n for n in cfg["linux"]["namespaces"] if n.get("type") == "network")
        assert PROBE_NETNS_NAME in str(net.get("path"))


@pytest.mark.asyncio
async def test_netns_setup_teardown_and_delete_kill(tmp_path, monkeypatch):
    async with _running(tmp_path, monkeypatch) as (server, client, captured, _b):
        info = await client.netns_setup("browser", 2, "10.201")
        assert info.name == "acbrw2"
        assert info.host_ip == "10.201.2.1"
        assert info.sbx_ip == "10.201.2.2"
        await client.netns_teardown("browser", 2)
        await client.delete("agentcore-box", force=True)
        await client.kill("agentcore-box", "SIGKILL")
        ip_calls = [a[1:] for a in captured if a and a[0] == "ip"]
        assert any(c[:3] == ["netns", "add", "acbrw2"] for c in ip_calls)
        delete = next(
            a for a in captured if a[:1] == ["runsc"] and "delete" in a and "--force" in a
        )
        assert f"--root={server._runtime_root}" in delete
        assert delete[-1] == "agentcore-box"
        kill = next(a for a in captured if a[:1] == ["runsc"] and "kill" in a)
        assert kill[-2:] == ["agentcore-box", "SIGKILL"]


@pytest.mark.asyncio
async def test_run_stdio_splices_bytes(tmp_path, monkeypatch):
    async with _running(tmp_path, monkeypatch, run_script=_STDIO_SCRIPT) as (
        server,
        client,
        captured,
        _b,
    ):
        bundle = Path(server._runtime_root) / "b"
        bundle.mkdir()
        netns = Path(server._netns_run_dir) / "acbrw0"
        stdio = await client.run_stdio(
            bundle_dir=str(bundle),
            container_id="agentcore-br1",
            netns_path=str(netns),
        )
        await stdio.write(b"hello-rpc\n")
        line = await asyncio.wait_for(stdio.readline(), timeout=5)
        assert line == b"hello-rpc\n"
        await stdio.aclose()
        run = _run_argv(captured)
        assert run == build_runsc_cmd(
            runsc_path="runsc",
            runtime_root=server._runtime_root,
            bundle_dir=str(bundle.resolve()),
            container_id="agentcore-br1",
            shape="net",
        )


@pytest.mark.asyncio
async def test_run_timeout_kills_and_exits(tmp_path, monkeypatch):
    async with _running(tmp_path, monkeypatch, run_script=_HANG_SCRIPT) as (
        server,
        client,
        _c,
        _b,
    ):
        bundle = Path(server._runtime_root) / "b"
        bundle.mkdir()
        code, _out, _err = await client.run_wait(
            shape="code",
            bundle_dir=str(bundle),
            container_id="agentcore-hang",
            timeout_seconds=0.3,
        )
        assert code != 0


@pytest.mark.asyncio
async def test_peer_denied_closes_without_rpc(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "peer_uid", lambda _sock: 65534)
    async with _running(tmp_path, monkeypatch) as (_s, client, _c, _b):
        with pytest.raises((SandboxdUnavailable, ConnectionResetError, OSError)):
            await client.ping()
