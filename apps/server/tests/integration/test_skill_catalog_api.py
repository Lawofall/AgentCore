"""GET/PUT/DELETE /v1/skill-catalog — 我的技能 + 账号级换用 / 藏起."""

from tests.integration.conftest import TEST_PASSWORD, register_and_login


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
    assert slots["product_help"]["replaced_by"] is None
    assert slots["product_help"]["muted"] is False


async def test_replace_and_restore_official_slot(client):
    await register_and_login(client, "skrep")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )

    r = await client.put(
        "/v1/skill-catalog/replacements/product_help",
        json={"document_id": doc["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    slot = next(s for s in body["slots"] if s["name"] == "product_help")
    assert slot["replaced_by"]["document_id"] == doc["id"]
    assert slot["replaced_by"]["name"] == "合同审查"
    mine = next(m for m in body["mine"] if m["id"] == doc["id"])
    assert mine["occupies"] == ["product_help"]

    r = await client.delete("/v1/skill-catalog/replacements/product_help")
    assert r.status_code == 200, r.text
    slot = next(s for s in r.json()["slots"] if s["name"] == "product_help")
    assert slot["replaced_by"] is None


async def test_replace_rejects_unknown_slot_and_always_rule(client):
    await register_and_login(client, "skbad")
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

    r = await client.put(
        "/v1/skill-catalog/replacements/not_a_skill",
        json={"document_id": always.json()["id"]},
    )
    assert r.status_code == 400
    assert (await client.put("/v1/skill-catalog/mutes/not_a_skill")).status_code == 400

    r = await client.put(
        "/v1/skill-catalog/replacements/product_help",
        json={"document_id": always.json()["id"]},
    )
    assert r.status_code == 400


async def test_account_rules_list_carries_replacements_and_skips_bound_name(client):
    await register_and_login(client, "skacc")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )
    r = await client.put(
        "/v1/skill-catalog/replacements/product_help",
        json={"document_id": doc["id"]},
    )
    assert r.status_code == 200, r.text

    listed = await client.post("/v1/account/rules/list", json={})
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    repl = payload["skill_replacements"]
    assert len(repl) == 1
    assert repl[0]["slot"] == "product_help"
    assert repl[0]["document_name"] == "合同审查"
    assert "怎么审合同" in repl[0]["content"]
    names = [d["name"] for d in payload["global_on_demand_rules"]]
    assert "合同审查.md" not in names


async def test_folder_overlay_nearer_wins_and_restore_inherits(client):
    await register_and_login(client, "skfold")
    doc_account = await _create_on_demand(
        client, "账号审查.md", "账号 HOW", description="账号触发"
    )
    doc_folder = await _create_on_demand(
        client, "本夹审查.md", "本夹 HOW", description="本夹触发"
    )
    folder = await client.post("/v1/folders", json={"name": "项目桌", "mode": "cloud"})
    assert folder.status_code == 201, folder.text
    fid = folder.json()["id"]

    r = await client.put(
        "/v1/skill-catalog/replacements/product_help",
        json={"document_id": doc_account["id"]},
    )
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/v1/skill-catalog/replacements/product_help?folder_id={fid}",
        json={"document_id": doc_folder["id"]},
    )
    assert r.status_code == 200, r.text
    slot = next(s for s in r.json()["slots"] if s["name"] == "product_help")
    assert slot["replaced_by"]["name"] == "本夹审查"
    assert slot["replaced_layer"] == "here"
    assert r.json()["folder_id"] == fid
    assert r.json()["writable"] is True

    listed = await client.post("/v1/account/rules/list", json={"folder_id": fid})
    assert listed.status_code == 200, listed.text
    assert listed.json()["skill_replacements"][0]["document_name"] == "本夹审查"

    r = await client.delete(
        f"/v1/skill-catalog/replacements/product_help?folder_id={fid}"
    )
    assert r.status_code == 200, r.text
    slot = next(s for s in r.json()["slots"] if s["name"] == "product_help")
    assert slot["replaced_by"]["name"] == "账号审查"
    assert slot["replaced_layer"] == "inherited"

    listed = await client.post("/v1/account/rules/list", json={"folder_id": fid})
    assert listed.json()["skill_replacements"][0]["document_name"] == "账号审查"


async def test_folder_mute_inherits_account_mute(client):
    await register_and_login(client, "skfoldm")
    folder = await client.post("/v1/folders", json={"name": "项目桌", "mode": "cloud"})
    assert folder.status_code == 201, folder.text
    fid = folder.json()["id"]

    r = await client.put("/v1/skill-catalog/mutes/product_help")
    assert r.status_code == 200, r.text
    r = await client.get(f"/v1/skill-catalog?folder_id={fid}")
    assert r.status_code == 200, r.text
    slot = next(s for s in r.json()["slots"] if s["name"] == "product_help")
    assert slot["muted"] is True
    assert slot["muted_layer"] == "inherited"

    r = await client.delete(f"/v1/skill-catalog/mutes/product_help?folder_id={fid}")
    slot = next(s for s in r.json()["slots"] if s["name"] == "product_help")
    assert slot["muted"] is True
    assert slot["muted_layer"] == "inherited"


async def test_mute_and_unmute_official_slot(client):
    await register_and_login(client, "skmute")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )
    r = await client.put(
        "/v1/skill-catalog/replacements/product_help",
        json={"document_id": doc["id"]},
    )
    assert r.status_code == 200, r.text

    r = await client.put("/v1/skill-catalog/mutes/product_help")
    assert r.status_code == 200, r.text
    slot = next(s for s in r.json()["slots"] if s["name"] == "product_help")
    assert slot["muted"] is True
    assert slot["replaced_by"]["document_id"] == doc["id"]

    listed = await client.post("/v1/account/rules/list", json={})
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert "product_help" in payload["skill_mutes"]
    assert payload["skill_replacements"][0]["slot"] == "product_help"

    r = await client.delete("/v1/skill-catalog/mutes/product_help")
    assert r.status_code == 200, r.text
    slot = next(s for s in r.json()["slots"] if s["name"] == "product_help")
    assert slot["muted"] is False
    assert slot["replaced_by"]["document_id"] == doc["id"]

    listed = await client.post("/v1/account/rules/list", json={})
    assert listed.json()["skill_mutes"] == []
    assert listed.json()["skill_replacements"][0]["slot"] == "product_help"


async def test_delete_account_clears_skill_overlay(client, session_factory):
    from sqlalchemy import select

    from agentcore.db.models.skill_slots import SkillSlotMute, SkillSlotReplacement

    user_id = await register_and_login(client, "skgone")
    doc = await _create_on_demand(
        client, "合同审查.md", "怎么审合同", description="审合同时用"
    )
    r = await client.put(
        "/v1/skill-catalog/replacements/product_help",
        json={"document_id": doc["id"]},
    )
    assert r.status_code == 200, r.text
    r = await client.put("/v1/skill-catalog/mutes/product_help")
    assert r.status_code == 200, r.text

    gone = await client.request(
        "DELETE", "/v1/auth/me", json={"password": TEST_PASSWORD}
    )
    assert gone.status_code == 200, gone.text

    async with session_factory() as session:
        replacements = list(
            (
                await session.execute(
                    select(SkillSlotReplacement).where(
                        SkillSlotReplacement.user_id == user_id
                    )
                )
            ).scalars().all()
        )
        mutes = list(
            (
                await session.execute(
                    select(SkillSlotMute).where(SkillSlotMute.user_id == user_id)
                )
            ).scalars().all()
        )
    assert replacements == []
    assert mutes == []
