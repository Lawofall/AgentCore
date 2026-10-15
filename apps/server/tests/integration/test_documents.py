"""Document subsystem integration tests.

Against a real PG schema: the tree CRUD API, owner-scoping, and user-rule documents.
Auto-skips when PostgreSQL is unavailable (integration conftest).
"""

import uuid

from agentcore.db.models import Folder
from agentcore.db.repositories import DocumentRepository
from agentcore.memory import DocumentMemoryStore, assemble_injected_rules
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    topic_path,
)
from tests.integration.conftest import register_and_login

# --- Tree CRUD API ---------------------------------------------------------------------------


async def test_document_tree_crud_roundtrip(client):
    await register_and_login(client, "docu1")

    # A folder node (user-owned → ai_maintained forced false).
    r = await client.post("/v1/documents", json={"name": "规则集", "kind": "folder"})
    assert r.status_code == 200, r.text
    folder = r.json()
    assert folder["kind"] == "folder" and folder["ai_maintained"] is False

    # A rule document under it (a user rule = role rule, ai_maintained false).
    r = await client.post(
        "/v1/documents",
        json={
            "name": "规则1.md",
            "kind": "document",
            "role": "rule",
            "content": "- 必须用中文",
            "parent_id": folder["id"],
        },
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["role"] == "rule" and doc["parent_id"] == folder["id"]
    version = doc["version"]

    # Listing the folder's children surfaces exactly the new doc.
    r = await client.get(f"/v1/documents?parent_id={folder['id']}")
    assert [n["id"] for n in r.json()] == [doc["id"]]

    # Body reads back carrying its frontmatter (the entry's sole writable source).
    fetched = (await client.get(f"/v1/documents/{doc['id']}")).json()
    assert fetched["content"] == "---\napply: always\n---\n- 必须用中文"
    assert fetched["apply_mode"] == "always"
    r = await client.put(
        f"/v1/documents/{doc['id']}", json={"content": "clobber", "baseline": "stale"}
    )
    assert r.json()["ok"] is False and r.json()["conflict"] is True
    r = await client.put(
        f"/v1/documents/{doc['id']}",
        json={"content": "- 必须用中文\n- 别用表格", "baseline": version},
    )
    assert r.json()["ok"] is True

    # Rename, then delete the folder — the subtree cascades.
    r = await client.patch(f"/v1/documents/{doc['id']}", json={"name": "规则1改.md"})
    assert r.json()["name"] == "规则1改.md"
    r = await client.delete(f"/v1/documents/{folder['id']}")
    assert r.json()["ok"] is True
    assert (await client.get(f"/v1/documents/{doc['id']}")).status_code == 404
    assert (await client.get(f"/v1/documents/{folder['id']}")).status_code == 404


async def test_documents_are_owner_scoped(client, new_client):
    await register_and_login(client, "docu2a")
    r = await client.post("/v1/documents", json={"name": "私密.md", "role": "rule", "content": "x"})
    doc_id = r.json()["id"]
    async with new_client() as other:
        await register_and_login(other, "docu2b")
        assert (await other.get(f"/v1/documents/{doc_id}")).status_code == 404
        assert (await other.delete(f"/v1/documents/{doc_id}")).status_code == 404


async def test_documents_require_auth(client):
    assert (await client.get("/v1/documents")).status_code == 401
    assert (await client.post("/v1/documents", json={"name": "x"})).status_code == 401


# --- User-rule injection (equal-authority always-on join) ------------------------------------


async def test_on_demand_user_rule_excluded_from_injected_rules(session_factory):
    """A description enrolls the catalog. Neither row enters ``<设定>``."""
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        await repo.create(
            uid,
            name="常驻.md",
            role="rule",
            ai_maintained=False,
            apply_mode="always",
            content="- always 规则",
        )
        await repo.create(
            uid,
            name="按需.md",
            role="rule",
            ai_maintained=False,
            apply_mode="on_demand",
            content="---\ndescription: 查阅按需\n---\n- on_demand 规则",
        )
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=None)
        on_demand = await repo.list_on_demand_user_rules(uid, None)
    assert rules_md == ""
    assert {d.name for d in on_demand} == {"按需.md"}


