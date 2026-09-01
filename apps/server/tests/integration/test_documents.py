"""Document 子系统第一期 integration tests (Agent记忆与知识系统 §5.7).

Against a real PG schema: the tree CRUD API, owner-scoping, user-rule injection (two-tier,
read-side full injection), the ``remember`` directive→user-rule path, and the one-time
file→document migration (idempotent, non-clobbering). Auto-skips when PostgreSQL is
unavailable (integration conftest).
"""

import uuid
from pathlib import Path

from agentcore.db.repositories import DocumentRepository
from agentcore.documents.frontmatter import set_entry_frontmatter
from agentcore.memory import DocumentMemoryStore, assemble_injected_rules
from agentcore.memory.migrate_documents import migrate_file_memory_to_documents
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    FileMemoryStore,
    topic_path,
)
from agentcore.tools.builtin.remember import RememberTool
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
    r = await client.post(
        "/v1/documents", json={"name": "私密.md", "role": "rule", "content": "x"}
    )
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
        rules_md = await assemble_injected_rules(
            store, repo, uid, folder_id=None, enabled=True
        )
        on_demand = await repo.list_on_demand_user_rules(uid, None)
    assert "always 规则" in rules_md
    assert "on_demand 规则" not in rules_md
    assert {d.name for d in on_demand} == {"按需.md"}


async def test_user_rule_follows_memory_slots_in_the_same_layer(session_factory):
    """Global layer: 偏好 → 画像 → 用户常驻规则（稳定顺序，不是作者权威）。"""
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
        rules_md = await assemble_injected_rules(
            store, repo, uid, folder_id=None, enabled=True
        )
    assert "必须始终用中文" in rules_md
    assert "用 Python" in rules_md and "倾向简洁" in rules_md
    assert rules_md.index("倾向简洁") < rules_md.index("用 Python")
    assert rules_md.index("用 Python") < rules_md.index("必须始终用中文")


async def test_user_rule_survives_when_memory_disabled(session_factory):
    # Turning off「AI 记忆」silences AI memory, but the user's OWN rule still injects.
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        await repo.create(
            uid, name="用户规则.md", role="rule", apply_mode="always", content="- 必须用中文"
        )
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- 用 Python", scope=None)
        rules_md = await assemble_injected_rules(
            store, repo, uid, folder_id=None, enabled=False
        )
    assert "必须用中文" in rules_md
    assert "用 Python" not in rules_md


async def test_injection_admits_global_and_project_rules(session_factory):
    # Read-side full injection: both global and project always rules survive.
    uid = str(uuid.uuid4())
    proj = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        await repo.create(
            uid, name="用户规则.md", role="rule", apply_mode="always", content="全局规则"
        )
        await repo.create(
            uid,
            name="用户规则.md",
            role="rule",
            folder_id=proj,
            apply_mode="always",
            content="项目规则",
        )
        rules_md = await assemble_injected_rules(
            store, repo, uid, folder_id=proj, enabled=True
        )
    assert "全局规则" in rules_md
    assert "项目规则" in rules_md


# --- remember directive → user rule ----------------------------------------------------------


def _ctx(user_id: str) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id,
        conversation_id="",
    )


async def test_remember_writes_user_rule_and_dedupes(session_factory, monkeypatch):
    from agentcore.tools.builtin import remember as remember_mod

    monkeypatch.setattr(remember_mod, "async_session_factory", session_factory)
    uid = str(uuid.uuid4())
    tool = RememberTool(folder_id=None)

    res = await tool.execute({"content": "以后都用中文"}, _ctx(uid))
    assert res.success and res.display["remembered"] is True and res.display["kind"] == "user_rule"

    # Re-remembering the same directive is a no-op (normalized dedup).
    res2 = await tool.execute({"content": "以后都用中文"}, _ctx(uid))
    assert res2.success and res2.display["remembered"] is False

    # It landed as an injectable ai_maintained=false rule doc.
    async with session_factory() as session:
        docs = await DocumentRepository(session).list_injectable_rules(
            uid, None, ai_maintained=False
        )
    assert any("以后都用中文" in d.content and d.ai_maintained is False for d in docs)


# --- one-time file→document migration --------------------------------------------------------


