"""Tests for probe-then-trim short-exec language surface."""

from __future__ import annotations

import asyncio

from agentcore.tools.builtin.run_short import execute_short
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.exec_languages import (
    ALL_EXEC_LANGUAGES,
    format_interpreters_line,
    resolve_exec_languages,
)
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult
from agentcore.workspace.protocol import WorkspaceIOError


class _FakeBackend:
    def __init__(self, result: ExecutionResult) -> None:
        self._result = result
        self.requests: list[ExecutionRequest] = []

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return self._result


def _ctx(backend: _FakeBackend) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,  # type: ignore[arg-type]
        user_id="u",
    )


def test_format_interpreters_line_marks_missing_bash():
    line = format_interpreters_line(("python", "javascript"))
    assert "Python" in line
    assert "JavaScript" in line
    assert "Bash" in line
    assert "不可用" in line


def test_format_interpreters_line_all_available():
    line = format_interpreters_line(ALL_EXEC_LANGUAGES)
    assert "不可用" not in line
    assert "Python" in line and "Bash" in line


async def test_execute_short_rejects_unprobed_bash():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {"code": "echo hi", "language": "bash"},
        _ctx(backend),
        location="local",
        languages=("python", "javascript"),
    )
    assert result.success is False
    assert result.contract_failure is True
    assert result.metadata.get("code") == "language_unavailable"
    err = result.error or ""
    assert "language=bash" in err
    assert "python" in err
    assert "javascript" in err
    assert backend.requests == []


async def test_execute_short_keeps_bash_when_available():
    backend = _FakeBackend(
        ExecutionResult(success=True, stdout="ok\n", stderr="", exit_code=0, duration_ms=1)
    )
    result = await execute_short(
        {"code": "echo hi", "language": "bash"},
        _ctx(backend),
        location="local",
        languages=ALL_EXEC_LANGUAGES,
    )
    assert result.success is True
    assert [req.language for req in backend.requests] == ["bash"]


def test_probe_available_languages_skips_missing_bash(monkeypatch):
    import agentcore.tools.sandbox.subprocess as sp

    monkeypatch.setattr(sp, "resolve_bash_launcher", lambda: None)

    def fake_which(name: str) -> str | None:
        if name == "python":
            return "/usr/bin/python"
        if name == "node":
            return "/usr/bin/node"
        return None

    monkeypatch.setattr(sp.shutil, "which", fake_which)
    langs = sp.probe_available_languages()
    assert "python" in langs
    assert "javascript" in langs
    assert "bash" not in langs


def test_resolve_exec_languages_cloud_keeps_full_surface():
    class _Server:
        location = "server"

    langs = asyncio.run(resolve_exec_languages(_Server()))
    assert langs == ALL_EXEC_LANGUAGES


def test_resolve_exec_languages_sidecar_probes_host(monkeypatch):
    class _Sidecar:
        location = "local"

    monkeypatch.setattr(
        "agentcore.tools.sandbox.exec_languages.probe_host_languages",
        lambda: ("python",),
    )
    backend = _Sidecar()
    langs = asyncio.run(resolve_exec_languages(backend))
    assert langs == ("python",)
    assert backend._exec_languages == ("python",)


def test_resolve_exec_languages_desktop_channel():
    class _Channel:
        async def request(self, op, args, *, timeout=None):
            assert str(op) == "probe_exec"
            return {"languages": ["python", "javascript"]}

    class _Local:
        location = "local"

        def __init__(self) -> None:
            self._channel = _Channel()

    langs = asyncio.run(resolve_exec_languages(_Local()))
    assert langs == ("python", "javascript")
    assert "bash" not in langs


def test_resolve_exec_languages_probe_timeout_fail_closed_advertise():
    """Probe hang → empty language surface (A1); channel sticky-dead is channel.py's job."""

    class _Channel:
        async def request(self, op, args, *, timeout=None):
            raise WorkspaceIOError(
                "local workspace op 'probe_exec' timed out（活性挂起）"
            )

    class _Local:
        location = "local"

        def __init__(self) -> None:
            self._channel = _Channel()

    langs = asyncio.run(resolve_exec_languages(_Local()))
    assert langs == ()
