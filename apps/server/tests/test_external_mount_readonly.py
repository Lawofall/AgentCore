"""Runtime host-path mount (file tools mint; no FC mount tool)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.desktop.channel import ExternalMountError
from agentcore.tools.builtin import build_ceo_tool_registry, build_worker_registry
from agentcore.tools.builtin.file_ops.prepare_path import prepare_tool_path
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace import grant_store
from agentcore.workspace.ensure_host_path import format_external_mount_error
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


def test_mount_tool_absent_from_registries():
    online = {s.name for s in build_ceo_tool_registry(desktop_online=True).list_all()}
    offline = {s.name for s in build_ceo_tool_registry(desktop_online=False).list_all()}
    assert "external_mount_readonly" not in online
    assert "external_mount_readonly" not in offline
    worker_on = {s.name for s in build_worker_registry(desktop_online=True).list_all()}
    assert "external_mount_readonly" not in worker_on


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


def test_format_cancelled():
    text = format_external_mount_error(ExternalMountError("nope", reason="cancelled"))
    assert "拒绝" in text
    assert "盲重试" in text


@pytest.mark.asyncio
async def test_prepare_requires_desktop_channel():
    result = await prepare_tool_path(
        "~/Desktop/咨询", _ctx(desktop_channel=None), as_directory=True
    )
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert "桌面" in (result.error or "")


@pytest.mark.asyncio
async def test_prepare_maps_not_found():
    channel = MagicMock()
    channel.request_external_mount = AsyncMock(
        side_effect=ExternalMountError("找不到该目录", reason="not_found")
    )
    result = await prepare_tool_path(
        "~/Desktop/nope", _ctx(desktop_channel=channel), as_directory=True
    )
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert "找不到" in (result.error or "")
    assert "reason=not_found" in (result.error or "")
    assert result.metadata.get("code") == "not_found"


@pytest.mark.asyncio
async def test_file_retry_parent_on_not_directory(tmp_path):
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    channel = MagicMock()
    channel.sink = MagicMock()
    channel.registry = MagicMock()
    channel.request_external_mount = AsyncMock(
        side_effect=[
            ExternalMountError("not a dir", reason="not_directory"),
            {
                "root_id": "root-1",
                "alias": "downloads",
                "label": "下载",
                "display_label": "下载",
                "namespace": "external/downloads",
            },
        ]
    )
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="conv:x",
        location="server",
    )
    got = await prepare_tool_path(
        r"D:\Downloads\foo.pdf",
        _ctx(desktop_channel=channel, backend=backend, conversation_id="conv-hot"),
    )
    assert got == "external/downloads/foo.pdf"
    assert channel.request_external_mount.await_count == 2
    grants = await grant_store.list_grants("conv-hot")
    assert len(grants) == 1
    assert grants[0].root_id == "root-1"


@pytest.mark.asyncio
async def test_well_known_rewrites_remainder(tmp_path):
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    channel = MagicMock()
    channel.sink = MagicMock()
    channel.registry = MagicMock()
    channel.request_external_mount = AsyncMock(
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
    got = await prepare_tool_path(
        "~/Desktop/咨询/a.md",
        _ctx(desktop_channel=channel, backend=backend, conversation_id="conv-hot"),
    )
    assert got == "external/consult/a.md"
    grants = await grant_store.list_grants("conv-hot")
    assert len(grants) == 1
    assert "consult" in backend._mounts  # noqa: SLF001


@pytest.mark.asyncio
async def test_cloud_file_write_host_path_denied():
    channel = MagicMock()
    channel.request_external_mount = AsyncMock()
    backend = MagicMock()
    backend.location = "server"
    result = await prepare_tool_path(
        r"D:\out\a.md",
        _ctx(desktop_channel=channel, backend=backend),
        grant_mode="attach_rw",
    )
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert "云对话" in (result.error or "")
    channel.request_external_mount.assert_not_called()


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
        await channel.request_external_mount(well_known="desktop")
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


@pytest.mark.asyncio
async def test_external_ns_write_upgrades_readonly_by_root_id(tmp_path):
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    channel = MagicMock()
    channel.sink = MagicMock()
    channel.registry = MagicMock()
    channel.request_external_mount = AsyncMock(
        return_value={
            "root_id": "root-1",
            "alias": "desk",
            "label": "桌面",
            "namespace": "external/desk",
        }
    )
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="conv:x",
        location="server",
    )
    await grant_store.add_grant(
        "conv-up", root_id="root-1", label="桌面", alias_hint="desk", mode="readonly"
    )
    await attach_grants_to_backend(
        backend, "conv-up", desktop_channel=channel
    )
    got = await prepare_tool_path(
        "external/desk/out.md",
        _ctx(desktop_channel=channel, backend=backend, conversation_id="conv-up"),
        grant_mode="organize",
    )
    assert got == "external/desk/out.md"
    channel.request_external_mount.assert_awaited_once()
    kwargs = channel.request_external_mount.await_args.kwargs
    assert kwargs["root_id"] == "root-1"
    assert kwargs["mode"] == "organize"
    assert kwargs["path"] is None
    grants = await grant_store.list_grants("conv-up")
    assert grants[0].mode == "organize"
    assert backend._mounts["desk"].mode == "organize"  # noqa: SLF001


@pytest.mark.asyncio
async def test_external_ns_write_skips_mint_when_mode_covers(tmp_path):
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    channel = MagicMock()
    channel.request_external_mount = AsyncMock()
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="conv:x",
        location="server",
    )
    await grant_store.add_grant(
        "conv-ok", root_id="root-1", label="桌面", alias_hint="desk", mode="organize"
    )
    got = await prepare_tool_path(
        "external/desk/out.md",
        _ctx(desktop_channel=channel, backend=backend, conversation_id="conv-ok"),
        grant_mode="organize",
    )
    assert got == "external/desk/out.md"
    channel.request_external_mount.assert_not_called()


@pytest.mark.asyncio
async def test_external_ns_cloud_attach_rw_does_not_upgrade():
    """Cloud file_write on an existing mount must not mint attach_rw.

    Backend organize/readonly policy is the model-facing reason.
    """
    channel = MagicMock()
    channel.request_external_mount = AsyncMock()
    backend = MagicMock()
    backend.location = "server"
    backend._mounts = {}
    await grant_store.add_grant(
        "conv-rw", root_id="root-1", label="桌面", alias_hint="desk", mode="organize"
    )
    got = await prepare_tool_path(
        "external/desk/a.md",
        _ctx(desktop_channel=channel, backend=backend, conversation_id="conv-rw"),
        grant_mode="attach_rw",
    )
    assert got == "external/desk/a.md"
    channel.request_external_mount.assert_not_called()


def test_cloud_attach_rw_receipt_is_not_writable_home():
    from agentcore.workspace.ensure_host_path import _CLOUD_ATTACH_RW

    assert "加成可覆盖写根" in _CLOUD_ATTACH_RW
    assert "原件" in _CLOUD_ATTACH_RW
    assert "不覆盖" in _CLOUD_ATTACH_RW
    assert "file_copy" in _CLOUD_ATTACH_RW
    assert "已经能改" not in _CLOUD_ATTACH_RW


def test_event_carries_root_id():
    from agentcore.runtime.events.desktop import external_mount_required

    ev = external_mount_required(
        request_id="r1",
        conversation_id="c1",
        mode="organize",
        root_id="root-1",
    )
    assert ev.payload["root_id"] == "root-1"
    assert ev.payload["mode"] == "organize"
    assert "path" not in ev.payload
    from agentcore.runtime.events.payloads.workspace import ExternalMountRequiredPayload

    ExternalMountRequiredPayload.model_validate(ev.payload)


def test_old_sse_event_name_absent():
    from agentcore.runtime.events.types import EventType

    names = {e.value for e in EventType}
    assert "external_mount_required" in names
    assert "external_mount_readonly_required" not in names


@pytest.mark.asyncio
async def test_hot_attach_preserves_live_abs_path(tmp_path):
    """Sidecar Path-I/O: grant-store has no abs; live snapshot must survive _mint."""
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.external_mounts import ExternalMount

    abs_dir = str(tmp_path / "desk")
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="conv:x",
        location="local",
    )
    backend.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk",
                root_id="r1",
                label="桌面",
                abs_path=abs_dir,
                mode="readonly",
            )
        }
    )
    await grant_store.add_grant(
        "conv-abs", root_id="r1", label="桌面", alias_hint="desk", mode="organize"
    )
    mounts = await attach_grants_to_backend(backend, "conv-abs")
    assert mounts["desk"].abs_path == abs_dir
    assert mounts["desk"].mode == "organize"
    assert backend._mounts["desk"].abs_path == abs_dir  # noqa: SLF001
    assert backend._mounts["desk"].mode == "organize"  # noqa: SLF001


@pytest.mark.asyncio
async def test_hot_attach_keeps_live_only_abs_mount(tmp_path):
    """Push can land before sidecar add_grant; do not drop the new alias."""
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.external_mounts import ExternalMount

    extra = str(tmp_path / "extra")
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="conv:x",
        location="local",
    )
    backend.attach_external_mounts(
        {
            "extra": ExternalMount(
                alias="extra",
                root_id="r-new",
                label="新目录",
                abs_path=extra,
                mode="readonly",
            )
        }
    )
    await grant_store.add_grant(
        "conv-keep", root_id="r-old", label="旧", alias_hint="old"
    )
    mounts = await attach_grants_to_backend(backend, "conv-keep")
    assert "old" in mounts
    assert mounts["extra"].abs_path == extra
    assert backend._mounts["extra"].abs_path == extra  # noqa: SLF001


def _folders_ticket():
    from agentcore.folders.credentials import FoldersCredentials

    return FoldersCredentials(
        api_key="tok", base_url="https://api.example.com/v1/folders"
    )


def _spy_grant_store(monkeypatch):
    add = AsyncMock(side_effect=AssertionError("grant_store.add_grant"))
    as_dict = AsyncMock(side_effect=AssertionError("grant_store.grants_as_dict"))
    monkeypatch.setattr(grant_store, "add_grant", add)
    monkeypatch.setattr(grant_store, "grants_as_dict", as_dict)
    return add, as_dict


def _local_backend(tmp_path, *, label: str = "W"):
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox

    return ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label=label,
        location="local",
    )


def _mount_channel(value: dict):
    channel = MagicMock()
    channel.sink = MagicMock()
    channel.registry = MagicMock()
    channel.request_external_mount = AsyncMock(return_value=value)
    return channel


@pytest.mark.asyncio
async def test_sidecar_workspace_root_abs_is_not_minted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    channel = MagicMock()
    channel.request_external_mount = AsyncMock()
    got = await prepare_tool_path(
        str(tmp_path),
        _ctx(
            desktop_channel=channel,
            backend=_local_backend(tmp_path),
            conversation_id="conv-w",
        ),
        as_directory=True,
    )
    assert got == "."
    channel.request_external_mount.assert_not_called()


@pytest.mark.asyncio
async def test_sidecar_workspace_child_abs_is_not_minted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    channel = MagicMock()
    channel.request_external_mount = AsyncMock()
    child = tmp_path / "src" / "a.md"
    child.parent.mkdir()
    child.write_text("x", encoding="utf-8")
    got = await prepare_tool_path(
        str(child),
        _ctx(
            desktop_channel=channel,
            backend=_local_backend(tmp_path),
            conversation_id="conv-w",
        ),
    )
    assert got == "src/a.md"
    channel.request_external_mount.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_process_does_not_rewrite_abs_under_root(tmp_path, monkeypatch):
    """Cloud API may report location=local; discriminator is sidecar process."""
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: False
    )
    channel = _mount_channel(
        {
            "root_id": "root-1",
            "alias": "w",
            "label": "W",
            "namespace": "external/w",
        }
    )
    got = await prepare_tool_path(
        str(tmp_path),
        _ctx(
            desktop_channel=channel,
            backend=_local_backend(tmp_path),
            conversation_id="conv-cloud-local",
        ),
        as_directory=True,
    )
    assert got == "external/w"
    channel.request_external_mount.assert_awaited_once()
    grants = await grant_store.list_grants("conv-cloud-local")
    assert len(grants) == 1


@pytest.mark.asyncio
async def test_ticketed_sidecar_mint_skips_grant_store(tmp_path, monkeypatch):
    from agentcore.folders.credentials import folders_credentials_scope
    from agentcore.workspace.external_mounts import ExternalMount

    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    add, as_dict = _spy_grant_store(monkeypatch)
    outside = tmp_path.parent / f"{tmp_path.name}-desk"
    outside.mkdir()
    abs_dir = str(outside)
    backend = _local_backend(tmp_path)
    backend.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk",
                root_id="root-1",
                label="桌面",
                abs_path=abs_dir,
                mode="readonly",
            )
        }
    )
    channel = _mount_channel(
        {
            "root_id": "root-1",
            "alias": "desk",
            "label": "桌面",
            "namespace": "external/desk",
        }
    )
    with folders_credentials_scope(_folders_ticket()):
        got = await prepare_tool_path(
            str(outside),
            _ctx(
                desktop_channel=channel,
                backend=backend,
                conversation_id="conv-tix",
            ),
            as_directory=True,
        )
    assert got == "external/desk"
    add.assert_not_called()
    as_dict.assert_not_called()


@pytest.mark.asyncio
async def test_ticketed_sidecar_attach_skips_grant_store(tmp_path, monkeypatch):
    from agentcore.folders.credentials import folders_credentials_scope
    from agentcore.workspace.external_mounts import ExternalMount

    add, as_dict = _spy_grant_store(monkeypatch)
    abs_dir = str(tmp_path / "desk")
    backend = _local_backend(tmp_path)
    backend.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk",
                root_id="r1",
                label="桌面",
                abs_path=abs_dir,
                mode="readonly",
            )
        }
    )
    with folders_credentials_scope(_folders_ticket()):
        mounts = await attach_grants_to_backend(backend, "conv-tix-attach")
    assert mounts["desk"].abs_path == abs_dir
    add.assert_not_called()
    as_dict.assert_not_called()


@pytest.mark.asyncio
async def test_unticketed_sidecar_host_path_still_uses_grant_store(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.core.is_sidecar_process", lambda: True
    )
    outside = tmp_path.parent / f"{tmp_path.name}-host"
    outside.mkdir()
    channel = _mount_channel(
        {
            "root_id": "root-1",
            "alias": "host",
            "label": "旁路",
            "namespace": "external/host",
        }
    )
    got = await prepare_tool_path(
        str(outside),
        _ctx(
            desktop_channel=channel,
            backend=_local_backend(tmp_path),
            conversation_id="conv-old-sidecar",
        ),
        as_directory=True,
    )
    assert got == "external/host"
    grants = await grant_store.list_grants("conv-old-sidecar")
    assert len(grants) == 1


@pytest.mark.asyncio
async def test_ticketed_sidecar_upgrade_uses_live_not_grant_store(
    tmp_path, monkeypatch
):
    from agentcore.folders.credentials import folders_credentials_scope
    from agentcore.workspace.external_mounts import ExternalMount

    add, as_dict = _spy_grant_store(monkeypatch)
    abs_dir = str(tmp_path / "desk")
    backend = _local_backend(tmp_path)
    backend.attach_external_mounts(
        {
            "desk": ExternalMount(
                alias="desk",
                root_id="root-1",
                label="桌面",
                abs_path=abs_dir,
                mode="readonly",
            )
        }
    )
    channel = _mount_channel(
        {
            "root_id": "root-1",
            "alias": "desk",
            "label": "桌面",
            "namespace": "external/desk",
        }
    )
    with folders_credentials_scope(_folders_ticket()):
        got = await prepare_tool_path(
            "external/desk/out.md",
            _ctx(
                desktop_channel=channel,
                backend=backend,
                conversation_id="conv-tix-up",
            ),
            grant_mode="organize",
        )
    assert got == "external/desk/out.md"
    channel.request_external_mount.assert_awaited_once()
    add.assert_not_called()
    as_dict.assert_not_called()


@pytest.mark.asyncio
async def test_ticketed_sidecar_upgrade_without_live_skips_store(monkeypatch):
    from agentcore.folders.credentials import folders_credentials_scope

    add, as_dict = _spy_grant_store(monkeypatch)
    backend = MagicMock()
    backend.location = "local"
    backend.root_label = "W"
    backend._mounts = {}
    channel = MagicMock()
    channel.request_external_mount = AsyncMock()
    with folders_credentials_scope(_folders_ticket()):
        got = await prepare_tool_path(
            "external/desk/out.md",
            _ctx(
                desktop_channel=channel,
                backend=backend,
                conversation_id="conv-tix-miss",
            ),
            grant_mode="organize",
        )
    assert got == "external/desk/out.md"
    channel.request_external_mount.assert_not_called()
    add.assert_not_called()
    as_dict.assert_not_called()


@pytest.mark.asyncio
async def test_hot_attach_empty_store_keeps_live_abs(tmp_path):
    from agentcore.workspace.external_mounts import ExternalMount

    extra = str(tmp_path / "extra")
    backend = _local_backend(tmp_path)
    backend.attach_external_mounts(
        {
            "extra": ExternalMount(
                alias="extra",
                root_id="r-new",
                label="新目录",
                abs_path=extra,
                mode="readonly",
            )
        }
    )
    mounts = await attach_grants_to_backend(backend, "conv-empty-store")
    assert mounts["extra"].abs_path == extra
    assert backend._mounts["extra"].abs_path == extra  # noqa: SLF001

