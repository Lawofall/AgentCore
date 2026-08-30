"""``git`` assembles only where the workspace can actually run git.

Cloud ``ServerWorkspace`` and the sidecar spawn ``git`` under ``backend.root``; a
rootless ``LocalWorkspace`` has only ``WorkspaceOp.GIT_RUN`` over the desktop channel,
so a desktop-offline session must not carry the tool at all (and the capability line
must say so, the same way the gVisor-less path reports ``code_execute=未装配``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.runtime.context.workspace_context import build_workspace_context
from agentcore.tools.builtin import (
    build_builtin_registry,
    build_ceo_tool_registry,
    build_worker_registry,
    git_execution_enabled_for,
)
from agentcore.tools.builtin.git_ops.binary_health import (
    reset_git_binary_health_for_tests,
    set_git_binary_health_for_tests,
)
from agentcore.tools.catalog import build_capability_catalog
from agentcore.tools.registration import declared_tool_name, declared_tools, tool_registration


@pytest.fixture(autouse=True)
def _unprobed_git_binary():
    """Default every case to "never probed" — the binary axis is tested explicitly."""
    reset_git_binary_health_for_tests()
    yield
    reset_git_binary_health_for_tests()


class _ChannelLocalBackend:
    """LocalWorkspace shape: location=local, no ``root`` (desktop channel only)."""

    location = "local"
    root_label = "MyProject"

    def __init__(self) -> None:
        self._channel = object()


def _rooted_backend(tmp_path: Path, *, location: str) -> object:
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    root = tmp_path / location
    root.mkdir(exist_ok=True)
    return ServerWorkspace(root=root, sandbox=SubprocessSandbox(), location=location)


def test_git_tool_declares_git_class():
    reg = {
        declared_tool_name(cls): tool_registration(cls) for cls in declared_tools()
    }["git"]
    assert reg.git_class is True
    # Orthogonal to the execution / host / browser faces — git is not sandbox-gated.
    assert not reg.execution_class
    assert not reg.host_class
    assert not reg.desktop_online_class
    assert not reg.browser_class


def test_predicate_cloud_and_sidecar_always_enabled(tmp_path):
    cloud = _rooted_backend(tmp_path, location="server")
    sidecar = _rooted_backend(tmp_path, location="local")
    for backend in (cloud, sidecar):
        assert git_execution_enabled_for(backend, desktop_online=True) is True
        # A rooted workspace spawns git in-process — the client channel is irrelevant.
        assert git_execution_enabled_for(backend, desktop_online=False) is True


def test_predicate_channel_local_follows_desktop_online():
    backend = _ChannelLocalBackend()
    assert git_execution_enabled_for(backend, desktop_online=True) is True
    assert git_execution_enabled_for(backend, desktop_online=False) is False


def test_predicate_no_backend_keeps_tool_listed():
    """Capability catalog / bare tests build without a backend — keep git advertised."""
    assert git_execution_enabled_for(None) is True


def test_worker_registry_drops_git_when_desktop_offline():
    backend = _ChannelLocalBackend()
    offline = build_worker_registry(backend=backend, desktop_online=False).names
    assert "git" not in offline
    # Withholding git must not disturb the rest of the local roster.
    assert "file_write" in offline
    assert "file_read" in offline

    online = build_worker_registry(backend=backend, desktop_online=True).names
    assert "git" in online


def test_worker_registry_keeps_git_for_cloud_and_sidecar(tmp_path):
    for location in ("server", "local"):
        backend = _rooted_backend(tmp_path, location=location)
        names = build_worker_registry(backend=backend, desktop_online=False).names
        assert "git" in names, location


def test_ceo_registry_follows_include_git():
    assert "git" in build_ceo_tool_registry().names
    assert "git" not in build_ceo_tool_registry(include_git=False).names


def test_ceo_toolset_mirrors_worker_verdict():
    """``_assemble_ceo_toolset`` derives ``include_git`` from the worker roster."""
    backend = _ChannelLocalBackend()
    worker = build_worker_registry(backend=backend, desktop_online=False)
    ceo = build_ceo_tool_registry(
        desktop_online=False,
        backend_location="local",
        include_git="git" in worker.names,
    )
    assert "git" not in ceo.names


def test_builtin_registry_include_git_flag():
    assert "git" in build_builtin_registry().names
    assert "git" not in build_builtin_registry(include_git=False).names


def test_capability_catalog_still_advertises_git():
    """能力图鉴 lists git like Host tools — runtime assembly is the per-turn gate."""
    assert "git" in {entry.schema.name for entry in build_capability_catalog()}


def test_capability_line_git_unassembled_when_desktop_offline():
    out = build_workspace_context(_ChannelLocalBackend(), desktop_online=False)
    assert "git=未装配" in out
    assert "未装配 `git` 工具" in out
    assert "通道未连接" in out
    assert "在桌面客户端打开【本对话】" in out
    assert "文件读写与其它已装配工具不受影响" in out
    # 「未装配怎么开工 / 勿声称已用」写在共享基座 <诚实>，事实层不复述
    assert "同轮可开工" not in out
    from agentcore.runtime.resolve.prompt import _CEO_CORE_HINT, _DEFAULT_SYSTEM_PROMPT

    assert "未装配" in _DEFAULT_SYSTEM_PROMPT and "不得声称" in _DEFAULT_SYSTEM_PROMPT
    assert "git…）" not in _DEFAULT_SYSTEM_PROMPT  # 不按能力枚举
    assert "未装配能力" not in _CEO_CORE_HINT
    # An unassembled turn must not advise a tool the model does not hold.
    assert "init_baseline" not in out


def test_capability_line_git_assembled_when_desktop_online():
    out = build_workspace_context(_ChannelLocalBackend(), desktop_online=True)
    assert "git=已装配" in out
    assert "未装配 `git` 工具" not in out


def test_capability_line_git_assembled_for_cloud_and_sidecar(tmp_path):
    for location in ("server", "local"):
        out = build_workspace_context(
            _rooted_backend(tmp_path, location=location), desktop_online=False
        )
        assert "git=已装配" in out, location


def test_capability_line_matches_registry_across_environments(tmp_path):
    """能力行与 registry 同一谓词（对齐 case 20260803 的执行类口径）。"""
    cases = [
        (_ChannelLocalBackend(), False),
        (_ChannelLocalBackend(), True),
        (_rooted_backend(tmp_path, location="server"), False),
        (_rooted_backend(tmp_path, location="local"), False),
    ]
    for backend, desktop_online in cases:
        assembled = "git" in build_worker_registry(
            backend=backend, desktop_online=desktop_online
        ).names
        out = build_workspace_context(backend, desktop_online=desktop_online)
        expected = "git=已装配" if assembled else "git=未装配"
        assert expected in out, (backend.location, desktop_online)


# ---- binary axis: the in-process transport also needs a real ``git`` on PATH ----


def test_missing_binary_withholds_git_from_rooted_workspaces(tmp_path):
    """An image without ``git`` must withhold the tool, not hand out FileNotFoundError."""
    set_git_binary_health_for_tests(False, failure=("not_found", "no git"))
    for location in ("server", "local"):
        backend = _rooted_backend(tmp_path, location=location)
        assert git_execution_enabled_for(backend, desktop_online=True) is False, location
        names = build_worker_registry(backend=backend, desktop_online=True).names
        assert "git" not in names, location
        # Withholding git must not disturb the rest of the roster.
        assert "file_read" in names, location


def test_missing_binary_never_touches_the_channel_transport():
    """Channel-backed local runs git on the USER's machine — the server probe is moot.

    Gating this branch on the API process's own PATH would strip git from every
    desktop user the moment the server image lost the binary.
    """
    set_git_binary_health_for_tests(False, failure=("not_found", "no git"))
    backend = _ChannelLocalBackend()
    assert git_execution_enabled_for(backend, desktop_online=True) is True
    assert "git" in build_worker_registry(backend=backend, desktop_online=True).names
    assert "git=已装配" in build_workspace_context(backend, desktop_online=True)


def test_missing_binary_keeps_catalog_listing():
    """能力图鉴 is environment-free — a dead binary must not erase the entry."""
    set_git_binary_health_for_tests(False, failure=("not_found", "no git"))
    assert git_execution_enabled_for(None) is True
    assert "git" in {entry.schema.name for entry in build_capability_catalog()}


def test_healthy_binary_keeps_rooted_workspaces_assembled(tmp_path):
    set_git_binary_health_for_tests(True)
    backend = _rooted_backend(tmp_path, location="server")
    assert git_execution_enabled_for(backend, desktop_online=False) is True
    assert "git" in build_worker_registry(backend=backend, desktop_online=False).names


def test_capability_line_matches_registry_when_binary_missing(tmp_path):
    """Same predicate on both sides, on the binary axis too."""
    set_git_binary_health_for_tests(False, failure=("not_found", "no git"))
    backend = _rooted_backend(tmp_path, location="server")
    assembled = "git" in build_worker_registry(backend=backend, desktop_online=False).names
    out = build_workspace_context(backend, desktop_online=False)
    assert assembled is False
    assert "git=未装配" in out