async def test_file_to_document_migration_idempotent_and_non_clobbering(
    session_factory, tmp_path
):
    uid = str(uuid.uuid4())
    proj = str(uuid.uuid4())
    fs = FileMemoryStore(tmp_path)
    await fs.save(uid, PREFERENCES_MEMORY_FILE, "## 沟通偏好\n- 用中文")
    await fs.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Python")
    await fs.save(uid, topic_path("部署"), "## 要点\n- 先构建")
    await fs.save(uid, CORE_MEMORY_FILE, "## 关于用户的事实\n- 本项目用 Rust", scope=proj)

    stats = await migrate_file_memory_to_documents(
        base_dir=tmp_path, session_factory=session_factory
    )
    assert stats.notes_migrated == 4 and stats.notes_failed == 0

    async with session_factory() as session:
        store = DocumentMemoryStore(session=session)
        assert "用中文" in await store.load(uid, PREFERENCES_MEMORY_FILE)
        assert "Python" in await store.load(uid, CORE_MEMORY_FILE)
        assert "先构建" in await store.load(uid, topic_path("部署"))
        assert "本项目用 Rust" in await store.load(uid, CORE_MEMORY_FILE, scope=proj)

    # Idempotent: a second run migrates nothing (all already present).
    stats2 = await migrate_file_memory_to_documents(
        base_dir=tmp_path, session_factory=session_factory
    )
    assert stats2.notes_migrated == 0 and stats2.notes_skipped_existing == 4

    # A post-migration edit is NOT clobbered by a later run (skip-if-exists).
    async with session_factory() as session:
        await DocumentMemoryStore(session=session).save(
            uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Python\n- Rust"
        )
    await migrate_file_memory_to_documents(base_dir=tmp_path, session_factory=session_factory)
    async with session_factory() as session:
        body = await DocumentMemoryStore(session=session).load(uid, CORE_MEMORY_FILE)
    assert "Rust" in body  # the edit survived the re-run


async def test_delete_memory_note_removes_disk_source(session_factory, tmp_path):
    """Deleting a memory note soft-deletes the DB row AND unlinks the on-disk source."""
    uid = str(uuid.uuid4())
    fs = FileMemoryStore(tmp_path)
    topic = topic_path("部署流程")
    await fs.save(uid, topic, "## 要点\n- 先构建")
    disk = tmp_path / uid / "主题" / "部署流程.md"
    assert disk.is_file()

    async with session_factory() as session:
        store = DocumentMemoryStore(session=session, file_store=fs)
        await store.save(uid, topic, "## 要点\n- 先构建")
        await store.delete(uid, topic)

    assert not disk.exists()
    async with session_factory() as session:
        # Soft-deleted: live load is empty; include_deleted still finds the tombstone.
        assert await DocumentMemoryStore(session=session).load(uid, topic) == ""
        note = await DocumentRepository(session).get_memory_note(
            uid, topic, None, include_deleted=True
        )
        assert note is not None and note.deleted_at is not None


async def test_migration_skips_soft_deleted_same_name(session_factory, tmp_path):
    """A leftover disk file must not resurrect a soft-deleted same-name memory note."""
    uid = str(uuid.uuid4())
    fs = FileMemoryStore(tmp_path)
    topic = topic_path("复活陷阱")
    await fs.save(uid, topic, "## 旧内容\n- 不该回来")

    # Migrate once, then soft-delete (leave the disk file in place — the pre-fix shape).
    stats = await migrate_file_memory_to_documents(
        base_dir=tmp_path, session_factory=session_factory
    )
    assert stats.notes_migrated == 1
    async with session_factory() as session:
        # Soft-delete DB only (bypass DocumentMemoryStore.delete's disk unlink) so the
        # leftover source remains — exactly the resurrection scenario under test.
        await DocumentRepository(session).delete_memory_note(uid, topic, None)
        assert await DocumentMemoryStore(session=session).load(uid, topic) == ""
    assert (tmp_path / uid / "主题" / "复活陷阱.md").is_file()

    # Re-run: soft-deleted same-name counts as existing → skip, do not re-INSERT.
    stats2 = await migrate_file_memory_to_documents(
        base_dir=tmp_path, session_factory=session_factory
    )
    assert stats2.notes_migrated == 0 and stats2.notes_skipped_existing == 1
    async with session_factory() as session:
        assert await DocumentMemoryStore(session=session).load(uid, topic) == ""
        live = await DocumentRepository(session).get_memory_note(uid, topic, None)
        assert live is None
        tombstone = await DocumentRepository(session).get_memory_note(
            uid, topic, None, include_deleted=True
        )
        assert tombstone is not None and tombstone.deleted_at is not None


