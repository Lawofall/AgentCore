"""Unit tests for packaging registry allowlist egress (A) + cache env (B)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.tools.builtin.package_install import install_cache_env, registry_pin_env
from agentcore.tools.sandbox.egress.hosts import (
    allowed_registry_hosts,
    host_is_allowed_registry,
)
from agentcore.tools.sandbox.egress.proxy import resolve_allowlist_dial_target
from agentcore.tools.sandbox.egress.ready import registry_egress_available
from agentcore.tools.sandbox.egress.runtime import (
    PACKAGE_CACHE_MOUNT,
    PackageEgressSession,
    install_proxy_env,
    is_ephemeral_bucket,
    open_package_egress,
    package_cache_host_dir,
    resolve_cache_bucket,
)
from agentcore.tools.sandbox.gvisor import GVisorSandbox
from agentcore.tools.sandbox.protocol import ExecutionRequest


def test_allowed_registry_hosts_from_allowlist():
    hosts = allowed_registry_hosts()
    assert "registry.npmjs.org" in hosts
    assert "registry.npmmirror.com" in hosts
    assert "cdn.npmmirror.com" in hosts
    assert "pypi.org" in hosts
    assert "files.pythonhosted.org" in hosts
    assert "mirrors.aliyun.com" in hosts


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("registry.npmjs.org", True),
        ("REGISTRY.NPMJS.ORG", True),
        ("registry.npmmirror.com", True),
        ("cdn.npmmirror.com", True),
        ("CDN.NPMMIRROR.COM", True),
        ("pypi.org", True),
        ("files.pythonhosted.org", True),
        ("mirrors.aliyun.com", True),
        ("evil.example.com", False),
        ("npmjs.org", False),
        ("169.254.169.254", False),
        ("", False),
    ],
)
def test_host_is_allowed_registry(host: str, ok: bool):
    assert host_is_allowed_registry(host) is ok


@pytest.mark.asyncio
async def test_allowlist_proxy_allows_registry_host(monkeypatch: pytest.MonkeyPatch):
    async def _run():
        real_loop = __import__("asyncio").get_running_loop()

        async def gai(host, port, *, family=0, type=0, proto=0, flags=0):
            return [(0, 0, 0, "", ("1.2.3.4", port))]

        monkeypatch.setattr(real_loop, "getaddrinfo", gai)
        ip, reason = await resolve_allowlist_dial_target("registry.npmjs.org", 443)
        assert ip == "1.2.3.4"
        assert reason == "ok"

    await _run()


@pytest.mark.asyncio
async def test_allowlist_proxy_refuses_non_allowlisted():
    ip, reason = await resolve_allowlist_dial_target("evil.example.com", 443)
    assert ip is None
    assert reason == "NOT_ALLOWLISTED"


def test_install_cache_env_non_empty():
    env = install_cache_env()
    assert env
    assert env["NPM_CONFIG_CACHE"].startswith(PACKAGE_CACHE_MOUNT)
    assert env["YARN_CACHE_FOLDER"].startswith(PACKAGE_CACHE_MOUNT)
    assert env["PNPM_STORE_PATH"].startswith(PACKAGE_CACHE_MOUNT)
    pin = registry_pin_env()
    assert "registry.npmjs.org" in pin["NPM_CONFIG_REGISTRY"]
    # CDN is egress-only; pin must stay on a real registry URL.
    assert "cdn.npmmirror.com" not in pin["NPM_CONFIG_REGISTRY"]


def test_package_egress_session_exposes_sbx_ip():
    class _Netns:
        host_ip = "10.202.1.1"
        sbx_ip = "10.202.1.2"
        netns_path = "/var/run/netns/acpkg1"

    session = PackageEgressSession(
        slot=1,
        netns=_Netns(),  # type: ignore[arg-type]
        proxy_url="http://10.202.1.1:8898",
        cache_host_dir=Path("/tmp"),
        cache_bucket="b",
    )
    assert session.sbx_ip == "10.202.1.2"
    assert session.host_ip == "10.202.1.1"


def test_package_cache_host_dir_buckets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    d = package_cache_host_dir("user-1")
    assert d == tmp_path / "pkg-cache" / "user-1"
    assert (d / "npm").is_dir()

    a = package_cache_host_dir(None)
    b = package_cache_host_dir("")
    for path in (a, b):
        normalized = path.as_posix()
        assert "/pkg-cache/global" not in normalized
        assert is_ephemeral_bucket(path.name)
        assert path.parent == tmp_path / "pkg-cache"
        assert (path / "npm").is_dir()
    assert a != b
    assert resolve_cache_bucket("user-1") == "user-1"
    assert resolve_cache_bucket(None) != resolve_cache_bucket(None)


@pytest.mark.asyncio
async def test_open_package_egress_ephemeral_buckets_not_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(
        "agentcore.tools.sandbox.egress.runtime.registry_egress_available",
        lambda: True,
    )

    async def _must_not_start_proxy():
        raise AssertionError("API must not start the packaging proxy")

    class _Netns:
        def __init__(self, *, slot: int, subnet_base: str):
            self.slot = slot
            self.host_ip = f"10.202.{slot}.1"
            self.netns_path = f"/var/run/netns/acpkg{slot}"

        async def setup(self) -> None:
            return None

        async def teardown(self) -> None:
            return None

    monkeypatch.setattr(
        "agentcore.tools.sandbox.egress.proxy.ensure_package_egress_proxy",
        _must_not_start_proxy,
    )
    monkeypatch.setattr("agentcore.tools.sandbox.egress.runtime.PackageNetns", _Netns)

    s1 = await open_package_egress(cache_bucket=None)
    s2 = await open_package_egress(cache_bucket="")
    try:
        assert isinstance(s1, PackageEgressSession)
        assert is_ephemeral_bucket(s1.cache_bucket)
        assert is_ephemeral_bucket(s2.cache_bucket)
        assert s1.cache_bucket != s2.cache_bucket
        assert s1.cache_host_dir != s2.cache_host_dir
        assert "/pkg-cache/global" not in s1.cache_host_dir.as_posix()
        assert "/pkg-cache/global" not in s2.cache_host_dir.as_posix()
        assert s1.cache_host_dir.is_dir()
        assert s2.cache_host_dir.is_dir()
    finally:
        await s1.close()
        await s2.close()
    assert not s1.cache_host_dir.exists()
    assert not s2.cache_host_dir.exists()


@pytest.mark.asyncio
async def test_open_package_egress_keeps_user_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(
        "agentcore.tools.sandbox.egress.runtime.registry_egress_available",
        lambda: True,
    )

    async def _must_not_start_proxy():
        raise AssertionError("API must not start the packaging proxy")

    class _Netns:
        def __init__(self, *, slot: int, subnet_base: str):
            self.slot = slot
            self.host_ip = f"10.202.{slot}.1"
            self.netns_path = f"/var/run/netns/acpkg{slot}"

        async def setup(self) -> None:
            return None

        async def teardown(self) -> None:
            return None

    monkeypatch.setattr(
        "agentcore.tools.sandbox.egress.proxy.ensure_package_egress_proxy",
        _must_not_start_proxy,
    )
    monkeypatch.setattr("agentcore.tools.sandbox.egress.runtime.PackageNetns", _Netns)

    session = await open_package_egress(cache_bucket="user-42")
    try:
        assert session.cache_bucket == "user-42"
        assert session.cache_host_dir == tmp_path / "pkg-cache" / "user-42"
        assert session.cache_host_dir.is_dir()
    finally:
        await session.close()
    # Stable user buckets are retained across close (cache reuse).
    assert session.cache_host_dir.is_dir()


def test_install_proxy_env_points_at_proxy():
    env = install_proxy_env("http://10.202.1.1:8898")
    assert env["HTTPS_PROXY"] == "http://10.202.1.1:8898"
    assert env["npm_config_https_proxy"] == "http://10.202.1.1:8898"


def test_registry_egress_unavailable_without_linux_gvisor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    assert registry_egress_available() is False


def test_registry_egress_available_follows_desk_health(
    monkeypatch: pytest.MonkeyPatch,
):
    import agentcore.tools.sandbox.egress.ready as ready
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests

    monkeypatch.setattr(ready.sys, "platform", "linux")
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(None)
    assert registry_egress_available() is True
    set_cloud_sandbox_health_for_tests(False)
    assert registry_egress_available() is False
    set_cloud_sandbox_health_for_tests(True)
    assert registry_egress_available() is True


@pytest.mark.asyncio
async def test_package_netns_setup_uses_sandboxd_client():
    from agentcore.tools.sandbox.egress.netns import PackageNetns, PackageNetnsError
    from agentcore.tools.sandbox.sandboxd.client import set_sandboxd_client_for_tests
    from agentcore.tools.sandbox.sandboxd.errors import SandboxdUnavailableError
    from agentcore.tools.sandbox.sandboxd.protocol import NetnsInfo

    class _Client:
        def __init__(self) -> None:
            self.setups: list[tuple] = []
            self.teardowns: list[tuple] = []

        async def netns_setup(self, family, slot, subnet_base):
            self.setups.append((family, slot, subnet_base))
            name = f"acpkg{slot}"
            return NetnsInfo(
                family="package",
                slot=slot,
                name=name,
                path=f"/var/run/netns/{name}",
                host_ip=f"{subnet_base}.{slot}.1",
                sbx_ip=f"{subnet_base}.{slot}.2",
            )

        async def netns_teardown(self, family, slot):
            self.teardowns.append((family, slot))

    client = _Client()
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    ns = PackageNetns(slot=1, subnet_base="10.202")
    await ns.setup()
    assert client.setups == [("package", 1, "10.202")]
    assert ns.host_ip == "10.202.1.1"
    await ns.teardown()
    assert client.teardowns == [("package", 1)]

    class _Boom:
        async def netns_setup(self, *_a, **_k):
            raise SandboxdUnavailableError("down")

    set_sandboxd_client_for_tests(_Boom())  # type: ignore[arg-type]
    with pytest.raises(PackageNetnsError):
        await PackageNetns(slot=2, subnet_base="10.202").setup()


def test_runsc_cmd_desk_is_non_rootless_sandbox(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cmd = sandbox._build_run_cmd(  # noqa: SLF001
        bundle_dir="/tmp/bundle",
        container_id="agentcore-test",
        network_mode="restricted",
    )
    run_idx = cmd.index("run")
    assert "--rootless" not in cmd[:run_idx]
    assert "--platform=systrap" in cmd[:run_idx]
    assert "--network=sandbox" in cmd[:run_idx]
    assert "--ignore-cgroups" in cmd[:run_idx]
    assert "--network=host" not in cmd[:run_idx]
    assert cmd[run_idx + 1] == "-detach"


def test_desk_oci_binds_cache_and_netns_path(tmp_path: Path):
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg = sandbox._build_desk_oci(  # noqa: SLF001
        workspace=str(tmp_path),
        scratch_dir=str(tmp_path / "scratch"),
        netns_path="/var/run/netns/acpkg1",
        cache_host_dir=str(cache),
        proxy_url="http://10.0.0.1:8898",
    )
    net = [n for n in cfg["linux"]["namespaces"] if n["type"] == "network"]
    assert len(net) == 1
    assert net[0].get("path") == "/var/run/netns/acpkg1"
    mounts = {m["destination"]: m for m in cfg["mounts"]}
    assert PACKAGE_CACHE_MOUNT in mounts
    assert mounts[PACKAGE_CACHE_MOUNT]["source"] == str(cache)
    assert "rw" in mounts[PACKAGE_CACHE_MOUNT]["options"]
    assert cfg["process"]["user"]["uid"] != 65534


def test_desk_oci_rw_binds_workspace_without_base64_wrap(tmp_path: Path):
    """Desk guest: durable rw-bind + no staged tmpfs/seed + no wrap argv."""
    sandbox = GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    ws = tmp_path / "workspace"
    ws.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg = sandbox._build_desk_oci(  # noqa: SLF001
        workspace=str(ws),
        scratch_dir=str(scratch),
        netns_path="/var/run/netns/acpkg1",
        cache_host_dir=str(cache),
        proxy_url="http://10.0.0.1:8898",
    )
    mounts = {m["destination"]: m for m in cfg["mounts"]}
    assert mounts["/workspace"]["type"] == "bind"
    assert mounts["/workspace"]["source"] == str(ws)
    assert "rw" in mounts["/workspace"]["options"]
    assert "/workspace-seed" not in mounts
    assert mounts["/workspace"]["type"] != "tmpfs"
    args = cfg["process"]["args"]
    joined = " ".join(args) if isinstance(args, list) else str(args)
    assert "__AGENTCORE_ARTIFACTS__" not in joined
    assert "base64" not in joined
    assert PACKAGE_CACHE_MOUNT in mounts
    assert cfg["process"]["user"]["uid"] != 65534


@pytest.mark.asyncio
async def test_install_execute_writes_nm_on_real_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Default execute bind-writes node_modules onto the persistent workspace."""
    import agentcore.tools.sandbox.gvisor as gvisor_mod
    from agentcore.tools.sandbox.gvisor import reset_desk_sessions_for_tests
    from agentcore.tools.sandbox.limits import reset_execution_slots
    from agentcore.tools.sandbox.sandboxd import set_sandboxd_client_for_tests
    from tests.sandboxd_testutil import LoopbackRunscClient
    from tests.test_gvisor_write_back import _install_fake_runsc

    reset_execution_slots()
    reset_desk_sessions_for_tests()
    monkeypatch.setattr(gvisor_mod, "_IS_LINUX", True)
    monkeypatch.setattr(settings, "gvisor_max_concurrent_executions", 2)
    monkeypatch.setattr(settings, "gvisor_slot_wait_seconds", 0.1)
    monkeypatch.setattr(settings, "gvisor_timeout_max_seconds", 30)
    monkeypatch.setattr(settings, "gvisor_memory_limit_mb", 256)

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "package.json").write_text('{"name":"t"}', encoding="utf-8")
    import os
    import time

    old = time.time() - 60
    os.utime(ws / "package.json", (old, old))

    async def _fake_egress(*, cache_bucket=None):  # noqa: ANN001
        class _S:
            netns_path = "/var/run/netns/fake"
            cache_host_dir = tmp_path / "pkg-cache" / "b"
            proxy_url = "http://10.0.0.1:8898"
            host_ip = "10.0.0.1"

            async def close(self):
                return None

        (_S.cache_host_dir).mkdir(parents=True, exist_ok=True)
        return _S()

    monkeypatch.setattr(
        "agentcore.tools.sandbox.egress.open_package_egress",
        _fake_egress,
    )
    monkeypatch.setattr(
        "agentcore.tools.sandbox.egress.install_proxy_env",
        lambda url: {"HTTPS_PROXY": url},
    )

    runsc = _install_fake_runsc(
        tmp_path, artifact_rel="node_modules/left-pad/index.js"
    )
    sandbox = GVisorSandbox(
        runsc_path=runsc,
        runtime_root=str(tmp_path / "rt"),
    )
    set_sandboxd_client_for_tests(
        LoopbackRunscClient(
            runsc_path=runsc,
            runtime_root=sandbox._runtime_root,  # noqa: SLF001
        )
    )
    await sandbox.ensure_workspace_desk(str(ws))
    result = await sandbox.execute(
        ExecutionRequest(
            code="print('install')",
            language="python",
            cwd=str(ws),
            timeout_seconds=10,
        )
    )
    assert result.success is True
    assert (ws / "node_modules" / "left-pad" / "index.js").is_file()
