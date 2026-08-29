"""Tests for the workspace file I/O service (upload / download / list).

End-to-end over the cloud-mode backend: resolves a conversation's workspace via
``locate`` and round-trips binary content through it. ``data_dir`` is redirected
to ``tmp_path`` so nothing touches the real ./data tree.
"""

import io
import zipfile
from pathlib import Path

import pytest

from agentcore.config import settings
from agentcore.core.errors import PayloadTooLargeError, ValidationError
from agentcore.storage._archive import ArchiveLimitError
from agentcore.workspace.files import (
    archive_filename,
    download_file,
    list_files,
    raise_http_for_archive_limit,
    raise_http_for_download_io,
    resolve_download_file,
    upload_file,
    zip_subtree_for_download,
)
from agentcore.workspace.limits import FILE_TOO_LARGE_DETAIL, WORKSPACE_READ_MAX_BYTES
from agentcore.workspace.protocol import (
    NotADirectory,
    NotAFile,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceIOError,
)


@pytest.fixture(autouse=True)
def _redirect_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))


async def test_upload_then_download_roundtrip():
    blob = bytes(range(256))
    written = await upload_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="in/data.bin", data=blob
    )
    assert written == 256
    got = await download_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="in/data.bin"
    )
    assert got == blob


async def test_download_allows_above_ai_read_gate_under_upload_ceiling():
    """Panel download path is decoupled from AI ``read_bytes`` 5 MiB."""
    size = WORKSPACE_READ_MAX_BYTES + 1024
    assert size < settings.workspace_upload_max_bytes
    # Bypass upload HTTP body path: write via service after building workspace root.
    from agentcore.workspace.locate import build_server_workspace

    backend = build_server_workspace(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1")
    await backend.write_bytes("big.pptx", b"B" * size)

    resolved = await resolve_download_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="big.pptx"
    )
    assert resolved.stat().st_size == size
    got = await download_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="big.pptx"
    )
    assert len(got) == size


