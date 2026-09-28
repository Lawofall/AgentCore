"""End-to-end API tests for conversation sharing (分享对话) against a real PG schema.

Covers the owner loop (create → list → revoke), the public read-only page (renders
the frozen snapshot; revoked / unknown / malformed tokens 404), snapshot freezing
(a later message never leaks into an existing link), owner scoping, auth gating, the
cascades (delete conversation / delete account revoke shares), and public XSS safety.
"""

from datetime import UTC, datetime, timedelta

import httpx

from agentcore.db.models import ConversationShare, Message
from agentcore.db.repositories import ConversationRepository
from tests.integration.conftest import TEST_PASSWORD, register_and_login


async def _new_conversation(client: httpx.AsyncClient, title: str) -> str:
    r = await client.post("/v1/conversations", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed_messages(session_factory, conversation_id: str, turns) -> None:
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


async def test_create_list_view_revoke(client, new_client, session_factory):
    await register_and_login(client, "sh1")
    conv_id = await _new_conversation(client, "分享测试")
    await _seed_messages(session_factory, conv_id, [("user", "问题甲"), ("assistant", "回答乙")])

    # Create a share.
    r = await client.post(f"/v1/conversations/{conv_id}/shares")
    assert r.status_code == 201, r.text
    share = r.json()
    assert share["url"] == f"/shared/{share['id']}"
    assert share["title"] == "分享测试"

    # It appears in the owner's list.
    r = await client.get(f"/v1/conversations/{conv_id}/shares")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    # The public page renders the snapshot — no cookies needed.
    async with new_client() as anon:
        page = await anon.get(share["url"])
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "问题甲" in page.text
        assert "回答乙" in page.text
        assert "分享测试" in page.text

    # Revoke → public page 404s, list empties.
    r = await client.delete(f"/v1/conversations/{conv_id}/shares/{share['id']}")
    assert r.status_code == 200
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404
    assert (await client.get(f"/v1/conversations/{conv_id}/shares")).json()["total"] == 0


async def test_revoke_is_not_repeatable(client, session_factory):
    await register_and_login(client, "sh2")
    conv_id = await _new_conversation(client, "x")
    await _seed_messages(session_factory, conv_id, [("user", "q")])
    share = (await client.post(f"/v1/conversations/{conv_id}/shares")).json()
    assert (
        await client.delete(f"/v1/conversations/{conv_id}/shares/{share['id']}")
    ).status_code == 200
    # A second revoke 404s (already revoked).
    assert (
        await client.delete(f"/v1/conversations/{conv_id}/shares/{share['id']}")
    ).status_code == 404


async def test_snapshot_is_frozen_against_later_messages(
    client, new_client, session_factory
):
    await register_and_login(client, "sh3")
    conv_id = await _new_conversation(client, "冻结")
    await _seed_messages(session_factory, conv_id, [("user", "最初的问题")])
    share = (await client.post(f"/v1/conversations/{conv_id}/shares")).json()

    # A new turn lands AFTER the share was created.
    await _seed_messages(session_factory, conv_id, [("assistant", "稍后才有的新内容")])

    async with new_client() as anon:
        page = await anon.get(share["url"])
        assert "最初的问题" in page.text
        # The later message is NOT exposed by the existing link (所见即所享).
        assert "稍后才有的新内容" not in page.text


async def test_public_view_unknown_and_malformed_token_404(client, new_client):
    # Need an initialized schema (client fixture) but no auth for the public page.
    await register_and_login(client, "sh4")
    async with new_client() as anon:
        # Well-formed but unknown uuid.
        r = await anon.get("/shared/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("text/html")
        # Malformed (not a uuid) — short-circuits to 404, never a 500.
        assert (await anon.get("/shared/not-a-real-token")).status_code == 404


async def test_share_requires_auth_and_owner(client, new_client, session_factory):
    await register_and_login(client, "owner5")
    conv_id = await _new_conversation(client, "private")
    await _seed_messages(session_factory, conv_id, [("user", "secret")])

    # Anonymous can't create.
    async with new_client() as anon:
        assert (await anon.post(f"/v1/conversations/{conv_id}/shares")).status_code == 401

    # A different user can't create / list (404, IDOR-safe).
    async with new_client() as other:
        await register_and_login(other, "intruder5")
        assert (await other.post(f"/v1/conversations/{conv_id}/shares")).status_code == 404
        assert (await other.get(f"/v1/conversations/{conv_id}/shares")).status_code == 404


async def test_delete_conversation_revokes_shares(client, new_client, session_factory):
    await register_and_login(client, "sh6")
    conv_id = await _new_conversation(client, "to delete")
    await _seed_messages(session_factory, conv_id, [("user", "q")])
    share = (await client.post(f"/v1/conversations/{conv_id}/shares")).json()

    assert (await client.delete(f"/v1/conversations/{conv_id}")).status_code == 200
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404
    async with session_factory() as session:
        await ConversationRepository(session).hard_delete(conv_id)
    async with session_factory() as session:
        row = await session.get(ConversationShare, share["id"])
    assert row is not None
    assert row.conversation_id is None
    assert row.revoked_at is not None
    assert row.snapshot


async def test_delete_account_revokes_shares(client, new_client, session_factory):
    await register_and_login(client, "sh7")
    conv_id = await _new_conversation(client, "acct")
    await _seed_messages(session_factory, conv_id, [("user", "q")])
    share = (await client.post(f"/v1/conversations/{conv_id}/shares")).json()

    r = await client.request("DELETE", "/v1/auth/me", json={"password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404


async def test_public_view_escapes_xss(client, new_client, session_factory):
    await register_and_login(client, "sh8")
    conv_id = await _new_conversation(client, "xss")
    await _seed_messages(
        session_factory,
        conv_id,
        [("assistant", "<script>alert('x')</script> hello")],
    )
    share = (await client.post(f"/v1/conversations/{conv_id}/shares")).json()
    async with new_client() as anon:
        page = await anon.get(share["url"])
        assert page.status_code == 200
        assert "<script" not in page.text
        assert "&lt;script&gt;" in page.text


async def test_share_defaults_to_30_day_expiry(client, session_factory):
    from sqlalchemy import select

    from agentcore.db.models import ConversationShare

    await register_and_login(client, "ttl-default")
    conv_id = await _new_conversation(client, "ttl-default")
    share = (await client.post(f"/v1/conversations/{conv_id}/shares")).json()
    assert share["expires_at"] is not None

    async with session_factory() as session:
        row = (
            await session.execute(select(ConversationShare).where(ConversationShare.id == share["id"]))
        ).scalar_one()
        delta = row.expires_at - datetime.now(UTC)
        assert timedelta(days=29) < delta < timedelta(days=31)


async def test_share_never_expires_when_requested(client):
    await register_and_login(client, "ttl-never")
    conv_id = await _new_conversation(client, "ttl-never")
    share = (
        await client.post(
            f"/v1/conversations/{conv_id}/shares",
            json={"expires_in_days": None},
        )
    ).json()
    assert share["expires_at"] is None


async def test_expired_share_returns_404(client, new_client, session_factory):
    from sqlalchemy import update

    from agentcore.db.models import ConversationShare

    await register_and_login(client, "ttl-expired")
    conv_id = await _new_conversation(client, "ttl-expired")
    share = (await client.post(f"/v1/conversations/{conv_id}/shares")).json()

    async with session_factory() as session:
        await session.execute(
            update(ConversationShare)
            .where(ConversationShare.id == share["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    async with new_client() as anon:
        assert (await anon.get(share["url"])).status_code == 404
