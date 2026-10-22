"""Working-root grant: authorization is not a path rename."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.desktop.channel import ExternalMountError
from agentcore.tools.builtin.file_ops.observe import pdf_next
from agentcore.tools.builtin.file_ops.prepare_path import prepare_tool_path
from agentcore.tools.builtin.host import HOST_TOOL_DESCRIPTION, HostTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace import grant_store
from agentcore.workspace.ensure_host_path import format_external_mount_error
from agentcore.workspace.external_mounts import ExternalMount, build_external_env
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.working_root import (
    HOST_GRANT_ABS_MSG,
    host_command_hits_grant_abs,
    internal_tool_path,
    model_facing_path,
    posix_rel,
    routes_to_working_root,
    stays_on_primary,
    working_mount,
)


@pytest.fixture(autouse=True)
def _memory_grants():
    grant_store.clear_all_for_tests()
    yield
    grant_store.clear_all_for_tests()


def _working(**kwargs) -> ExternalMount:
    return ExternalMount(
        alias=kwargs.get("alias", "bills"),
        root_id=kwargs.get("root_id", "r1"),
        label=kwargs.get("label", "对账单"),
        abs_path=kwargs.get("abs_path", r"D:\对账单"),
        mode=kwargs.get("mode", "attach_rw"),
        working_root=True,
    )


def test_stays_on_primary_keeps_attachments_and_agentcore():
    assert stays_on_primary("attachments/a.pdf")
    assert stays_on_primary("AgentCore/trash")
    assert not stays_on_primary("赵智雯/对账单.pdf")
    assert not stays_on_primary(".")


def test_routes_relatives_to_working_root():
    mounts = {"bills": _working()}
    assert routes_to_working_root("赵智雯/a.pdf", mounts)
    assert not routes_to_working_root("attachments/a.pdf", mounts)
    assert not routes_to_working_root("external/bills/a.pdf", mounts)
    assert working_mount(mounts) is mounts["bills"]


def test_model_facing_working_root_is_relative():
    mount = _working()
    assert model_facing_path(mount, "赵智雯/a.pdf") == "赵智雯/a.pdf"
    assert posix_rel("") == "."


def test_model_facing_extra_uses_abs():
    extra = ExternalMount(
        alias="desk",
        root_id="r2",
        label="桌面",
        abs_path=r"C:\Users\me\Desktop",
        mode="readonly",
        working_root=False,
    )
    got = model_facing_path(extra, "a.pdf")
    assert "external/" not in got
    assert got.endswith("a.pdf")
    assert "Desktop" in got


def test_internal_tool_path_splits_cwd_and_extra():
    assert internal_tool_path(_working(), "a.pdf") == "a.pdf"
    extra = ExternalMount(
        alias="desk",
        root_id="r2",
        label="桌面",
        abs_path=None,
        mode="readonly",
    )
    assert internal_tool_path(extra, "a.pdf") == "external/desk/a.pdf"


def test_host_command_hits_grant_abs():
    mounts = {"bills": _working(abs_path=r"D:\对账单")}
    assert host_command_hits_grant_abs(r"python -c \"open(r'D:\对账单\a.pdf')\"", mounts)
    assert not host_command_hits_grant_abs("Get-Date", mounts)


def test_grant_error_does_not_say_mount():
    text = format_external_mount_error(
        ExternalMountError("找不到该目录", reason="not_found")
    )
    assert "无法挂载" not in text
    assert "找不到" in text


def test_pdf_next_points_at_run():
    assert "run" in pdf_next(code_execute_assembled=True)
    assert "手抄" in pdf_next(code_execute_assembled=True)


@pytest.mark.asyncio
async def test_add_grant_memory_promotes_when_asked():
    first = await grant_store.add_grant(
        "c1", root_id="r1", label="对账单", working_root=True
    )
    assert first.working_root is True
    second = await grant_store.add_grant(
        "c1", root_id="r2", label="桌面", working_root=True
    )
    assert second.working_root is False
    listed = await grant_store.list_grants("c1")
    assert sum(1 for m in listed if m.working_root) == 1
    again = await grant_store.add_grant(
        "c1", root_id="r1", label="对账单（改名）", working_root=True
    )
    assert again.working_root is True
    assert again.label == "对账单（改名）"


@pytest.mark.asyncio
async def test_prepare_working_root_returns_relative(tmp_path: Path):
    channel = MagicMock()
    channel.sink = MagicMock()
    channel.registry = MagicMock()
    channel.request_external_mount = AsyncMock(
        return_value={
            "root_id": "root-1",
            "alias": "bills",
            "label": "对账单",
            "display_label": "对账单",
            "namespace": ".",
        }
    )
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="scratch",
        location="server",
    )
    dest = r"D:\对账单\a.pdf"
    await grant_store.add_grant(
        "conv-hot", root_id="root-1", label="对账单", working_root=True
    )
    got = await prepare_tool_path(
        dest,
        ToolContext.create(
            execution_id="e1",
            run_id="r1",
            agent_id="ceo",
            backend=backend,
            user_id="u1",
            conversation_id="conv-hot",
            desktop_channel=channel,
        ),
    )
    assert got == "a.pdf"
    assert "external/" not in got


@pytest.mark.asyncio
async def test_server_safe_routes_relative_to_working_abs(tmp_path: Path):
    bills = tmp_path / "bills"
    bills.mkdir()
    (bills / "a.pdf").write_bytes(b"%PDF")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    backend = ServerWorkspace(
        root=scratch,
        sandbox=SubprocessSandbox(),
        root_label="scratch",
        location="local",
    )
    backend.attach_external_mounts(
        {
            "bills": ExternalMount(
                alias="bills",
                root_id="r1",
                label="对账单",
                abs_path=str(bills),
                mode="attach_rw",
                working_root=True,
            )
        }
    )
    resolved = backend._safe("a.pdf")  # noqa: SLF001
    assert resolved == (bills / "a.pdf").resolve()
    listed = await backend.list(".", "*")
    paths = [e.path for e in listed.entries]
    assert "a.pdf" in paths
    assert not any("external/" in p for p in paths)


@pytest.mark.asyncio
async def test_host_rejects_cd_into_working_abs(tmp_path: Path):
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="scratch",
        location="local",
    )
    backend.attach_external_mounts({"bills": _working()})
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="ceo",
        backend=backend,
        user_id="u1",
        conversation_id="c1",
        desktop_channel=MagicMock(),
    )
    result = await HostTool().execute(
        {"command": r"cd /d D:\对账单 && dir"},
        ctx,
    )
    assert result.success is False
    assert HOST_GRANT_ABS_MSG in (result.error or "")
    assert HOST_GRANT_ABS_MSG in HOST_TOOL_DESCRIPTION


def test_build_external_env_skips_working_root():
    env = build_external_env(
        {
            "bills": _working(),
            "desk": ExternalMount(
                alias="desk",
                root_id="r2",
                label="桌面",
                abs_path=r"C:\Users\me\Desktop",
                mode="readonly",
            ),
        }
    )
    assert env == {"AGENTCORE_EXTERNAL_DESK": r"C:\Users\me\Desktop"}


def test_format_strips_legacy_mount_copy():
    text = format_external_mount_error(
        ExternalMountError("找不到该目录，无法挂载")
    )
    assert "无法挂载" not in text
    assert "找不到该目录" in text


@pytest.mark.asyncio
async def test_host_injects_working_cwd(tmp_path: Path):
    channel = MagicMock()
    channel.request_host = AsyncMock(
        return_value={
            "timed_out": False,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }
    )
    backend = ServerWorkspace(
        root=tmp_path,
        sandbox=SubprocessSandbox(),
        root_label="scratch",
        location="local",
    )
    backend.attach_external_mounts({"bills": _working()})
    ctx = ToolContext.create(
        execution_id="e1",
        run_id="r1",
        agent_id="ceo",
        backend=backend,
        user_id="u1",
        conversation_id="c1",
        desktop_channel=channel,
    )
    result = await HostTool().execute({"command": "Get-Date"}, ctx)
    assert result.success is True
    payload = channel.request_host.await_args.args[1]
    assert payload["cwd"] == r"D:\对账单"
    assert payload["root_id"] == "r1"
