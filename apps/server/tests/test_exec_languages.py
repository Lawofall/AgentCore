"""Tests for probe-then-trim ``code_execute`` language surface."""

from __future__ import annotations

import asyncio

from agentcore.tools.builtin.code_execute import CodeExecuteTool
from agentcore.tools.sandbox.exec_languages import (
    ALL_EXEC_LANGUAGES,
    format_interpreters_line,
    resolve_exec_languages,
)
from agentcore.workspace.protocol import WorkspaceIOError


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


def test_code_execute_schema_drops_bash_when_probed_away():
    tool = CodeExecuteTool(location="local", languages=("python", "javascript"))
    enum = tool.schema.parameters["properties"]["language"]["enum"]
    assert enum == ["python", "javascript"]
    assert "bash" not in enum
    # Opening support phrase lists only probed languages.
    assert "支持 Python、JavaScript" in tool.schema.description
    assert "支持 Python、JavaScript、Bash" not in tool.schema.description


def test_code_execute_schema_keeps_bash_when_available():
    tool = CodeExecuteTool(location="local", languages=ALL_EXEC_LANGUAGES)
    enum = tool.schema.parameters["properties"]["language"]["enum"]
    assert enum == list(ALL_EXEC_LANGUAGES)


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


def test_workspace_context_lists_interpreters():
    from agentcore.runtime.context.workspace_context import build_workspace_context

    class _Fake:
        location = "local"
        root_label = "proj"

    out = build_workspace_context(
        _Fake(),
        desktop_online=True,
        code_execute_enabled=True,
        terminal_enabled=True,
        browser_enabled=False,
        exec_languages=("python", "javascript"),
    )
    assert "可用解释器" in out
    assert "Python" in out
    assert "不可用" in out
    assert "Bash" in out


def test_worker_registry_trims_code_execute_enum():
    from agentcore.tools.builtin import build_worker_registry

    class _Local:
        location = "local"

    reg = build_worker_registry(
        backend=_Local(), languages=("python", "javascript")
    )
    tool = reg.get("code_execute")
    assert tool is not None
    assert tool.schema.parameters["properties"]["language"]["enum"] == [
        "python",
        "javascript",
    ]
    # Same factory path also constructs ``terminal`` (needs_location, no languages).
    assert reg.get("terminal") is not None


def test_instantiate_declared_languages_skips_unflagged_location_tools():
    """Local prepare always passes languages=; every needs_location tool must survive."""
    from agentcore.tools.registration import (
        ToolSurface,
        declared_tools,
        instantiate_declared,
        tool_registration,
    )

    constructed = []
    for surface in ToolSurface:
        for cls in declared_tools(surface=surface):
            if not tool_registration(cls).needs_location:
                continue
            tool = instantiate_declared(
                cls, location="local", languages=("python", "javascript")
            )
            constructed.append(tool.schema.name)
    assert "code_execute" in constructed
    assert "terminal" in constructed
