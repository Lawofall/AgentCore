"""Unit tests for Host tool and DesktopClientChannel host ops."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.core.types import (
    ToolApproval,
    WorkspaceBoundary,
)
from agentcore.desktop.channel import DesktopClientChannel, HostOp, HostOpError
from agentcore.runtime.engine import resolve_tool_timeout
from agentcore.tools.builtin import (
    build_ceo_tool_registry,
    build_worker_registry,
    delegation_grantable_tool_names,
)
from agentcore.tools.builtin.host import (
    HostTool,
    host_call_requires_approval,
    host_tool_timeout_seconds,
    shell_cmd_env_blocks,
    shell_fuse_blocks,
    shell_silent_install_blocks,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registration import execution_class_tool_names, host_class_tool_names
from agentcore.workspace.write_claims import WriteCoordinator

_RETIRED_HOST_NAMES = frozenset(
    {
        "host_ping",
        "host_info",
        "host_audio_devices",
        "host_storage",
        "host_power",
        "host_network_summary",
        "host_apps",
        "host_os_log_summary",
        "host_shell",
        "host_open_settings",
        "host_audio_set_default",
        "host_service_restart",
        "host_package_install",
    }
)


def _ctx(
    *,
    as_worker: bool = False,
    channel: object | None = None,
    location: str = "local",
) -> ToolContext:
    return ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="w1" if as_worker else "ceo",
        backend=MagicMock(location=location),
        user_id="u1",
        desktop_channel=channel,
        write_coordinator=WriteCoordinator() if as_worker else None,
    )


@pytest.mark.asyncio
async def test_host_shell_rejects_empty_command():
    result = await HostTool().execute(
        {"action": "shell", "command": "  "},
        _ctx(channel=MagicMock()),
    )
    assert not result.success
    assert "非空" in (result.error or "")


@pytest.mark.asyncio
async def test_host_shell_rejects_cmd_style_env():
    ctx = _ctx(channel=MagicMock())
    result = await HostTool().execute(
        {
            "action": "shell",
            "command": "if (Test-Path '%APPDATA%\\Cursor\\logs') { 'ok' }",
        },
        ctx,
    )
    assert not result.success
    assert "%VAR%" in (result.error or "") or "$env:" in (result.error or "")
    ctx.desktop_channel.request_host.assert_not_called()


@pytest.mark.asyncio
async def test_host_shell_fuse_blocks_rm_rf_root():
    ctx = _ctx(channel=MagicMock())
    result = await HostTool().execute({"action": "shell", "command": "rm -rf /"}, ctx)
    assert not result.success
    assert "熔断" in (result.error or "")
    ctx.desktop_channel.request_host.assert_not_called()


@pytest.mark.asyncio
async def test_host_shell_rejects_long_running_dev_server():
    ctx = _ctx(channel=MagicMock())
    result = await HostTool().execute({"action": "shell", "command": "npm run dev"}, ctx)
    assert not result.success
    assert "长驻" in (result.error or "")
    assert "run" in (result.error or "")
    assert "terminal" not in (result.error or "")
    ctx.desktop_channel.request_host.assert_not_called()


def test_shell_fuse_and_timeout_helpers():
    assert shell_fuse_blocks("shutdown /s /t 0")
    assert shell_fuse_blocks("Format-Volume -DriveLetter C")
    assert shell_fuse_blocks("echo hi") is None
    assert shell_cmd_env_blocks("Get-ChildItem $env:APPDATA") is None
    assert shell_cmd_env_blocks("dir %APPDATA%\\Cursor\\logs")
    assert shell_cmd_env_blocks("echo %LOCALAPPDATA%")


@pytest.mark.asyncio
async def test_host_shell_forwards_with_timeout():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "timed_out": False,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "cwd": "C:\\Users\\u",
        }
    )
    result = await HostTool().execute(
        {"action": "shell", "command": "echo ok", "timeout_seconds": 15},
        _ctx(channel=channel),
    )
    assert result.success
    assert "ok" in result.output
    assert "<不可信内容>" not in result.output
    assert json.loads(result.output)["exit_code"] == 0
    assert result.display == {
        "stdout": "ok",
        "stderr": "",
        "exit_code": 0,
        "language": "host",
    }
    channel.request_host.assert_awaited_once()
    call = channel.request_host.await_args
    assert call.args[0] is HostOp.SHELL
    assert call.args[1]["command"] == "echo ok"
    assert call.args[1]["timeout_seconds"] == 60
    assert call.kwargs["timeout"] == 75.0
    assert call.args[1]["conversation_id"] == ""
    assert "cwd" not in call.args[1]


@pytest.mark.asyncio
async def test_host_shell_injects_local_cwd_and_ignores_model_cwd():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "timed_out": False,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "cwd": "/tmp/ws",
        }
    )
    backend = MagicMock(location="local")
    backend.root = Path("/tmp/ws")
    backend._channel = MagicMock(root_id="root-1")
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="ceo",
        backend=backend,
        user_id="u1",
        desktop_channel=channel,
        conversation_id="conv-1",
    )
    result = await HostTool().execute(
        {
            "action": "shell",
            "command": "echo ok",
            "cwd": "/etc",
            "timeout_seconds": 15,
        },
        ctx,
    )
    assert result.success
    payload = channel.request_host.await_args.args[1]
    assert payload["cwd"] == str(Path("/tmp/ws"))
    assert payload["root_id"] == "root-1"
    assert payload["conversation_id"] == "conv-1"
    assert payload["command"] == "echo ok"


@pytest.mark.asyncio
async def test_host_shell_cloud_does_not_forward_model_cwd():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "timed_out": False,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "cwd": "/home/u",
        }
    )
    result = await HostTool().execute(
        {"action": "shell", "command": "echo ok", "cwd": "/etc"},
        _ctx(channel=channel, location="server"),
    )
    assert result.success
    payload = channel.request_host.await_args.args[1]
    assert "cwd" not in payload
    assert "root_id" not in payload
    assert payload["command"] == "echo ok"


def test_host_dynamic_timeout_aligns_today_tiers():
    schema = HostTool().schema
    assert schema.timeout_seconds is None
    assert set(schema.parameters["properties"]) == {
        "action",
        "command",
        "ref",
        "text",
        "snapshot_version",
    }
    assert "required" not in schema.parameters
    assert host_tool_timeout_seconds({"command": "echo ok"}) == 75.0
    assert host_tool_timeout_seconds({"command": "winget install Git.Git"}) == 630.0
    assert host_tool_timeout_seconds({"action": "attach"}) == 200.0
    assert host_tool_timeout_seconds({"action": "snapshot"}) == 60.0
    assert resolve_tool_timeout(schema, {"command": "echo ok"}) == 75.0
    assert resolve_tool_timeout(schema, {"command": "brew install --cask docker"}) == 630.0


def test_host_call_requires_approval_for_command():
    assert host_call_requires_approval({"command": "echo ok"})
    assert host_call_requires_approval({"command": "winget install Git.Git"})
    assert host_call_requires_approval({"action": "attach"})
    assert not host_call_requires_approval({})
    assert not host_call_requires_approval({"command": "  "})
    assert not host_call_requires_approval({"action": "snapshot"})
    assert not host_call_requires_approval({"action": "click", "ref": "e1"})
    assert not host_call_requires_approval({"action": "type", "ref": "e1", "text": "hi"})


def test_host_schema_includes_gui_actions():
    props = HostTool().schema.parameters["properties"]
    assert set(props) == {"action", "command", "ref", "text", "snapshot_version"}
    assert set(props["action"]["enum"]) == {
        "attach",
        "click",
        "shell",
        "snapshot",
        "type",
    }


@pytest.mark.asyncio
async def test_channel_request_host_emits_and_returns():
    from tests.client_tool_fulfill_testutil import DELIVERED_EVENTS

    DELIVERED_EVENTS.clear()
    registry = MagicMock()

    async def _suspend(*_a, **kwargs):
        on_suspended = kwargs.get("on_suspended")
        if callable(on_suspended):
            on_suspended()
        return {"ok": True, "value": {"ok": True, "platform": "win32"}}

    registry.suspend = AsyncMock(side_effect=_suspend)
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c1",
        registry=registry,
        timeout_seconds=1.0,
    )
    value = await channel.request_host(HostOp.PING)
    assert value["ok"] is True
    assert len(DELIVERED_EVENTS) == 1
    event = DELIVERED_EVENTS[0]
    assert event.type.value == "host_op_required"
    assert event.payload["op"] == "host_ping"


@pytest.mark.asyncio
async def test_channel_maps_host_failure():
    registry = MagicMock()
    registry.suspend = AsyncMock(
        return_value={"ok": False, "error": {"detail": "desktop gone"}}
    )
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c1",
        registry=registry,
        timeout_seconds=1.0,
    )
    with pytest.raises(HostOpError, match="desktop gone"):
        await channel.request_host(HostOp.AUDIO_DEVICES)


def test_host_tools_gated_on_boundary_not_desktop_heartbeat():
    computer = WorkspaceBoundary.COMPUTER
    names_off = {
        s.name
        for s in build_worker_registry(
            desktop_online=False, permission_axes=computer
        ).list_all()
    }
    assert "host" in names_off
    assert names_off.isdisjoint(_RETIRED_HOST_NAMES)

    names_folder = {
        s.name
        for s in build_worker_registry(
            desktop_online=True, permission_axes=WorkspaceBoundary.FOLDER
        ).list_all()
    }
    assert "host" not in names_folder

    names_on = {
        s.name
        for s in build_worker_registry(
            desktop_online=True, permission_axes=computer
        ).list_all()
    }
    assert "host" in names_on
    assert names_on.isdisjoint(_RETIRED_HOST_NAMES)

    ceo_offline = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=False, permission_axes=computer
        ).list_all()
    }
    assert "host" in ceo_offline
    ceo = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=True, permission_axes=computer
        ).list_all()
    }
    assert "host" in ceo
    assert ceo.isdisjoint(_RETIRED_HOST_NAMES)
    host_schema = build_ceo_tool_registry(
        desktop_online=True, permission_axes=computer
    ).get("host").schema
    assert host_schema.approval is ToolApproval.NEVER


def test_host_not_in_execution_or_kickoff_whitelist():
    host_names = host_class_tool_names()
    assert host_names == frozenset({"host"})
    assert host_names.isdisjoint(execution_class_tool_names())
    assert host_names.isdisjoint(delegation_grantable_tool_names())
    assert host_names.isdisjoint(_RETIRED_HOST_NAMES)


def test_host_is_audit_grantable():
    """Runtime-elevated host GRANTABLE actions land on agent_audit_events."""
    from agentcore.runtime.audit.projector import _grantable_tool_names

    names = _grantable_tool_names()
    assert "host" in names
    assert names.isdisjoint(_RETIRED_HOST_NAMES)


@pytest.mark.asyncio
async def test_host_package_install_forwards_as_shell():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "timed_out": False,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }
    )
    result = await HostTool().execute(
        {"command": "winget install Microsoft.VisualStudioCode"},
        _ctx(as_worker=True, channel=channel),
    )
    assert result.success
    channel.request_host.assert_awaited_once()
    call = channel.request_host.await_args
    assert call.args[0] is HostOp.SHELL
    assert call.args[1]["command"] == "winget install Microsoft.VisualStudioCode"
    assert call.args[1]["timeout_seconds"] == 600
    assert call.kwargs["timeout"] == 630.0


@pytest.mark.asyncio
async def test_host_shell_silent_install_fuse():
    ctx = _ctx(channel=MagicMock())
    result = await HostTool().execute(
        {"action": "shell", "command": r"msiexec /i Setup.msi /quiet"},
        ctx,
    )
    assert not result.success
    assert "静默安装" in (result.error or "") or "启发式" in (result.error or "")
    assert "winget" in (result.error or "")
    ctx.desktop_channel.request_host.assert_not_called()


def test_shell_silent_install_and_package_helpers():
    samples = [
        r"msiexec /i foo.msi /qn",
        r".\Setup.exe /S",
        r"Start-Process Setup.exe -ArgumentList '/quiet'",
        r"Installer.exe /VERYSILENT",
        r"curl -L https://example.com/Setup.exe -o Setup.exe",
    ]
    for cmd in samples:
        assert shell_silent_install_blocks(cmd), cmd
    assert shell_silent_install_blocks("echo hi") is None
    assert shell_silent_install_blocks("winget install Git.Git") is None
    assert shell_fuse_blocks("echo hi") is None


def test_host_stays_listed_without_desktop_online():
    names_off = {
        s.name
        for s in build_worker_registry(
            desktop_online=False, permission_axes=WorkspaceBoundary.COMPUTER
        ).list_all()
    }
    assert "host" in names_off
    assert names_off.isdisjoint(_RETIRED_HOST_NAMES)


def test_computer_keeps_host_and_execution():
    """这台电脑 includes host and run; 只看 includes neither."""
    axes = WorkspaceBoundary.COMPUTER
    names = {
        s.name
        for s in build_worker_registry(
            desktop_online=True, permission_axes=axes
        ).list_all()
    }
    assert "host" in names
    assert names.isdisjoint(_RETIRED_HOST_NAMES)
    assert "run" in names
    read_names = {
        s.name
        for s in build_worker_registry(
            desktop_online=True, permission_axes=WorkspaceBoundary.READ
        ).list_all()
    }
    assert "host" not in read_names
    assert "run" not in read_names

    ceo = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=True, permission_axes=axes
        ).list_all()
    }
    assert "host" in ceo
    assert ceo.isdisjoint(_RETIRED_HOST_NAMES)
