"""Conversation workspace routes resolve the same root as AI turn tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.api.routes.conversations import files as files_mod
from agentcore.api.routes.conversations.files import _file_workspace_folder_id
from agentcore.folders.placement import FolderPlacement
from agentcore.workspace.protocol import DirListing


@pytest.fixture(autouse=True)
def _stub_placement(monkeypatch):
    """No database here — these tests assert routing, not where a folder sits.

    The visible path mirrors the id so a wrong folder still shows up as a wrong path.
    """

    async def _placement(folder_id, **_kw):
        return FolderPlacement(folder_id=folder_id, rel_path=folder_id)

    monkeypatch.setattr(files_mod, "resolve_folder_placement", _placement)


def test_file_workspace_prefers_birth_over_auto_desk():
    conv = SimpleNamespace(folder_id="birth", auto_desk_folder_id="auto")
    assert _file_workspace_folder_id(conv) == "birth"


def test_file_workspace_uses_auto_desk_for_bare_chat():
    conv = SimpleNamespace(folder_id=None, auto_desk_folder_id="auto-desk")
    assert _file_workspace_folder_id(conv) == "auto-desk"


def test_file_workspace_bare_without_auto_desk_is_scratch():
    conv = SimpleNamespace(folder_id=None, auto_desk_folder_id=None)
    assert _file_workspace_folder_id(conv) is None


@pytest.mark.asyncio
async def test_list_uses_auto_desk_not_scratch():
    user = SimpleNamespace(user_id="u1")
    conv = SimpleNamespace(id="c1", folder_id=None, auto_desk_folder_id="desk-1")
    conv_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=conv))

    listing = DirListing(entries=[])
    with patch.object(files_mod, "list_files", new=AsyncMock(return_value=listing)) as listed:
        await files_mod.list_workspace_files(
            conversation_id="c1",
            user=user,
            recursive=False,
            conv_repo=conv_repo,
        )

    listed.assert_awaited_once()
    assert listed.await_args.kwargs["folder_id"] == "desk-1"
    assert listed.await_args.kwargs["conversation_id"] == "c1"


@pytest.mark.asyncio
async def test_upload_uses_auto_desk_not_scratch():
    user = SimpleNamespace(user_id="u1")
    conv = SimpleNamespace(id="c1", folder_id=None, auto_desk_folder_id="desk-1")
    conv_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=conv))
    request = SimpleNamespace(
        headers={},
        body=AsyncMock(return_value=b"hello"),
    )

    with patch.object(files_mod, "upload_file", new=AsyncMock(return_value=5)) as uploaded:
        resp = await files_mod.upload_workspace_file(
            conversation_id="c1",
            path="attachments/a.txt",
            request=request,
            user=user,
            conv_repo=conv_repo,
        )

    uploaded.assert_awaited_once()
    assert uploaded.await_args.kwargs["folder_id"] == "desk-1"
    assert uploaded.await_args.kwargs["path"] == "attachments/a.txt"
    assert resp.size_bytes == 5


@pytest.mark.asyncio
async def test_download_uses_auto_desk_not_scratch(tmp_path):
    user = SimpleNamespace(user_id="u1")
    conv = SimpleNamespace(id="c1", folder_id=None, auto_desk_folder_id="desk-1")
    conv_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=conv))
    target = tmp_path / "out.bin"
    target.write_bytes(b"x")

    with patch.object(
        files_mod, "resolve_download_file", new=AsyncMock(return_value=target)
    ) as resolved:
        await files_mod.download_workspace_file(
            conversation_id="c1",
            path="out.bin",
            user=user,
            conv_repo=conv_repo,
        )

    resolved.assert_awaited_once()
    assert resolved.await_args.kwargs["folder_id"] == "desk-1"


@pytest.mark.asyncio
async def test_archive_download_uses_auto_desk_not_scratch():
    user = SimpleNamespace(user_id="u1")
    conv = SimpleNamespace(id="c1", folder_id=None, auto_desk_folder_id="desk-1")
    conv_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=conv))

    with patch.object(
        files_mod, "zip_subtree_for_download", new=AsyncMock(return_value=b"PK")
    ) as zipped:
        await files_mod.download_workspace_archive(
            conversation_id="c1",
            path="docs",
            user=user,
            conv_repo=conv_repo,
        )

    zipped.assert_awaited_once()
    assert zipped.await_args.kwargs["folder_id"] == "desk-1"
    assert zipped.await_args.kwargs["path"] == "docs"


@pytest.mark.asyncio
async def test_list_birth_folder_unchanged():
    user = SimpleNamespace(user_id="u1")
    conv = SimpleNamespace(id="c1", folder_id="birth-f", auto_desk_folder_id="desk-ignored")
    conv_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=conv))

    listing = DirListing(entries=[])
    with patch.object(files_mod, "list_files", new=AsyncMock(return_value=listing)) as listed:
        await files_mod.list_workspace_files(
            conversation_id="c1",
            user=user,
            recursive=True,
            conv_repo=conv_repo,
        )

    assert listed.await_args.kwargs["folder_id"] == "birth-f"


@pytest.mark.asyncio
async def test_list_bare_without_auto_desk_stays_scratch():
    user = SimpleNamespace(user_id="u1")
    conv = SimpleNamespace(id="c1", folder_id=None, auto_desk_folder_id=None)
    conv_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=conv))

    listing = DirListing(entries=[])
    with patch.object(files_mod, "list_files", new=AsyncMock(return_value=listing)) as listed:
        await files_mod.list_workspace_files(
            conversation_id="c1",
            user=user,
            recursive=False,
            conv_repo=conv_repo,
        )

    assert listed.await_args.kwargs["folder_id"] is None
    assert listed.await_args.kwargs["conversation_id"] == "c1"
