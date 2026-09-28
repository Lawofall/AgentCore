"""Document 子系统第一期 integration tests (Agent记忆与知识系统 §5.7).

Against a real PG schema: the tree CRUD API, owner-scoping, user-rule injection (two-tier,
read-side full injection), the ``write`` ``.agentcore/rules/`` overlay, and the one-time
file→document migration (idempotent, non-clobbering). Auto-skips when PostgreSQL is
unavailable (integration conftest).
"""

import uuid
from pathlib import Path

from agentcore.db.models import Folder
from agentcore.db.repositories import DocumentRepository
from agentcore.documents.frontmatter import set_entry_frontmatter
from agentcore.memory import DocumentMemoryStore, assemble_injected_rules
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    topic_path,
)
from agentcore.tools.builtin.file_ops import FileWriteTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
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
    """on_demand user rules never enter the always ``<设定>`` budget/compose path."""
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
            content="- on_demand 规则",
        )
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=None)
        on_demand = await repo.list_on_demand_user_rules(uid, None)
    assert "always 规则" in rules_md
    assert "on_demand 规则" not in rules_md
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
            apply_mode="always",
            content="- 必须始终用中文",
        )
        await store.save(uid, PREFERENCES_MEMORY_FILE, "## 沟通偏好\n- 倾向简洁", scope=None)
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- 用 Python", scope=None)
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=None)
        # Keep loads on the live session: using the bound store after this
        # block returns leaves a checkout that blocks DROP SCHEMA in teardown.
        prefs = await store.load(uid, PREFERENCES_MEMORY_FILE)
        core = await store.load(uid, CORE_MEMORY_FILE)
    assert "必须始终用中文" in rules_md
    assert "### .agentcore/rules/用户规则.md" in rules_md
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
            uid, name="用户规则.md", role="rule", apply_mode="always", content="- 必须用中文"
        )
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- 用 Python", scope=None)
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=None)
    assert "必须用中文" in rules_md
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
            uid, name="全局约定.md", role="rule", apply_mode="always", content="全局规则"
        )
        await repo.create(
            uid,
            name="用户规则.md",
            role="rule",
            folder_id=proj,
            apply_mode="always",
            content="项目规则",
        )
        rules_md = await assemble_injected_rules(store, repo, uid, folder_id=proj)
    assert "全局规则" in rules_md
    assert "项目规则" in rules_md
    assert rules_md.index("全局规则") < rules_md.index("项目规则")


# --- write .agentcore/rules → user rule ----------------------------------------------------------


def _ctx(user_id: str) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id,
        conversation_id="",
    )


async def test_file_write_rule_writes_user_rule_and_dedupes(session_factory, monkeypatch):
    from agentcore.tools.builtin.file_ops import user_rules as overlay

    monkeypatch.setattr(overlay, "async_session_factory", session_factory)
    monkeypatch.setattr(
        "agentcore.account.credentials.get_account_credentials", lambda: None
    )
    uid = str(uuid.uuid4())

    res = await FileWriteTool().execute(
        {
            "file_path": ".agentcore/rules/回复语言.md",
            "content": "以后都用中文",
        },
        _ctx(uid),
    )
    assert res.success is True
    assert "已写入" in (res.output or "")

    res2 = await FileWriteTool().execute(
        {
            "file_path": ".agentcore/rules/回复语言.md",
            "content": "以后都用中文",
        },
        _ctx(uid),
    )
    assert res2.success is True
    assert (res2.metadata or {}).get("already_applied") is True

    async with session_factory() as session:
        docs = await DocumentRepository(session).list_user_rule_docs(uid, None)
    assert any("以后都用中文" in d.content and d.ai_maintained is False for d in docs)


# --- one-time file→document migration --------------------------------------------------------





# --- AgentCore/ convention layout (§5.0) ------------------------------------------------------