async def test_download_rejects_over_upload_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "workspace_upload_max_bytes", 64)
    from agentcore.workspace.locate import build_server_workspace

    ceiling = settings.workspace_upload_max_bytes
    backend = build_server_workspace(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1")
    await backend.write_bytes("huge.bin", b"H" * (ceiling + 1))

    with pytest.raises(WorkspaceIOError) as ei:
        await resolve_download_file(
            user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="huge.bin"
        )
    assert str(ei.value) == FILE_TOO_LARGE_DETAIL


def test_raise_http_for_download_io_maps_too_large_to_413():
    with pytest.raises(PayloadTooLargeError) as ei:
        raise_http_for_download_io(WorkspaceIOError(FILE_TOO_LARGE_DETAIL))
    assert ei.value.status_code == 413
    assert str(settings.workspace_upload_max_bytes) in ei.value.message


def test_raise_http_for_download_io_maps_other_io_to_422():
    with pytest.raises(ValidationError) as ei:
        raise_http_for_download_io(WorkspaceIOError("disk full"))
    assert ei.value.status_code == 422
    assert "disk full" in ei.value.message


async def test_uploaded_file_appears_in_listing():
    await upload_file(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="top.txt", data=b"x")
    await upload_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="sub/deep.txt", data=b"y"
    )
    top = {e.path for e in await list_files(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1")}
    assert "top.txt" in top

    deep = {
        e.path
        for e in await list_files(
            user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", recursive=True
        )
    }
    assert "sub/deep.txt" in deep


async def test_list_entries_include_size_and_mtime():
    """Cloud list fills file size/mtime; directories keep size_bytes=None."""
    body = b"hello-meta"
    await upload_file(
        user_id="u1",
        folder_id="f1", folder_rel_path="f1",
        conversation_id="c1",
        path="nested/note.txt",
        data=body,
    )
    top = {
        e.path: e
        for e in await list_files(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1")
    }
    assert "nested" in top
    assert top["nested"].is_dir is True
    assert top["nested"].size_bytes is None
    if top["nested"].mtime_ms is not None:
        assert top["nested"].mtime_ms > 0

    by_path = {
        e.path: e
        for e in await list_files(
            user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", recursive=True
        )
    }
    file_entry = by_path["nested/note.txt"]
    assert file_entry.is_dir is False
    assert file_entry.size_bytes == len(body)
    assert file_entry.mtime_ms is not None and file_entry.mtime_ms > 0


async def test_download_missing_raises():
    with pytest.raises(PathNotFound):
        await download_file(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="ghost.bin")


async def test_download_directory_raises_not_a_file():
    await upload_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="d/inner.txt", data=b"x"
    )
    with pytest.raises(NotAFile):
        await download_file(user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="d")


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


async def test_zip_subtree_uses_selected_dir_as_root():
    await upload_file(
        user_id="u1",
        folder_id="f1",
        folder_rel_path="f1",
        conversation_id="c1",
        path="pack/a.txt",
        data=b"A",
    )
    await upload_file(
        user_id="u1",
        folder_id="f1",
        folder_rel_path="f1",
        conversation_id="c1",
        path="pack/sub/b.txt",
        data=b"B",
    )
    await upload_file(
        user_id="u1",
        folder_id="f1",
        folder_rel_path="f1",
        conversation_id="c1",
        path="sibling.txt",
        data=b"S",
    )
    data = await zip_subtree_for_download(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="pack"
    )
    names = _zip_names(data)
    assert names == {"a.txt", "sub/b.txt"}
    assert "sibling.txt" not in names
    assert "pack/a.txt" not in names


async def test_zip_subtree_file_raises_not_a_directory():
    await upload_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="only.txt", data=b"x"
    )
    with pytest.raises(NotADirectory):
        await zip_subtree_for_download(
            user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="only.txt"
        )


async def test_zip_subtree_missing_raises():
    with pytest.raises(PathNotFound):
        await zip_subtree_for_download(
            user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="ghost"
        )


async def test_zip_subtree_rejects_over_upload_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "workspace_upload_max_bytes", 64)
    await upload_file(
        user_id="u1",
        folder_id="f1",
        folder_rel_path="f1",
        conversation_id="c1",
        path="d/huge.bin",
        data=b"H" * 65,
    )
    with pytest.raises(ArchiveLimitError):
        await zip_subtree_for_download(
            user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="d"
        )


def test_raise_http_for_archive_limit_maps_to_413():
    with pytest.raises(PayloadTooLargeError) as ei:
        raise_http_for_archive_limit(
            ArchiveLimitError(reason="max_bytes", file_count=1, total_bytes=99)
        )
    assert ei.value.status_code == 413
    assert str(settings.workspace_upload_max_bytes) in ei.value.message
    assert "文件夹" in ei.value.message


def test_archive_filename_uses_dir_name():
    assert archive_filename("docs") == "docs.zip"
    assert archive_filename("a/b/notes") == "notes.zip"
    assert archive_filename(".") == "folder.zip"


async def test_upload_traversal_is_blocked():
    with pytest.raises(OutsideWorkspace):
        await upload_file(
            user_id="u1",
            folder_id="f1", folder_rel_path="f1",
            conversation_id="c1",
            path="../escape.bin",
            data=b"x",
        )


async def test_conversation_scratch_spaces_are_independent_when_bare():
    """Bare chats (folder_id=None) each own an independent scratch."""
    await upload_file(
        user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="c1", path="shared.txt", data=b"v"
    )
    with pytest.raises(PathNotFound):
        await download_file(
            user_id="u1", folder_id=None, folder_rel_path=None, conversation_id="c2", path="shared.txt"
        )


async def test_project_conversations_share_folder_space():
    """Siblings in the same project share one workspace root."""
    await upload_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c1", path="shared.txt", data=b"v"
    )
    got = await download_file(
        user_id="u1", folder_id="f1", folder_rel_path="f1", conversation_id="c2", path="shared.txt"
    )
    assert got == b"v"