async def test_user_rule_injects_without_ai_notes(session_factory):
    """User always-rules enter ``<设定>``; AI-maintained 偏好 / 画像 stay on disk."""
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        await repo.create(
            uid,
            name="用户规则.md",
            role="rule",
            ai_maintained=False,
            apply_mode="on_demand",
            content="---\ndescription: 回复语言\n---\n- 必须始终用中文",
        )
        await store.save(uid, PREFERENCES_MEMORY_FILE, "## 沟通偏好\n- 倾向简洁", scope=None)
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- 用 Python", scope=None)
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=None)
        listed = await repo.list_on_demand_user_rules(uid, None)
        # Keep loads on the live session: using the bound store after this
        # block returns leaves a checkout that blocks DROP SCHEMA in teardown.
        prefs = await store.load(uid, PREFERENCES_MEMORY_FILE)
        core = await store.load(uid, CORE_MEMORY_FILE)
    assert rules_md == ""
    assert any("必须始终用中文" in (d.content or "") for d in listed)
    assert "用 Python" not in rules_md
    assert "倾向简洁" not in rules_md
    assert "倾向简洁" in prefs
    assert "用 Python" in core


async def test_user_rule_survives_when_ai_notes_exist(session_factory):
    # AI notes on disk must not enter ``<设定>``; the user's OWN rule still injects.
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        await repo.create(
            uid,
            name="用户规则.md",
            role="rule",
            apply_mode="on_demand",
            content="---\ndescription: 回复语言\n---\n- 必须用中文",
        )
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- 用 Python", scope=None)
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=None)
        listed = await repo.list_on_demand_user_rules(uid, None)
    assert rules_md == ""
    assert any("必须用中文" in (d.content or "") for d in listed)
    assert "用 Python" not in rules_md


async def test_injection_admits_global_and_project_rules(session_factory):
    # Different consult names both inject (global, then this folder). The same
    # name is nearest-only and is covered by the inheritance unit tests.
    uid = str(uuid.uuid4())
    proj = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(Folder(id=proj, user_id=uid, name="proj", rel_path="proj"))
        await session.flush()
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        await repo.create(
            uid,
            name="全局约定.md",
            role="rule",
            apply_mode="on_demand",
            content="---\ndescription: 全局\n---\n全局规则",
        )
        await repo.create(
            uid,
            name="用户规则.md",
            role="rule",
            folder_id=proj,
            apply_mode="on_demand",
            content="---\ndescription: 项目\n---\n项目规则",
        )
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=proj)
        global_rules = await repo.list_on_demand_user_rules(uid, None)
        project_rules = await repo.list_on_demand_user_rules(uid, proj)
    assert rules_md == ""
    assert any("全局规则" in (d.content or "") for d in global_rules)
    assert any("项目规则" in (d.content or "") for d in project_rules)



# --- AgentCore/ convention layout (§5.0) ------------------------------------------------------


async def test_new_writes_land_under_agentcore(session_factory):
    """Memory notes stay under the convention root. User rules are ordinary documents."""
    from agentcore.db.repositories.documents import (
        AGENTCORE_ROOT_NAME,
        MEMORY_ROOT_NAME,
    )

    uid = str(uuid.uuid4())
    async with session_factory() as session:
        store = DocumentMemoryStore(session=session)
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Python", scope=None)
        repo = DocumentRepository(session)
        await repo.upsert_user_rule_doc(uid, None, "必须用中文.md", "- 必须用中文")

        mem_root = await repo.get_memory_root(uid, None)
        assert mem_root is not None
        ac = await repo.get(mem_root.parent_id, user_id=uid)
        assert ac is not None and ac.name == AGENTCORE_ROOT_NAME and ac.parent_id is None

        rule = await repo.get_user_rule_doc(uid, None, "必须用中文.md")
        assert rule is not None and rule.parent_id is None
        assert rule.name == "必须用中文.md"

        note = await repo.get_memory_note(uid, CORE_MEMORY_FILE, None)
        assert note is not None and note.parent_id == mem_root.id
        assert mem_root.name == MEMORY_ROOT_NAME


async def test_user_rule_is_found_by_name_without_a_parent_folder(session_factory):
    """A markdown rule is the document itself. No rules folder is created or required."""
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        await repo.upsert_user_rule_doc(uid, None, "语气.md", "- 简洁")
        rule = await repo.get_user_rule_doc(uid, None, "语气.md")
        assert rule is not None and rule.parent_id is None

        stray = await repo.create(
            uid,
            name="漏网规则.md",
            role="rule",
            content="- 也是一篇",
            parent_id=None,
            apply_mode="always",
        )
        docs = await repo.list_injectable_rules(uid, None, ai_maintained=False)
        assert stray.id in {d.id for d in docs}


