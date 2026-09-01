"""Integration tests for folder CRUD + conversation grouping (项目即工作区).

Auto-skips (via the shared ``client`` fixture) when no PostgreSQL is reachable.
Covers create modes, birth-time membership, soft-delete archives (no ungroup),
最近删除 list + restore, permanent wipe (conversations + cloud space), absence of
PATCH …/folder, and IDOR isolation.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, update

import agentcore.folders.permanent_delete as permanent_delete_mod
from agentcore.config import settings
from agentcore.db.models import Conversation, Document, Folder
from agentcore.db.repositories import DocumentRepository, FolderRepository, MessageRepository
from agentcore.db.repositories.folders import FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM
from agentcore.storage.factory import build_storage_provider
from agentcore.workspace.locate import workspace_root_path
from tests.integration.conftest import register_and_login

_ROOT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def _fs_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "storage_backend", "filesystem")
    build_storage_provider.cache_clear()
    try:
        yield tmp_path
    finally:
        build_storage_provider.cache_clear()


async def _new_conversation(client: httpx.AsyncClient, title: str) -> str:
    r = await client.post("/v1/conversations", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed_message(session_factory, conversation_id: str) -> None:
    async with session_factory() as session:
        await MessageRepository(session).create(
            conversation_id=conversation_id, role="user", content="hi"
        )


async def _create_cloud_folder(client: httpx.AsyncClient, name: str) -> str:
    r = await client.post("/v1/folders", json={"name": name, "mode": "cloud"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_folders_require_auth(client):
    assert (await client.get("/v1/folders")).status_code == 401
    assert (await client.get("/v1/conversations/grouped")).status_code == 401


async def test_create_cloud_and_local_folders(client):
    await register_and_login(client, "folderuser1")

    r = await client.post("/v1/folders", json={"name": "Cloud Proj", "mode": "cloud"})
    assert r.status_code == 201, r.text
    cloud = r.json()
    assert cloud["name"] == "Cloud Proj"
    assert cloud["mode"] == "cloud"
    assert cloud["local_root_id"] is None
    assert cloud["local_subpath"] is None
    assert "local_dir" not in cloud

    r = await client.post(
        "/v1/folders",
        json={
            "name": "Local Proj",
            "mode": "local",
            "local_root_id": _ROOT,
            "local_subpath": "apps",
        },
    )
    assert r.status_code == 201, r.text
    local = r.json()
    assert local["mode"] == "local"
    assert local["local_root_id"] == _ROOT
    assert local["local_subpath"] == "apps"

    r = await client.get("/v1/folders")
    assert r.status_code == 200, r.text
    assert {f["id"] for f in r.json()} == {cloud["id"], local["id"]}


async def test_cloud_tree_nest_rename_move_and_restore(client, _fs_data_dir):
    """真目录树端到端：嵌套建、改名带子树、移动、软删释放名字、恢复重新占位。"""
    user_id = await register_and_login(client, "foldertree")

    def tree(rel: str) -> Path:
        return workspace_root_path(
            user_id=user_id, folder_rel_path=rel, conversation_id=""
        )

    parent = await _create_cloud_folder(client, "研究")
    r = await client.post(
        "/v1/folders", json={"name": "2026/Q1", "mode": "cloud", "parent_id": parent}
    )
    assert r.status_code == 201, r.text
    child = r.json()
    # The slash is a path separator, not part of a name — it gets sanitized away.
    assert child["rel_path"] == "研究/2026_Q1"
    assert child["parent_rel_path"] == "研究"

    ws = f"folder:{child['id']}"
    assert (
        await client.put(f"/v1/workspaces/{ws}/files/note.md", content=b"body")
    ).status_code == 200
    assert (tree("研究/2026_Q1") / "note.md").exists()

    # Rename the parent: the child's directory (and its files) follow by prefix.
    r = await client.patch(f"/v1/folders/{parent}", json={"name": "调研"})
    assert r.status_code == 200, r.text
    assert r.json()["rel_path"] == "调研"
    assert (tree("调研/2026_Q1") / "note.md").read_text(encoding="utf-8") == "body"
    assert not tree("研究").exists()
    r = await client.get(f"/v1/workspaces/{ws}/files/note.md")
    assert r.status_code == 200 and r.content == b"body"

    # Move the child back to the tree root.
    r = await client.patch(f"/v1/folders/{child['id']}", json={"parent_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["rel_path"] == "2026_Q1"
    assert (tree("2026_Q1") / "note.md").exists()

    # A folder cannot be moved into its own subtree.
    r = await client.patch(f"/v1/folders/{parent}", json={"parent_id": parent})
    assert r.status_code == 422, r.text

    # Soft-delete frees the visible name at once (directory → tombstone area).
    assert (await client.delete(f"/v1/folders/{child['id']}")).status_code == 200
    assert not tree("2026_Q1").exists()
    again = await client.post(
        "/v1/folders", json={"name": "2026_Q1", "mode": "cloud"}
    )
    assert again.json()["rel_path"] == "2026_Q1"

    # Restore lands beside the squatter rather than on top of it, files intact.
    r = await client.post(f"/v1/folders/trash/{child['id']}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["rel_path"] == "2026_Q1 (2)"
    assert (tree("2026_Q1 (2)") / "note.md").read_text(encoding="utf-8") == "body"


async def test_create_folder_requires_mode(client):
    await register_and_login(client, "foldermode")
    assert (await client.post("/v1/folders", json={"name": "NoMode"})).status_code == 422


async def test_create_local_folder_requires_root(client):
    await register_and_login(client, "folderlroot")
    r = await client.post("/v1/folders", json={"name": "Local", "mode": "local"})
    assert r.status_code == 422, r.text


async def test_create_cloud_folder_rejects_local_binding(client):
    await register_and_login(client, "folderuser12")
    r = await client.post(
        "/v1/folders",
        json={"name": "Cloud", "mode": "cloud", "local_root_id": _ROOT},
    )
    assert r.status_code == 422, r.text


async def test_create_rejects_local_dir_field(client):
    await register_and_login(client, "folderldir")
    r = await client.post(
        "/v1/folders",
        json={"name": "X", "mode": "cloud", "local_dir": "/tmp"},
    )
    assert r.status_code == 422, r.text


async def test_create_local_folder_reuses_same_binding(client):
    """Same (root, subpath) POST returns the existing row with HTTP 200."""
    await register_and_login(client, "folderreuse")

    body = {
        "name": "First",
        "mode": "local",
        "local_root_id": _ROOT,
        "local_subpath": "apps",
    }
    r1 = await client.post("/v1/folders", json=body)
    assert r1.status_code == 201, r1.text
    first = r1.json()

    r2 = await client.post(
        "/v1/folders",
        json={**body, "name": "Second Attempt"},
    )
    assert r2.status_code == 200, r2.text
    reused = r2.json()
    assert reused["id"] == first["id"]
    assert reused["name"] == first["name"]  # original name kept
    assert reused["local_root_id"] == _ROOT
    assert reused["local_subpath"] == "apps"

    listed = (await client.get("/v1/folders")).json()
    assert len(listed) == 1


async def test_create_local_folder_different_subpath_is_distinct(client):
    """Different subpaths under the same root remain separate projects."""
    await register_and_login(client, "foldersub")

    r_a = await client.post(
        "/v1/folders",
        json={
            "name": "A",
            "mode": "local",
            "local_root_id": _ROOT,
            "local_subpath": "apps",
        },
    )
    assert r_a.status_code == 201, r_a.text
    r_b = await client.post(
        "/v1/folders",
        json={
            "name": "B",
            "mode": "local",
            "local_root_id": _ROOT,
            "local_subpath": "packages",
        },
    )
    assert r_b.status_code == 201, r_b.text
    assert r_a.json()["id"] != r_b.json()["id"]

    listed = (await client.get("/v1/folders")).json()
    assert {f["id"] for f in listed} == {r_a.json()["id"], r_b.json()["id"]}


async def test_create_local_folder_empty_subpath_normalizes_for_reuse(
    client
):
    """Empty-string local_subpath normalizes to NULL and reuses root binding."""
    await register_and_login(client, "folderemptysub")

    r1 = await client.post(
        "/v1/folders",
        json={
            "name": "Root Bind",
            "mode": "local",
            "local_root_id": _ROOT,
            "local_subpath": "",
        },
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["local_subpath"] is None

    r2 = await client.post(
        "/v1/folders",
        json={
            "name": "Again",
            "mode": "local",
            "local_root_id": _ROOT,
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == r1.json()["id"]


async def test_grouped_reflects_birth_membership(client):
    await register_and_login(client, "folderuser2")

    folder_id = await _create_cloud_folder(client, "Proj")
    grouped_conv_r = await client.post(
        "/v1/conversations", json={"title": "in folder", "folder_id": folder_id}
    )
    assert grouped_conv_r.status_code == 201, grouped_conv_r.text
    grouped_conv = grouped_conv_r.json()["id"]
    loose_conv = await _new_conversation(client, "loose")

    body = (await client.get("/v1/conversations/grouped")).json()
    group = body["folders"][0]
    assert group["mode"] == "cloud"
    assert [c["id"] for c in group["conversations"]] == [grouped_conv]
    assert [c["id"] for c in body["ungrouped"]] == [loose_conv]


async def test_patch_conversation_folder_gone(client):
    """Birth-time membership: PATCH /conversations/{id}/folder no longer exists."""
    await register_and_login(client, "folderuser7")
    folder_id = await _create_cloud_folder(client, "Proj")
    conv = await _new_conversation(client, "started")

    r = await client.patch(
        f"/v1/conversations/{conv}/folder", json={"folder_id": folder_id}
    )
    assert r.status_code == 404, r.text


async def test_create_in_folder_files_at_creation(client):
    await register_and_login(client, "folderuser9")
    folder_id = await _create_cloud_folder(client, "Born")

    r = await client.post(
        "/v1/conversations", json={"title": "in folder", "folder_id": folder_id}
    )
    assert r.status_code == 201, r.text
    conv_id = r.json()["id"]
    assert r.json()["folder_id"] == folder_id
    assert r.json()["local_container_root_id"] is None

    body = (await client.get("/v1/conversations/grouped")).json()
    assert [c["id"] for c in body["folders"][0]["conversations"]] == [conv_id]


async def test_create_in_missing_folder_404(client):
    await register_and_login(client, "folderuser10")
    r = await client.post(
        "/v1/conversations",
        json={"title": "x", "folder_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404, r.text


async def test_grouped_reports_message_count(client, session_factory):
    await register_and_login(client, "folderuser11")
    conv = await _new_conversation(client, "counts")

    body = (await client.get("/v1/conversations/grouped")).json()
    assert body["ungrouped"][0]["message_count"] == 0

    await _seed_message(session_factory, conv)
    await _seed_message(session_factory, conv)

    body = (await client.get("/v1/conversations/grouped")).json()
    assert body["ungrouped"][0]["message_count"] == 2


async def test_update_folder_renames_only(client):
    await register_and_login(client, "folderuser4")
    folder_id = (
        await client.post(
            "/v1/folders",
            json={"name": "A", "mode": "local", "local_root_id": _ROOT},
        )
    ).json()["id"]

    r = await client.patch(f"/v1/folders/{folder_id}", json={"name": "B"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "B"
    assert r.json()["mode"] == "local"
    assert r.json()["local_root_id"] == _ROOT

    # Relocate fields are rejected (extra forbid on create; update ignores unknown —
    # binding stays immutable).
    r = await client.patch(
        f"/v1/folders/{folder_id}", json={"local_root_id": "other-root"}
    )
    # Pydantic UpdateFolderRequest has no local_root_id → ignored or 422 depending
    # on extra; either way binding must not change.
    refreshed = (await client.get("/v1/folders")).json()
    match = next(f for f in refreshed if f["id"] == folder_id)
    assert match["local_root_id"] == _ROOT


async def test_delete_folder_archives_conversations(client):
    await register_and_login(client, "folderuser5")
    folder_id = await _create_cloud_folder(client, "Temp")
    r = await client.post(
        "/v1/conversations", json={"title": "keep me", "folder_id": folder_id}
    )
    assert r.status_code == 201, r.text
    conv = r.json()["id"]

    r = await client.delete(f"/v1/folders/{folder_id}")
    assert r.status_code == 200, r.text

    body = (await client.get("/v1/conversations/grouped")).json()
    assert body["folders"] == []
    assert body["ungrouped"] == []

    # Conversation survives archived (not ungrouped): live list empty, archived list has it.
    live = (await client.get("/v1/conversations")).json()
    assert live["data"] == []
    archived = (await client.get("/v1/conversations", params={"archived": True})).json()
    assert [c["id"] for c in archived["data"]] == [conv]
    detail = (await client.get(f"/v1/conversations/{conv}")).json()
    assert detail["folder_id"] == folder_id
    assert detail["archived"] is True


async def test_soft_delete_keeps_member_updated_at(client, session_factory):
    """删除项目不得改写成员对话的最近活动时间——刷了就再也追不回来。"""
    await register_and_login(client, "folderkeepstamp")
    folder_id = await _create_cloud_folder(client, "Stamped")
    conv = (
        await client.post(
            "/v1/conversations", json={"title": "old news", "folder_id": folder_id}
        )
    ).json()["id"]

    async with session_factory() as s:
        before = (
            await s.execute(select(Conversation.updated_at).where(Conversation.id == conv))
        ).scalar_one()

    assert (await client.delete(f"/v1/folders/{folder_id}")).status_code == 200

    async with session_factory() as s:
        row = (
            await s.execute(
                select(Conversation.updated_at, Conversation.archived).where(
                    Conversation.id == conv
                )
            )
        ).one()
    assert row.archived is True
    assert row.updated_at == before


async def test_restore_project_unarchives_only_what_the_delete_archived(client):
    """用户自己归档的对话不该被恢复拽回侧栏；连带归档的那批才解档。"""
    await register_and_login(client, "folderrestore")
    folder_id = await _create_cloud_folder(client, "Comeback")
    kept = (
        await client.post(
            "/v1/conversations", json={"title": "live one", "folder_id": folder_id}
        )
    ).json()["id"]
    self_archived = (
        await client.post(
            "/v1/conversations", json={"title": "I archived this", "folder_id": folder_id}
        )
    ).json()["id"]
    assert (
        await client.patch(
            f"/v1/conversations/{self_archived}", json={"archived": True}
        )
    ).status_code == 200

    assert (await client.delete(f"/v1/folders/{folder_id}")).status_code == 200

    listed = (await client.get("/v1/folders/trash")).json()
    assert listed["total"] == 1
    entry = listed["data"][0]
    assert entry["id"] == folder_id
    assert entry["name"] == "Comeback"
    assert listed["retention_days"] == settings.workspace_retention_days
    # 清算时刻由服务端算好，前端不拿 deleted_at 自己减。
    purge_at = datetime.fromisoformat(entry["purge_at"])
    deleted_at = datetime.fromisoformat(entry["deleted_at"])
    assert purge_at - deleted_at == timedelta(days=settings.workspace_retention_days)

    restored = await client.post(f"/v1/folders/trash/{folder_id}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["id"] == folder_id
    assert restored.json()["name"] == "Comeback"

    assert [f["id"] for f in (await client.get("/v1/folders")).json()] == [folder_id]
    assert (await client.get("/v1/folders/trash")).json()["data"] == []

    live = (await client.get("/v1/conversations")).json()
    assert [c["id"] for c in live["data"]] == [kept]
    archived = (await client.get("/v1/conversations", params={"archived": True})).json()
    assert [c["id"] for c in archived["data"]] == [self_archived]


async def test_restore_past_retention_is_refused_not_silently_ok(
    client, session_factory
):
    await register_and_login(client, "folderexpired")
    folder_id = await _create_cloud_folder(client, "TooLate")
    assert (await client.delete(f"/v1/folders/{folder_id}")).status_code == 200

    aged = datetime.now(UTC) - timedelta(days=settings.workspace_retention_days + 1)
    async with session_factory() as s:
        await s.execute(update(Folder).where(Folder.id == folder_id).values(deleted_at=aged))
        await s.commit()

    # 过期的不再列为可恢复。
    assert (await client.get("/v1/folders/trash")).json()["data"] == []

    r = await client.post(f"/v1/folders/trash/{folder_id}/restore")
    assert r.status_code == 409, r.text
    assert (await client.get("/v1/folders")).json() == []


async def test_trash_excludes_machine_reclaimed_auto_desks(client, session_factory):
    """自动铸的裸聊云桌名字像正常项目，但它是机器垃圾，不进回收站。"""
    user_id = await register_and_login(client, "folderautodesk")
    user_deleted = await _create_cloud_folder(client, "Real Project")
    assert (await client.delete(f"/v1/folders/{user_deleted}")).status_code == 200

    async with session_factory() as s:
        desk = await FolderRepository(s).create(user_id=user_id, name="聊聊周报怎么写")
    async with session_factory() as s:
        assert await FolderRepository(s).soft_delete(
            desk.id,
            user_id=user_id,
            origin=FOLDER_DELETE_ORIGIN_AUTO_DESK_RECLAIM,
        )

    listed = (await client.get("/v1/folders/trash")).json()
    assert [f["id"] for f in listed["data"]] == [user_deleted]

    # 够不到就是够不到：直接点名恢复也不行。
    assert (await client.post(f"/v1/folders/trash/{desk.id}/restore")).status_code == 404


async def test_trash_ignores_projects_soft_deleted_before_the_feature(
    client, session_factory
):
    """历史软删行没有 delete_origin，宁可少列也不冒充「用户删的」。"""
    user_id = await register_and_login(client, "folderlegacy")
    async with session_factory() as s:
        legacy = await FolderRepository(s).create(user_id=user_id, name="Legacy")
    async with session_factory() as s:
        await s.execute(
            update(Folder)
            .where(Folder.id == legacy.id)
            .values(deleted_at=datetime.now(UTC), delete_origin=None)
        )
        await s.commit()

    assert (await client.get("/v1/folders/trash")).json()["data"] == []
    assert (
        await client.post(f"/v1/folders/trash/{legacy.id}/restore")
    ).status_code == 404


async def test_trash_is_access_session_only(client, new_client):
    """folders 窄票是给 sidecar CEO 干名册活的；恢复是用户补救面，AI 够不到。"""
    await register_and_login(client, "foldertrashticket")
    folder_id = await _create_cloud_folder(client, "Ticketed")
    token = (await client.post("/v1/folders/token")).json()["token"]
    assert (await client.delete(f"/v1/folders/{folder_id}")).status_code == 200

    async with new_client() as sidecar:
        headers = {"Authorization": f"Bearer {token}"}
        # 同一张票读得动名册……
        assert (await sidecar.get("/v1/folders", headers=headers)).status_code == 200
        # ……但看不到回收站，也恢复不了。
        assert (await sidecar.get("/v1/folders/trash", headers=headers)).status_code == 401
        assert (
            await sidecar.post(
                f"/v1/folders/trash/{folder_id}/restore", headers=headers
            )
        ).status_code == 401


async def test_trash_is_isolated_between_users(client, new_client):
    await register_and_login(client, "trashowner")
    folder_id = await _create_cloud_folder(client, "Mine To Restore")
    assert (await client.delete(f"/v1/folders/{folder_id}")).status_code == 200

    async with new_client() as other:
        await register_and_login(other, "trashintruder")
        assert (await other.get("/v1/folders/trash")).json()["data"] == []
        assert (
            await other.post(f"/v1/folders/trash/{folder_id}/restore")
        ).status_code == 404

    assert (await client.post(f"/v1/folders/trash/{folder_id}/restore")).status_code == 200


async def test_soft_delete_folder_hides_workspace_from_hub(
    client, _fs_data_dir
):
    """Soft-deleted ``folder:<id>`` must not appear in list or resolve via locate."""
    await register_and_login(client, "foldersoftws")
    folder_id = await _create_cloud_folder(client, "SoftGone")
    ws = f"folder:{folder_id}"
    await client.put(f"/v1/workspaces/{ws}/files/keep.txt", content=b"x")

    assert (await client.delete(f"/v1/folders/{folder_id}")).status_code == 200

    listed = (await client.get("/v1/workspaces")).json()["data"]
    assert ws not in {w["ws_id"] for w in listed}
    assert (await client.get(f"/v1/workspaces/{ws}/files")).status_code == 404


async def test_permanent_delete_folder_wipes_conversations_and_cloud_space(
    client, session_factory, monkeypatch, _fs_data_dir
):
    """彻底删除: member chats gone, shared cloud files + snapshots purged."""
    monkeypatch.setattr(permanent_delete_mod, "async_session_factory", session_factory)
    user_id = await register_and_login(client, "folderuser7p")
    folder_id = await _create_cloud_folder(client, "Gone")
    r = await client.post(
        "/v1/conversations", json={"title": "wipe me", "folder_id": folder_id}
    )
    conv = r.json()["id"]
    await _seed_message(session_factory, conv)

    ws = f"folder:{folder_id}"
    assert (
        await client.put(f"/v1/workspaces/{ws}/files/docs/a.txt", content=b"payload")
    ).status_code == 200
    snap = await client.post(f"/v1/workspaces/{ws}/snapshots", json={"label": "pre"})
    assert snap.status_code == 201, snap.text

    r = await client.delete(f"/v1/folders/{folder_id}/permanent")
    assert r.status_code == 200, r.text

    body = (await client.get("/v1/conversations/grouped")).json()
    assert body["folders"] == []
    assert body["ungrouped"] == []
    assert (await client.get(f"/v1/conversations/{conv}")).status_code == 404
    assert (await client.get("/v1/folders")).json() == []
    assert (await client.get(f"/v1/workspaces/{ws}/files")).status_code == 404
    assert not workspace_root_path(
        user_id=user_id, folder_rel_path="Gone", conversation_id=""
    ).exists()


async def test_permanent_delete_folder_wipes_that_desk_documents(
    client, session_factory, monkeypatch, _fs_data_dir
):
    """彻底删除物理清这张桌的设定；全局层留下。"""
    monkeypatch.setattr(permanent_delete_mod, "async_session_factory", session_factory)
    user_id = await register_and_login(client, "folderuser7docs")
    folder_id = await _create_cloud_folder(client, "GoneDocs")
    async with session_factory() as session:
        repo = DocumentRepository(session)
        await repo.create(
            user_id,
            name="用户规则.md",
            role="rule",
            apply_mode="always",
            content="- 全局规则留下",
        )
        await repo.create(
            user_id,
            name="用户规则.md",
            role="rule",
            folder_id=folder_id,
            apply_mode="always",
            content="- 本桌规则清掉",
        )

    r = await client.delete(f"/v1/folders/{folder_id}/permanent")
    assert r.status_code == 200, r.text

    async with session_factory() as session:
        folder_n = (
            await session.execute(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.user_id == user_id,
                    Document.folder_id == folder_id,
                )
            )
        ).scalar_one()
        global_n = (
            await session.execute(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.user_id == user_id,
                    Document.folder_id.is_(None),
                    Document.name == "用户规则.md",
                )
            )
        ).scalar_one()
    assert folder_n == 0
    assert global_n == 1


async def test_permanent_delete_folder_wipes_nested_subtree(
    client, session_factory, monkeypatch, _fs_data_dir
):
    """嵌套子文件夹跟着父一起走：父目录被整个 rmtree，留下子行就是指向空气的幽灵项目。"""
    monkeypatch.setattr(permanent_delete_mod, "async_session_factory", session_factory)
    user_id = await register_and_login(client, "folderuser7n")
    parent = await _create_cloud_folder(client, "Root")
    r = await client.post(
        "/v1/folders", json={"name": "Nested", "mode": "cloud", "parent_id": parent}
    )
    assert r.status_code == 201, r.text
    child = r.json()["id"]

    r = await client.post(
        "/v1/conversations", json={"title": "in parent", "folder_id": parent}
    )
    parent_conv = r.json()["id"]
    r = await client.post(
        "/v1/conversations", json={"title": "in child", "folder_id": child}
    )
    child_conv = r.json()["id"]
    await _seed_message(session_factory, child_conv)
    assert (
        await client.put(f"/v1/workspaces/folder:{child}/files/n.txt", content=b"x")
    ).status_code == 200

    r = await client.delete(f"/v1/folders/{parent}/permanent")
    assert r.status_code == 200, r.text

    assert (await client.get("/v1/folders")).json() == []
    for conv in (parent_conv, child_conv):
        assert (await client.get(f"/v1/conversations/{conv}")).status_code == 404
    assert (await client.get(f"/v1/workspaces/folder:{child}/files")).status_code == 404
    assert not workspace_root_path(
        user_id=user_id, folder_rel_path="Root", conversation_id=""
    ).exists()


async def test_permanent_delete_local_folder_keeps_os_sentinel(
    client, session_factory, monkeypatch, _fs_data_dir, tmp_path: Path
):
    """Local project wipe clears DB/server data only — never the user's OS directory."""
    monkeypatch.setattr(permanent_delete_mod, "async_session_factory", session_factory)
    # Sentinel stands in for the user's real project directory (desktop root handle
    # is opaque; the server must not rm anything outside data_dir).
    os_sentinel = tmp_path / "user-os-project"
    os_sentinel.mkdir()
    (os_sentinel / "important.txt").write_text("do-not-touch", encoding="utf-8")

    await register_and_login(client, "folderuser7l")
    r = await client.post(
        "/v1/folders",
        json={
            "name": "LocalGone",
            "mode": "local",
            "local_root_id": _ROOT,
            "local_subpath": "apps",
        },
    )
    assert r.status_code == 201, r.text
    folder_id = r.json()["id"]
    r = await client.post(
        "/v1/conversations", json={"title": "local wipe", "folder_id": folder_id}
    )
    conv = r.json()["id"]

    assert (
        await client.delete(f"/v1/folders/{folder_id}/permanent")
    ).status_code == 200

    assert (await client.get(f"/v1/conversations/{conv}")).status_code == 404
    assert (await client.get("/v1/folders")).json() == []
    assert os_sentinel.exists()
    assert (os_sentinel / "important.txt").read_text(encoding="utf-8") == "do-not-touch"


