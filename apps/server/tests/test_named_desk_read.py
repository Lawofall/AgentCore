"""CEO ``read`` one-shot bind of a this-turn named neighbor Folder."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.runtime.graph.desk.target_desktop_binding import TargetFolderBinding
from agentcore.tools.builtin.file_ops.named_desk_read import (
    named_desk_folder_for_read,
    stamp_named_file_pins,
)
from agentcore.tools.builtin.file_ops.read import FileListTool, FileReadTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="ceo",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
    )


def _binding(folder_id: str, name: str = "邻桌") -> TargetFolderBinding:
    return TargetFolderBinding(
        folder_id=folder_id, name=name, local_binding=None, rel_path=None
    )


@pytest.fixture
def desks(tmp_path: Path) -> tuple[Path, Path, ToolContext]:
    birth = tmp_path / "birth"
    other = tmp_path / "other"
    birth.mkdir()
    other.mkdir()
    ctx = _ctx(birth)
    ctx.ownership_desk_id = "desk-a"
    return birth, other, ctx


def test_named_desk_folder_unique_hint_not_sitting(desks: tuple[Path, Path, ToolContext]):
    _birth, _other, ctx = desks
    ctx.turn_target_desk.note_folder("desk-b")
    assert named_desk_folder_for_read(ctx, "secret.md") == "desk-b"


def test_named_desk_folder_skips_same_desk(desks: tuple[Path, Path, ToolContext]):
    _birth, _other, ctx = desks
    ctx.turn_target_desk.note_folder("desk-a")
    assert named_desk_folder_for_read(ctx, "secret.md") is None


def test_named_desk_folder_skips_host_abs(desks: tuple[Path, Path, ToolContext]):
    _birth, _other, ctx = desks
    ctx.turn_target_desk.note_folder("desk-b")
    assert named_desk_folder_for_read(ctx, r"C:\Users\me\Projects\foo.md") is None


def test_named_desk_folder_attachment_pin_wins(desks: tuple[Path, Path, ToolContext]):
    _birth, _other, ctx = desks
    ctx.named_file_pins.add("desk-b", "docs/a.md")
    assert named_desk_folder_for_read(ctx, "docs/a.md") == "desk-b"
    assert named_desk_folder_for_read(ctx, "docs/b.md") is None


def test_named_desk_folder_ambiguous_pin_drops(desks: tuple[Path, Path, ToolContext]):
    _birth, _other, ctx = desks
    ctx.named_file_pins.add("desk-b", "docs/a.md")
    ctx.named_file_pins.add("desk-c", "docs/a.md")
    assert named_desk_folder_for_read(ctx, "docs/a.md") is None


def test_stamp_named_file_pins_skips_sitting_and_attachments_copy(
    desks: tuple[Path, Path, ToolContext],
):
    _birth, _other, ctx = desks
    stamp_named_file_pins(
        ctx,
        [
            {
                "workspace_path": "docs/a.md",
                "source_folder_id": "desk-b",
            },
            {
                "workspace_path": "attachments/x.md",
                "source_folder_id": "desk-b",
            },
            {
                "workspace_path": "same.md",
                "source_folder_id": "desk-a",
            },
        ],
        sitting_folder_id="desk-a",
    )
    assert ctx.named_file_pins.folder_for("docs/a.md") == "desk-b"
    assert ctx.named_file_pins.folder_for("attachments/x.md") is None
    assert ctx.named_file_pins.folder_for("same.md") is None


async def test_file_read_retries_unique_other_desk(desks: tuple[Path, Path, ToolContext]):
    _birth, other, ctx = desks
    (other / "secret.md").write_text("from-other\n", encoding="utf-8")
    ctx.turn_target_desk.note_folder("desk-b")
    other_ws = ServerWorkspace(root=other, sandbox=SubprocessSandbox())
    with (
        patch(
            "agentcore.runtime.graph.desk.target_desktop_binding.load_target_folder_binding",
            new=AsyncMock(return_value=_binding("desk-b")),
        ) as load,
        patch(
            "agentcore.runtime.graph.desk.target_desktop_binding.build_target_backend",
            return_value=other_ws,
        ),
    ):
        result = await FileReadTool().execute({"file_path": "secret.md"}, ctx)
    assert result.success
    assert "from-other" in result.output
    assert "邻桌" in result.output
    assert "不是当前工作区" in result.output
    load.assert_awaited()


async def test_file_read_miss_without_pin_does_not_scan(
    desks: tuple[Path, Path, ToolContext],
):
    _birth, _other, ctx = desks
    with patch(
        "agentcore.runtime.graph.desk.target_desktop_binding.load_target_folder_binding",
        new=AsyncMock(),
    ) as load:
        result = await FileReadTool().execute({"file_path": "missing.md"}, ctx)
    assert result.success is False
    assert result.failure_code == "not_found"
    load.assert_not_called()


async def test_file_read_worker_does_not_inherit_pin(
    desks: tuple[Path, Path, ToolContext],
):
    _birth, other, ctx = desks
    (other / "secret.md").write_text("from-other\n", encoding="utf-8")
    ctx.turn_target_desk.note_folder("desk-b")
    ctx.named_desk_read = False
    with patch(
        "agentcore.runtime.graph.desk.target_desktop_binding.load_target_folder_binding",
        new=AsyncMock(),
    ) as load:
        result = await FileReadTool().execute({"file_path": "secret.md"}, ctx)
    assert result.success is False
    assert result.failure_code == "not_found"
    load.assert_not_called()


async def test_file_list_does_not_pin_other_desk(desks: tuple[Path, Path, ToolContext]):
    birth, other, ctx = desks
    (other / "secret.md").write_text("from-other\n", encoding="utf-8")
    ctx.turn_target_desk.note_folder("desk-b")
    with patch(
        "agentcore.runtime.graph.desk.target_desktop_binding.load_target_folder_binding",
        new=AsyncMock(),
    ) as load:
        result = await FileListTool().execute({"directory": "."}, ctx)
    assert result.success
    assert "secret.md" not in (result.output or "")
    load.assert_not_called()
    assert birth.exists()
