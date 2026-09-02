"""Empty-desk project-shell rewrite (write / read / mkdir / artifacts / claims)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agentcore.runtime.runs.types import Deliverable
from agentcore.tools.builtin.file_ops import (
    FileBatchTool,
    FileCopyTool,
    FileDeleteTool,
    FileListTool,
    FileMoveTool,
    FileReadTool,
    FileWriteTool,
    MkdirTool,
)
from agentcore.tools.protocol import ToolContext, fork_workspace_slot
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace._paths import sanitize_write_relpath
from agentcore.workspace.project_shell import (
    CONVENTION_PROJECT_DIRS,
    rewrite_deliverable_shell,
    rewrite_project_shell_relpath,
)
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.stage_dirs import AGENTCORE_ROOT


def _empty_ctx(workspace: Path, *, agent_id: str = "ceo") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id=agent_id,
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def test_sanitize_write_relpath_does_not_strip_shell():
    assert sanitize_write_relpath("court-game/x") == "court-game/x"
    assert "site" in CONVENTION_PROJECT_DIRS
    assert "app" in CONVENTION_PROJECT_DIRS
    assert AGENTCORE_ROOT in CONVENTION_PROJECT_DIRS


async def test_empty_desk_write_strips_shell(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    result = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "hello"}, ctx
    )
    assert result.success is True
    assert (tmp_path / "x").read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "court-game").exists()
    assert ctx.project_shell.stripped_slug == "court-game"


async def test_same_turn_second_write_and_read_still_strip(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    first = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "one"}, ctx
    )
    assert first.success is True
    second = await FileWriteTool().execute(
        {"path": "court-game/y", "content": "two"}, ctx
    )
    assert second.success is True
    assert (tmp_path / "y").read_text(encoding="utf-8") == "two"
    assert not (tmp_path / "court-game").exists()

    read = await FileReadTool().execute({"path": "court-game/y"}, ctx)
    assert read.success is True
    assert "two" in read.output


async def test_independent_mkdir_uses_same_rewrite(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    made = await MkdirTool().execute({"path": "court-game/src"}, ctx)
    assert made.success is True
    assert (tmp_path / "src").is_dir()
    assert not (tmp_path / "court-game").exists()
    assert ctx.project_shell.stripped_slug == "court-game"

    write = await FileWriteTool().execute(
        {"path": "court-game/src/main.py", "content": "print(1)\n"}, ctx
    )
    assert write.success is True
    assert (tmp_path / "src" / "main.py").read_text(encoding="utf-8") == "print(1)\n"


async def test_desk_with_user_structure_does_not_strip(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "keep.md").write_text("keep\n", encoding="utf-8")
    ctx = _empty_ctx(tmp_path)
    result = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "nested"}, ctx
    )
    assert result.success is True
    assert (tmp_path / "court-game" / "x").read_text(encoding="utf-8") == "nested"
    assert ctx.project_shell.stripped_slug is None


async def test_agentcore_and_attachments_do_not_count_as_structure(tmp_path: Path):
    (tmp_path / AGENTCORE_ROOT / "文档" / "工作稿").mkdir(parents=True)
    (tmp_path / AGENTCORE_ROOT / "文档" / "工作稿" / "a.md").write_text(
        "draft\n", encoding="utf-8"
    )
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "scan.pdf").write_text("bin", encoding="utf-8")
    ctx = _empty_ctx(tmp_path)
    result = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "ok"}, ctx
    )
    assert result.success is True
    assert (tmp_path / "x").read_text(encoding="utf-8") == "ok"
    assert not (tmp_path / "court-game").exists()


async def test_convention_dirs_are_not_stripped(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    for rel, body in (
        ("site/index.html", "<html></html>"),
        ("app/main.py", "print(1)\n"),
        ("src/lib.ts", "export {}\n"),
    ):
        result = await FileWriteTool().execute({"path": rel, "content": body}, ctx)
        assert result.success is True
        assert tmp_path.joinpath(*rel.split("/")).read_text(encoding="utf-8") == body
    assert ctx.project_shell.stripped_slug is None


async def test_slug_shared_across_replace_and_workspace_fork(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    worker = replace(ctx, run_id="w1", agent_id="worker")
    first = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "ceo"}, ctx
    )
    assert first.success is True
    assert worker.project_shell is ctx.project_shell
    assert worker.project_shell.stripped_slug == "court-game"

    alien = ServerWorkspace(root=tmp_path / "alien", sandbox=SubprocessSandbox())
    (tmp_path / "alien").mkdir()
    forked = replace(
        ctx,
        _workspace=fork_workspace_slot(alien, material_paths=frozenset()),
        run_id="w2",
    )
    assert forked.project_shell is ctx.project_shell
    assert forked.project_shell.stripped_slug == "court-game"
    second = await FileWriteTool().execute(
        {"path": "court-game/y", "content": "worker"}, worker
    )
    assert second.success is True
    assert (tmp_path / "y").read_text(encoding="utf-8") == "worker"


async def test_artifacts_and_write_claim_use_stripped_path(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    deliverable = Deliverable(form="files", artifacts=["court-game/x", "court-game/y"])
    await rewrite_deliverable_shell(deliverable, ctx)
    assert deliverable.artifacts == ["x", "y"]
    assert ctx.project_shell.stripped_slug == "court-game"

    from agentcore.workspace.write_claims import WriteCoordinator

    coord = WriteCoordinator()
    owner = coord.declare("x", "w1", frozenset())
    assert owner is None
    write = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "claimed"},
        replace(ctx, run_id="w1", write_coordinator=coord),
    )
    assert write.success is True
    assert (tmp_path / "x").read_text(encoding="utf-8") == "claimed"


async def test_rewrite_function_register_false_does_not_stamp(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    actual, note = await rewrite_project_shell_relpath(
        "court-game/x", ctx, register=False
    )
    assert actual == "court-game/x"
    assert note == ""
    assert ctx.project_shell.stripped_slug is None


async def test_child_folder_name_is_not_stripped(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    ctx._workspace.child_folder_names = frozenset({"课题A"})
    result = await FileWriteTool().execute(
        {"path": "课题A/notes.md", "content": "nested desk\n"}, ctx
    )
    assert result.success is True
    assert (tmp_path / "课题A" / "notes.md").read_text(encoding="utf-8") == "nested desk\n"
    assert not (tmp_path / "notes.md").exists()
    assert ctx.project_shell.stripped_slug is None


async def test_child_folder_names_do_not_block_other_shell_strip(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    ctx._workspace.child_folder_names = frozenset({"课题A"})
    other = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "shell\n"}, ctx
    )
    assert other.success is True
    assert (tmp_path / "x").read_text(encoding="utf-8") == "shell\n"
    assert not (tmp_path / "court-game").exists()
    assert ctx.project_shell.stripped_slug == "court-game"


async def test_file_list_applies_registered_slug(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    write = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "hello"}, ctx
    )
    assert write.success is True
    listed = await FileListTool().execute({"directory": "court-game"}, ctx)
    assert listed.success is True
    assert "x" in listed.output
    assert "不是目录" not in listed.output


async def test_mkdir_bare_slug_then_write_strips(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    made = await MkdirTool().execute({"path": "court-game"}, ctx)
    assert made.success is True
    assert not (tmp_path / "court-game").exists()
    assert ctx.project_shell.stripped_slug == "court-game"
    assert "空桌" not in made.output
    assert "工程壳" not in made.output
    assert "无需创建" not in made.output
    assert "`court-game/` 即工作区根" in made.output

    write = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "hello"}, ctx
    )
    assert write.success is True
    assert (tmp_path / "x").read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "court-game").exists()
    assert "空桌" not in write.output
    assert "工程壳" not in write.output


async def test_bare_file_write_does_not_register_filename_as_slug(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    result = await FileWriteTool().execute(
        {"path": "README.md", "content": "hi\n"}, ctx
    )
    assert result.success is True
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "hi\n"
    assert ctx.project_shell.stripped_slug is None


async def test_hallucinated_delete_does_not_block_later_shell_strip(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    gone = await FileDeleteTool().execute({"path": "ghost-proj/foo.txt"}, ctx)
    assert gone.success is False
    assert ctx.project_shell.stripped_slug is None

    batch = await FileBatchTool().execute(
        {"operations": [{"op": "delete", "path": "other-ghost/bar.txt"}]},
        ctx,
    )
    assert batch.success is True
    assert ctx.project_shell.stripped_slug is None

    write = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "ok"}, ctx
    )
    assert write.success is True
    assert (tmp_path / "x").read_text(encoding="utf-8") == "ok"
    assert not (tmp_path / "court-game").exists()
    assert ctx.project_shell.stripped_slug == "court-game"


async def test_write_child_folder_then_unknown_shell_does_not_strip(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    ctx._workspace.child_folder_names = frozenset({"课题A"})
    first = await FileWriteTool().execute(
        {"path": "课题A/notes.md", "content": "nested\n"}, ctx
    )
    assert first.success is True
    assert (tmp_path / "课题A" / "notes.md").read_text(encoding="utf-8") == "nested\n"

    second = await FileWriteTool().execute(
        {"path": "court-game/x", "content": "shell\n"}, ctx
    )
    assert second.success is True
    assert (tmp_path / "court-game" / "x").read_text(encoding="utf-8") == "shell\n"
    assert not (tmp_path / "x").exists()
    assert ctx.project_shell.stripped_slug is None


async def test_empty_desk_first_move_and_copy_share_shell(tmp_path: Path):
    move_ctx = _empty_ctx(tmp_path)
    moved = await FileMoveTool().execute(
        {"source": "court-game/a.txt", "destination": "court-game/b.txt"},
        move_ctx,
    )
    assert moved.success is False
    assert "源路径不存在：a.txt" in moved.error
    assert "court-game/a.txt" not in moved.error
    assert move_ctx.project_shell.stripped_slug == "court-game"
    assert not (tmp_path / "court-game").exists()

    copy_ctx = _empty_ctx(tmp_path)
    copied = await FileCopyTool().execute(
        {"source": "court-game/a.txt", "destination": "court-game/b.txt"},
        copy_ctx,
    )
    assert copied.success is False
    assert "源路径不存在：a.txt" in copied.error
    assert "court-game/a.txt" not in copied.error
    assert copy_ctx.project_shell.stripped_slug == "court-game"
    assert not (tmp_path / "court-game").exists()

    batch_ctx = _empty_ctx(tmp_path)
    batched = await FileBatchTool().execute(
        {
            "operations": [
                {
                    "op": "copy",
                    "source": "court-game/a.txt",
                    "destination": "court-game/b.txt",
                }
            ]
        },
        batch_ctx,
    )
    assert batched.success is False
    assert "a.txt → b.txt" in batched.output
    assert "court-game/a.txt" not in batched.output
    assert batch_ctx.project_shell.stripped_slug == "court-game"
    assert not (tmp_path / "court-game").exists()


async def test_rewrite_does_not_treat_dotdot_as_shell(tmp_path: Path):
    ctx = _empty_ctx(tmp_path)
    actual, note = await rewrite_project_shell_relpath(
        "../secret.txt", ctx, register=True
    )
    assert actual == "../secret.txt"
    assert note == ""
    assert ctx.project_shell.stripped_slug is None

    nested, nested_note = await rewrite_project_shell_relpath(
        "court-game/../secret.txt", ctx, register=True
    )
    assert nested == "court-game/../secret.txt"
    assert nested_note == ""
    assert ctx.project_shell.stripped_slug is None