# --- AgentCore/ convention layout (§5.0) ------------------------------------------------------


async def test_new_writes_land_under_agentcore(session_factory):
    """Memory notes + remember target land under AgentCore/{记忆,规则}/."""
    from agentcore.db.repositories.documents import (
        AGENTCORE_ROOT_NAME,
        MEMORY_ROOT_NAME,
        RULES_DIR_NAME,
        USER_RULES_DOC_NAME,
    )

    uid = str(uuid.uuid4())
    async with session_factory() as session:
        store = DocumentMemoryStore(session=session)
        await store.save(uid, CORE_MEMORY_FILE, "## 技术栈与工具\n- Python", scope=None)
        repo = DocumentRepository(session)
        await repo.upsert_user_rules_doc(uid, None, "- 必须用中文")

        mem_root = await repo.get_memory_root(uid, None)
        assert mem_root is not None
        ac = await repo.get(mem_root.parent_id, user_id=uid)
        assert ac is not None and ac.name == AGENTCORE_ROOT_NAME and ac.parent_id is None

        rules_dir = await repo.get_rules_dir(uid, None)
        assert rules_dir is not None and rules_dir.name == RULES_DIR_NAME
        assert rules_dir.parent_id == ac.id
        rule = await repo.get_user_rules_doc(uid, None)
        assert rule is not None and rule.parent_id == rules_dir.id
        assert rule.name == USER_RULES_DOC_NAME

        note = await repo.get_memory_note(uid, CORE_MEMORY_FILE, None)
        assert note is not None and note.parent_id == mem_root.id
        assert mem_root.name == MEMORY_ROOT_NAME


async def test_agentcore_layout_migration_idempotent(session_factory):
    """Bare 记忆/ + top-level user rules reparent into AgentCore/; second run is a no-op."""
    from agentcore.db.repositories.documents import (
        AGENTCORE_ROOT_NAME,
        MEMORY_ROOT_NAME,
        RULES_DIR_NAME,
    )
    from agentcore.memory.migrate_agentcore import migrate_agentcore_layout

    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        # Pre-§5.0 shape: bare memory root + top-level rule.
        bare = await repo.create(
            uid,
            name=MEMORY_ROOT_NAME,
            kind="folder",
            role="general",
            ai_maintained=True,
            parent_id=None,
        )
        note = await repo.create(
            uid,
            name=CORE_MEMORY_FILE,
            kind="document",
            role="rule",
            ai_maintained=True,
            parent_id=bare.id,
            content="## 技术栈与工具\n- Python",
        )
        top_rule = await repo.create(
            uid, name="语气.md", role="rule", content="- 简洁", parent_id=None
        )
        bare_id, note_id, rule_id = bare.id, note.id, top_rule.id

    stats = await migrate_agentcore_layout(session_factory=session_factory)
    assert stats.scopes_failed == 0
    assert stats.memory_roots_moved >= 1
    assert stats.rules_moved >= 1

    async with session_factory() as session:
        repo = DocumentRepository(session)
        mem = await repo.get_memory_root(uid, None)
        assert mem is not None and mem.id == bare_id
        ac = await repo.get(mem.parent_id, user_id=uid)
        assert ac is not None and ac.name == AGENTCORE_ROOT_NAME
        rules_dir = await repo.get_rules_dir(uid, None)
        assert rules_dir is not None and rules_dir.name == RULES_DIR_NAME
        moved_rule = await repo.get(rule_id, user_id=uid)
        assert moved_rule is not None and moved_rule.parent_id == rules_dir.id
        moved_note = await repo.get(note_id, user_id=uid)
        assert moved_note is not None and moved_note.parent_id == mem.id
        assert "Python" in moved_note.content

    stats2 = await migrate_agentcore_layout(session_factory=session_factory)
    assert stats2.memory_roots_moved == 0
    assert stats2.rules_moved == 0


async def test_agentcore_layout_migration_default_factory(session_factory, monkeypatch):
    """No-arg call resolves the default session factory (the boot path).

    Regression: every other test injects ``session_factory=``, so the ``is None``
    branch was never executed and a wrong module path there (``agentcore.db.session``)
    silently no-op'd the migration on every real boot — swallowed by the best-effort
    ``except`` in ``main.lifespan``.
    """
    import agentcore.db.base as db_base
    from agentcore.memory.migrate_agentcore import migrate_agentcore_layout

    monkeypatch.setattr(db_base, "async_session_factory", session_factory)

    stats = await migrate_agentcore_layout()
    assert stats.scopes_failed == 0


