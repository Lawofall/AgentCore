"""Cloud sandbox health probe (boot + TTL refresh) → ``code_execution_enabled_for`` gate."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agentcore.config import settings
from agentcore.tools.builtin import (
    browser_execution_enabled_for,
    build_worker_registry,
    code_execution_enabled_for,
)
from agentcore.tools.sandbox.cloud_health import (
    age_cloud_sandbox_health_for_tests,
    cloud_sandbox_health,
    cloud_sandbox_health_failure,
    pending_cloud_sandbox_refresh_for_tests,
    probe_cloud_sandbox_at_startup,
    set_cloud_sandbox_health_for_tests,
)
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import LocalBackend

# Well past any healthy TTL / unhealthy backoff window — the exact durations are
# tuning knobs, "an aged verdict gets re-probed" is the contract under test.
_WELL_PAST_TTL = 3600.0


class _FakeSandbox:
    def __init__(
        self,
        *,
        ok: bool = True,
        raise_exc: BaseException | None = None,
        last_health_failure: tuple[str, str | None] | None = None,
        gate: asyncio.Event | None = None,
    ):
        self.ok = ok
        self.raise_exc = raise_exc
        self.last_health_failure = last_health_failure
        # Blocks inside ``health_check`` so a test can observe an in-flight probe.
        self.gate = gate
        self.entered = asyncio.Event()
        self.calls = 0

    async def health_check(self) -> bool:
        self.calls += 1
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.ok


class _CloudBackend:
    location = "server"


class _SandboxWithoutHealth:
    """Provider that omits ``health_check`` — must be treated as unhealthy."""


@pytest.mark.asyncio
async def test_probe_skipped_when_cloud_execution_config_off(monkeypatch: pytest.MonkeyPatch):
    called: list[Any] = []

    def _boom() -> Any:
        called.append(True)
        raise AssertionError("sandbox must not be built when config is off")

    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        _boom,
    )
    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is None
    assert called == []


@pytest.mark.asyncio
async def test_probe_success_caches_healthy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: _FakeSandbox(ok=True),
    )
    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is True


@pytest.mark.asyncio
async def test_probe_failure_caches_unhealthy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "code_execute_cloud_enabled", True)
    monkeypatch.setattr(settings, "code_execute_cloud_unsafe_ack", True)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: _FakeSandbox(ok=False),
    )
    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is False


@pytest.mark.asyncio
async def test_probe_surfaces_sandbox_last_health_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """GVisor ``last_health_failure`` (e.g. not_linux) must reach the warning reason."""
    set_cloud_sandbox_health_for_tests(None)
    logged: list[dict[str, Any]] = []

    class _Logger:
        def debug(self, *_a: Any, **_k: Any) -> None:
            return None

        def warning(self, event: str, **kwargs: Any) -> None:
            logged.append({"event": event, **kwargs})

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: _FakeSandbox(
            ok=False,
            last_health_failure=("not_linux", "platform=win32"),
        ),
    )
    monkeypatch.setattr(
        "agentcore.tools.sandbox.cloud_health.logger",
        _Logger(),
    )
    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is False
    assert logged and logged[0]["event"] == "sandbox.cloud_health_failed"
    assert logged[0]["reason"] == "not_linux"
    assert logged[0]["detail"] == "platform=win32"


@pytest.mark.asyncio
async def test_probe_exception_caches_unhealthy_without_raising(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: _FakeSandbox(raise_exc=RuntimeError("runsc gone")),
    )
    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is False


@pytest.mark.asyncio
async def test_probe_missing_health_check_caches_unhealthy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: _SandboxWithoutHealth(),
    )
    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is False


def test_local_backend_ignores_unhealthy_cloud_probe(tmp_path: Path):
    set_cloud_sandbox_health_for_tests(False)
    assert code_execution_enabled_for(LocalBackend()) is True
    # Server backend with config off stays false regardless of probe.
    assert code_execution_enabled_for(ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())) is False


# --- Post-boot rot: the verdict must expire, not last the process life ------------


@pytest.mark.asyncio
async def test_sandbox_broken_after_boot_is_detected_on_stale_read(
    monkeypatch: pytest.MonkeyPatch,
):
    """AppArmor / runtime_root / userns regression AFTER boot must close the gate.

    Boot says healthy, runsc then rots. A boot-only cache would keep assembling
    code_execute forever; the aged verdict has to be re-probed. Browser is
    gated by shape B, not this shape-A verdict.
    """
    from agentcore.tools.sandbox.browser.netns import set_browser_netns_health_for_tests

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    sandbox = _FakeSandbox(ok=True)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: sandbox,
    )
    backend = _CloudBackend()

    set_browser_netns_health_for_tests(True)
    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is True
    assert code_execution_enabled_for(backend) is True
    assert browser_execution_enabled_for(backend) is True

    sandbox.ok = False
    sandbox.last_health_failure = ("runsc_failed", "userns disabled")

    # Stale read serves the cached verdict (no inline probe) and schedules the re-probe.
    age_cloud_sandbox_health_for_tests(_WELL_PAST_TTL)
    assert code_execution_enabled_for(backend) is True
    task = pending_cloud_sandbox_refresh_for_tests()
    assert task is not None
    await task

    assert sandbox.calls == 2
    assert cloud_sandbox_health() is False
    assert cloud_sandbox_health_failure() == ("runsc_failed", "userns disabled")
    assert code_execution_enabled_for(backend) is False
    # Shape A rot must not withhold a healthy shape-B browser.
    assert browser_execution_enabled_for(backend) is True


@pytest.mark.asyncio
async def test_fresh_verdict_is_never_reprobed_per_call(monkeypatch: pytest.MonkeyPatch):
    """A live verdict must not put a runsc start in front of every gate read."""
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    sandbox = _FakeSandbox(ok=True)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: sandbox,
    )
    backend = _CloudBackend()

    from agentcore.tools.sandbox.browser.netns import set_browser_netns_health_for_tests

    set_browser_netns_health_for_tests(True)
    await probe_cloud_sandbox_at_startup()
    for _ in range(5):
        assert code_execution_enabled_for(backend) is True
        assert browser_execution_enabled_for(backend) is True
    await asyncio.sleep(0)

    assert sandbox.calls == 1
    assert pending_cloud_sandbox_refresh_for_tests() is None


@pytest.mark.asyncio
async def test_refresh_in_flight_keeps_last_known_verdict(monkeypatch: pytest.MonkeyPatch):
    """「探测失败」must not decay into「从未探过」mid-refresh — that would fail open."""
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    gate = asyncio.Event()
    sandbox = _FakeSandbox(ok=False, last_health_failure=("runsc_failed", "boom"))
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: sandbox,
    )
    backend = _CloudBackend()

    await probe_cloud_sandbox_at_startup()
    assert cloud_sandbox_health() is False

    sandbox.entered.clear()
    sandbox.gate = gate
    sandbox.ok = True
    age_cloud_sandbox_health_for_tests(_WELL_PAST_TTL)
    assert code_execution_enabled_for(backend) is False
    task = pending_cloud_sandbox_refresh_for_tests()
    assert task is not None

    await sandbox.entered.wait()
    assert cloud_sandbox_health() is False
    assert code_execution_enabled_for(backend) is False

    gate.set()
    await task
    assert cloud_sandbox_health() is True
    assert cloud_sandbox_health_failure() is None
    assert code_execution_enabled_for(backend) is True


@pytest.mark.asyncio
async def test_unprobed_process_is_never_refreshed(monkeypatch: pytest.MonkeyPatch):
    """No boot probe → stays ``None`` (config-only), and reads must not start one."""
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    built: list[Any] = []

    def _build() -> Any:
        built.append(True)
        raise AssertionError("an unprobed process must not probe from a gate read")

    monkeypatch.setattr("agentcore.workspace.locate._default_server_sandbox", _build)
    backend = _CloudBackend()

    assert cloud_sandbox_health() is None
    assert code_execution_enabled_for(backend) is True
    await asyncio.sleep(0)

    assert built == []
    assert pending_cloud_sandbox_refresh_for_tests() is None


@pytest.mark.asyncio
async def test_unhealthy_backs_off_before_retrying(monkeypatch: pytest.MonkeyPatch):
    """A broken host is retried (recovery is detected) but not hammered."""
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    sandbox = _FakeSandbox(ok=False, last_health_failure=("runsc_failed", "boom"))
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: sandbox,
    )
    backend = _CloudBackend()

    await probe_cloud_sandbox_at_startup()
    for _ in range(5):
        assert code_execution_enabled_for(backend) is False
    await asyncio.sleep(0)
    assert sandbox.calls == 1
    assert pending_cloud_sandbox_refresh_for_tests() is None

    sandbox.ok = True
    age_cloud_sandbox_health_for_tests(_WELL_PAST_TTL)
    assert code_execution_enabled_for(backend) is False
    task = pending_cloud_sandbox_refresh_for_tests()
    assert task is not None
    await task

    assert cloud_sandbox_health() is True
    assert cloud_sandbox_health_failure() is None


@pytest.mark.asyncio
async def test_wedged_probe_times_out_instead_of_freezing_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
):
    """A hung ``health_check`` must resolve unhealthy, not pin the refresh forever."""
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    monkeypatch.setattr("agentcore.tools.sandbox.cloud_health._PROBE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        "agentcore.workspace.locate._default_server_sandbox",
        lambda: _FakeSandbox(ok=True, gate=asyncio.Event()),
    )

    await probe_cloud_sandbox_at_startup()

    assert cloud_sandbox_health() is False
    failure = cloud_sandbox_health_failure()
    assert failure is not None and failure[0] == "probe_timeout"
    assert code_execution_enabled_for(_CloudBackend()) is False


def test_sidecar_withholds_cloud_execution_even_when_unprobed(
    monkeypatch: pytest.MonkeyPatch,
):
    """Desktop engine sitting on a cloud desk must not assemble gVisor tools."""
    from agentcore.tools.sandbox.cloud_health import reset_cloud_sandbox_health_for_tests

    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    reset_cloud_sandbox_health_for_tests()
    assert code_execution_enabled_for(_CloudBackend()) is False
    assert code_execution_enabled_for(LocalBackend()) is True
    names = set(build_worker_registry(backend=_CloudBackend()).names)
    assert "code_execute" not in names
    assert "test_run" not in names
    local_names = set(build_worker_registry(backend=LocalBackend()).names)
    assert "code_execute" in local_names


def test_sidecar_withholds_cloud_execution_even_if_probe_says_healthy(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    assert code_execution_enabled_for(_CloudBackend()) is False
