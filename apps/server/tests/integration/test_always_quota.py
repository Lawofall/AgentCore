"""Integration: write-side always-entry quota (闸在写侧，读侧全量)."""

from __future__ import annotations

import uuid

import pytest

from agentcore.config import settings
from agentcore.db.repositories import DocumentRepository, MemoryUpdateRepository, UserRepository
from agentcore.memory.always_quota import (
    AlwaysQuotaExceededError,
    memory_write_conversation_id,
)
from agentcore.memory.document_store import DocumentMemoryStore
from tests.integration.conftest import register_and_login


@pytest.fixture
def tiny_always_cap(monkeypatch):
    monkeypatch.setattr(settings, "memory_always_max_chars", 80)


async def test_user_over_limit_edit_saves_with_warning(client, tiny_always_cap):
    await register_and_login(client, "aq_user_edit")

    # Seed a small always rule under the cap.
    r = await client.post(
        "/v1/documents",
        json={
            "name": "规则.md",
            "role": "rule",
            "kind": "document",
            "apply_mode": "always",
            "content": "短",
        },
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    version = doc["version"]

    # Grow past the cap — user editing existing always must save + warn.
    big = "超限正文" * 30  # well over 80 chars once stored
    r = await client.put(
        f"/v1/documents/{doc['id']}",
        json={"content": f"---\napply: always\n---\n{big}", "baseline": version},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["conflict"] is False
    assert body["quota_warning"]
    assert "已保存" in body["quota_warning"]
    assert "字符" not in body["quota_warning"]

    fetched = (await client.get(f"/v1/documents/{doc['id']}")).json()
    assert big in fetched["content"]


async def test_user_create_over_limit_rejected(client, tiny_always_cap):
    await register_and_login(client, "aq_user_create")

    r = await client.post(
        "/v1/documents",
        json={
            "name": "满.md",
            "role": "rule",
            "kind": "document",
            "apply_mode": "always",
            "content": "x" * 200,
        },
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "ALWAYS_QUOTA_EXCEEDED"


async def test_ai_over_limit_write_rejected(client, session_factory, tiny_always_cap):
    await register_and_login(client, "aq_ai")

    # Fill the pool via user edit (allowed with warning).
    r = await client.post(
        "/v1/documents",
        json={
            "name": "底.md",
            "role": "rule",
            "kind": "document",
            "apply_mode": "always",
            "content": "底",
        },
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    r = await client.put(
        f"/v1/documents/{doc['id']}",
        json={
            "content": "---\napply: always\n---\n" + ("u" * 100),
            "baseline": doc["version"],
        },
    )
    assert r.status_code == 200 and r.json()["quota_warning"]

    async with session_factory() as session:
        user = await UserRepository(session).get_by_username("aq_ai")
        assert user is not None
        store = DocumentMemoryStore(session)
        with pytest.raises(AlwaysQuotaExceededError):
            await store.save(user.user_id, "画像.md", "---\napply: always\n---\n" + ("a" * 50))


async def test_quota_card_same_pending_state_only_once(
    client, session_factory, tiny_always_cap, monkeypatch
):
    import agentcore.db.base as db_base
    import agentcore.memory.always_quota as quota_mod

    monkeypatch.setattr(db_base, "async_session_factory", session_factory)
    # notify imports the factory inside the function from db.base — also pin the module
    # attribute used if any top-level binding appears later.
    monkeypatch.setattr(quota_mod, "async_session_factory", session_factory, raising=False)

    await register_and_login(client, "aq_card")
    conv = str(uuid.uuid4())

    async with session_factory() as session:
        user = await UserRepository(session).get_by_username("aq_card")
        assert user is not None
        uid = user.user_id
        # Fill always pool.
        repo = DocumentRepository(session)
        await repo.create(
            uid,
            name="满规则.md",
            role="rule",
            kind="document",
            apply_mode="always",
            content="---\napply: always\n---\n" + ("z" * 100),
        )

    async with session_factory() as session:
        user = await UserRepository(session).get_by_username("aq_card")
        assert user is not None
        uid = user.user_id
        store = DocumentMemoryStore(session)
        token = memory_write_conversation_id.set(conv)
        try:
            with pytest.raises(AlwaysQuotaExceededError):
                await store.save(uid, "画像.md", "---\napply: always\n---\n" + ("a" * 40))
            with pytest.raises(AlwaysQuotaExceededError):
                await store.save(uid, "画像.md", "---\napply: always\n---\n" + ("b" * 40))
        finally:
            memory_write_conversation_id.reset(token)

    async with session_factory() as session:
        rows = await MemoryUpdateRepository(session).list_for_conversation(conv, limit=20)
        quota_rows = [r for r in rows if r.kind == "quota"]
        assert len(quota_rows) == 1


async def test_always_quota_endpoint(client, tiny_always_cap):
    await register_and_login(client, "aq_meter")
    r = await client.get("/v1/documents/always-quota")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_chars"] == 80
    assert body["used_chars"] == 0
    assert body["global_chars"] == 0
    assert body["project_chars"] == 0
    assert body["percent"] == 0.0

    created = (
        await client.post(
            "/v1/documents",
            json={
                "name": "一.md",
                "role": "rule",
                "apply_mode": "always",
                "content": "hello",
            },
        )
    ).json()
    assert created["always_chars"] == len("hello")

    body = (await client.get("/v1/documents/always-quota")).json()
    assert body["used_chars"] == len("hello")
    assert body["global_chars"] == len("hello")
    assert body["project_chars"] == 0
    assert body["used_chars"] == body["global_chars"] + body["project_chars"]
    assert body["percent"] > 0

    listed = (await client.get("/v1/documents", params={"parent_id": created["parent_id"]})).json()
    always_rows = [n for n in listed if n["id"] == created["id"]]
    assert always_rows and always_rows[0]["always_chars"] == len("hello")
    on_demand = (
        await client.post(
            "/v1/documents",
            json={
                "name": "按需.md",
                "role": "rule",
                "apply_mode": "on_demand",
                "content": "skip-me",
            },
        )
    ).json()
    assert on_demand["always_chars"] is None


async def test_always_quota_project_split_sums_to_used(client, tiny_always_cap):
    """Project meter = global ∪ project; used_chars == global_chars + project_chars."""
    await register_and_login(client, "aq_split")
    proj = str(uuid.uuid4())

    await client.post(
        "/v1/documents",
        json={
            "name": "全局.md",
            "role": "rule",
            "apply_mode": "always",
            "content": "GLO",
        },
    )
    await client.post(
        "/v1/documents",
        json={
            "name": "项目.md",
            "role": "rule",
            "apply_mode": "always",
            "folder_id": proj,
            "content": "PRO",
        },
    )

    global_body = (await client.get("/v1/documents/always-quota")).json()
    assert global_body["used_chars"] == len("GLO")
    assert global_body["global_chars"] == len("GLO")
    assert global_body["project_chars"] == 0

    proj_body = (
        await client.get("/v1/documents/always-quota", params={"folder_id": proj})
    ).json()
    assert proj_body["global_chars"] == len("GLO")
    assert proj_body["project_chars"] == len("PRO")
    assert proj_body["used_chars"] == proj_body["global_chars"] + proj_body["project_chars"]
    assert proj_body["used_chars"] == len("GLO") + len("PRO")
