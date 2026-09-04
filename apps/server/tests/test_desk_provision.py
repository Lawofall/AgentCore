"""Cloud desk provision lives in prepare/resume — never inside ``run``."""

from __future__ import annotations

import inspect

import pytest

from agentcore.core.errors import SandboxError
from agentcore.tools.builtin.run_short import execute_short
from agentcore.tools.sandbox.desk_provision import provision_server_desk
from agentcore.workspace.server import ServerWorkspace


@pytest.mark.asyncio
async def test_provision_noops_for_local_backend():
    class _Local:
        location = "local"

        async def ensure_workspace_desk(self) -> None:
            raise AssertionError("local must not provision a cloud desk")

    await provision_server_desk(_Local())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_provision_calls_ensure_on_server_backend():
    calls: list[int] = []

    class _Server:
        location = "server"

        async def ensure_workspace_desk(self) -> None:
            calls.append(1)

    await provision_server_desk(_Server())  # type: ignore[arg-type]
    assert calls == [1]


@pytest.mark.asyncio
async def test_provision_swallows_ensure_failure():
    class _Boom:
        location = "server"

        async def ensure_workspace_desk(self) -> None:
            raise SandboxError("boot failed", code="exec_env_sandbox_unavailable")

    await provision_server_desk(_Boom())  # type: ignore[arg-type]


def test_prepare_and_resume_call_provision():
    from agentcore.runtime.pipeline import prepare as prepare_mod
    from agentcore.runtime.pipeline.resume import wire as wire_mod

    assert "provision_server_desk" in inspect.getsource(prepare_mod.prepare_fresh_turn)
    assert "provision_server_desk" in inspect.getsource(
        wire_mod._wire_continuation_toolset
    )


def test_short_run_and_server_execute_do_not_boot_desk():
    assert "ensure_workspace_desk" not in inspect.getsource(execute_short)
    assert "ensure_workspace_desk" not in inspect.getsource(ServerWorkspace.execute)


def test_gvisor_without_running_desk_withholds_execution(tmp_path, monkeypatch):
    from agentcore.config import settings
    from agentcore.tools.builtin import (
        browser_execution_enabled_for,
        build_worker_registry,
        code_execution_enabled_for,
    )
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests
    from agentcore.tools.sandbox.gvisor import GVisorSandbox, reset_desk_sessions_for_tests

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    reset_desk_sessions_for_tests()
    backend = ServerWorkspace(
        root=tmp_path, sandbox=GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    )
    assert backend.cloud_desk_ready() is False
    assert code_execution_enabled_for(backend) is False
    assert browser_execution_enabled_for(backend) is False
    names = set(build_worker_registry(backend=backend).names)
    assert "run" not in names
    assert "browser" not in names


def test_gvisor_with_running_desk_assembles_execution(tmp_path, monkeypatch):
    from agentcore.config import settings
    from agentcore.tools.builtin import (
        browser_execution_enabled_for,
        build_worker_registry,
        code_execution_enabled_for,
    )
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests
    from agentcore.tools.sandbox.gvisor import GVisorSandbox

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    backend = ServerWorkspace(
        root=tmp_path, sandbox=GVisorSandbox(runtime_root=str(tmp_path / "rt"))
    )
    monkeypatch.setattr(
        "agentcore.tools.sandbox.gvisor.has_running_desk", lambda _ws: True
    )
    assert backend.cloud_desk_ready() is True
    assert code_execution_enabled_for(backend) is True
    assert browser_execution_enabled_for(backend) is True
    names = set(build_worker_registry(backend=backend).names)
    assert "run" in names
    assert "browser" in names


def test_subprocess_cloud_backend_ignores_desk_map(tmp_path, monkeypatch):
    from agentcore.config import settings
    from agentcore.tools.builtin import code_execution_enabled_for
    from agentcore.tools.sandbox.cloud_health import set_cloud_sandbox_health_for_tests
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    monkeypatch.setattr(settings, "gvisor_enabled", True)
    set_cloud_sandbox_health_for_tests(True)
    backend = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    assert backend.cloud_desk_ready() is True
    assert code_execution_enabled_for(backend) is True
