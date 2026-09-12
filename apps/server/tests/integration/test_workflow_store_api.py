"""GET/POST/DELETE /v1/workflow-store — 开放上架 / 安装快照 / 举报 / admin 下架."""

from agentcore.workflows.playbook_templates import list_playbook_templates
from tests.integration.conftest import (
    TEST_PASSWORD,
    client_platform_headers,
    login_admin,
    register_and_login,
)


def _definition() -> dict:
    return {
        "nodes": [
            {
                "id": "research",
                "kind": "agent_step",
                "role": "研究员",
                "task": "调研现状",
            }
        ],
        "edges": [],
    }


async def _create_workflow(
    client, name: str, description: str | None = "周报流水线", definition: dict | None = None
) -> dict:
    r = await client.post(
        "/v1/workflows",
        json={
            "name": name,
            "description": description,
            "definition": definition or _definition(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _login(client, username: str) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
        headers=client_platform_headers(),
    )
    assert r.status_code == 200, r.text


async def test_workflow_store_requires_auth(client):
    assert (await client.get("/v1/workflow-store")).status_code == 401
    assert (await client.get("/v1/workflow-store/mine")).status_code == 401
    assert (await client.get("/v1/workflow-store/installed")).status_code == 401


async def test_workflow_store_publish_list_install_update_unpublish_report(client):
    await register_and_login(client, "wfauthor")
    wf = await _create_workflow(client, "周报流水线", "每周写周报")

    published = await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})
    assert published.status_code == 200, published.text
    listing = published.json()
    lid = listing["id"]
    assert listing["name"] == "周报流水线"
    assert listing["description"] == "每周写周报"
    assert listing["author"] == "wfauthor"
    assert listing["version_n"] == 1
    assert listing["source_workflow_id"] == wf["id"]
    assert listing["status"] == "published"
    assert listing["definition"]["nodes"][0]["id"] == "research"
    assert listing.get("trigger") is None

    shelf = await client.get("/v1/workflow-store")
    assert shelf.status_code == 200, shelf.text
    rows = shelf.json()["data"]
    community = next(r for r in rows if r["id"] == lid)
    assert "definition" not in community
    assert community["installed"] is False
    assert community["has_update"] is False
    playbook_ids = {item.id for item in list_playbook_templates()}
    assert playbook_ids.isdisjoint({r["id"] for r in rows})

    detail = await client.get(f"/v1/workflow-store/{lid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["definition"]["nodes"][0]["task"] == "调研现状"

    mine = await client.get("/v1/workflow-store/mine")
    assert mine.status_code == 200, mine.text
    assert mine.json()["data"][0]["id"] == lid

    await register_and_login(client, "wfbuyer")
    installed = await client.post(f"/v1/workflow-store/{lid}/install")
    assert installed.status_code == 200, installed.text
    assert installed.json()["installed"] is True
    assert installed.json()["has_update"] is False
    copy_id = installed.json()["workflow_id"]
    assert copy_id != wf["id"]

    copy = await client.get(f"/v1/workflows/{copy_id}")
    assert copy.status_code == 200, copy.text
    body = copy.json()
    assert body["source"] is None
    assert body.get("trigger") is None
    assert body["name"] == "周报流水线"
    assert body["description"] == "每周写周报"

    again = await client.post(f"/v1/workflow-store/{lid}/install")
    assert again.status_code == 200, again.text
    assert again.json()["workflow_id"] == copy_id

    await _login(client, "wfauthor")
    patched = await client.patch(
        f"/v1/workflows/{wf['id']}",
        json={
            "name": "周报流水线 v2",
            "description": "每周写周报",
            "definition": {
                "nodes": [
                    {
                        "id": "write",
                        "kind": "agent_step",
                        "role": "写手",
                        "task": "写新版周报",
                    }
                ],
                "edges": [],
            },
        },
    )
    assert patched.status_code == 200, patched.text
    v2 = await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})
    assert v2.status_code == 200, v2.text
    assert v2.json()["version_n"] == 2
    assert v2.json()["definition"]["nodes"][0]["task"] == "写新版周报"

    await _login(client, "wfbuyer")
    folder = await client.post("/v1/folders", json={"name": "触发桌", "mode": "cloud"})
    assert folder.status_code == 201, folder.text
    clock = await client.put(
        f"/v1/workflows/{copy_id}/trigger",
        json={"kind": "webhook", "folder_id": folder.json()["id"]},
    )
    assert clock.status_code == 200, clock.text
    assert clock.json()["trigger"]["kind"] == "webhook"
    webhook_id = clock.json()["trigger"]["webhook_id"]

    shelf = await client.get("/v1/workflow-store")
    row = next(r for r in shelf.json()["data"] if r["id"] == lid)
    assert row["installed"] is True
    assert row["has_update"] is True
    assert "definition" not in row

    installed_list = await client.get("/v1/workflow-store/installed")
    assert installed_list.status_code == 200, installed_list.text
    inst = next(i for i in installed_list.json()["data"] if i["id"] == lid)
    assert inst["has_update"] is True

    refreshed = await client.post(f"/v1/workflow-store/{lid}/install")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["has_update"] is False
    assert refreshed.json()["workflow_id"] == copy_id

    after = (await client.get(f"/v1/workflows/{copy_id}")).json()
    assert after["name"] == "周报流水线 v2"
    assert after["definition"]["nodes"][0]["task"] == "写新版周报"
    assert after["source"] is None
    assert after["trigger"]["kind"] == "webhook"
    assert after["trigger"]["webhook_id"] == webhook_id

    reported = await client.post(
        f"/v1/workflow-store/{lid}/reports", json={"reason": "图有问题"}
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["reason"] == "图有问题"

    await _login(client, "wfauthor")
    gone = await client.delete(f"/v1/workflow-store/{lid}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["status"] == "unpublished"

    await _login(client, "wfbuyer")
    assert (await client.get(f"/v1/workflow-store/{lid}")).status_code == 404
    public = await client.get("/v1/workflow-store")
    assert all(r["id"] != lid for r in public.json()["data"])
    assert (await client.get(f"/v1/workflows/{copy_id}")).status_code == 200

    await _login(client, "wfauthor")
    republished = await client.post(f"/v1/workflow-store/{lid}/versions")
    assert republished.status_code == 200, republished.text
    assert republished.json()["status"] == "published"
    assert republished.json()["version_n"] == 3

    await _login(client, "wfbuyer")
    public = await client.get("/v1/workflow-store")
    assert any(r["id"] == lid for r in public.json()["data"])
    assert (await client.get(f"/v1/workflow-store/{lid}")).status_code == 200


async def test_workflow_store_admin_takedown_hides_listing(client, make_admin):
    await register_and_login(client, "wftake")
    wf = await _create_workflow(client, "周报流水线", "每周写周报")
    published = await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})
    assert published.status_code == 200, published.text
    lid = published.json()["id"]

    await register_and_login(client, "wfinst")
    installed = await client.post(f"/v1/workflow-store/{lid}/install")
    assert installed.status_code == 200, installed.text
    copy_id = installed.json()["workflow_id"]

    admin_user, admin_pass = await make_admin()
    await login_admin(client, admin_user, admin_pass)
    listed = await client.get("/v1/admin/workflow-store/listings")
    assert listed.status_code == 200, listed.text
    assert any(row["id"] == lid for row in listed.json()["data"])

    takedown = await client.post(f"/v1/admin/workflow-store/listings/{lid}/takedown")
    assert takedown.status_code == 200, takedown.text
    assert takedown.json()["status"] == "taken_down"

    await _login(client, "wfinst")
    assert (await client.get(f"/v1/workflow-store/{lid}")).status_code == 404
    public = await client.get("/v1/workflow-store")
    assert all(row["id"] != lid for row in public.json()["data"])
    assert (await client.get(f"/v1/workflows/{copy_id}")).status_code == 200

    await _login(client, "wftake")
    blocked = await client.post(f"/v1/workflow-store/{lid}/versions")
    assert blocked.status_code == 403
    blocked_pub = await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})
    assert blocked_pub.status_code == 403


