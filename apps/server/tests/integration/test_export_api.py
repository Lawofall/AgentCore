"""End-to-end API tests for conversation export (导出对话) against a real PG schema.

Covers both formats (Markdown / JSON), the default, format validation, the download
headers (UTF-8 filename), auth gating, and owner scoping (a non-owner gets 404).
"""

import json
from datetime import UTC, datetime, timedelta

import httpx

from agentcore.db.models import Message
from tests.integration.conftest import register_and_login


async def _new_conversation(client: httpx.AsyncClient, title: str) -> str:
    r = await client.post("/v1/conversations", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed_messages(session_factory, conversation_id: str, turns) -> None:
    """Insert messages with strictly increasing timestamps for stable render order."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    async with session_factory() as session:
        for i, (role, content) in enumerate(turns):
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    created_at=base + timedelta(minutes=i),
                )
            )
        await session.commit()


async def test_export_markdown_roundtrip(client, session_factory):
    await register_and_login(client, "exmd")
    conv_id = await _new_conversation(client, "导出测试")
    await _seed_messages(
        session_factory,
        conv_id,
        [("user", "你好世界"), ("assistant", "这是回答")],
    )

    r = await client.get(f"/v1/conversations/{conv_id}/export")  # default md
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    assert body.startswith("# 导出测试")
    assert "你好世界" in body
    assert "这是回答" in body
    assert "## 用户" in body and "## GAgent" in body


async def test_export_json_roundtrip(client, session_factory):
    await register_and_login(client, "exjson")
    conv_id = await _new_conversation(client, "J")
    await _seed_messages(session_factory, conv_id, [("user", "q"), ("assistant", "a")])

    r = await client.get(f"/v1/conversations/{conv_id}/export?format=json")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert ".json" in r.headers["content-disposition"]
    payload = json.loads(r.text)
    assert payload["title"] == "J"
    assert payload["conversation_id"] == conv_id
    assert [m["content"] for m in payload["messages"]] == ["q", "a"]


async def test_export_rejects_unknown_format(client):
    await register_and_login(client, "exfmt")
    conv_id = await _new_conversation(client, "x")
    r = await client.get(f"/v1/conversations/{conv_id}/export?format=pdf")
    assert r.status_code == 422


async def test_export_filename_carries_utf8(client, session_factory):
    await register_and_login(client, "exfn")
    conv_id = await _new_conversation(client, "中文标题")
    await _seed_messages(session_factory, conv_id, [("user", "hi")])
    r = await client.get(f"/v1/conversations/{conv_id}/export")
    assert r.status_code == 200
    # RFC 5987 filename* carries the non-ASCII title.
    assert "filename*=UTF-8''" in r.headers["content-disposition"]


async def test_export_requires_auth(client, new_client):
    await register_and_login(client, "exauth")
    conv_id = await _new_conversation(client, "x")
    # A fresh client with no cookies is unauthenticated.
    async with new_client() as anon:
        r = await anon.get(f"/v1/conversations/{conv_id}/export")
    assert r.status_code == 401


async def test_export_non_owner_404(client, new_client, session_factory):
    await register_and_login(client, "owner6")
    conv_id = await _new_conversation(client, "private")
    await _seed_messages(session_factory, conv_id, [("user", "secret")])

    async with new_client() as other:
        await register_and_login(other, "intruder6")
        r = await other.get(f"/v1/conversations/{conv_id}/export")
    assert r.status_code == 404
