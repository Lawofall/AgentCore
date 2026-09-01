"""CEO read-only cross-folder tools: list_folder_dir / read_folder_file."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.delegate.target_desktop import (
    TargetDesktopError,
    TargetFolderBinding,
)
from agentcore.tools.builtin.folder_fs import (
    ListFolderDirTool,
    ReadFolderFileTool,
)
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.registration import (
    AUDIENCE_CEO,
    CeoWire,
    ToolSurface,
    declared_tool_name,
    declared_tools,
    tool_registration,
)
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.locate import LocalBinding
from agentcore.workspace.server import ServerWorkspace


def _birth_backend(tmp_path: Path) -> ServerWorkspace:
    birth = tmp_path / "birth"
    birth.mkdir()
    (birth / "birth_only.txt").write_text("birth", encoding="utf-8")
    return ServerWorkspace(root=birth, sandbox=SubprocessSandbox())


def _target_backend(tmp_path: Path) -> ServerWorkspace:
    target = tmp_path / "target"
    target.mkdir()
    (target / "readme.md").write_text("hello from target\nline2\n", encoding="utf-8")
    (target / "src").mkdir()
    (target / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    return ServerWorkspace(root=target, sandbox=SubprocessSandbox())


def _ctx(tmp_path: Path, *, user_id: str = "u1") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=_birth_backend(tmp_path),
        user_id=user_id,
        conversation_id="conv-birth",
    )


def _local_binding() -> TargetFolderBinding:
    return TargetFolderBinding(
        folder_id="folder_local",
        name="LocalFolder",
        local_binding=LocalBinding(
            root_id="root-1",
            root_label="LocalFolder",
            subpath="",
        ),
    )


# --- schema / registration --------------------------------------------------


def test_list_folder_dir_schema_and_registration():
    tool = ListFolderDirTool()
    assert tool.schema.name == "list_folder_dir"
    assert tool.schema.category is ToolCategory.ORCHESTRATION
    assert tool.schema.approval is ToolApproval.NEVER
    props = tool.schema.parameters["properties"]
    assert "folder_id" in props
    assert "directory" in props
    assert "pattern" not in props
    assert "recursive" not in props
    assert "max_depth" not in props
    assert "target_folder_id" not in props
    assert tool.schema.parameters["required"] == ["folder_id"]
    # Nesting HOW lives in consult(team_cross_folder); schema is one-line function.
    assert "列出" in tool.schema.description
    assert "HOW→consult(team_cross_folder)" in tool.schema.description
    assert "轻量认桌" not in tool.schema.description
    from agentcore.runtime.skills import build_system_skill_registry

    cross = build_system_skill_registry().get("team_cross_folder")
    assert cross is not None
    assert "子文件夹" in cross.body
    assert "轻量认桌" in cross.body
    assert "file_list" not in tool.schema.description
    assert "list_folders" not in tool.schema.description
    folder_id_desc = props["folder_id"]["description"]
    assert "list_folders" in folder_id_desc
    assert "resolve_folder" in folder_id_desc
    assert "create_folder" in folder_id_desc
    assert "文件夹清单" not in folder_id_desc
    reg = tool_registration(ListFolderDirTool)
    assert reg.surface is ToolSurface.CEO_ORCHESTRATION
    assert reg.audience == (AUDIENCE_CEO,)
    assert reg.ceo_wire is CeoWire.ALWAYS


@pytest.mark.asyncio
async def test_list_folder_dir_leftover_pattern_does_not_point_at_glob(tmp_path: Path):
    tool = ListFolderDirTool()
    result = await tool.execute(
        {"folder_id": "folder_local", "directory": ".", "pattern": "*.py"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.contract_failure is True
    text = result.error or result.output or ""
    assert "delegate" in text
    assert "出生桌" in text
    assert "请用 glob" not in text


def test_read_folder_file_schema_and_registration():
    tool = ReadFolderFileTool()
    assert tool.schema.name == "read_folder_file"
    assert tool.schema.category is ToolCategory.ORCHESTRATION
    assert tool.schema.approval is ToolApproval.NEVER
    props = tool.schema.parameters["properties"]
    assert "folder_id" in props
    assert "path" in props
    assert "offset" in props
    assert "limit" in props
    assert "target_folder_id" not in props
    assert set(tool.schema.parameters["required"]) == {"folder_id", "path"}
    assert "读取" in tool.schema.description
    assert "HOW→consult(team_cross_folder)" in tool.schema.description
    assert "轻量认桌" not in tool.schema.description
    assert "抽样" not in tool.schema.description
    assert "读到文件末尾" not in tool.schema.description
    folder_id_desc = props["folder_id"]["description"]
    assert "list_folders" in folder_id_desc
    assert "resolve_folder" in folder_id_desc
    assert "create_folder" in folder_id_desc
    assert "文件夹清单" not in folder_id_desc
    limit_schema = props["limit"]
    assert limit_schema["maximum"] == 500
    assert "500" in limit_schema["description"]
    assert "抽样" in limit_schema["description"]
    assert "读到文件末尾" not in limit_schema["description"]
    reg = tool_registration(ReadFolderFileTool)
    assert reg.surface is ToolSurface.CEO_ORCHESTRATION
    assert reg.audience == (AUDIENCE_CEO,)
    assert reg.ceo_wire is CeoWire.ALWAYS


def test_declared_roster_includes_folder_fs_tools():
    names = {declared_tool_name(cls) for cls in declared_tools()}
    assert "list_folder_dir" in names
    assert "read_folder_file" in names


def test_no_write_folder_fs_variants_on_roster():
    """只读跨文件夹：禁止写/改/删变体进入名册。"""
    names = {declared_tool_name(cls) for cls in declared_tools()}
    forbidden = {
        "write_folder_file",
        "append_folder_file",
        "delete_folder_file",
        "str_replace_folder_file",
        "mkdir_folder_dir",
        "file_write_folder",
    }
    assert names.isdisjoint(forbidden)


# --- execute ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_and_read_registered_local_folder(tmp_path: Path):
    """CEO can list/read an owned local Folder without touching birth desk."""
    birth_backend = _birth_backend(tmp_path)
    target_backend = _target_backend(tmp_path)
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=birth_backend,
        user_id="u1",
        conversation_id="conv-birth",
        desktop_channel=SimpleNamespace(user_id="u1"),
    )
    binding = _local_binding()

    with (
        patch(
            "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ) as load_mock,
        patch(
            "agentcore.tools.builtin.folder_fs.build_target_backend",
            return_value=target_backend,
        ) as build_mock,
        patch(
            "agentcore.tools.builtin.folder_fs.workspace_channel_for_tools",
            return_value=None,
        ),
    ):
        listed = await ListFolderDirTool().execute(
            {"folder_id": "folder_local", "directory": "."},
            ctx,
        )
        assert listed.success is True
        assert "readme.md" in listed.output
        assert "src" in listed.output

        read = await ReadFolderFileTool().execute(
            {"folder_id": "folder_local", "path": "readme.md"},
            ctx,
        )
        assert read.success is True
        assert "hello from target" in read.output

    load_mock.assert_awaited()
    assert build_mock.call_count >= 1
    # Session birth backend untouched (no mount rewrite).
    assert ctx.backend is birth_backend
    assert (tmp_path / "birth" / "birth_only.txt").read_text(encoding="utf-8") == "birth"
    # Cross-folder did not invent files on birth desk.
    assert not (tmp_path / "birth" / "readme.md").exists()


@pytest.mark.asyncio
async def test_read_folder_file_omitted_limit_stays_sample_window(tmp_path: Path):
    """Omit limit → inject 500 before FileReadTool; do not inherit worker 2000-line cap."""
    target_backend = _target_backend(tmp_path)
    total = 800
    (tmp_path / "target" / "long.txt").write_text(
        "".join(f"line-{i}\n" for i in range(1, total + 1)),
        encoding="utf-8",
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=_birth_backend(tmp_path),
        user_id="u1",
        conversation_id="conv-birth",
        desktop_channel=SimpleNamespace(user_id="u1"),
    )
    with (
        patch(
            "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
            new=AsyncMock(return_value=_local_binding()),
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.build_target_backend",
            return_value=target_backend,
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.workspace_channel_for_tools",
            return_value=None,
        ),
        patch("agentcore.tools.builtin.file_ops.read._DEFAULT_READ_LINES", 2000),
    ):
        read = await ReadFolderFileTool().execute(
            {"folder_id": "folder_local", "path": "long.txt"},
            ctx,
        )
    assert read.success is True
    assert "第 1–500 行" in read.output
    assert f"共 {total} 行" in read.output
    assert "line-500" in read.output
    assert "line-501" not in read.output


@pytest.mark.asyncio
async def test_read_folder_file_omitted_limit_injects_before_file_read(tmp_path: Path):
    """Delegated FileReadTool args must include limit=500 when the model omits it."""
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=_birth_backend(tmp_path),
        user_id="u1",
        conversation_id="conv-birth",
        desktop_channel=SimpleNamespace(user_id="u1"),
    )
    with (
        patch(
            "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
            new=AsyncMock(return_value=_local_binding()),
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.build_target_backend",
            return_value=_target_backend(tmp_path),
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.workspace_channel_for_tools",
            return_value=None,
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.FileReadTool.execute",
            new=AsyncMock(
                return_value=ToolResult(tool_call_id="", success=True, output="ok")
            ),
        ) as execute_mock,
    ):
        result = await ReadFolderFileTool().execute(
            {"folder_id": "folder_local", "path": "readme.md"},
            ctx,
        )
    assert result.success is True
    forwarded = execute_mock.await_args.args[0]
    assert forwarded["limit"] == 500
    assert forwarded["path"] == "readme.md"
    assert "folder_id" not in forwarded


@pytest.mark.asyncio
async def test_read_folder_file_explicit_limit_respected(tmp_path: Path):
    target_backend = _target_backend(tmp_path)
    (tmp_path / "target" / "long.txt").write_text(
        "".join(f"line-{i}\n" for i in range(1, 80)),
        encoding="utf-8",
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=_birth_backend(tmp_path),
        user_id="u1",
        conversation_id="conv-birth",
        desktop_channel=SimpleNamespace(user_id="u1"),
    )
    with (
        patch(
            "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
            new=AsyncMock(return_value=_local_binding()),
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.build_target_backend",
            return_value=target_backend,
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.workspace_channel_for_tools",
            return_value=None,
        ),
    ):
        read = await ReadFolderFileTool().execute(
            {"folder_id": "folder_local", "path": "long.txt", "limit": 20},
            ctx,
        )
    assert read.success is True
    assert "第 1–20 行" in read.output
    assert "line-20" in read.output
    assert "line-21" not in read.output


@pytest.mark.asyncio
async def test_cloud_target_backend_gets_folder_rel_path(tmp_path: Path):
    """Nested cloud folders: the tree path rides the binding into the backend."""
    ctx = _ctx(tmp_path)
    binding = TargetFolderBinding(
        folder_id="f-nested",
        name="图标",
        local_binding=None,
        rel_path="设计/图标",
    )
    with (
        patch(
            "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.build_target_backend",
            return_value=_target_backend(tmp_path),
        ) as build_mock,
        patch(
            "agentcore.tools.builtin.folder_fs.workspace_channel_for_tools",
            return_value=None,
        ),
    ):
        result = await ListFolderDirTool().execute({"folder_id": "f-nested"}, ctx)
    assert result.success is True
    assert build_mock.call_args.kwargs["folder_rel_path"] == "设计/图标"


@pytest.mark.asyncio
async def test_denied_folder_fails(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with patch(
        "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
        new=AsyncMock(return_value=None),
    ):
        listed = await ListFolderDirTool().execute(
            {"folder_id": "nope", "directory": "."},
            ctx,
        )
        read = await ReadFolderFileTool().execute(
            {"folder_id": "nope", "path": "x.txt"},
            ctx,
        )
    assert listed.success is False
    assert "无权" in listed.output or "不存在" in listed.output
    assert listed.error == "folder_denied"
    assert read.success is False
    assert "无权" in read.output or "不存在" in read.output


@pytest.mark.asyncio
async def test_target_desktop_error_surfaces(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with patch(
        "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
        new=AsyncMock(side_effect=TargetDesktopError("无法绑定目标文件夹。数据库不可用")),
    ):
        result = await ListFolderDirTool().execute(
            {"folder_id": "f1"},
            ctx,
        )
    assert result.success is False
    assert "无法绑定目标文件夹" in result.output
    assert result.error == "target_desktop_error"


@pytest.mark.asyncio
@pytest.mark.real_fulfill_dispatch
async def test_local_folder_without_fulfiller_fails(tmp_path: Path):
    """Local binding with no online fulfiller fails immediately (honest reject)."""
    ctx = _ctx(tmp_path)  # no desktop_channel / workspace_channel
    with patch(
        "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
        new=AsyncMock(return_value=_local_binding()),
    ):
        result = await ReadFolderFileTool().execute(
            {"folder_id": "folder_local", "path": "readme.md"},
            ctx,
        )
    assert result.success is False
    assert "无履约方" in (result.output or "") or "无履约方" in (result.error or "")


@pytest.mark.asyncio
async def test_missing_folder_id(tmp_path: Path):
    ctx = _ctx(tmp_path)
    result = await ListFolderDirTool().execute({"directory": "."}, ctx)
    assert result.success is False
    assert result.error == "missing folder_id"
    assert "list_folders" in result.output
    assert "resolve_folder" in result.output
    assert "文件夹清单" not in result.output


@pytest.mark.asyncio
async def test_does_not_call_apply_target_desktop(tmp_path: Path):
    """Read-only cross-folder must not rewrite target-desk memory via apply_*."""
    target_backend = _target_backend(tmp_path)
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=_birth_backend(tmp_path),
        user_id="u1",
        conversation_id="conv-birth",
        desktop_channel=SimpleNamespace(user_id="u1"),
    )
    with (
        patch(
            "agentcore.tools.builtin.folder_fs.load_target_folder_binding",
            new=AsyncMock(return_value=_local_binding()),
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.build_target_backend",
            return_value=target_backend,
        ),
        patch(
            "agentcore.tools.builtin.folder_fs.workspace_channel_for_tools",
            return_value=None,
        ),
        patch(
            "agentcore.runtime.delegate.target_desktop.apply_target_desktop",
            new=AsyncMock(),
        ) as apply_mock,
    ):
        await ReadFolderFileTool().execute(
            {"folder_id": "folder_local", "path": "readme.md"},
            ctx,
        )
    apply_mock.assert_not_called()


def test_generic_file_tools_have_no_folder_id_param():
    from agentcore.tools.builtin.file_ops import FileListTool, FileReadTool

    for tool in (FileListTool(), FileReadTool()):
        props = tool.schema.parameters.get("properties") or {}
        assert "folder_id" not in props
        assert "target_folder_id" not in props
