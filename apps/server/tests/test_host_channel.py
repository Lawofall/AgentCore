"""Unit tests for Host tool and DesktopClientChannel host ops."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.core.types import (
    CommandAxis,
    FileWriteAxis,
    HostAxis,
    PermissionAxes,
    ToolApproval,
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
    clamp_package_timeout,
    clamp_shell_timeout,
    host_call_requires_approval,
    host_tool_timeout_seconds,
    normalize_os_log_args,
    shell_cmd_env_blocks,
    shell_fuse_blocks,
    shell_silent_install_blocks,
    validate_package_install_args,
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
async def test_host_status_requires_channel():
    result = await HostTool().execute({"action": "status"}, _ctx())
    assert not result.success
    assert "桌面" in (result.error or "")


@pytest.mark.asyncio
async def test_host_status_fanout_all_facets():
    channel = MagicMock()

    async def _reply(op, args, timeout=None):
        return {"op": op.value, "ok": True}

    channel.request_host = AsyncMock(side_effect=_reply)
    result = await HostTool().execute({"action": "status"}, _ctx(channel=channel))
    assert result.success
    assert "<不可信内容>" in result.output
    ops = [c.args[0] for c in channel.request_host.await_args_list]
    assert ops == [
        HostOp.INFO,
        HostOp.AUDIO_DEVICES,
        HostOp.STORAGE,
        HostOp.POWER,
        HostOp.NETWORK_SUMMARY,
        HostOp.APPS,
    ]
    assert HostOp.PING not in ops
    assert HostOp.OS_LOG_SUMMARY not in ops
    for facet in (
        "info",
        "audio_devices",
        "storage",
        "power",
        "network_summary",
        "apps",
    ):
        assert facet in result.output


@pytest.mark.asyncio
async def test_host_status_facets_subset():
    channel = MagicMock()
    channel.request_host = AsyncMock(return_value={"platform": "win32", "hostname": "DESKTOP-1"})
    result = await HostTool().execute(
        {"action": "status", "facets": ["info"]},
        _ctx(channel=channel),
    )
    assert result.success
    assert "DESKTOP-1" in result.output
    channel.request_host.assert_awaited_once_with(HostOp.INFO, {}, timeout=20.0)


@pytest.mark.asyncio
async def test_host_open_settings_rejects_unknown_panel():
    result = await HostTool().execute(
        {"action": "open_settings", "panel": "bluetooth"},
        _ctx(as_worker=True, channel=MagicMock()),
    )
    assert not result.success
    assert "sound" in (result.error or "")
    assert "display" in (result.error or "")


@pytest.mark.asyncio
async def test_host_open_settings_accepts_display():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={"opened": True, "panel": "display", "uri": "ms-settings:display"}
    )
    result = await HostTool().execute(
        {"action": "open_settings", "panel": "display"},
        _ctx(as_worker=True, channel=channel),
    )
    assert result.success
    channel.request_host.assert_awaited_once_with(
        HostOp.OPEN_SETTINGS, {"panel": "display"}, timeout=30.0
    )


@pytest.mark.asyncio
async def test_host_ceo_worker_only_action_tells_delegate():
    result = await HostTool().execute(
        {"action": "open_settings", "panel": "sound"},
        _ctx(as_worker=False, channel=MagicMock()),
    )
    assert not result.success
    assert result.contract_failure is True
    assert "delegate" in (result.error or "")
    assert "Worker" in (result.error or "")


@pytest.mark.asyncio
async def test_host_audio_set_default_requires_device():
    result = await HostTool().execute(
        {"action": "set_audio"},
        _ctx(as_worker=True, channel=MagicMock()),
    )
    assert not result.success
    assert "device_id" in (result.error or "")


@pytest.mark.asyncio
async def test_host_audio_set_default_forwards():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={"set": True, "device_id": "{0.0.0.00000000}.{abc}", "name": "Speakers"}
    )
    result = await HostTool().execute(
        {"action": "set_audio", "device_name": "Speakers"},
        _ctx(as_worker=True, channel=channel),
    )
    assert result.success
    channel.request_host.assert_awaited_once_with(
        HostOp.AUDIO_SET_DEFAULT, {"device_name": "Speakers"}, timeout=45.0
    )


@pytest.mark.asyncio
async def test_host_service_restart_rejects_unknown():
    result = await HostTool().execute(
        {"action": "restart_service", "service": "Spooler"},
        _ctx(as_worker=True, channel=MagicMock()),
    )
    assert not result.success
    assert "Audiosrv" in (result.error or "")
    assert "Spooler" in (result.error or "")


@pytest.mark.asyncio
async def test_host_service_restart_accepts_audiosrv():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={"restarted": True, "service": "Audiosrv", "status": "Running"}
    )
    result = await HostTool().execute(
        {"action": "restart_service", "service": "audiosrv"},
        _ctx(as_worker=True, channel=channel),
    )
    assert result.success
    channel.request_host.assert_awaited_once_with(
        HostOp.SERVICE_RESTART, {"service": "Audiosrv"}, timeout=60.0
    )


@pytest.mark.asyncio
async def test_host_os_log_via_channel():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "platform": "win32",
            "bounded": True,
            "entries": [{"time": "t", "level": "Error", "source": "App", "message": "x"}],
            "note": "os_event_log_bounded_summary",
        }
    )
    result = await HostTool().execute(
        {
            "action": "os_log",
            "source": "App",
            "level": "error",
            "minutes": 30,
            "max_entries": 5,
        },
        _ctx(channel=channel),
    )
    assert result.success
    assert "bounded" in result.output
    channel.request_host.assert_awaited_once_with(
        HostOp.OS_LOG_SUMMARY,
        {
            "source": "App",
            "level": "error",
            "minutes": 30,
            "max_entries": 5,
            "max_bytes": 24_000,
        },
        timeout=45.0,
    )


def test_normalize_os_log_args_clamps():
    out = normalize_os_log_args(
        {"minutes": 99999, "max_entries": 999, "max_bytes": 9_999_999, "level": "nope"}
    )
    assert out["minutes"] == 1440
    assert out["max_entries"] == 80
    assert out["max_bytes"] == 48_000
    assert out["level"] == "warning"


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
    assert "terminal" in (result.error or "")
    ctx.desktop_channel.request_host.assert_not_called()


def test_shell_fuse_and_timeout_helpers():
    assert shell_fuse_blocks("shutdown /s /t 0")
    assert shell_fuse_blocks("Format-Volume -DriveLetter C")
    assert shell_fuse_blocks("echo hi") is None
    assert shell_cmd_env_blocks("Get-ChildItem $env:APPDATA") is None
    assert shell_cmd_env_blocks("dir %APPDATA%\\Cursor\\logs")
    assert shell_cmd_env_blocks("echo %LOCALAPPDATA%")
    assert clamp_shell_timeout(None) == 60
    assert clamp_shell_timeout(999) == 120
    assert clamp_shell_timeout(0) == 1
    assert clamp_shell_timeout("45") == 45


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
    channel.request_host.assert_awaited_once()
    call = channel.request_host.await_args
    assert call.args[0] is HostOp.SHELL
    assert call.args[1]["command"] == "echo ok"
    assert call.args[1]["timeout_seconds"] == 15
    assert call.kwargs["timeout"] == 30.0  # 15 + 15 slack
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
    assert host_tool_timeout_seconds({"action": "status"}) == 45.0
    assert host_tool_timeout_seconds({"action": "status", "facets": ["info"]}) == 20.0
    assert host_tool_timeout_seconds({"action": "os_log"}) == 45.0
    assert host_tool_timeout_seconds({"action": "open_settings"}) == 30.0
    assert host_tool_timeout_seconds({"action": "set_audio"}) == 45.0
    assert host_tool_timeout_seconds({"action": "restart_service"}) == 60.0
    assert host_tool_timeout_seconds({"action": "shell"}) == 75.0
    assert host_tool_timeout_seconds({"action": "shell", "timeout_seconds": 120}) == 135.0
    assert host_tool_timeout_seconds({"action": "install_package"}) == 630.0
    assert (
        host_tool_timeout_seconds({"action": "install_package", "timeout_seconds": 900})
        == 930.0
    )
    assert resolve_tool_timeout(schema, {"action": "install_package"}) == 630.0
    assert resolve_tool_timeout(schema, {"action": "shell"}) == 75.0


def test_host_call_requires_approval_by_action():
    assert not host_call_requires_approval({"action": "status"})
    assert not host_call_requires_approval({"action": "os_log"})
    assert host_call_requires_approval({"action": "shell"})
    assert host_call_requires_approval({"action": "open_settings"})
    assert host_call_requires_approval({"action": "set_audio"})
    assert host_call_requires_approval({"action": "restart_service"})
    assert host_call_requires_approval({"action": "install_package"})
    assert not host_call_requires_approval({"action": "nope"})


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


def test_host_tools_gated_on_desktop_online_and_host_axis():
    names_off = {s.name for s in build_worker_registry(desktop_online=False).list_all()}
    assert "host" not in names_off
    assert names_off.isdisjoint(_RETIRED_HOST_NAMES)

    axes_off = PermissionAxes(
        host=HostAxis.OFF,
    )
    names_axis_off = {
        s.name
        for s in build_worker_registry(
            desktop_online=True, permission_axes=axes_off
        ).list_all()
    }
    assert "host" not in names_axis_off

    names_on = {
        s.name for s in build_worker_registry(desktop_online=True).list_all()
    }
    assert "host" in names_on
    assert names_on.isdisjoint(_RETIRED_HOST_NAMES)

    ceo = {
        s.name
        for s in build_ceo_tool_registry(desktop_online=True).list_all()
    }
    assert "host" in ceo
    assert ceo.isdisjoint(_RETIRED_HOST_NAMES)
    host_schema = build_ceo_tool_registry(desktop_online=True).get("host").schema
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
async def test_host_package_install_rejects_non_allowlisted_manager():
    ctx = _ctx(as_worker=True, channel=MagicMock())
    result = await HostTool().execute(
        {"action": "install_package", "manager": "choco", "package_id": "git"},
        ctx,
    )
    assert not result.success
    assert "winget" in (result.error or "")
    ctx.desktop_channel.request_host.assert_not_called()


@pytest.mark.asyncio
async def test_host_package_install_forwards_winget():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "timed_out": False,
            "exit_code": 0,
            "manager": "winget",
            "package_id": "Microsoft.VisualStudioCode",
        }
    )
    result = await HostTool().execute(
        {
            "action": "install_package",
            "manager": "winget",
            "package_id": "Microsoft.VisualStudioCode",
            "timeout_seconds": 120,
        },
        _ctx(as_worker=True, channel=channel),
    )
    assert result.success
    channel.request_host.assert_awaited_once()
    call = channel.request_host.await_args
    assert call.args[0] is HostOp.PACKAGE_INSTALL
    assert call.args[1]["manager"] == "winget"
    assert call.args[1]["package_id"] == "Microsoft.VisualStudioCode"
    assert call.args[1]["timeout_seconds"] == 120
    assert call.kwargs["timeout"] == 150.0  # 120 + 30 slack


@pytest.mark.asyncio
async def test_host_shell_silent_install_fuse():
    ctx = _ctx(channel=MagicMock())
    result = await HostTool().execute(
        {"action": "shell", "command": r"msiexec /i Setup.msi /quiet"},
        ctx,
    )
    assert not result.success
    assert "静默安装" in (result.error or "") or "启发式" in (result.error or "")
    assert "install_package" in (result.error or "")
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
    assert shell_fuse_blocks("echo hi") is None
    assert validate_package_install_args(manager="choco", package_id="git")
    assert validate_package_install_args(
        manager="winget", package_id="Microsoft.VisualStudioCode"
    ) is None
    assert validate_package_install_args(
        manager="brew", package_id="docker", cask=True
    ) is None
    assert validate_package_install_args(
        manager="apt", package_id="docker.io", cask=True
    )
    assert clamp_package_timeout(None) == 600
    assert clamp_package_timeout(30) == 60
    assert clamp_package_timeout(9999) == 900


def test_host_absent_without_desktop_online():
    names_off = {s.name for s in build_worker_registry(desktop_online=False).list_all()}
    assert "host" not in names_off
    assert names_off.isdisjoint(_RETIRED_HOST_NAMES)


def test_command_ask_keeps_host():
    """command=ask withholds execution_class but must not strip Host."""
    axes = PermissionAxes(
        file_write=FileWriteAxis.ASK,
        command=CommandAxis.ASK,
        host=HostAxis.ASK,
    )
    names = {
        s.name
        for s in build_worker_registry(
            desktop_online=True, permission_axes=axes
        ).list_all()
    }
    assert "host" in names
    assert names.isdisjoint(_RETIRED_HOST_NAMES)
    assert "code_execute" not in names

    ceo = {
        s.name
        for s in build_ceo_tool_registry(
            desktop_online=True, permission_axes=axes
        ).list_all()
    }
    assert "host" in ceo
    assert ceo.isdisjoint(_RETIRED_HOST_NAMES)