async def test_new_writes_land_under_agentcore(session_factory):
    """Memory notes + user-rule writes land under AgentCore/{记忆,rules}/."""
    from agentcore.db.repositories.documents import (
        AGENTCORE_ROOT_NAME,
        MEMORY_ROOT_NAME,
        RULES_DIR_NAME,
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

        rules_dir = await repo.get_rules_dir(uid, None)
        assert rules_dir is not None and rules_dir.name == RULES_DIR_NAME
        assert rules_dir.parent_id == ac.id
        rule = await repo.get_user_rule_doc(uid, None, "必须用中文.md")
        assert rule is not None and rule.parent_id == rules_dir.id
        assert rule.name == "必须用中文.md"

        note = await repo.get_memory_note(uid, CORE_MEMORY_FILE, None)
        assert note is not None and note.parent_id == mem_root.id
        assert mem_root.name == MEMORY_ROOT_NAME


async def test_legacy_rules_dir_renamed_on_read(session_factory):
    """Leftover ``规则/`` parent is renamed to ``rules/``; children stay injectable."""
    from agentcore.db.repositories.documents import (
        LEGACY_RULES_DIR_NAME,
        RULES_DIR_NAME,
    )

    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        ac = await repo.ensure_agentcore_root(uid, None)
        legacy = await repo.create(
            uid,
            name=LEGACY_RULES_DIR_NAME,
            kind="folder",
            role="general",
            parent_id=ac.id,
        )
        await repo.create(
            uid,
            name="语气.md",
            kind="document",
            role="rule",
            parent_id=legacy.id,
            content="- 简洁",
        )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        rules_dir = await repo.get_rules_dir(uid, None)
        assert rules_dir is not None and rules_dir.name == RULES_DIR_NAME
        rule = await repo.get_user_rule_doc(uid, None, "语气.md")
        assert rule is not None and rule.parent_id == rules_dir.id




async def test_injectable_rules_skip_stray_outside_convention(session_factory):
    """With AgentCore/rules/ present, a top-level stray always-rule is not injectable."""

    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        await repo.upsert_user_rule_doc(uid, None, "必须用中文.md", "- 必须用中文")
        stray = await repo.create(
            uid,
            name="漏网规则.md",
            role="rule",
            content="- 不该注入",
            parent_id=None,
            apply_mode="always",
        )
        stray_id = stray.id
        rules_dir = await repo.get_rules_dir(uid, None)
        assert rules_dir is not None

        docs = await repo.list_injectable_rules(uid, None, ai_maintained=False)
        ids = {d.id for d in docs}
        assert stray_id not in ids
        assert any(d.name == "必须用中文.md" for d in docs)
        assert all(d.parent_id == rules_dir.id for d in docs)



async def test_create_rule_api_auto_parents_under_agentcore(client):
    await register_and_login(client, "acrule")
    r = await client.post(
        "/v1/documents",
        json={"name": "新规则.md", "kind": "document", "role": "rule", "content": "x"},
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["role"] == "rule" and doc["parent_id"] is not None
    assert doc["apply_mode"] == "always"

    r = await client.get(f"/v1/documents/{doc['parent_id']}")
    assert r.status_code == 200
    rules_dir = r.json()
    assert rules_dir["name"] == "rules" and rules_dir["kind"] == "folder"

    r = await client.get(f"/v1/documents/{rules_dir['parent_id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "AgentCore"


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


async def test_apply_description_if_empty_column_only_preserves_content(session_factory):
    """AI fill writes the column only; content (CAS) and user FM description stay intact."""
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        doc = await repo.create(
            uid,
            name="条目.md",
            role="rule",
            apply_mode="on_demand",
            content="- 部署约定与回滚步骤",
        )
        assert (doc.description or "") == ""
        content_before = doc.content
        filled = await repo.apply_description_if_empty(
            doc.id, user_id=uid, description="部署与回滚"
        )
        assert filled is not None
        assert filled.description == "部署与回滚"
        assert filled.content == content_before
        assert "description:" not in filled.content

        again = await repo.apply_description_if_empty(doc.id, user_id=uid, description="不该覆盖")
        assert again is not None
        assert again.description == "部署与回滚"
        assert again.content == content_before

        # Body write with empty FM description clears stale AI column → fill again.
        refreshed = await repo.update_content(
            doc.id, user_id=uid, content="---\napply: on_demand\n---\n新正文\n"
        )
        assert refreshed is not None
        assert (refreshed.description or "") == ""
        content_after_edit = refreshed.content
        regen = await repo.apply_description_if_empty(
            doc.id, user_id=uid, description="清空后新摘要"
        )
        assert regen is not None
        assert regen.description == "清空后新摘要"
        assert regen.content == content_after_edit


async def test_apply_description_if_empty_respects_user_frontmatter(session_factory):
    """User-written frontmatter description is never overwritten by AI fill."""
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        body = set_entry_frontmatter(
            "---\napply: on_demand\n---\n手写条目\n", description="用户手写摘要"
        )
        doc = await repo.create(
            uid,
            name="手写.md",
            role="rule",
            apply_mode="on_demand",
            content=body,
        )
        assert doc.description == "用户手写摘要"
        content_before = doc.content
        skipped = await repo.apply_description_if_empty(
            doc.id, user_id=uid, description="AI不该覆盖"
        )
        assert skipped is not None
        assert skipped.description == "用户手写摘要"
        assert skipped.content == content_before
        assert "description: 用户手写摘要" in skipped.content


async def test_apply_description_if_empty_skips_stale_content(session_factory):
    """Fill generated against an older body must not land after a later save."""
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        doc = await repo.create(
            uid,
            name="竞态.md",
            role="rule",
            apply_mode="on_demand",
            content="- v1 正文",
        )
        old_content = doc.content
        updated = await repo.update_content(
            doc.id, user_id=uid, content="---\napply: on_demand\n---\n- v2 正文\n"
        )
        assert updated is not None
        assert (updated.description or "") == ""
        stale = await repo.apply_description_if_empty(
            doc.id,
            user_id=uid,
            description="针对 v1 的摘要",
            expected_content=old_content,
        )
        assert stale is None
        fresh = await repo.get(doc.id, user_id=uid)
        assert fresh is not None
        assert (fresh.description or "") == ""
        assert "v2" in fresh.content


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
