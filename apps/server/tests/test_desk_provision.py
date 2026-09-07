"""Cloud desk provision lives in prepare/resume — never inside ``run``."""

from __future__ import annotations

import inspect

import pytest

from agentcore.core.errors import SandboxError
from agentcore.tools.builtin.run_short import execute_short
from agentcore.tools.sandbox.desk_provision import (
    desk_provision_log_fields,
    provision_server_desk,
)
from agentcore.tools.sandbox.sandboxd.errors import SandboxdRpcError
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


@pytest.mark.asyncio
async def test_provision_logs_sandboxd_cause_not_user_face():
    from structlog.testing import capture_logs

    class _Boom:
        location = "server"

        async def ensure_workspace_desk(self) -> None:
            try:
                raise SandboxdRpcError(
                    "cannot read client sync file: waiting for sandbox to start: EOF",
                    code="sandboxd_rpc",
                )
            except SandboxdRpcError as exc:
                raise SandboxError(
                    "云端隔离执行环境当前不可用，代码没有运行。我会换个方式继续。",
                    code="exec_env_sandbox_unavailable",
                ) from exc

    with capture_logs() as logs:
        await provision_server_desk(_Boom())  # type: ignore[arg-type]
    failed = [e for e in logs if e.get("event") == "sandbox.desk_provision_failed"]
    assert len(failed) == 1
    assert failed[0]["code"] == "sandboxd_rpc"
    assert "client sync file" in failed[0]["cause"]
    assert "云端隔离执行环境当前不可用" in failed[0]["error"]


def test_desk_provision_log_fields_uses_health_hint_without_cause():
    from agentcore.tools.sandbox.cloud_health import (
        reset_cloud_sandbox_health_for_tests,
        set_cloud_sandbox_health_for_tests,
    )

    reset_cloud_sandbox_health_for_tests()
    set_cloud_sandbox_health_for_tests(
        False, failure=("runsc_failed", "cannot create sandbox: EOF")
    )
    try:
        fields = desk_provision_log_fields(
            SandboxError(
                "云端隔离执行环境当前不可用，代码没有运行。我会换个方式继续。",
                code="exec_env_sandbox_unavailable",
            )
        )
    finally:
        reset_cloud_sandbox_health_for_tests()
    assert fields["code"] == "exec_env_sandbox_unavailable"
    assert "runsc_failed" in fields["cause"]
    assert "EOF" in fields["cause"]


@pytest.mark.asyncio
async def test_provision_emits_desk_wait_on_sink():
    events: list[tuple[str, bool]] = []

    class _Sink:
        def emit(self, event: object) -> None:
            payload = getattr(event, "payload", {})
            events.append((str(getattr(event, "type", "")), bool(payload.get("waiting"))))

    class _Server:
        location = "server"

        async def ensure_workspace_desk(self) -> None:
            events.append(("ensure", False))

    await provision_server_desk(
        _Server(),  # type: ignore[arg-type]
        conversation_id="conv-1",
        sink=_Sink(),
    )
    assert events[0] == ("desk_provision_wait", True)
    assert events[1] == ("ensure", False)
    assert events[2] == ("desk_provision_wait", False)


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
