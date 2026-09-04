"""Integration tests for cloud-folder collaboration desk (双模式工作区 §八)."""

from pathlib import Path

import httpx
import pytest

from agentcore.config import settings
from agentcore.storage.factory import build_storage_provider
from agentcore.workspace.locate import workspace_root_path
from tests.integration.conftest import register_and_login


@pytest.fixture
def _fs_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    build_storage_provider.cache_clear()
    try:
        yield tmp_path
    finally:
        build_storage_provider.cache_clear()


async def _cloud_folder(client: httpx.AsyncClient, name: str) -> dict:
    r = await client.post("/v1/folders", json={"name": name, "mode": "cloud"})
    assert r.status_code == 201, r.text
    return r.json()


async def _invite_and_accept(
    owner_client: httpx.AsyncClient,
    member_client: httpx.AsyncClient,
    folder_id: str,
    member_id: str,
    role: str,
) -> None:
    r = await owner_client.post(
        f"/v1/folders/{folder_id}/invites",
        json={"user_id": member_id, "role": role},
    )
    assert r.status_code == 201, r.text
    r = await member_client.post(f"/v1/folders/{folder_id}/invites/accept")
    assert r.status_code == 200, r.text


async def test_shared_spaces_route_gone(client):
    r = await client.get("/v1/shared-spaces")
    assert r.status_code == 404


