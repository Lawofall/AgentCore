"""Sandbox subsystem for isolated code execution."""

from __future__ import annotations

from agentcore.tools.sandbox.exec_languages import (
    ALL_EXEC_LANGUAGES,
    format_interpreters_line,
    probe_host_languages,
    resolve_exec_languages,
)
from agentcore.tools.sandbox.gvisor import GVisorSandbox
from agentcore.tools.sandbox.protocol import (
    ExecutionRequest,
    ExecutionResult,
    SandboxCapabilities,
    SandboxProvider,
)
from agentcore.tools.sandbox.subprocess import SubprocessSandbox, probe_available_languages


def create_sandbox(
    *,
    workspace_root: str | None = None,
    location: str,
    gvisor_enabled: bool = False,
    runsc_path: str = "runsc",
    runtime_root: str | None = None,
) -> SandboxProvider:
    """Pick a sandbox backend for the given deployment location."""
    if location == "server" and gvisor_enabled:
        # Non-Linux health_check fails immediately (``not_linux``). Do **not**
        # fall back to SubprocessSandbox here: that would run user code on the
        # host against a ``location=server`` desk (SEC-005).
        return GVisorSandbox(
            runsc_path=runsc_path,
            workspace_root=workspace_root,
            runtime_root=runtime_root,
        )
    return SubprocessSandbox()


__all__ = [
    "ALL_EXEC_LANGUAGES",
    "ExecutionRequest",
    "ExecutionResult",
    "GVisorSandbox",
    "SandboxCapabilities",
    "SandboxProvider",
    "SubprocessSandbox",
    "create_sandbox",
    "format_interpreters_line",
    "probe_available_languages",
    "probe_host_languages",
    "resolve_exec_languages",
]
