"""Creation-tool 文档 live draft API (folder-hung block body)."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

import agentcore.folders.permanent_delete as permanent_delete_mod
from agentcore.config import settings
from agentcore.db.models import Doc
from agentcore.db.repositories import FolderRepository
from agentcore.storage.factory import build_storage_provider
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


async def test_create_save_reopen_doc(client, _fs_data_dir):
    await register_and_login(client, "doc_owner")
    folder = await _cloud_folder(client, "方案")
    created = await client.post(
        "/v1/docs", json={"folder_id": folder["id"], "title": "Q3"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["title"] == "Q3"
    assert body["folder_id"] == folder["id"]
    assert body["folder_name"] == "方案"
    assert body["version"] == 1
    assert body["can_write"] is True
    doc_id = body["id"]

    saved = await client.put(
        f"/v1/docs/{doc_id}/body",
        json={
            "baseline": 1,
            "body": {
                "schemaVersion": 1,
                "blocks": [
                    {"id": "h1", "type": "heading", "level": 1, "text": "结论"},
                    {"id": "p1", "type": "paragraph", "text": "可以发"},
                    {
                        "id": "l1",
                        "type": "list",
                        "ordered": False,
                        "items": ["甲", "乙"],
                    },
                    {
                        "id": "t1",
                        "type": "table",
                        "columns": ["项", "值"],
                        "rows": [["甲", "1"], ["乙", "2"]],
                    },
                    {
                        "id": "ch1",
                        "type": "chart",
                        "title": "季度",
                        "items": [{"label": "Q1", "value": 3}],
                    },
                    {"id": "x", "type": "html", "text": "<script>"},
                ],
            },
        },
    )
    assert saved.status_code == 200, saved.text
    write = saved.json()
    assert write["ok"] is True
    assert write["conflict"] is False
    assert write["version"] == 2

    loaded = await client.get(f"/v1/docs/{doc_id}")
    assert loaded.status_code == 200, loaded.text
    detail = loaded.json()
    types = [b["type"] for b in detail["body"]["blocks"]]
    assert types == ["heading", "paragraph", "list", "table", "chart"]
    assert detail["body"]["blocks"][0]["text"] == "结论"
    table = detail["body"]["blocks"][3]
    assert table["columns"] == ["项", "值"]
    assert table["rows"] == [["甲", "1"], ["乙", "2"]]
    chart = detail["body"]["blocks"][4]
    assert chart["kind"] == "bar"
    assert chart["title"] == "季度"
    assert chart["items"] == [{"label": "Q1", "value": 3.0}]
    assert detail["version"] == 2

    listed = await client.get("/v1/docs")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == doc_id
    assert rows[0]["can_write"] is True
    assert "body" not in rows[0]


async def test_doc_cas_conflict_does_not_clobber(client, _fs_data_dir):
    await register_and_login(client, "doc_cas")
    folder = await _cloud_folder(client, "桌")
    doc_id = (
        await client.post("/v1/docs", json={"folder_id": folder["id"]})
    ).json()["id"]
    first = await client.put(
        f"/v1/docs/{doc_id}/body",
        json={
            "baseline": 1,
            "body": {"blocks": [{"type": "paragraph", "text": "A"}]},
        },
    )
    assert first.status_code == 200
    stale = await client.put(
        f"/v1/docs/{doc_id}/body",
        json={
            "baseline": 1,
            "body": {"blocks": [{"type": "paragraph", "text": "B"}]},
        },
    )
    assert stale.status_code == 200
    result = stale.json()
    assert result["ok"] is False
    assert result["conflict"] is True
    assert result["doc"]["body"]["blocks"][0]["text"] == "A"


async def test_doc_rejects_local_folder_and_stranger(
    client, session_factory, new_client, _fs_data_dir
):
    uid = await register_and_login(client, "doc_local")
    async with session_factory() as s:
        local = await FolderRepository(s).create(
            user_id=uid, name="本机", local_root_id="root-1"
        )
        local_id = local.id
    r = await client.post("/v1/docs", json={"folder_id": local_id})
    assert r.status_code == 422

    cloud = await _cloud_folder(client, "云")
    doc_id = (await client.post("/v1/docs", json={"folder_id": cloud["id"]})).json()[
        "id"
    ]
    async with new_client() as other:
        await register_and_login(other, "doc_stranger")
        assert (await other.get(f"/v1/docs/{doc_id}")).status_code == 404
        assert (
            await other.post("/v1/docs", json={"folder_id": cloud["id"]})
        ).status_code == 404


async def test_doc_viewer_reads_editor_writes(client, new_client, _fs_data_dir):
    await register_and_login(client, "doc_desk_owner")
    folder = await _cloud_folder(client, "协作文档")
    folder_id = folder["id"]
    doc_id = (
        await client.post(
            "/v1/docs", json={"folder_id": folder_id, "title": "共用"}
        )
    ).json()["id"]

    async with new_client() as viewer:
        viewer_id = await register_and_login(viewer, "doc_desk_viewer")
        await _invite_and_accept(client, viewer, folder_id, viewer_id, "viewer")
        got = await viewer.get(f"/v1/docs/{doc_id}")
        assert got.status_code == 200, got.text
        assert got.json()["title"] == "共用"
        assert got.json()["can_write"] is False
        listed = await viewer.get("/v1/docs")
        assert any(row["id"] == doc_id for row in listed.json())
        assert all(
            row["can_write"] is False
            for row in listed.json()
            if row["id"] == doc_id
        )
        assert (
            await viewer.put(
                f"/v1/docs/{doc_id}/body",
                json={"baseline": 1, "body": {"blocks": []}},
            )
        ).status_code == 403
        assert (await viewer.delete(f"/v1/docs/{doc_id}")).status_code == 403

    async with new_client() as editor:
        editor_id = await register_and_login(editor, "doc_desk_editor")
        await _invite_and_accept(client, editor, folder_id, editor_id, "editor")
        patched = await editor.patch(f"/v1/docs/{doc_id}", json={"title": "改名"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["title"] == "改名"
        assert patched.json()["can_write"] is True
        got = await editor.get(f"/v1/docs/{doc_id}")
        assert got.json()["can_write"] is True


async def test_permanent_delete_folder_wipes_docs(
    client, session_factory, monkeypatch, _fs_data_dir
):
    monkeypatch.setattr(permanent_delete_mod, "async_session_factory", session_factory)
    await register_and_login(client, "doc_purge")
    folder = await _cloud_folder(client, "清文档")
    doc_id = (
        await client.post("/v1/docs", json={"folder_id": folder["id"]})
    ).json()["id"]
    r = await client.delete(f"/v1/folders/{folder['id']}/permanent")
    assert r.status_code == 200, r.text
    assert (await client.get(f"/v1/docs/{doc_id}")).status_code == 404
    async with session_factory() as session:
        n = (
            await session.execute(
                select(func.count()).select_from(Doc).where(Doc.id == doc_id)
            )
        ).scalar_one()
    assert n == 0