async def test_folder_isolation_between_users(client, new_client):
    await register_and_login(client, "owneruser")
    folder_id = await _create_cloud_folder(client, "Mine")

    async with new_client() as other:
        await register_and_login(other, "intruder")

        assert (await other.get("/v1/folders")).json() == []
        assert (
            await other.patch(f"/v1/folders/{folder_id}", json={"name": "x"})
        ).status_code == 404
        assert (await other.delete(f"/v1/folders/{folder_id}")).status_code == 404

        intruder_conv = await _new_conversation(other, "intruder")
        # PATCH folder endpoint is gone for everyone.
        assert (
            await other.patch(
                f"/v1/conversations/{intruder_conv}/folder",
                json={"folder_id": folder_id},
            )
        ).status_code == 404


async def test_soft_delete_hibernates_folder_settings_until_restore(
    client, session_factory
):
    """软删：这张桌的设定退出注入、行还在；恢复后回来。全局层不陪葬。"""
    from agentcore.memory.document_store import DocumentMemoryStore
    from agentcore.memory.rules_injection import assemble_injected_rules
    from agentcore.memory.scope_chain import db_scope_chain

    uid = await register_and_login(client, "foldersettings")
    folder_id = await _create_cloud_folder(client, "SettingsDesk")
    assert (
        await client.put(
            "/v1/users/me/memory/files/preferences",
            json={"content": "- 全局偏好别用 emoji", "baseline": None},
        )
    ).status_code == 200
    assert (
        await client.put(
            f"/v1/users/me/memory/files/profile?folder_id={folder_id}",
            json={"content": "- 本仓用 Rust", "baseline": None},
        )
    ).status_code == 200
    async with session_factory() as session:
        await DocumentRepository(session).upsert_user_rules_doc(
            uid, folder_id, "- 本桌必须用中文"
        )

    async def _inject() -> str:
        async with session_factory() as session:
            return await assemble_injected_rules(
                DocumentMemoryStore(session=session),
                DocumentRepository(session),
                uid,
                folder_id=folder_id,
                enabled=True,
                scope_chain=await db_scope_chain(uid, folder_id, session=session),
            )

    live = await _inject()
    assert "全局偏好别用 emoji" in live
    assert "本仓用 Rust" in live
    assert "本桌必须用中文" in live

    assert (await client.delete(f"/v1/folders/{folder_id}")).status_code == 200
    hibernating = await _inject()
    assert "全局偏好别用 emoji" in hibernating
    assert "本仓用 Rust" not in hibernating
    assert "本桌必须用中文" not in hibernating
    async with session_factory() as session:
        leftover = (
            await session.execute(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.user_id == uid,
                    Document.folder_id == folder_id,
                )
            )
        ).scalar_one()
    assert leftover >= 1

    restored = await client.post(f"/v1/folders/trash/{folder_id}/restore")
    assert restored.status_code == 200, restored.text
    back = await _inject()
    assert "本仓用 Rust" in back
    assert "本桌必须用中文" in back
