"""Unit tests for ``archive_extract`` builtin tool."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from agentcore.tools.builtin.archive_extract import ArchiveExtractTool
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


def _write_zip(path: Path, mapping: dict[str, str]) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in mapping.items():
            zf.writestr(name, text)
    path.write_bytes(buf.getvalue())


async def test_archive_extract_success(tmp_path: Path):
    _write_zip(
        tmp_path / "pkg.zip",
        {"_inventory/a.txt": "alpha", "readme.md": "# hi"},
    )
    result = await ArchiveExtractTool().execute(
        {"archive": "pkg.zip", "dest": "out"},
        _ctx(tmp_path),
    )
    assert result.success is True
    assert (tmp_path / "out" / "_inventory" / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (tmp_path / "out" / "readme.md").read_text(encoding="utf-8") == "# hi"
    assert result.metadata is not None
    assert result.metadata["files_written"] == 2
    assert "2 个文件" in result.output


async def test_archive_extract_rejects_zip_slip(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok.txt", "safe")
        zf.writestr("../escape.txt", "evil")
    (tmp_path / "evil.zip").write_bytes(buf.getvalue())

    result = await ArchiveExtractTool().execute(
        {"archive": "evil.zip", "dest": "out"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "zip-slip" in result.error
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "out" / "ok.txt").exists()  # fail closed — no partial write


async def test_archive_extract_missing_archive(tmp_path: Path):
    result = await ArchiveExtractTool().execute(
        {"archive": "missing.zip", "dest": "out"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "找不到" in result.error


async def test_archive_extract_in_schema_and_points_off_code_execute():
    schema = ArchiveExtractTool().schema
    assert schema.name == "archive_extract"
    assert "code_execute" not in schema.description
    assert "zip-slip" in schema.description


async def test_archive_extract_rejects_lied_file_size(tmp_path: Path, monkeypatch):
    """Under-reported ZIP ``file_size`` must not bypass the uncompressed ceiling."""
    import io
    import zipfile

    import agentcore.tools.builtin.archive_extract as mod

    monkeypatch.setattr(mod, "_EXTRACT_MAX_UNCOMPRESSED_BYTES", 100)

    payload = b"Z" * 200
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("bomb.bin", payload)
    (tmp_path / "lied.zip").write_bytes(buf.getvalue())

    # Simulate under-report in metadata while the body reader returns full bytes.
    real_infolist = zipfile.ZipFile.infolist

    def infolist(self):
        infos = real_infolist(self)
        for info in infos:
            info.file_size = 10
        return infos

    class _Full:
        def __init__(self, blob: bytes) -> None:
            self._b = io.BytesIO(blob)

        def read(self, n: int = -1) -> bytes:
            return self._b.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def patched_open(self, name, *args, **kwargs):
        return _Full(payload)

    monkeypatch.setattr(zipfile.ZipFile, "infolist", infolist)
    monkeypatch.setattr(zipfile.ZipFile, "open", patched_open)

    result = await ArchiveExtractTool().execute(
        {"archive": "lied.zip", "dest": "out"},
        _ctx(tmp_path),
    )
    assert result.success is False
    assert result.error and "解压后体积超过上限" in result.error
    assert not (tmp_path / "out" / "bomb.bin").exists()
