"""Boot-time browser netns health probe → ``browser_execution_enabled_for`` gate."""

from __future__ import annotations

import os
from typing import Any

import pytest

from agentcore.config import settings
from agentcore.tools.sandbox.browser.netns import (
    NetnsError,
    SessionNetns,
    browser_netns_health,
    chmod_netns_inode,
    probe_browser_netns_at_startup,
    set_browser_netns_health_for_tests,
)
from agentcore.tools.sandbox.sandboxd.client import set_sandboxd_client_for_tests
from agentcore.tools.sandbox.sandboxd.errors import SandboxdUnavailable
from agentcore.tools.sandbox.sandboxd.protocol import NetnsInfo


class _HealthClient:
    def __init__(self, *, ok: bool = True, exc: BaseException | None = None) -> None:
        self.ok = ok
        self.exc = exc
        self.shapes: list[str] = []

    async def health(self, shape: str) -> tuple[bool, str]:
        self.shapes.append(shape)
        if self.exc is not None:
            raise self.exc
        return self.ok, "ok" if self.ok else "unhealthy"


class _NetnsClient:
    def __init__(self) -> None:
        self.setups: list[tuple[str, int, str]] = []
        self.teardowns: list[tuple[str, int]] = []

    async def netns_setup(self, family: str, slot: int, subnet_base: str) -> NetnsInfo:
        self.setups.append((family, slot, subnet_base))
        name = f"acbrw{slot}"
        return NetnsInfo(
            family="browser",
            slot=slot,
            name=name,
            path=f"/var/run/netns/{name}",
            host_ip=f"{subnet_base}.{slot}.1",
            sbx_ip=f"{subnet_base}.{slot}.2",
        )

    async def netns_teardown(self, family: str, slot: int) -> None:
        self.teardowns.append((family, slot))


@pytest.mark.asyncio
async def test_probe_skipped_when_gvisor_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    client = _HealthClient()
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    await probe_browser_netns_at_startup()
    assert browser_netns_health() is None
    assert client.shapes == []


@pytest.mark.asyncio
async def test_probe_skipped_on_non_linux(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr("agentcore.tools.sandbox.browser.netns.sys.platform", "win32")
    client = _HealthClient()
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    await probe_browser_netns_at_startup()
    assert browser_netns_health() is None
    assert client.shapes == []


@pytest.mark.asyncio
async def test_probe_success_caches_healthy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr("agentcore.tools.sandbox.browser.netns.sys.platform", "linux")
    client = _HealthClient(ok=True)
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    await probe_browser_netns_at_startup()
    assert browser_netns_health() is True
    assert client.shapes == ["net"]


@pytest.mark.asyncio
async def test_probe_failure_caches_unhealthy_without_raising(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr("agentcore.tools.sandbox.browser.netns.sys.platform", "linux")
    client = _HealthClient(ok=False)
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    await probe_browser_netns_at_startup()
    assert browser_netns_health() is False
    assert client.shapes == ["net"]


@pytest.mark.asyncio
async def test_probe_unavailable_is_fail_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr("agentcore.tools.sandbox.browser.netns.sys.platform", "linux")
    client = _HealthClient(exc=SandboxdUnavailable("socket missing"))
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    await probe_browser_netns_at_startup()
    assert browser_netns_health() is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX inode modes")
def test_chmod_netns_inode_sets_mode(tmp_path):
    inode = tmp_path / "acbrw0"
    inode.write_bytes(b"")
    inode.chmod(0o000)
    chmod_netns_inode("acbrw0", run_dir=str(tmp_path))
    assert inode.stat().st_mode & 0o777 == 0o644


def test_chmod_netns_inode_missing_path_is_silent(tmp_path):
    chmod_netns_inode("missing", run_dir=str(tmp_path))


def test_set_for_tests_roundtrip():
    set_browser_netns_health_for_tests(True)
    assert browser_netns_health() is True
    set_browser_netns_health_for_tests(False)
    assert browser_netns_health() is False
    set_browser_netns_health_for_tests(None)
    assert browser_netns_health() is None


@pytest.mark.asyncio
async def test_session_netns_setup_uses_sandboxd_client():
    client = _NetnsClient()
    set_sandboxd_client_for_tests(client)  # type: ignore[arg-type]
    ns = SessionNetns(slot=0, subnet_base="10.201")
    await ns.setup()
    assert client.setups == [("browser", 0, "10.201")]
    assert ns.host_ip == "10.201.0.1"
    assert ns.netns_path == "/var/run/netns/acbrw0"
    await ns.teardown()
    assert client.teardowns == [("browser", 0)]


@pytest.mark.asyncio
async def test_session_netns_setup_wraps_unavailable():
    class _Boom:
        async def netns_setup(self, *_a: Any, **_k: Any) -> NetnsInfo:
            raise SandboxdUnavailable("down")

    set_sandboxd_client_for_tests(_Boom())  # type: ignore[arg-type]
    ns = SessionNetns(slot=0, subnet_base="10.201")
    with pytest.raises(NetnsError):
        await ns.setup()
