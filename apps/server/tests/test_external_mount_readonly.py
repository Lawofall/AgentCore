"""Tests for ``external_mount_readonly`` (C1 silent read-only mount)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.desktop.channel import ExternalMountError
from agentcore.tools.builtin import build_ceo_tool_registry, build_worker_registry
from agentcore.tools.builtin.external_mount_readonly import (
    EXTERNAL_MOUNT_READONLY_TOOL_NAME,
    ExternalMountReadonlyTool,
    format_external_mount_error,
)
from agentcore.tools.protocol import ToolContext
from agentcore.workspace import grant_store
from agentcore.workspace.hot_attach import attach_grants_to_backend
from agentcore.workspace.server import ServerWorkspace


@pytest.fixture(autouse=True)
def _memory_grants():
    grant_store.clear_all_for_tests()
    yield
    grant_store.clear_all_for_tests()


def _ctx(**kwargs) -> ToolContext:
    backend = kwargs.pop("backend", MagicMock())
    return ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="ceo",
        backend=backend,
        user_id="u1",
        conversation_id=kwargs.pop("conversation_id", "conv-1"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_requires_desktop_channel():
    tool = ExternalMountReadonlyTool()
    result = await tool.execute(
        {"well_known": "desktop", "target_name": "咨询"},
        _ctx(desktop_channel=None),
    )
    assert result.success is False
    assert "桌面" in (result.error or "")


@pytest.mark.asyncio
async def test_requires_path_or_well_known():
    tool = ExternalMountReadonlyTool()
    channel = MagicMock()
    channel.request_external_mount_readonly = AsyncMock()
    result = await tool.execute({}, _ctx(desktop_channel=channel))
    assert result.success is False
    assert "path" in (result.error or "")
    channel.request_external_mount_readonly.assert_not_called()


@pytest.mark.asyncio
async def test_maps_not_found_error():
    tool = ExternalMountReadonlyTool()
    channel = MagicMock()
    channel.request_external_mount_readonly = AsyncMock(
        side_effect=ExternalMountError("找不到该目录", reason="not_found")
    )
    result = await tool.execute(
        {"well_known": "desktop", "target_name": "nope"},
        _ctx(desktop_channel=channel),
    )
    assert result.success is False
    assert "找不到" in (result.error or "")
    assert "挂载只接受文件夹" in (result.error or "")
    assert "reason=not_found" in (result.error or "")
    assert "盲重试" in (result.error or "")
    assert result.metadata.get("code") == "not_found"


@pytest.mark.asyncio
async def test_maps_ambiguous_error_with_stable_reason():
    tool = ExternalMountReadonlyTool()
    channel = MagicMock()
    channel.request_external_mount_readonly = AsyncMock(
        side_effect=ExternalMountError(
            "匹配到多个目录，请说得更具体", reason="ambiguous"
        )
    )
    result = await tool.execute(
        {"well_known": "desktop", "target_name": "docs"},
        _ctx(desktop_channel=channel),
    )
    assert result.success is False
    assert "reason=ambiguous" in (result.error or "")
    assert result.metadata.get("code") == "ambiguous"


def test_format_external_mount_error_preserves_reason():
    exc = ExternalMountError("路径指向的是文件，不是目录", reason="not_directory")
    text = format_external_mount_error(exc)
    assert "reason=not_directory" in text
    assert "盲重试" in text
    assert "这是文件不是文件夹" in text
    assert "请选它所在的目录" in text
    assert "工作区" in text
    assert "找不到" not in text


def test_format_external_mount_not_found_names_folder_only():
    exc = ExternalMountError("找不到该目录", reason="not_found")
    text = format_external_mount_error(exc)
    assert "找不到" in text
    assert "挂载只接受文件夹" in text
    assert "安装包/文件" in text
    assert "reason=not_found" in text


def test_schema_mentions_reason_and_soft_recovery():
    tool = ExternalMountReadonlyTool()
    desc = tool.schema.description
    assert "reason" in desc
    assert "盲重试" in desc
    assert "well_known" in desc


@pytest.mark.asyncio
async def test_channel_preserves_error_reason():
    from agentcore.desktop.channel import DesktopClientChannel

    registry = MagicMock()
    registry.suspend = AsyncMock(
        return_value={
            "ok": False,
            "error": {
                "kind": "ExternalMountError",
                "detail": "找不到该目录",
                "reason": "not_found",
            },
        }
    )
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c1",
        registry=registry,
        timeout_seconds=1.0,
    )
    with pytest.raises(ExternalMountError) as ei:
        await channel.request_external_mount_readonly(well_known="desktop")
    assert ei.value.reason == "not_found"
    assert "找不到" in str(ei.value)


def test_workspace_op_error_roundtrips_reason():
    from agentcore.api.schemas.messages import (
        ResolveClientToolInteraction,
        WorkspaceOpError,
        interaction_result_from_body,
    )

    body = ResolveClientToolInteraction(
        ok=False,
        error=WorkspaceOpError(
            kind="ExternalMountError",
            detail="匹配到多个目录，请说得更具体",
            reason="ambiguous",
        ),
    )
    envelope = interaction_result_from_body(body)
    assert envelope["ok"] is False
    assert envelope["error"]["reason"] == "ambiguous"
    assert envelope["error"]["detail"].startswith("匹配")


@pytest.mark.asyncio
async def test_success_registers_grant_and_hot_attaches(tmp_path):
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    tool = ExternalMountReadonlyTool()
    channel = MagicMock()
    channel.sink = MagicMock()
    channel.registry = MagicMock()
    channel.request_external_mount_readonly = AsyncMock(
        return_value={
            "root_id": "root-1",
            "alias": "consult",
            "label": "咨询",
            "display_label": "咨询",
            "namespace": "external/consult",
        }
    )
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="conv:x",
        location="server",
    )
    result = await tool.execute(
        {"well_known": "desktop", "target_name": "咨询"},
        _ctx(desktop_channel=channel, backend=backend, conversation_id="conv-hot"),
    )
    assert result.success is True
    assert "external/consult" in (result.output or "")
    assert "abs" not in (result.output or "").lower()
    grants = await grant_store.list_grants("conv-hot")
    assert len(grants) == 1
    assert grants[0].root_id == "root-1"
    assert "consult" in backend._mounts  # noqa: SLF001
    assert backend._external_bridge is not None  # noqa: SLF001


@pytest.mark.asyncio
async def test_hot_attach_helper_merges_mounts(tmp_path):
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="conv:x",
        location="server",
    )
    await grant_store.add_grant(
        "conv-ha", root_id="r1", label="桌面", alias_hint="desk"
    )
    channel = MagicMock()
    channel.sink = MagicMock()
    channel.registry = MagicMock()
    mounts = await attach_grants_to_backend(
        backend, "conv-ha", desktop_channel=channel
    )
    assert "desk" in mounts
    assert backend._mounts["desk"].root_id == "r1"  # noqa: SLF001
    assert backend._external_bridge is not None  # noqa: SLF001


def test_tool_assembled_only_when_desktop_online():
    online = {s.name for s in build_ceo_tool_registry(desktop_online=True).list_all()}
    offline = {s.name for s in build_ceo_tool_registry(desktop_online=False).list_all()}
    assert EXTERNAL_MOUNT_READONLY_TOOL_NAME in online
    assert EXTERNAL_MOUNT_READONLY_TOOL_NAME not in offline

    worker_on = {s.name for s in build_worker_registry(desktop_online=True).list_all()}
    worker_off = {s.name for s in build_worker_registry(desktop_online=False).list_all()}
    assert EXTERNAL_MOUNT_READONLY_TOOL_NAME in worker_on
    assert EXTERNAL_MOUNT_READONLY_TOOL_NAME not in worker_off


def test_approval_never_and_audience_both():
    tool = ExternalMountReadonlyTool()
    assert tool.schema.approval.value == "never"
    from agentcore.tools.registration import AUDIENCE_BOTH, tool_registration

    assert tool_registration(ExternalMountReadonlyTool).audience == AUDIENCE_BOTH
