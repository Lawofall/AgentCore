"""Attachment archive unpack: zip/tar stdlib, bomb/password, persist wiring."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from agentcore.storage._archive import ZipExtractLimitError
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.archive_unpack import (
    ArchiveStatus,
    _parse_7z_slt_entries,
    _parse_7z_slt_paths,
    _raise_if_unpack_budget,
    extract_members,
    find_7z,
    list_member_names,
    unpack_dir_for,
    unpack_resident,
)
from agentcore.workspace.attachments import persist_attachments, to_stored_metadata
from agentcore.workspace.file_kind import sniff_bytes
from agentcore.workspace.server import ServerWorkspace


def _ws(root: Path) -> ServerWorkspace:
    return ServerWorkspace(root=root, sandbox=SubprocessSandbox())


def _zip_bytes(*files: tuple[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files:
            zf.writestr(name, data)
    return buf.getvalue()


def _tar_bytes(*files: tuple[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in files:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_unpack_dir_strips_compound_tar_suffix():
    assert unpack_dir_for("attachments/notes.tar.gz") == "attachments/notes"
    assert unpack_dir_for("attachments/pack.zip") == "attachments/pack"


def test_list_member_names_zip():
    names = list_member_names(
        _zip_bytes(("a.txt", b"hello"), ("sub/b.md", b"# b")),
        sniff="zip",
        name="pack.zip",
    )
    assert names == ["a.txt", "sub/b.md"]


def test_parse_7z_slt_skips_archive_and_folders():
    stdout = (
        "Path = archive.bin\nFolder = -\nSize = 10\n\n"
        "Path = notes.txt\nFolder = -\nSize = 5\n\n"
        "Path = dir\nFolder = +\n"
    )
    assert _parse_7z_slt_paths(stdout) == ["notes.txt"]
    assert _parse_7z_slt_entries(stdout) == [("notes.txt", 5)]


def test_declared_unpack_budget_bombs_before_extract(monkeypatch: pytest.MonkeyPatch):
    from agentcore.workspace import archive_unpack as mod

    monkeypatch.setattr(mod, "ARCHIVE_UNPACK_MAX_BYTES", 10)
    with pytest.raises(ZipExtractLimitError) as exc:
        _raise_if_unpack_budget(file_count=1, total_bytes=11)
    assert exc.value.reason == "max_uncompressed_bytes"


def test_find_7z_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = tmp_path / "7zz"
    fake.write_bytes(b"x")
    monkeypatch.setenv("AGENTCORE_7Z_PATH", str(fake))
    assert find_7z() == str(fake)


def test_extract_zip_and_tar_members():
    zip_members = extract_members(
        _zip_bytes(("a.txt", b"hello"), ("sub/b.md", b"# b")),
        sniff="zip",
        name="pack.zip",
    )
    assert dict(zip_members) == {"a.txt": b"hello", "sub/b.md": b"# b"}
    tar_members = extract_members(
        _tar_bytes(("c.txt", b"tar")),
        sniff="tar",
        name="pack.tar",
    )
    assert dict(tar_members) == {"c.txt": b"tar"}


def test_extract_zip_slip_skipped():
    members = extract_members(
        _zip_bytes(("ok.txt", b"ok"), ("../evil.txt", b"no")),
        sniff="zip",
        name="pack.zip",
    )
    assert [name for name, _ in members] == ["ok.txt"]


def test_extract_zip_bomb_hits_file_cap(monkeypatch: pytest.MonkeyPatch):
    from agentcore.workspace import archive_unpack as mod

    monkeypatch.setattr(mod, "ARCHIVE_UNPACK_MAX_FILES", 1)
    with pytest.raises(ZipExtractLimitError) as exc:
        extract_members(
            _zip_bytes(("a.txt", b"a"), ("b.txt", b"b")),
            sniff="zip",
            name="pack.zip",
        )
    assert exc.value.reason == "max_files"


@pytest.mark.asyncio
async def test_persist_unpacks_zip_beside_original(tmp_path: Path):
    ws = _ws(tmp_path)
    (tmp_path / "attachments").mkdir()
    payload = _zip_bytes(("readme.txt", b"hi"), ("docs/a.md", b"# a"))
    (tmp_path / "attachments" / "pack.zip").write_bytes(payload)
    out = await persist_attachments(
        ws,
        [
            {
                "name": "pack.zip",
                "path": "attachments/pack.zip",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/pack.zip",
            }
        ],
    )
    row = out[0]
    assert row["archive_status"] == ArchiveStatus.OK.value
    assert row["archive_dir"] == "attachments/pack"
    assert row["archive_member_count"] == 2
    assert "readme.txt" in row["archive_members"]
    assert (tmp_path / "attachments" / "pack.zip").read_bytes() == payload
    assert (tmp_path / "attachments" / "pack" / "readme.txt").read_bytes() == b"hi"
    assert (tmp_path / "attachments" / "pack" / "docs" / "a.md").read_bytes() == b"# a"
    stored = to_stored_metadata(out)
    assert stored[0]["archive_status"] == ArchiveStatus.OK.value
    assert stored[0]["archive_dir"] == "attachments/pack"


@pytest.mark.asyncio
async def test_persist_invalid_zip_stays_failed_original(tmp_path: Path):
    ws = _ws(tmp_path)
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "pack.zip").write_bytes(b"PK\x03\x04noise")
    out = await persist_attachments(
        ws,
        [
            {
                "name": "pack.zip",
                "path": "attachments/pack.zip",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/pack.zip",
            }
        ],
    )
    assert out[0]["archive_status"] == ArchiveStatus.FAILED.value
    assert out[0].get("archive_dir") is None
    assert await ws.exists("attachments/pack.zip") is True


@pytest.mark.asyncio
async def test_persist_does_not_unpack_office_zip(tmp_path: Path):
    ws = _ws(tmp_path)
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "report.xlsx").write_bytes(b"PK\x03\x04")
    out = await persist_attachments(
        ws,
        [
            {
                "name": "report.xlsx",
                "path": "attachments/report.xlsx",
                "text": "",
                "binary": True,
                "workspace_path": "attachments/report.xlsx",
            }
        ],
    )
    assert "archive_status" not in out[0]
    assert not (tmp_path / "attachments" / "report").exists()


@pytest.mark.asyncio
async def test_citation_outside_attachments_is_not_unpacked(tmp_path: Path):
    ws = _ws(tmp_path)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.zip").write_bytes(_zip_bytes(("x.txt", b"x")))
    out = await persist_attachments(
        ws,
        [
            {
                "name": "lib.zip",
                "path": "vendor/lib.zip",
                "text": "",
                "binary": True,
                "workspace_path": "vendor/lib.zip",
            }
        ],
    )
    assert "archive_status" not in out[0]
    assert not (tmp_path / "vendor" / "lib").exists()


@pytest.mark.asyncio
async def test_unpack_resident_none_for_plain_text(tmp_path: Path):
    ws = _ws(tmp_path)
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "note.md").write_text("# hi\n", encoding="utf-8")
    assert sniff_bytes((tmp_path / "attachments" / "note.md").read_bytes()[:16]) == "text"
    result = await unpack_resident(ws, "attachments/note.md", name="note.md")
    assert result is None
