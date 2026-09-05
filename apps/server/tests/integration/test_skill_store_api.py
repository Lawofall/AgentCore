"""GET/POST/DELETE /v1/skill-store — 开放上架 / 安装快照 / 举报 / admin 下架."""

from tests.integration.conftest import (
    TEST_PASSWORD,
    client_platform_headers,
    login_admin,
    register_and_login,
)


async def _create_on_demand(client, name: str, content: str, description: str = "") -> dict:
    body = content
    if description:
        body = f"---\napply: on_demand\ndescription: {description}\n---\n{content}"
    r = await client.post(
        "/v1/documents",
        json={
            "name": name,
            "role": "rule",
            "content": body,
            "apply_mode": "on_demand",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _login(client, username: str) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
        headers=client_platform_headers(),
    )
    assert r.status_code == 200, r.text


async def test_skill_store_requires_auth(client):
    assert (await client.get("/v1/skill-store")).status_code == 401
    assert (await client.get("/v1/skill-store/mine")).status_code == 401
    assert (await client.get("/v1/skill-store/installed")).status_code == 401


async def test_skill_store_publish_list_install_update_unpublish_report(client):
    await register_and_login(client, "ssauthor")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )

    published = await client.post("/v1/skill-store", json={"document_id": doc["id"]})
    assert published.status_code == 200, published.text
    listing = published.json()
    lid = listing["id"]
    assert listing["name"] == "合同审查"
    assert listing["description"] == "审合同时用"
    assert listing["author"] == "ssauthor"
    assert listing["version_n"] == 1
    assert listing["source_document_id"] == doc["id"]
    assert listing["status"] == "published"
    assert "怎么审合同" in listing["content"]

    shelf = await client.get("/v1/skill-store")
    assert shelf.status_code == 200, shelf.text
    rows = shelf.json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == lid
    assert "content" not in rows[0]
    assert rows[0]["installed"] is False
    assert rows[0]["has_update"] is False

    detail = await client.get(f"/v1/skill-store/{lid}")
    assert detail.status_code == 200, detail.text
    assert "怎么审合同" in detail.json()["content"]

    mine = await client.get("/v1/skill-store/mine")
    assert mine.status_code == 200, mine.text
    assert mine.json()["data"][0]["id"] == lid

    await register_and_login(client, "ssbuyer")
    installed = await client.post(f"/v1/skill-store/{lid}/install")
    assert installed.status_code == 200, installed.text
    assert installed.json()["installed"] is True
    assert installed.json()["has_update"] is False
    copy_id = installed.json()["document_id"]

    again = await client.post(f"/v1/skill-store/{lid}/install")
    assert again.status_code == 200, again.text
    assert again.json()["document_id"] == copy_id

    catalog = await client.get("/v1/skill-catalog")
    assert catalog.status_code == 200, catalog.text
    mine_skills = catalog.json()["mine"]
    copy = next(m for m in mine_skills if m["id"] == copy_id)
    assert copy["occupies"] == []
    assert "怎么审合同" in copy["content"]

    await _login(client, "ssauthor")
    updated = await client.put(
        f"/v1/documents/{doc['id']}",
        json={
            "content": "---\napply: on_demand\ndescription: 审合同时用\n---\n新版本正文",
        },
    )
    assert updated.status_code == 200, updated.text
    v2 = await client.post("/v1/skill-store", json={"document_id": doc["id"]})
    assert v2.status_code == 200, v2.text
    assert v2.json()["version_n"] == 2
    assert "新版本正文" in v2.json()["content"]

    await _login(client, "ssbuyer")
    shelf = await client.get("/v1/skill-store")
    row = next(r for r in shelf.json()["data"] if r["id"] == lid)
    assert row["installed"] is True
    assert row["has_update"] is True
    assert "content" not in row

    installed_list = await client.get("/v1/skill-store/installed")
    assert installed_list.status_code == 200, installed_list.text
    inst = next(i for i in installed_list.json()["data"] if i["id"] == lid)
    assert inst["has_update"] is True

    refreshed = await client.post(f"/v1/skill-store/{lid}/install")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["has_update"] is False
    assert refreshed.json()["document_id"] == copy_id

    catalog = await client.get("/v1/skill-catalog")
    copy = next(m for m in catalog.json()["mine"] if m["id"] == copy_id)
    assert "新版本正文" in copy["content"]
    assert copy["occupies"] == []

    reported = await client.post(
        f"/v1/skill-store/{lid}/reports", json={"reason": "正文有问题"}
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["reason"] == "正文有问题"

    await _login(client, "ssauthor")
    gone = await client.delete(f"/v1/skill-store/{lid}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["status"] == "unpublished"

    await _login(client, "ssbuyer")
    assert (await client.get(f"/v1/skill-store/{lid}")).status_code == 404
    public = await client.get("/v1/skill-store")
    assert all(r["id"] != lid for r in public.json()["data"])
    catalog = await client.get("/v1/skill-catalog")
    assert any(m["id"] == copy_id for m in catalog.json()["mine"])

    await _login(client, "ssauthor")
    republished = await client.post(f"/v1/skill-store/{lid}/versions")
    assert republished.status_code == 200, republished.text
    assert republished.json()["status"] == "published"
    assert republished.json()["version_n"] == 3

    await _login(client, "ssbuyer")
    public = await client.get("/v1/skill-store")
    assert any(r["id"] == lid for r in public.json()["data"])
    assert (await client.get(f"/v1/skill-store/{lid}")).status_code == 200


async def test_skill_store_admin_takedown_hides_listing(client, make_admin):
    await register_and_login(client, "sstake")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )
    published = await client.post("/v1/skill-store", json={"document_id": doc["id"]})
    assert published.status_code == 200, published.text
    lid = published.json()["id"]

    await register_and_login(client, "ssinst")
    installed = await client.post(f"/v1/skill-store/{lid}/install")
    assert installed.status_code == 200, installed.text
    copy_id = installed.json()["document_id"]

    admin_user, admin_pass = await make_admin()
    await login_admin(client, admin_user, admin_pass)
    listed = await client.get("/v1/admin/skill-store/listings")
    assert listed.status_code == 200, listed.text
    assert any(row["id"] == lid for row in listed.json()["data"])

    takedown = await client.post(f"/v1/admin/skill-store/listings/{lid}/takedown")
    assert takedown.status_code == 200, takedown.text
    assert takedown.json()["status"] == "taken_down"

    await _login(client, "ssinst")
    assert (await client.get(f"/v1/skill-store/{lid}")).status_code == 404
    public = await client.get("/v1/skill-store")
    assert all(row["id"] != lid for row in public.json()["data"])
    catalog = await client.get("/v1/skill-catalog")
    assert any(m["id"] == copy_id for m in catalog.json()["mine"])

    await _login(client, "sstake")
    blocked = await client.post(f"/v1/skill-store/{lid}/versions")
    assert blocked.status_code == 403
    blocked_pub = await client.post("/v1/skill-store", json={"document_id": doc["id"]})
    assert blocked_pub.status_code == 403


async def test_skill_store_publish_rejects_ineligible(client):
    await register_and_login(client, "ssbad")
    always = await client.post(
        "/v1/documents",
        json={
            "name": "常驻.md",
            "role": "rule",
            "content": "- 常驻",
            "apply_mode": "always",
        },
    )
    assert always.status_code == 200, always.text
    r = await client.post("/v1/skill-store", json={"document_id": always.json()["id"]})
    assert r.status_code == 400

    empty_desc = await _create_on_demand(client, "空说明.md", "有正文")
    r = await client.post("/v1/skill-store", json={"document_id": empty_desc["id"]})
    assert r.status_code == 400

    empty_body = await client.post(
        "/v1/documents",
        json={
            "name": "空正文.md",
            "role": "rule",
            "content": "---\napply: on_demand\ndescription: 有说明\n---\n",
            "apply_mode": "on_demand",
        },
    )
    assert empty_body.status_code == 200, empty_body.text
    r = await client.post("/v1/skill-store", json={"document_id": empty_body.json()["id"]})
    assert r.status_code == 400


async def test_skill_store_admin_sees_reports(client, make_admin):
    await register_and_login(client, "ssrep")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )
    lid = (await client.post("/v1/skill-store", json={"document_id": doc["id"]})).json()["id"]
    await register_and_login(client, "ssflag")
    r = await client.post(f"/v1/skill-store/{lid}/reports", json={"reason": "垃圾"})
    assert r.status_code == 200, r.text

    admin_user, admin_pass = await make_admin()
    await login_admin(client, admin_user, admin_pass)
    reports = await client.get("/v1/admin/skill-store/reports")
    assert reports.status_code == 200, reports.text
    assert any(
        row["listing_id"] == lid and row["reason"] == "垃圾" for row in reports.json()["data"]
    )
    body = await client.get(f"/v1/admin/skill-store/listings/{lid}")
    assert body.status_code == 200, body.text
    assert "怎么审合同" in body.json()["content"]
    assert body.json()["id"] == lid