async def test_workflow_store_publish_rejects_ineligible(client):
    await register_and_login(client, "wfbad")
    empty_desc = await _create_workflow(client, "空说明", None)
    r = await client.post("/v1/workflow-store", json={"workflow_id": empty_desc["id"]})
    assert r.status_code == 400

    blank = await _create_workflow(client, "空白说明", "   ")
    r = await client.post("/v1/workflow-store", json={"workflow_id": blank["id"]})
    assert r.status_code == 400

    missing = await client.post(
        "/v1/workflow-store", json={"workflow_id": "00000000-0000-0000-0000-000000000001"}
    )
    assert missing.status_code == 404


async def test_workflow_store_admin_sees_reports(client, make_admin):
    await register_and_login(client, "wfrep")
    wf = await _create_workflow(client, "周报流水线", "每周写周报")
    lid = (await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})).json()[
        "id"
    ]
    await register_and_login(client, "wfflag")
    r = await client.post(f"/v1/workflow-store/{lid}/reports", json={"reason": "垃圾"})
    assert r.status_code == 200, r.text

    admin_user, admin_pass = await make_admin()
    await login_admin(client, admin_user, admin_pass)
    reports = await client.get("/v1/admin/workflow-store/reports")
    assert reports.status_code == 200, reports.text
    assert any(
        row["listing_id"] == lid and row["reason"] == "垃圾" for row in reports.json()["data"]
    )
    body = await client.get(f"/v1/admin/workflow-store/listings/{lid}")
    assert body.status_code == 200, body.text
    assert body.json()["definition"]["nodes"][0]["id"] == "research"
    assert body.json()["id"] == lid