async def test_owner_solo_folder_does_not_regress(client):
    await register_and_login(client, "desk_solo_owner")
    folder = await _cloud_folder(client, "独用")
    assert folder["my_role"] == "owner"
    assert folder["owner_user_id"]
    assert folder["collaborator_count"] == 0
    members = (await client.get(f"/v1/folders/{folder['id']}/members")).json()
    assert members["total"] == 1
    assert members["data"][0]["role"] == "owner"

    r = await client.post(
        "/v1/conversations", json={"title": "独聊", "folder_id": folder["id"]}
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert (await client.get(f"/v1/conversations/{cid}")).status_code == 200
    listed = (await client.get("/v1/conversations")).json()["data"]
    assert any(c["id"] == cid for c in listed)


async def test_invite_lifecycle_and_stranger_404(client, new_client):
    owner_id = await register_and_login(client, "desk_life_owner")
    folder = await _cloud_folder(client, "协作")
    folder_id = folder["id"]

    async with new_client() as editor_client:
        editor_id = await register_and_login(editor_client, "desk_life_editor")
        r = await client.post(
            f"/v1/folders/{folder_id}/invites",
            json={"user_id": editor_id, "role": "editor"},
        )
        assert r.status_code == 201, r.text
        pending = (await editor_client.get("/v1/folders/invites/pending")).json()
        assert len(pending) == 1
        assert pending[0]["id"] == folder_id
        assert pending[0]["my_state"] == "pending"

        assert (await editor_client.get(f"/v1/folders/{folder_id}")).status_code == 404

        r = await editor_client.post(f"/v1/folders/{folder_id}/invites/accept")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["my_role"] == "editor"
        assert body["owner_user_id"] == owner_id

        shared = (await editor_client.get("/v1/folders/shared-with-me")).json()
        assert any(f["id"] == folder_id for f in shared)
        mine = (await editor_client.get("/v1/folders")).json()
        assert all(f["id"] != folder_id for f in mine)

        assert (await editor_client.get(f"/v1/folders/{folder_id}")).status_code == 200

    async with new_client() as stranger:
        await register_and_login(stranger, "desk_life_stranger")
        assert (await stranger.get(f"/v1/folders/{folder_id}")).status_code == 404
        assert (
            await stranger.post(
                f"/v1/folders/{folder_id}/invites",
                json={"user_id": owner_id, "role": "editor"},
            )
        ).status_code == 404


async def test_local_folder_invite_rejected(client, new_client):
    await register_and_login(client, "desk_local_owner")
    r = await client.post(
        "/v1/folders",
        json={
            "name": "本机桌",
            "mode": "local",
            "local_root_id": "11111111-2222-3333-4444-555555555555",
        },
    )
    assert r.status_code == 201, r.text
    folder_id = r.json()["id"]
    async with new_client() as other:
        other_id = await register_and_login(other, "desk_local_peer")
        r = await client.post(
            f"/v1/folders/{folder_id}/invites",
            json={"user_id": other_id, "role": "editor"},
        )
        assert r.status_code == 422, r.text


async def test_block_clears_pending_and_blocks_new_invite(client, new_client):
    await register_and_login(client, "desk_block_owner")
    folder = await _cloud_folder(client, "挡邀")
    async with new_client() as peer_client:
        peer_id = await register_and_login(peer_client, "desk_block_peer")
        r = await client.post(
            f"/v1/folders/{folder['id']}/invites",
            json={"user_id": peer_id, "role": "editor"},
        )
        assert r.status_code == 201, r.text
        r = await client.post("/v1/messages/blocks", json={"user_id": peer_id})
        assert r.status_code == 200, r.text
        pending = (await peer_client.get("/v1/folders/invites/pending")).json()
        assert pending == []
        r = await client.post(
            f"/v1/folders/{folder['id']}/invites",
            json={"user_id": peer_id, "role": "editor"},
        )
        assert r.status_code == 422, r.text


async def test_editor_sees_owner_thread_and_writes_owner_disk(
    client, new_client, _fs_data_dir
):
    owner_id = await register_and_login(client, "desk_write_owner")
    folder = await _cloud_folder(client, "写盘")
    folder_id = folder["id"]
    r = await client.post(
        "/v1/conversations", json={"title": "主人开的", "folder_id": folder_id}
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    async with new_client() as editor_client:
        editor_id = await register_and_login(editor_client, "desk_write_editor")
        await _invite_and_accept(client, editor_client, folder_id, editor_id, "editor")

        assert (await editor_client.get(f"/v1/conversations/{cid}")).status_code == 200
        listed = (await editor_client.get("/v1/conversations")).json()["data"]
        assert any(c["id"] == cid for c in listed)
        grouped = (await editor_client.get("/v1/conversations/grouped")).json()
        member_group = next(g for g in grouped["folders"] if g["id"] == folder_id)
        assert member_group["my_role"] == "editor"
        assert member_group["owner_user_id"] == owner_id

        r = await editor_client.put(
            f"/v1/conversations/{cid}/workspace/files/from-editor.txt",
            content=b"hello-desk",
        )
        assert r.status_code == 200, r.text

        path = (
            workspace_root_path(
                user_id=owner_id,
                folder_rel_path=folder["rel_path"],
                conversation_id="",
            )
            / "from-editor.txt"
        )
        assert path.is_file()
        assert path.read_bytes() == b"hello-desk"

        r = await editor_client.post(f"/v1/conversations/{cid}/stop")
        assert r.status_code == 200, r.text


async def test_viewer_read_ok_write_forbidden(client, new_client):
    await register_and_login(client, "desk_view_owner")
    folder = await _cloud_folder(client, "只读桌")
    folder_id = folder["id"]
    r = await client.post(
        "/v1/conversations", json={"title": "只读看", "folder_id": folder_id}
    )
    cid = r.json()["id"]

    async with new_client() as viewer_client:
        viewer_id = await register_and_login(viewer_client, "desk_view_viewer")
        await _invite_and_accept(client, viewer_client, folder_id, viewer_id, "viewer")
        assert (await viewer_client.get(f"/v1/conversations/{cid}")).status_code == 200
        r = await viewer_client.put(
            f"/v1/conversations/{cid}/workspace/files/nope.txt",
            content=b"x",
        )
        assert r.status_code == 403, r.text
        r = await viewer_client.post(
            f"/v1/conversations/{cid}/messages",
            json={"content": "hi", "delivery": "queue"},
        )
        assert r.status_code == 403, r.text
        r = await viewer_client.post(f"/v1/conversations/{cid}/stop")
        assert r.status_code == 403, r.text
        r = await viewer_client.post(
            f"/v1/conversations/{cid}/run-stop",
            json={"execution_id": "exec-viewer-forbidden"},
        )
        assert r.status_code == 403, r.text
        r = await viewer_client.post(
            f"/v1/conversations/{cid}/run-redirect",
            json={
                "execution_id": "exec-viewer-forbidden",
                "run_id": "run-viewer-forbidden",
                "feedback": "nope",
            },
        )
        assert r.status_code == 403, r.text
        r = await viewer_client.post(
            f"/v1/conversations/{cid}/debate-steer",
            json={"execution_id": "exec-viewer-forbidden", "decision": "continue"},
        )
        assert r.status_code == 403, r.text
        r = await viewer_client.post(
            "/v1/conversations", json={"title": "观众建", "folder_id": folder_id}
        )
        assert r.status_code == 403, r.text


async def test_stranger_conversation_404(client, new_client):
    await register_and_login(client, "desk_404_owner")
    folder = await _cloud_folder(client, "外人")
    r = await client.post(
        "/v1/conversations", json={"title": "密聊", "folder_id": folder["id"]}
    )
    cid = r.json()["id"]
    async with new_client() as stranger:
        await register_and_login(stranger, "desk_404_stranger")
        assert (await stranger.get(f"/v1/conversations/{cid}")).status_code == 404
        assert (await stranger.get(f"/v1/folders/{folder['id']}")).status_code == 404


async def test_pins_are_per_user(client, new_client):
    await register_and_login(client, "desk_pin_owner")
    folder = await _cloud_folder(client, "置顶桌")
    folder_id = folder["id"]
    r = await client.post(
        "/v1/conversations", json={"title": "钉钉", "folder_id": folder_id}
    )
    cid = r.json()["id"]
    r = await client.patch(f"/v1/conversations/{cid}", json={"pinned": True})
    assert r.status_code == 200, r.text
    assert r.json()["pinned"] is True

    async with new_client() as editor_client:
        editor_id = await register_and_login(editor_client, "desk_pin_editor")
        await _invite_and_accept(client, editor_client, folder_id, editor_id, "editor")
        got = (await editor_client.get(f"/v1/conversations/{cid}")).json()
        assert got["pinned"] is False
        r = await editor_client.patch(f"/v1/conversations/{cid}", json={"pinned": True})
        assert r.status_code == 200, r.text
        assert r.json()["pinned"] is True

    owner_got = (await client.get(f"/v1/conversations/{cid}")).json()
    assert owner_got["pinned"] is True


async def test_collaborator_count_excludes_owner(client, new_client):
    await register_and_login(client, "desk_count_owner")
    solo = await _cloud_folder(client, "独用计数")
    collab = await _cloud_folder(client, "协作计数")
    assert solo["collaborator_count"] == 0
    assert collab["collaborator_count"] == 0

    async with new_client() as editor_client:
        editor_id = await register_and_login(editor_client, "desk_count_editor")
        r = await client.post(
            f"/v1/folders/{collab['id']}/invites",
            json={"user_id": editor_id, "role": "editor"},
        )
        assert r.status_code == 201, r.text

        listed = {
            f["id"]: f["collaborator_count"]
            for f in (await client.get("/v1/folders")).json()
        }
        assert listed[solo["id"]] == 0
        assert listed[collab["id"]] == 1

        grouped = (await client.get("/v1/conversations/grouped")).json()
        by_id = {g["id"]: g["collaborator_count"] for g in grouped["folders"]}
        assert by_id[solo["id"]] == 0
        assert by_id[collab["id"]] == 1

        got = (await client.get(f"/v1/folders/{collab['id']}")).json()
        assert got["collaborator_count"] == 1

        r = await editor_client.post(f"/v1/folders/{collab['id']}/invites/accept")
        assert r.status_code == 200, r.text
        assert r.json()["collaborator_count"] == 1

        listed = {
            f["id"]: f["collaborator_count"]
            for f in (await client.get("/v1/folders")).json()
        }
        assert listed[collab["id"]] == 1