async def test_injectable_rules_skip_stray_outside_convention(session_factory):
    """With AgentCore/规则/ present, a top-level stray always-rule is not injectable."""
    from agentcore.db.repositories.documents import USER_RULES_DOC_NAME

    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        await repo.upsert_user_rules_doc(uid, None, "- 必须用中文")
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
        assert any(d.name == USER_RULES_DOC_NAME for d in docs)
        assert all(d.parent_id == rules_dir.id for d in docs)


async def test_dual_memory_roots_soft_deletes_empty_bare(session_factory):
    """After dual-root fold, empty bare 记忆/ gets soft-deleted; notes live under convention."""
    from agentcore.db.models import Document
    from agentcore.db.repositories.documents import (
        AGENTCORE_ROOT_NAME,
        MEMORY_ROOT_NAME,
    )
    from agentcore.memory.migrate_agentcore import migrate_agentcore_layout

    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        ac = await repo.ensure_agentcore_root(uid, None)
        under = await repo.create(
            uid,
            name=MEMORY_ROOT_NAME,
            kind="folder",
            role="general",
            ai_maintained=True,
            parent_id=ac.id,
        )
        note_under = await repo.create(
            uid,
            name=CORE_MEMORY_FILE,
            kind="document",
            role="rule",
            ai_maintained=True,
            parent_id=under.id,
            content="## 技术栈与工具\n- under",
            apply_mode="always",
        )
        bare = await repo.create(
            uid,
            name=MEMORY_ROOT_NAME,
            kind="folder",
            role="general",
            ai_maintained=True,
            parent_id=None,
        )
        note_bare = await repo.create(
            uid,
            name=PREFERENCES_MEMORY_FILE,
            kind="document",
            role="rule",
            ai_maintained=True,
            parent_id=bare.id,
            content="## 沟通偏好\n- bare",
            apply_mode="always",
        )
        bare_id, under_id = bare.id, under.id
        note_bare_id, note_under_id = note_bare.id, note_under.id

    stats = await migrate_agentcore_layout(session_factory=session_factory)
    assert stats.scopes_failed == 0
    assert stats.bare_memory_roots_soft_deleted >= 1

    async with session_factory() as session:
        repo = DocumentRepository(session)
        bare_row = await session.get(Document, bare_id)
        assert bare_row is not None and bare_row.deleted_at is not None

        mem = await repo.get_memory_root(uid, None)
        assert mem is not None and mem.id == under_id
        ac = await repo.get(mem.parent_id, user_id=uid)
        assert ac is not None and ac.name == AGENTCORE_ROOT_NAME

        moved_pref = await repo.get(note_bare_id, user_id=uid)
        assert moved_pref is not None and moved_pref.parent_id == under_id
        kept = await repo.get(note_under_id, user_id=uid)
        assert kept is not None and kept.parent_id == under_id


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
    assert rules_dir["name"] == "规则" and rules_dir["kind"] == "folder"

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

    r = await client.patch(
        f"/v1/documents/{always['id']}", json={"apply_mode": "on_demand"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["apply_mode"] == "on_demand"

    r = await client.patch(
        f"/v1/documents/{on_demand['id']}", json={"apply_mode": "always"}
    )
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

        again = await repo.apply_description_if_empty(
            doc.id, user_id=uid, description="不该覆盖"
        )
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
        core = await DocumentRepository(session).get_memory_note(
            uid, CORE_MEMORY_FILE, None
        )
        topic = await DocumentRepository(session).get_memory_note(
            uid, topic_path("部署"), None
        )
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
        core = await DocumentRepository(session).get_memory_note(
            uid, CORE_MEMORY_FILE, None
        )
        topic = await DocumentRepository(session).get_memory_note(
            uid, topic_path("部署"), None
        )
        assert core is not None and topic is not None
        core_id, topic_id = core.id, topic.id

    for doc_id in (core_id, topic_id):
        r = await client.patch(
            f"/v1/documents/{doc_id}", json={"apply_mode": "on_demand"}
        )
        assert r.status_code == 400, r.text
        assert "AI-maintained" in r.json()["detail"]