async def test_workflow_store_install_does_not_write_turn_source(client):
    await register_and_login(client, "wfsrc")
    wf = await _create_workflow(client, "固化样", "不要骗抽槽")
    lid = (await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})).json()[
        "id"
    ]
    await register_and_login(client, "wfcopy")
    installed = await client.post(f"/v1/workflow-store/{lid}/install")
    copy = (await client.get(f"/v1/workflows/{installed.json()['workflow_id']}")).json()
    assert copy["source"] is None
    assert (copy.get("source") or {}).get("kind") != "turn"


async def test_delete_installed_workflow_copy_clears_shelf_and_allows_reinstall(client):
    await register_and_login(client, "wfdelauthor")
    wf = await _create_workflow(client, "周报流水线", "每周写周报")
    lid = (await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})).json()[
        "id"
    ]

    await register_and_login(client, "wfdelbuyer")
    installed = await client.post(f"/v1/workflow-store/{lid}/install")
    assert installed.status_code == 200, installed.text
    copy_id = installed.json()["workflow_id"]

    await _login(client, "wfdelauthor")
    patched = await client.patch(
        f"/v1/workflows/{wf['id']}",
        json={
            "name": "周报流水线 v2",
            "description": "每周写周报",
            "definition": {
                "nodes": [
                    {
                        "id": "write",
                        "kind": "agent_step",
                        "role": "写手",
                        "task": "写新版周报",
                    }
                ],
                "edges": [],
            },
        },
    )
    assert patched.status_code == 200, patched.text
    v2 = await client.post("/v1/workflow-store", json={"workflow_id": wf["id"]})
    assert v2.status_code == 200, v2.text
    assert v2.json()["version_n"] == 2

    await _login(client, "wfdelbuyer")
    stale = await client.get("/v1/workflow-store")
    row = next(r for r in stale.json()["data"] if r["id"] == lid)
    assert row["installed"] is True
    assert row["has_update"] is True

    gone = await client.delete(f"/v1/workflows/{copy_id}")
    assert gone.status_code == 200, gone.text

    shelf = await client.get("/v1/workflow-store")
    row = next(r for r in shelf.json()["data"] if r["id"] == lid)
    assert row["installed"] is False
    assert row["has_update"] is False
    detail = await client.get(f"/v1/workflow-store/{lid}")
    assert detail.json()["installed"] is False
    assert detail.json()["has_update"] is False
    installed_list = await client.get("/v1/workflow-store/installed")
    assert all(r["id"] != lid for r in installed_list.json()["data"])

    again = await client.post(f"/v1/workflow-store/{lid}/install")
    assert again.status_code == 200, again.text
    assert again.json()["installed"] is True
    assert again.json()["has_update"] is False
    assert again.json()["workflow_id"] != copy_id
