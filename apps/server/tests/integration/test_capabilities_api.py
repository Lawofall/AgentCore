"""End-to-end API integration tests for GET /v1/capabilities (能力图鉴).

Covers auth gating + the complete shape: the full tool catalog (CEO orchestration +
worker mutation, annotated with reach), the system Skills (summary + body), and the
CEO / worker system-prompt templates — the data the desktop 能力图鉴 renders.
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
    # Mutation + unified `run` are CEO+worker (solo CEO writes and executes).
    # `run` replaced code_execute / test_run / terminal — those names must stay gone.
    for name in ("write", "run"):
        assert name in tools
        assert set(tools[name]["available_to"]) == {"ceo", "worker"}
    for retired in ("code_execute", "test_run", "terminal"):
        assert retired not in tools
    assert "escalate" in tools
    assert tools["escalate"]["available_to"] == ["worker"]
    assert "update_folder_profile" not in tools
    assert "remember" not in tools
    assert "code_search" not in tools
    assert "project_shell" not in tools
    # Shared read/retrieval built-ins.
    for name in ("web_search",):
        assert name in tools
        assert set(tools[name]["available_to"]) == {"ceo", "worker"}
    # Each tool carries its call JSON Schema (用法教学) plus catalog face/resident/summary.
    assert tools["web_search"]["parameters"]["type"] == "object"
    sample = tools["web_search"]
    assert "category" not in sample
    assert sample["face"]
    assert isinstance(sample["resident"], bool)
    assert "summary" in sample
    assert sample["blurb"]
    assert sample["blurb"] != sample["summary"]


async def test_capabilities_lists_system_skills_with_body(client):
    await register_and_login(client, "skilluser")

    body = (await client.get("/v1/capabilities")).json()
    skills = {s["name"]: s for s in body["skills"]}
    assert "page_ui" in skills
    assert skills["page_ui"]["group"] == "交付"
    assert set(skills["page_ui"]["audience"]) == {"ceo", "worker"}
    for skill in skills.values():
        assert skill["summary"]
        assert skill["body"]  # the full guidance, not just the catalog one-liner
        assert skill["blurb"]
        assert skill["blurb"] != skill["summary"]
        assert "requires_tools" in skill
        assert isinstance(skill["requires_tools"], list)
    assert skills["page_ui"]["requires_tools"] == []
    assert "packs" not in body


async def test_capabilities_exposes_prompt_template(client):
    await register_and_login(client, "promptuser")

    guidelines = (await client.get("/v1/capabilities")).json()["guidelines"]
    assert guidelines["shared_base"] == ""
    ceo = guidelines["ceo"]
    addon = guidelines["ceo_addon"]
    # The CEO template carries the always-on 按需目录; factory identity is empty.
    assert "<身份>" not in ceo
    assert "按需目录" in ceo
    # The shared base is a prefix of the CEO prompt (it layers hints onto the base).
    assert ceo.startswith(guidelines["shared_base"])
    # ceo_addon is the catalog delta (no repeated shared-base sections).
    assert addon
    assert "<身份>" not in addon
    assert "<按需目录>" in addon
    assert addon == ceo[len(guidelines["shared_base"]) :].lstrip("\n")
    assert ceo == guidelines["shared_base"] + ceo[len(guidelines["shared_base"]) :]
    # Worker catalog: factory identity empty. Nest-cap is live-only.
    leaf = guidelines["worker_leaf"]
    captain = guidelines["worker_captain"]
    assert leaf == ""
    assert captain == ""
    assert "你的子成员" not in leaf
    assert "你的子成员" not in captain
    # Catalog is not form HOW (per-turn 交付物规格).
    for body in (leaf, captain):
        assert "form=files" not in body
        assert "form=prose" not in body
        assert "form=workspace" not in body
        assert "<身份>" not in body
