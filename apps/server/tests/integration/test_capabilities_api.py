"""End-to-end API integration tests for GET /v1/capabilities (能力图鉴).

Covers auth gating + the complete shape: the full tool catalog (CEO orchestration +
worker mutation, annotated with reach), the system Skills (summary + body), and the CEO
system-prompt template — the data the desktop 能力图鉴 renders.
"""

from tests.integration.conftest import register_and_login


async def test_capabilities_requires_auth(client):
    assert (await client.get("/v1/capabilities")).status_code == 401


async def test_capabilities_returns_full_catalog(client):
    await register_and_login(client, "capuser")

    r = await client.get("/v1/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()

    tools = {t["name"]: t for t in body["tools"]}
    # The complete repertoire — CEO orchestration the old /v1/tools never served…
    for name in ("delegate", "replan", "debate", "ask_user"):
        assert name in tools
        assert tools[name]["available_to"] == ["ceo"]
    assert "revise" not in tools
    # consult is AUDIENCE_BOTH (步 1 · Skill 对 worker 放开).
    assert "consult" in tools
    assert set(tools["consult"]["available_to"]) == {"ceo", "worker"}
    # …worker-only mutation + execution + escalate (test_run runs project code through
    # the same sandbox chain as code_execute, so it is worker-only, not a CEO read tool)…
    for name in ("file_write", "code_execute", "test_run", "escalate"):
        assert name in tools
        assert tools[name]["available_to"] == ["worker"]
    # …and shared read/retrieval built-ins.
    for name in ("web_search",):
        assert name in tools
        assert set(tools[name]["available_to"]) == {"ceo", "worker"}
    # Each tool carries its call JSON Schema (用法教学).
    assert tools["web_search"]["parameters"]["type"] == "object"


async def test_capabilities_lists_system_skills_with_body(client):
    await register_and_login(client, "skilluser")

    body = (await client.get("/v1/capabilities")).json()
    skills = {s["name"]: s for s in body["skills"]}
    assert "team_orchestration_advanced" in skills
    assert "asking_the_user" in skills
    assert "ask_user_kickoff" not in skills
    assert "verify_and_fix" not in skills
    assert "long_form_writing" in skills
    assert "long_form_landing" in skills
    for skill in skills.values():
        assert skill["summary"]
        assert skill["body"]  # the full guidance, not just the catalog one-liner
    # New packs[] field: additive; gate-off default ⇒ empty (desktop ignores unknown fields).
    assert body["packs"] == []


async def test_capabilities_exposes_prompt_template(client):
    await register_and_login(client, "promptuser")

    guidelines = (await client.get("/v1/capabilities")).json()["guidelines"]
    assert guidelines["shared_base"]
    ceo = guidelines["ceo"]
    addon = guidelines["ceo_addon"]
    # The CEO template carries the routing core + the always-on 按需目录.
    assert "CEO" in ceo
    assert "按需目录" in ceo
    # The shared base is a prefix of the CEO prompt (it layers hints onto the base).
    assert ceo.startswith(guidelines["shared_base"])
    # ceo_addon is the catalog delta (no repeated shared-base sections).
    assert addon
    assert "CEO" in addon
    assert addon == ceo[len(guidelines["shared_base"]) :].lstrip("\n")
    assert ceo == guidelines["shared_base"] + ceo[len(guidelines["shared_base"]) :]
