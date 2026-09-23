"""读取侧三洞的集成回归：纠错通道、为检索而写的描述、配额挤出可见。

- ① 纠错通道 (D6)：用户显式说「这条不对」后，那条记忆不再进系统提示、也不再进按需目录，
  但内容留着、可撤销，而且**扛得住 AI 之后整篇重写**（标记在列上，不在正文里）。
- ② 按需召回：目录摘要取条目的 ``description``（为检索而写），不是笔记第一行。
- ③ 配额挤出 (CTX-A2)：常驻池满时，被拒的是那一条写入，整趟整合继续跑，卡片按条目说清
  「什么没写进来 / 谁占着」。
"""

from __future__ import annotations

import uuid

import pytest

from agentcore.config import settings
from agentcore.db.repositories import (
    DocumentRepository,
    MemoryUpdateRepository,
    UserRepository,
)
from agentcore.memory import DocumentMemoryStore, assemble_injected_rules
from agentcore.memory.always_quota import memory_write_conversation_id
from agentcore.memory.rules_injection import mutate_user_rule
from tests.integration.conftest import register_and_login


@pytest.fixture
def tiny_always_cap(monkeypatch):
    monkeypatch.setattr(settings, "memory_always_max_chars", 80)


def _entry(description: str, body: str, *, apply: str = "on_demand") -> str:
    return f"---\napply: {apply}\ndescription: {description}\n---\n{body}"


# --- ① 纠错通道 -------------------------------------------------------------------------------


async def test_disputed_user_rule_leaves_injection_but_keeps_content(session_factory):
    uid = str(uuid.uuid4())
    async with session_factory() as session:
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        doc = await repo.create(
            uid,
            name="用户规则.md",
            role="rule",
            apply_mode="always",
            content="- 用户偏好 X",
        )
        assert "用户偏好 X" in await assemble_injected_rules(store, repo, uid, folder_id=None)

        marked = await repo.set_disputed(doc.id, user_id=uid, disputed=True)
        assert marked is not None and marked.disputed_at is not None

        assert "用户偏好 X" not in await assemble_injected_rules(store, repo, uid, folder_id=None)
        # Kept, not deleted: the user can still read (and undo) what was wrong.
        still_there = await repo.get(doc.id, user_id=uid)
        assert still_there is not None and "用户偏好 X" in still_there.content

        await repo.set_disputed(doc.id, user_id=uid, disputed=False)
        assert "用户偏好 X" in await assemble_injected_rules(store, repo, uid, folder_id=None)





async def test_disputed_always_entry_stops_spending_quota(client):
    """A disputed entry does not inject, so it must not hold the pool either."""
    await register_and_login(client, "mem_dispute_quota")
    doc = (
        await client.post(
            "/v1/documents",
            json={
                "name": "占位.md",
                "role": "rule",
                "apply_mode": "always",
                "content": "12345",
            },
        )
    ).json()
    before = (await client.get("/v1/documents/always-quota")).json()["used_chars"]
    assert before >= len("12345")

    await client.patch(f"/v1/documents/{doc['id']}", json={"disputed": True})
    after = (await client.get("/v1/documents/always-quota")).json()["used_chars"]
    assert after == before - len("12345")




# --- ② 为检索而写的描述 -----------------------------------------------------------------------




# --- ③ 配额挤出可见 (CTX-A2) ------------------------------------------------------------------


async def test_quota_card_names_denied_entry_and_holders(
    client, session_factory, tiny_always_cap, monkeypatch
):
    import agentcore.db.base as db_base

    monkeypatch.setattr(db_base, "async_session_factory", session_factory)
    await register_and_login(client, "aq_visible")
    conv = str(uuid.uuid4())

    async with session_factory() as session:
        user = await UserRepository(session).get_by_username("aq_visible")
        assert user is not None
        uid = user.user_id
        await DocumentRepository(session).create(
            uid,
            name="占坑规则.md",
            role="rule",
            apply_mode="always",
            content="z" * 100,
        )

    async with session_factory() as session:
        repo = DocumentRepository(session)
        token = memory_write_conversation_id.set(conv)
        try:
            from agentcore.memory.always_quota import AlwaysQuotaExceededError

            with pytest.raises(AlwaysQuotaExceededError):
                await mutate_user_rule(
                    repo,
                    uid,
                    folder_id=None,
                    action="write",
                    name="新规则.md",
                    content="a" * 40,
                    apply="always",
                )
        finally:
            memory_write_conversation_id.reset(token)

    async with session_factory() as session:
        rows = await MemoryUpdateRepository(session).list_for_conversation(conv, limit=20)
    quota_rows = [r for r in rows if r.kind == "quota"]
    assert len(quota_rows) == 1
    items = quota_rows[0].items
    denied = [it for it in items if it["action"] == "quota_denied"]
    holders = [it for it in items if it["action"] == "quota_holder"]
    # 条目级可见：哪一条没写进来 + 现在谁占着池子。
    assert [it["file"] for it in denied] == ["新规则.md"]
    assert any(it["file"] == "占坑规则.md" and "占用" in it["content"] for it in holders)
    assert "没能写进常驻" in (quota_rows[0].summary or "")
    assert "写不进" in (quota_rows[0].summary or "")
    assert "字符" not in (quota_rows[0].summary or "")
    # 诚实：没有静默淘汰，占坑的那条还在。
    async with session_factory() as session:
        kept = await DocumentRepository(session).list_injectable_rules(
            uid, None, ai_maintained=None
        )
    assert any(d.name == "占坑规则.md" for d in kept)


