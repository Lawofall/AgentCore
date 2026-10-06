"""GET /v1/skill-catalog — 我的技能 + 官方槽名册。"""

from tests.integration.conftest import register_and_login


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


async def test_skill_catalog_requires_auth(client):
    assert (await client.get("/v1/skill-catalog")).status_code == 401


async def test_skill_catalog_empty_mine_lists_official_slots(client):
    await register_and_login(client, "skcat")
    r = await client.get("/v1/skill-catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mine"] == []
    slots = {s["name"]: s for s in body["slots"]}
    assert "product_help" in slots
    assert "summary" in slots["product_help"]
    assert "replaced_by" not in slots["product_help"]
    assert "home_parent_id" not in slots["product_help"]
    assert "muted" not in slots["product_help"]


async def test_skill_catalog_lists_mine(client):
    await register_and_login(client, "skmine")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )
    r = await client.get("/v1/skill-catalog")
    assert r.status_code == 200, r.text
    mine = next(m for m in r.json()["mine"] if m["id"] == doc["id"])
    assert mine["name"] == "合同审查"
    assert "occupies" not in mine


async def test_skill_catalog_write_routes_gone(client):
    await register_and_login(client, "sknowrite")
    put_body = {"document_id": "00000000-0000-0000-0000-000000000001"}
    for method, path in (
        ("PUT", "/v1/skill-catalog/replacements/product_help"),
        ("DELETE", "/v1/skill-catalog/replacements/product_help"),
        ("PUT", "/v1/skill-catalog/homes/product_help"),
        ("DELETE", "/v1/skill-catalog/homes/product_help"),
        ("PUT", "/v1/skill-catalog/mutes/product_help"),
        ("DELETE", "/v1/skill-catalog/mutes/product_help"),
    ):
        r = await client.request(method, path, json=put_body if method == "PUT" else None)
        assert r.status_code == 404, path


async def test_account_rules_list_has_no_skill_replacements(client):
    await register_and_login(client, "skacc")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )

    listed = await client.post("/v1/account/rules/list", json={})
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert "skill_replacements" not in payload
    names = [d["name"] for d in payload["global_on_demand_rules"]]
    assert "合同审查.md" in names
    assert doc["id"]
    assert "skill_mutes" not in payload
    assert "skill_homes" not in payload
