"""Unit tests for ``archive_create`` builtin tool."""

from __future__ import annotations

import zipfile
from pathlib import Path

from agentcore.tools.builtin.archive_create import ArchiveCreateTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(workspace: Path) -> ToolContext:
    keep = workspace / "README.md"
    if not keep.exists():
        keep.write_text("desk\n", encoding="utf-8")
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=workspace, sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return {info.filename for info in zf.infolist() if not info.is_dir()}


async def test_archive_create_success(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha", encoding="utf-8")
    (src / "nested").mkdir()
    (src / "nested" / "b.md").write_text("# hi", encoding="utf-8")

    result = await ArchiveCreateTool().execute(
        {"sources": ["src"], "dest": "out/pkg.zip"},
        _ctx(tmp_path),
    )
    assert result.success is True
    zip_path = tmp_path / "out" / "pkg.zip"
    assert zip_path.is_file()
    names = _zip_names(zip_path)
    assert names == {"src/a.txt", "src/nested/b.md"}
    assert result.metadata is not None
    assert result.metadata["files_packed"] == 2
    assert "2 个文件" in result.output
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read("src/a.txt").decode("utf-8") == "alpha"


async def test_archive_create_prunes_vcs_and_deps(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.txt").write_text("ok", encoding="utf-8")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "pkg.js").write_text("nope", encoding="utf-8")
    git = src / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref", encoding="utf-8")

    result = await ArchiveCreateTool().execute(
        {"sources": ["src"], "dest": "pkg.zip"},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert _zip_names(tmp_path / "pkg.zip") == {"src/keep.txt"}


async def test_archive_create_rejects_path_escape(tmp_path: Path):
    result = await ArchiveCreateTool().execute(
        {"sources": ["../secret"], "dest": "out.zip"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "超出工作区" in result.error
    assert not (tmp_path / "out.zip").exists()


async def test_archive_create_missing_source(tmp_path: Path):
    result = await ArchiveCreateTool().execute(
        {"sources": ["missing"], "dest": "out.zip"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "找不到" in result.error


async def test_archive_create_in_schema_and_points_off_code_execute():
    schema = ArchiveCreateTool().schema
    assert schema.name == "archive_create"
    assert "code_execute" not in schema.description
    assert "本工具" in schema.description
    assert "HOW→consult(archive_create)" in schema.description


async def test_archive_create_rejects_over_file_limit(
    tmp_path: Path, monkeypatch
):
    import agentcore.tools.builtin.archive_create as mod

    monkeypatch.setattr(mod, "_CREATE_MAX_FILES", 1)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")
    (src / "b.txt").write_text("b", encoding="utf-8")

    result = await ArchiveCreateTool().execute(
        {"sources": ["src"], "dest": "out.zip"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "文件数超过上限" in result.error
    assert not (tmp_path / "out.zip").exists()
