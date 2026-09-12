"""Public 文档 shares: freeze snapshot, /shared/<id>, desk can_write, cascades."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update

import agentcore.folders.permanent_delete as permanent_delete_mod
from agentcore.config import settings
from agentcore.db.models import DocShare
from agentcore.storage.factory import build_storage_provider
from tests.integration.conftest import TEST_PASSWORD, register_and_login


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


async def _doc_with_paragraph(
    client: httpx.AsyncClient, *, folder_id: str, title: str, text: str
) -> str:
    created = await client.post(
        "/v1/docs", json={"folder_id": folder_id, "title": title}
    )
    assert created.status_code == 201, created.text
    doc_id = created.json()["id"]
    saved = await client.put(
        f"/v1/docs/{doc_id}/body",
        json={
            "baseline": 1,
            "body": {"blocks": [{"type": "paragraph", "text": text}]},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["ok"] is True
    return doc_id


async def test_create_list_view_revoke(client, new_client, _fs_data_dir):
    await register_and_login(client, "docshare1")
    folder = await _cloud_folder(client, "方案")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="Q3", text="可见正文"
    )

    r = await client.post(f"/v1/docs/{doc_id}/shares")
    assert r.status_code == 201, r.text
    share = r.json()
    assert share["url"] == f"/shared/{share['id']}"
    assert share["title"] == "Q3"
    assert share["expires_at"] is not None

    listed = await client.get(f"/v1/docs/{doc_id}/shares")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    async with new_client() as anon:
        page = await anon.get(share["url"])
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "可见正文" in page.text
        assert "Q3" in page.text
        assert "noindex" in page.text

    r = await client.delete(f"/v1/docs/{doc_id}/shares/{share['id']}")
    assert r.status_code == 200
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404
    assert (await client.get(f"/v1/docs/{doc_id}/shares")).json()["total"] == 0


async def test_snapshot_is_frozen_against_later_edits(client, new_client, _fs_data_dir):
    await register_and_login(client, "docshare_freeze")
    folder = await _cloud_folder(client, "桌")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="冻结", text="最初的段落"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    later = await client.put(
        f"/v1/docs/{doc_id}/body",
        json={
            "baseline": 2,
            "body": {"blocks": [{"type": "paragraph", "text": "稍后才改的内容"}]},
        },
    )
    assert later.status_code == 200
    assert later.json()["ok"] is True

    async with new_client() as anon:
        page = await anon.get(share["url"])
        assert "最初的段落" in page.text
        assert "稍后才改的内容" not in page.text


async def test_viewer_forbidden_editor_can_mint_and_revoke_others(
    client, new_client, _fs_data_dir
):
    await register_and_login(client, "docshare_owner")
    folder = await _cloud_folder(client, "协作")
    folder_id = folder["id"]
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder_id, title="共用", text="正文"
    )
    owner_share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()

    async with new_client() as viewer:
        viewer_id = await register_and_login(viewer, "docshare_viewer")
        await _invite_and_accept(client, viewer, folder_id, viewer_id, "viewer")
        assert (await viewer.post(f"/v1/docs/{doc_id}/shares")).status_code == 403
        assert (await viewer.get(f"/v1/docs/{doc_id}/shares")).status_code == 403
        assert (
            await viewer.delete(f"/v1/docs/{doc_id}/shares/{owner_share['id']}")
        ).status_code == 403

    async with new_client() as editor:
        editor_id = await register_and_login(editor, "docshare_editor")
        await _invite_and_accept(client, editor, folder_id, editor_id, "editor")
        minted = await editor.post(
            f"/v1/docs/{doc_id}/shares", json={"expires_in_days": 7}
        )
        assert minted.status_code == 201, minted.text
        listed = await editor.get(f"/v1/docs/{doc_id}/shares")
        assert listed.status_code == 200
        ids = {row["id"] for row in listed.json()["data"]}
        assert owner_share["id"] in ids
        assert minted.json()["id"] in ids
        assert (
            await editor.delete(f"/v1/docs/{doc_id}/shares/{owner_share['id']}")
        ).status_code == 200

    async with new_client() as anon:
        assert (await anon.get(owner_share["url"])).status_code == 404


async def test_stranger_and_anon_cannot_manage(client, new_client, _fs_data_dir):
    await register_and_login(client, "docshare_private")
    folder = await _cloud_folder(client, "私")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="密", text="secret"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()

    async with new_client() as anon:
        assert (await anon.post(f"/v1/docs/{doc_id}/shares")).status_code == 401
        page = await anon.get(share["url"])
        assert page.status_code == 200

    async with new_client() as other:
        await register_and_login(other, "docshare_intruder")
        assert (await other.post(f"/v1/docs/{doc_id}/shares")).status_code == 404
        assert (await other.get(f"/v1/docs/{doc_id}/shares")).status_code == 404
        assert (
            await other.delete(f"/v1/docs/{doc_id}/shares/{share['id']}")
        ).status_code == 404


async def test_delete_doc_revokes_shares(client, new_client, _fs_data_dir):
    await register_and_login(client, "docshare_del")
    folder = await _cloud_folder(client, "删")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="to delete", text="q"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    assert (await client.delete(f"/v1/docs/{doc_id}")).status_code == 200
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404


async def test_delete_account_revokes_shares(client, new_client, _fs_data_dir):
    await register_and_login(client, "docshare_acct")
    folder = await _cloud_folder(client, "注销桌")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="acct", text="q"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    r = await client.request("DELETE", "/v1/auth/me", json={"password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404


async def test_soft_delete_folder_revokes_shares_restore_does_not_revive(
    client, new_client, _fs_data_dir
):
    await register_and_login(client, "docshare_trash")
    folder = await _cloud_folder(client, "回收分享")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="trash", text="q"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    r = await client.delete(f"/v1/folders/{folder['id']}")
    assert r.status_code == 200, r.text
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404
    restored = await client.post(f"/v1/folders/trash/{folder['id']}/restore")
    assert restored.status_code == 200, restored.text
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404


async def test_permanent_delete_folder_revokes_shares(
    client, new_client, session_factory, monkeypatch, _fs_data_dir
):
    monkeypatch.setattr(permanent_delete_mod, "async_session_factory", session_factory)
    await register_and_login(client, "docshare_purge")
    folder = await _cloud_folder(client, "清分享")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="清", text="q"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    r = await client.delete(f"/v1/folders/{folder['id']}/permanent")
    assert r.status_code == 200, r.text
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404


async def test_public_view_escapes_xss(client, new_client, _fs_data_dir):
    await register_and_login(client, "docshare_xss")
    folder = await _cloud_folder(client, "xss")
    created = await client.post(
        "/v1/docs",
        json={"folder_id": folder["id"], "title": "<script>alert(1)</script>"},
    )
    doc_id = created.json()["id"]
    await client.put(
        f"/v1/docs/{doc_id}/body",
        json={
            "baseline": 1,
            "body": {
                "blocks": [
                    {"type": "paragraph", "text": "<script>alert('x')</script> hello"}
                ]
            },
        },
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    async with new_client() as anon:
        page = await anon.get(share["url"])
        assert page.status_code == 200
        assert "<script" not in page.text
        assert "&lt;script&gt;" in page.text


async def test_share_defaults_to_30_day_expiry(client, session_factory, _fs_data_dir):
    await register_and_login(client, "docshare_ttl")
    folder = await _cloud_folder(client, "ttl")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="ttl", text="q"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    assert share["expires_at"] is not None
    async with session_factory() as session:
        row = (
            await session.execute(select(DocShare).where(DocShare.id == share["id"]))
        ).scalar_one()
        delta = row.expires_at - datetime.now(UTC)
        assert timedelta(days=29) < delta < timedelta(days=31)


async def test_share_never_expires_when_requested(client, _fs_data_dir):
    await register_and_login(client, "docshare_never")
    folder = await _cloud_folder(client, "never")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="never", text="q"
    )
    share = (
        await client.post(
            f"/v1/docs/{doc_id}/shares",
            json={"expires_in_days": None},
        )
    ).json()
    assert share["expires_at"] is None


async def test_expired_share_returns_404(client, new_client, session_factory, _fs_data_dir):
    await register_and_login(client, "docshare_expired")
    folder = await _cloud_folder(client, "exp")
    doc_id = await _doc_with_paragraph(
        client, folder_id=folder["id"], title="exp", text="q"
    )
    share = (await client.post(f"/v1/docs/{doc_id}/shares")).json()
    async with session_factory() as session:
        await session.execute(
            update(DocShare)
            .where(DocShare.id == share["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404
