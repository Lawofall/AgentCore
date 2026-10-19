"""Host GUI face: attach / snapshot / click / type over the desktop channel."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.desktop.channel import HostOp, HostOpError
from agentcore.runtime.always_confirm import requires_always_confirm
from agentcore.tools.builtin.host import HostTool
from agentcore.tools.builtin.host_gui import _SEQ
from agentcore.tools.protocol import ToolContext
from agentcore.workspace.write_claims import WriteCoordinator


def _ctx(*, channel: object | None = None) -> ToolContext:
    backend = MagicMock(location="local")
    backend.write_bytes = AsyncMock(return_value=12)
    return ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="ceo",
        backend=backend,
        user_id="u1",
        conversation_id="c-gui",
        desktop_channel=channel,
        write_coordinator=WriteCoordinator(),
    )


@pytest.fixture(autouse=True)
def _reset_keyframe_seq():
    _SEQ.clear()
    yield
    _SEQ.clear()


def test_always_confirm_attach_not_snapshot_or_leftover_install():
    assert requires_always_confirm("host", {"action": "attach"})
    assert not requires_always_confirm("host", {"action": "snapshot"})
    assert not requires_always_confirm("host", {"action": "click", "ref": "e1"})
    assert not requires_always_confirm(
        "host",
        {"action": "snapshot", "command": "winget install Git.Git"},
    )
    assert requires_always_confirm("host", {"command": "winget install Git.Git"})


@pytest.mark.asyncio
async def test_gui_click_requires_ref():
    result = await HostTool().execute(
        {"action": "click"},
        _ctx(channel=MagicMock()),
    )
    assert not result.success
    assert result.contract_failure
    assert "ref" in (result.error or "")


@pytest.mark.asyncio
async def test_gui_type_requires_text():
    result = await HostTool().execute(
        {"action": "type", "ref": "e1"},
        _ctx(channel=MagicMock()),
    )
    assert not result.success
    assert result.contract_failure
    assert "text" in (result.error or "")


@pytest.mark.asyncio
async def test_gui_unattached_maps_kind():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        side_effect=HostOpError("gone", kind="HostGuiNotAttached")
    )
    result = await HostTool().execute({"action": "snapshot"}, _ctx(channel=channel))
    assert not result.success
    assert "还没有附着" in (result.error or "")
    assert (result.metadata or {}).get("code") == "HostGuiNotAttached"


@pytest.mark.asyncio
async def test_gui_empty_tree_honest_fail():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        side_effect=HostOpError("empty", kind="HostGuiEmptyTree")
    )
    result = await HostTool().execute({"action": "snapshot"}, _ctx(channel=channel))
    assert not result.success
    assert "看不见" in (result.error or "")
    assert "坐标" in (result.error or "")


@pytest.mark.asyncio
async def test_gui_password_denied():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        side_effect=HostOpError("pw", kind="HostGuiPasswordDenied")
    )
    result = await HostTool().execute(
        {"action": "type", "ref": "e1", "text": "secret"},
        _ctx(channel=channel),
    )
    assert not result.success
    assert "密码框" in (result.error or "")
    assert (result.metadata or {}).get("code") == "HostGuiPasswordDenied"


@pytest.mark.asyncio
async def test_gui_user_abort():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        side_effect=HostOpError("abort", kind="HostGuiUserAbort")
    )
    result = await HostTool().execute(
        {"action": "click", "ref": "e1"},
        _ctx(channel=channel),
    )
    assert not result.success
    assert "动了键鼠" in (result.error or "")


@pytest.mark.asyncio
async def test_snapshot_persists_keyframe_and_hides_pixels_from_model():
    jpeg = base64.b64encode(b"\xff\xd8jpeg").decode()
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "ok": True,
            "title": "记事本",
            "snapshot_version": 1,
            "controls": '- button "确定" [ref=e1]',
            "jpeg_b64": jpeg,
        }
    )
    ctx = _ctx(channel=channel)
    result = await HostTool().execute({"action": "snapshot"}, ctx)
    assert result.success
    payload = json.loads(result.output)
    assert payload["action"] == "snapshot"
    assert payload["snapshot_version"] == 1
    assert payload["untrusted_window_content"]["controls"].startswith("- button")
    assert "jpeg_b64" not in payload
    assert result.display["kind"] == "host"
    assert result.display["action"] == "snapshot"
    assert result.display["frame"] == "host/step-0001.jpg"
    assert result.display["title"] == "记事本"
    ctx.backend.write_bytes.assert_awaited_once()
    path, data = ctx.backend.write_bytes.await_args.args
    assert path == "host/step-0001.jpg"
    assert data.startswith(b"\xff\xd8")
    channel.request_host.assert_awaited_once()
    call = channel.request_host.await_args
    assert call.args[0] is HostOp.GUI_SNAPSHOT
    assert call.args[1]["conversation_id"] == "c-gui"


@pytest.mark.asyncio
async def test_click_postcondition_failed_keeps_display():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "ok": True,
            "title": "安装向导",
            "control_name": "下一步",
            "clicked": {"was_disabled": True, "matched": True, "name": "下一步"},
        }
    )
    result = await HostTool().execute({"action": "click", "ref": "e1"}, _ctx(channel=channel))
    assert not result.success
    assert "禁用" in (result.error or "")
    assert (result.metadata or {}).get("code") == "postcondition_failed"
    assert result.display["body"]


@pytest.mark.asyncio
async def test_unsupported_platform_kind():
    channel = MagicMock()
    channel.request_host = AsyncMock(
        side_effect=HostOpError("mac", kind="HostGuiUnsupportedPlatform")
    )
    result = await HostTool().execute({"action": "attach"}, _ctx(channel=channel))
    assert not result.success
    assert "Windows" in (result.error or "")