async def test_create_rule_api_does_not_parent_under_a_convention_folder(client):
    await register_and_login(client, "acrule")
    r = await client.post(
        "/v1/documents",
        json={"name": "新规则.md", "kind": "document", "role": "rule", "content": "x"},
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["role"] == "rule" and doc["parent_id"] is None


async def test_user_rule_apply_mode_always_on_demand_and_reject_conditional(
    client, session_factory
):
    """定案 B: create/PATCH always|on_demand; conditional 422; on_demand not injectable."""
    uid = await register_and_login(client, "apmode")

    r = await client.post(
        "/v1/documents",
        json={
            "name": "合规附录.md",
            "role": "rule",
            "content": "- 对外须用中文",
            "apply_mode": "on_demand",
        },
    )
    assert r.status_code == 200, r.text
    on_demand = r.json()
    assert on_demand["apply_mode"] == "on_demand"

    r = await client.post(
        "/v1/documents",
        json={
            "name": "常驻规则.md",
            "role": "rule",
            "content": "- 必须写测试",
            "apply_mode": "always",
        },
    )
    assert r.status_code == 200, r.text
    always = r.json()
    assert always["apply_mode"] == "always"

    r = await client.post(
        "/v1/documents",
        json={
            "name": "条件规则.md",
            "role": "rule",
            "content": "x",
            "apply_mode": "conditional",
        },
    )
    assert r.status_code == 422

    r = await client.patch(f"/v1/documents/{always['id']}", json={"apply_mode": "on_demand"})
    assert r.status_code == 200, r.text
    assert r.json()["apply_mode"] == "on_demand"

    r = await client.patch(f"/v1/documents/{on_demand['id']}", json={"apply_mode": "always"})
    assert r.status_code == 200
    assert r.json()["apply_mode"] == "always"

    # Flip back for injectable check: one always + one on_demand.
    await client.patch(f"/v1/documents/{on_demand['id']}", json={"apply_mode": "on_demand"})
    await client.patch(f"/v1/documents/{always['id']}", json={"apply_mode": "always"})

    async with session_factory() as session:
        repo = DocumentRepository(session)
        injectable = await repo.list_injectable_rules(uid, None, ai_maintained=False)
        names = {d.name for d in injectable}
        assert "常驻规则.md" in names
        assert "合规附录.md" not in names
        on_demand_docs = await repo.list_on_demand_user_rules(uid, None)
        assert {d.name for d in on_demand_docs} == {"合规附录.md"}


# --- AI memory write guards (API) ------------------------------------------------------------


async def test_delete_ai_core_memory_leaf_rejected(client, session_factory):
    """DELETE of AI-maintained 画像/偏好/导航 is refused; topic leaves stay deletable."""
    uid = await register_and_login(client, "aicoredel")

    async with session_factory() as session:
        store = DocumentMemoryStore(session=session)
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Python", scope=None)
        await store.save(uid, topic_path("部署"), "## 要点\n- 先构建", scope=None)
        core = await DocumentRepository(session).get_memory_note(uid, CORE_MEMORY_FILE, None)
        topic = await DocumentRepository(session).get_memory_note(uid, topic_path("部署"), None)
        assert core is not None and topic is not None
        core_id, topic_id = core.id, topic.id

    r = await client.delete(f"/v1/documents/{core_id}")
    assert r.status_code == 400, r.text
    assert "core memory" in r.json()["detail"]

    r = await client.get(f"/v1/documents/{core_id}")
    assert r.status_code == 200

    r = await client.delete(f"/v1/documents/{topic_id}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r = await client.get(f"/v1/documents/{topic_id}")
    assert r.status_code == 404


async def test_patch_apply_mode_ai_maintained_rejected(client, session_factory):
    """PATCH apply_mode on any AI-maintained entry is refused (cores + topics)."""
    uid = await register_and_login(client, "aiapply")

    async with session_factory() as session:
        store = DocumentMemoryStore(session=session)
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Python", scope=None)
        await store.save(uid, topic_path("部署"), "## 要点\n- 先构建", scope=None)
        core = await DocumentRepository(session).get_memory_note(uid, CORE_MEMORY_FILE, None)
        topic = await DocumentRepository(session).get_memory_note(uid, topic_path("部署"), None)
        assert core is not None and topic is not None
        core_id, topic_id = core.id, topic.id

    for doc_id in (core_id, topic_id):
        r = await client.patch(f"/v1/documents/{doc_id}", json={"apply_mode": "on_demand"})
        assert r.status_code == 400, r.text
        assert "AI-maintained" in r.json()["detail"]
