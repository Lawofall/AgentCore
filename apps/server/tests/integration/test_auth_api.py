"""End-to-end API integration tests for auth + per-user isolation (real PG).

Exercises the full HTTP chain (cookies, DI, error mapping) against a real
PostgreSQL schema. Covers: unauthenticated 401s, the register/login/me flow,
owner-scoped conversation CRUD, IDOR protection, refresh rotation + reuse
detection, login lockout, and logout.
"""

from datetime import timedelta
from uuid import uuid4

from agentcore.config import settings
from tests.integration.conftest import (
    client_platform_headers,
    login_admin,
    register_and_login,
)

_PW = "password123"
_DESKTOP = client_platform_headers()


async def test_protected_endpoints_require_auth(client):
    assert (await client.get("/v1/conversations")).status_code == 401
    assert (await client.post("/v1/conversations", json={"title": "x"})).status_code == 401
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_register_login_me_flow(client):
    r = await client.post(
        "/v1/auth/register",
        json={"username": "alice", "password": _PW},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "alice" and body["id"]

    r = await client.post(
        "/v1/auth/login",
        json={"username": "alice", "password": _PW},
        headers=_DESKTOP,
    )
    assert r.status_code == 200
    assert "access_token" in client.cookies
    assert "refresh_token" in client.cookies

    r = await client.get("/v1/auth/me")
    assert r.status_code == 200 and r.json()["username"] == "alice"


async def test_register_closed_returns_403(client, monkeypatch):
    monkeypatch.setattr(settings, "registration_open", False)
    r = await client.post(
        "/v1/auth/register",
        json={"username": "bob", "password": _PW},
    )
    assert r.status_code == 403
    assert "注册已关闭" in r.text


async def test_conversation_crud_for_owner(client):
    await register_and_login(client, "carol")

    r = await client.post("/v1/conversations", json={"title": "hello"})
    assert r.status_code == 201
    conv_id = r.json()["id"]

    assert (await client.get("/v1/conversations")).json()["total"] == 1

    r = await client.get(f"/v1/conversations/{conv_id}")
    assert r.status_code == 200 and r.json()["id"] == conv_id

    r = await client.patch(f"/v1/conversations/{conv_id}", json={"title": "renamed"})
    assert r.status_code == 200 and r.json()["title"] == "renamed"

    assert (await client.delete(f"/v1/conversations/{conv_id}")).status_code == 200
    assert (await client.get("/v1/conversations")).json()["total"] == 0


async def test_idor_user_cannot_touch_others_conversation(client, new_client):
    await register_and_login(client, "owner")
    conv_id = (await client.post("/v1/conversations", json={"title": "secret"})).json()["id"]

    async with new_client() as attacker:
        await register_and_login(attacker, "attacker")

        assert (await attacker.get(f"/v1/conversations/{conv_id}")).status_code == 404
        assert (
            await attacker.patch(f"/v1/conversations/{conv_id}", json={"title": "hacked"})
        ).status_code == 404
        assert (await attacker.delete(f"/v1/conversations/{conv_id}")).status_code == 404
        assert (
            await attacker.post(
                f"/v1/conversations/{conv_id}/messages",
                json={"content": "hi", "delivery": "steer"},
            )
        ).status_code == 404
        assert (await attacker.get("/v1/conversations")).json()["total"] == 0

    # owner's data is untouched
    r = await client.get(f"/v1/conversations/{conv_id}")
    assert r.status_code == 200 and r.json()["title"] == "secret"


async def test_refresh_rotates_cookie(client):
    await register_and_login(client, "dave")

    old_refresh = client.cookies.get("refresh_token")
    r = await client.post("/v1/auth/refresh")
    assert r.status_code == 200

    new_refresh = client.cookies.get("refresh_token")
    assert new_refresh and new_refresh != old_refresh
    # the freshly rotated access cookie still authorizes
    assert (await client.get("/v1/auth/me")).status_code == 200


async def test_refresh_reuse_detected_revokes_family(client, new_client, monkeypatch):
    # Close the benign-concurrency grace window so an already-rotated token reads
    # as a genuine replay/leak (the security property under test). The within-grace
    # benign path is covered by tests/test_auth_service.py.
    monkeypatch.setattr("agentcore.auth.service._REFRESH_REUSE_GRACE", timedelta(0))
    await register_and_login(client, "erin")

    r1 = client.cookies.get("refresh_token")
    assert (await client.post("/v1/auth/refresh")).status_code == 200
    r2 = client.cookies.get("refresh_token")
    assert r2 != r1

    # Replay the already-rotated r1 from a clean jar -> reuse detected (401).
    async with new_client() as raw:
        resp = await raw.post("/v1/auth/refresh", headers={"Cookie": f"refresh_token={r1}"})
        assert resp.status_code == 401

    # Reuse must revoke the whole family, so the live r2 is now dead too.
    async with new_client() as raw:
        resp = await raw.post("/v1/auth/refresh", headers={"Cookie": f"refresh_token={r2}"})
        assert resp.status_code == 401


async def test_login_lockout_after_repeated_failures(client):
    await register_and_login(client, "frank")

    attempts = []
    for _ in range(5):
        r = await client.post(
            "/v1/auth/login",
            json={"username": "frank", "password": "wrong-pass"},
            headers=_DESKTOP,
        )
        assert r.status_code == 401
        attempts.append(r)

    assert attempts[3].json()["error"]["message"] == "用户名或密码错误"
    assert "recovery_at" not in attempts[3].json()["error"]

    locked = attempts[4].json()["error"]
    assert locked["message"] == "账户已临时锁定，请稍后再试"
    assert locked["recovery_at"].endswith("Z")
    retry_after = int(attempts[4].headers["retry-after"])
    assert 9 * 60 <= retry_after <= 10 * 60

    # Even the correct password is rejected while the account is locked, and
    # the wait is the time left — not a fresh window.
    r = await client.post(
        "/v1/auth/login",
        json={"username": "frank", "password": _PW},
        headers=_DESKTOP,
    )
    assert r.status_code == 401
    assert r.json()["error"]["recovery_at"]
    assert int(r.headers["retry-after"]) <= retry_after


async def test_logout_clears_cookies(client):
    await register_and_login(client, "gina")
    assert (await client.get("/v1/auth/me")).status_code == 200

    assert (await client.post("/v1/auth/logout")).status_code == 200
    # cookies cleared -> protected route is unauthenticated again
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_refresh_cookie_path_carries_reverse_proxy_prefix(client, monkeypatch):
    # Behind the prod Nginx the API is mounted at /api/, so the browser's real refresh
    # path is /api/v1/auth/refresh. RFC 6265 path-matching only sends the cookie if its
    # Path is a prefix of that — a bare /v1/auth scope silently drops it (forced
    # re-login once the access token expires). COOKIE_PATH_PREFIX must carry the mount.
    monkeypatch.setattr(settings, "cookie_path_prefix", "/api")
    r = await client.post(
        "/v1/auth/register",
        json={"username": "pat", "password": _PW},
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        "/v1/auth/login",
        json={"username": "pat", "password": _PW},
        headers=_DESKTOP,
    )
    assert r.status_code == 200

    set_cookies = r.headers.get_list("set-cookie")
    refresh = next(c for c in set_cookies if c.startswith("refresh_token="))
    assert "Path=/api/v1/auth" in refresh, refresh
    # Access cookie stays at root so it rides every /api/* request.
    access = next(c for c in set_cookies if c.startswith("access_token="))
    assert "Path=/" in access and "Path=/api/v1/auth" not in access, access


def _cookie_has_persistent_expiry(header: str) -> bool:
    lower = header.lower()
    return "max-age=" in lower or "expires=" in lower


async def test_login_default_persist_sets_cookie_max_age(client):
    await client.post(
        "/v1/auth/register",
        json={"username": "persist_def", "password": _PW},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"username": "persist_def", "password": _PW},
        headers=_DESKTOP,
    )
    assert r.status_code == 200
    set_cookies = r.headers.get_list("set-cookie")
    access = next(c for c in set_cookies if c.startswith("access_token="))
    refresh = next(c for c in set_cookies if c.startswith("refresh_token="))
    assert _cookie_has_persistent_expiry(access), access
    assert _cookie_has_persistent_expiry(refresh), refresh


async def test_login_persist_false_sets_session_cookies(client):
    await client.post(
        "/v1/auth/register",
        json={"username": "ephem_cookie", "password": _PW},
    )
    r = await client.post(
        "/v1/auth/login",
        json={
            "username": "ephem_cookie",
            "password": _PW,
            "persist_session": False,
        },
        headers=_DESKTOP,
    )
    assert r.status_code == 200
    set_cookies = r.headers.get_list("set-cookie")
    access = next(c for c in set_cookies if c.startswith("access_token="))
    refresh = next(c for c in set_cookies if c.startswith("refresh_token="))
    assert not _cookie_has_persistent_expiry(access), access
    assert not _cookie_has_persistent_expiry(refresh), refresh
    # Session still authorizes; refresh keeps session-cookie policy.
    assert (await client.get("/v1/auth/me")).status_code == 200
    refreshed = await client.post("/v1/auth/refresh")
    assert refreshed.status_code == 200
    refresh_cookies = refreshed.headers.get_list("set-cookie")
    access2 = next(c for c in refresh_cookies if c.startswith("access_token="))
    refresh2 = next(c for c in refresh_cookies if c.startswith("refresh_token="))
    assert not _cookie_has_persistent_expiry(access2), access2
    assert not _cookie_has_persistent_expiry(refresh2), refresh2


async def test_token_login_persist_false_short_refresh_expires_in(client):
    await client.post(
        "/v1/auth/register",
        json={"username": "ephem_bearer", "password": _PW},
    )
    long = (
        await client.post(
            "/v1/auth/token",
            json={"username": "ephem_bearer", "password": _PW},
            headers=_DESKTOP,
        )
    ).json()
    short = (
        await client.post(
            "/v1/auth/token",
            json={
                "username": "ephem_bearer",
                "password": _PW,
                "persist_session": False,
            },
            headers=_DESKTOP,
        )
    ).json()
    assert long["refresh_expires_in"] == settings.jwt_refresh_token_expire_days * 86400
    assert (
        short["refresh_expires_in"]
        == settings.ephemeral_refresh_family_max_hours * 3600
    )
    assert short["refresh_expires_in"] < long["refresh_expires_in"]
    assert short["access_token"] and short["refresh_token"]


# --- self-service account ops (账户设置: 改密码 / 改资料 / 注销) ---


async def test_change_password_keeps_session_and_rotates_secret(client):
    await register_and_login(client, "harry")

    r = await client.post(
        "/v1/auth/change-password",
        json={"current_password": _PW, "new_password": "newpassword456"},
    )
    assert r.status_code == 200, r.text
    # this session stays valid — the route re-issues fresh cookies
    assert (await client.get("/v1/auth/me")).status_code == 200
    # old password is dead; the new one logs in
    assert (
        await client.post(
            "/v1/auth/login",
            json={"username": "harry", "password": _PW},
            headers=_DESKTOP,
        )
    ).status_code == 401
    assert (
        await client.post(
            "/v1/auth/login",
            json={"username": "harry", "password": "newpassword456"},
            headers=_DESKTOP,
        )
    ).status_code == 200


async def test_change_password_wrong_current_rejected(client):
    await register_and_login(client, "iris")
    r = await client.post(
        "/v1/auth/change-password",
        json={"current_password": "wrong-pw", "new_password": "newpassword456"},
    )
    assert r.status_code == 401


async def test_change_password_requires_auth(client):
    r = await client.post(
        "/v1/auth/change-password",
        json={"current_password": _PW, "new_password": "newpassword456"},
    )
    assert r.status_code == 401


async def test_update_profile_changes_display_name_and_email(client):
    await register_and_login(client, "jack")

    r = await client.patch(
        "/v1/auth/me",
        json={"display_name": "Jack Jones", "email": "jack@example.com"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Jack Jones"
    assert body["email"] == "jack@example.com"
    # persisted across a fresh read
    me = (await client.get("/v1/auth/me")).json()
    assert me["display_name"] == "Jack Jones" and me["email"] == "jack@example.com"


async def test_update_profile_rejects_duplicate_email(client, new_client):
    await register_and_login(client, "kate")
    assert (
        await client.patch("/v1/auth/me", json={"email": "shared@example.com"})
    ).status_code == 200

    async with new_client() as other:
        await register_and_login(other, "liam")
        r = await other.patch("/v1/auth/me", json={"email": "shared@example.com"})
        assert r.status_code == 422


async def test_update_profile_requires_auth(client):
    assert (await client.patch("/v1/auth/me", json={"display_name": "x"})).status_code == 401


async def test_delete_account_anonymizes_and_frees_username(client):
    await register_and_login(client, "mona")
    # give the account data so the route's conversation cascade actually runs
    assert (await client.post("/v1/conversations", json={"title": "to be gone"})).status_code == 201

    r = await client.request("DELETE", "/v1/auth/me", json={"password": _PW})
    assert r.status_code == 200, r.text
    # cookies cleared → unauthenticated, and the old credentials are dead
    assert (await client.get("/v1/auth/me")).status_code == 401
    assert (
        await client.post(
            "/v1/auth/login",
            json={"username": "mona", "password": _PW},
            headers=_DESKTOP,
        )
    ).status_code == 401
    # the username was anonymized away → a brand-new account can reclaim it
    r = await client.post(
        "/v1/auth/register",
        json={"username": "mona", "password": _PW},
    )
    assert r.status_code == 201, r.text


async def test_delete_account_wrong_password_rejected(client):
    await register_and_login(client, "nate")
    r = await client.request("DELETE", "/v1/auth/me", json={"password": "wrong-pw"})
    assert r.status_code == 401
    # the account is untouched and still usable
    assert (await client.get("/v1/auth/me")).status_code == 200


async def test_delete_account_requires_auth(client):
    r = await client.request("DELETE", "/v1/auth/me", json={"password": _PW})
    assert r.status_code == 401


# --- bearer-token flow (mobile web / Capacitor shell, M2) ---


async def test_token_login_returns_tokens_and_authorizes_via_bearer(client):
    r = await client.post(
        "/v1/auth/register",
        json={"username": "mobile1", "password": _PW},
    )
    assert r.status_code == 201, r.text

    r = await client.post(
        "/v1/auth/token",
        json={"username": "mobile1", "password": _PW},
        headers=_DESKTOP,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer" and body["expires_in"] > 0
    assert body["refresh_expires_in"] and body["refresh_expires_in"] > body["expires_in"]
    assert body["user"]["username"] == "mobile1"
    # The bearer flow sets NO cookies (it's for capacitor:// / new-origin clients).
    assert "access_token" not in client.cookies

    # The access token alone (no cookie) authorizes a protected route.
    me = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200 and me.json()["username"] == "mobile1"


async def test_bearer_invalid_token_rejected(client):
    r = await client.get("/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


async def test_token_refresh_rotates_via_body_and_detects_reuse(client, monkeypatch):
    # Close the grace window so the old token re-presented after rotation reads as
    # a genuine reuse (benign within-grace concurrency is unit-tested separately).
    monkeypatch.setattr("agentcore.auth.service._REFRESH_REUSE_GRACE", timedelta(0))
    await client.post(
        "/v1/auth/register",
        json={"username": "mobile2", "password": _PW},
    )
    tok = (
        await client.post(
            "/v1/auth/token",
            json={"username": "mobile2", "password": _PW},
            headers=_DESKTOP,
        )
    ).json()

    r = await client.post("/v1/auth/token/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r.status_code == 200, r.text
    rotated = r.json()
    assert rotated["refresh_token"] != tok["refresh_token"]
    # the rotated access token authorizes
    me = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {rotated['access_token']}"},
    )
    assert me.status_code == 200

    # replaying the old (already-rotated) refresh token is reuse -> 401
    assert (
        await client.post("/v1/auth/token/refresh", json={"refresh_token": tok["refresh_token"]})
    ).status_code == 401


async def test_token_revoke_kills_refresh(client):
    await client.post(
        "/v1/auth/register",
        json={"username": "mobile3", "password": _PW},
    )
    tok = (
        await client.post(
            "/v1/auth/token",
            json={"username": "mobile3", "password": _PW},
            headers=_DESKTOP,
        )
    ).json()

    assert (
        await client.post("/v1/auth/token/revoke", json={"refresh_token": tok["refresh_token"]})
    ).status_code == 200
    # a revoked refresh token can no longer rotate
    assert (
        await client.post("/v1/auth/token/refresh", json={"refresh_token": tok["refresh_token"]})
    ).status_code == 401


async def test_admin_cannot_login_on_desktop(client, make_admin):
    username, password = await make_admin()
    r = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Client-Platform": "desktop"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "ADMIN_PRODUCT_FORBIDDEN"


async def test_admin_mfa_login_flow(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    assert (await client.get("/v1/admin/overview")).status_code == 200


async def test_admin_password_only_when_mfa_disabled(client, make_admin, monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "admin_mfa_required", False)
    username, password = await make_admin()
    r = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Client-Platform": "admin"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mfa_required"] is False
    assert body["mfa_setup_required"] is False
    assert (await client.get("/v1/admin/overview")).status_code == 200


async def test_mfa_status_reports_required_flag(client, make_admin, monkeypatch):
    from agentcore.config import settings

    username, password = await make_admin()
    await login_admin(client, username, password)
    monkeypatch.setattr(settings, "admin_mfa_required", False)
    r = await client.get("/v1/auth/mfa/status")
    assert r.status_code == 200
    assert r.json()["required"] is False


# --- sessions (device management) ---


async def test_list_sessions_aggregates_and_marks_current(client, new_client):
    await register_and_login(client, "sess_alice")

    async with new_client() as other:
        r = await other.post(
            "/v1/auth/login",
            json={"username": "sess_alice", "password": _PW},
            headers=_DESKTOP,
        )
        assert r.status_code == 200

    listed = await client.get("/v1/auth/sessions")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] >= 2
    currents = [s for s in body["data"] if s["current"]]
    assert len(currents) == 1
    assert currents[0]["platform"] == "desktop"
    assert "user_agent" in currents[0] and "ip" in currents[0]
    assert currents[0]["created_at"] and currents[0]["last_used_at"]


async def test_sessions_cross_user_family_404(client, new_client):
    await register_and_login(client, "sess_owner")
    family_id = (await client.get("/v1/auth/sessions")).json()["data"][0]["id"]

    async with new_client() as attacker:
        await register_and_login(attacker, "sess_attacker")
        assert (
            await attacker.delete(f"/v1/auth/sessions/{family_id}")
        ).status_code == 404


async def test_revoke_session_blocks_refresh(client):
    await register_and_login(client, "sess_bob")
    family_id = (await client.get("/v1/auth/sessions")).json()["data"][0]["id"]
    assert (await client.delete(f"/v1/auth/sessions/{family_id}")).status_code == 200
    assert (await client.post("/v1/auth/refresh")).status_code == 401


async def test_revoke_others_keeps_current(client, new_client):
    await register_and_login(client, "sess_carol")

    async with new_client() as other:
        r = await other.post(
            "/v1/auth/login",
            json={"username": "sess_carol", "password": _PW},
            headers=_DESKTOP,
        )
        assert r.status_code == 200
        other_refresh = other.cookies.get("refresh_token")

        assert (await client.post("/v1/auth/sessions/revoke-others")).status_code == 200
        # current device still refreshes
        assert (await client.post("/v1/auth/refresh")).status_code == 200
        # other device's refresh is dead
        resp = await other.post(
            "/v1/auth/refresh", headers={"Cookie": f"refresh_token={other_refresh}"}
        )
        assert resp.status_code == 401


async def test_family_max_days_rejects_refresh(client, session_factory, monkeypatch):
    from datetime import UTC, datetime

    from sqlalchemy import select, update

    from agentcore.db.models import RefreshToken

    monkeypatch.setattr(settings, "refresh_family_max_days", 1)
    await register_and_login(client, "sess_dave")

    async with session_factory() as session:
        result = await session.execute(select(RefreshToken))
        rows = list(result.scalars().all())
        assert rows
        await session.execute(
            update(RefreshToken).values(
                family_started_at=datetime.now(UTC) - timedelta(days=2)
            )
        )
        await session.commit()

    assert (await client.post("/v1/auth/refresh")).status_code == 401


async def test_admin_family_max_hours_rejects_refresh(
    client, make_admin, session_factory, monkeypatch
):
    from datetime import UTC, datetime

    from sqlalchemy import update

    from agentcore.db.models import RefreshToken

    monkeypatch.setattr(settings, "admin_mfa_required", False)
    monkeypatch.setattr(settings, "admin_refresh_family_max_hours", 1)
    username, password = await make_admin()
    r = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Client-Platform": "admin"},
    )
    assert r.status_code == 200, r.text

    async with session_factory() as session:
        await session.execute(
            update(RefreshToken).values(
                family_started_at=datetime.now(UTC) - timedelta(hours=2)
            )
        )
        await session.commit()

    assert (await client.post("/v1/auth/refresh")).status_code == 401


async def test_refresh_token_gc_keeps_active_tips(session_factory, monkeypatch):
    from datetime import UTC, datetime

    import agentcore.auth.retention as retention_mod
    from agentcore.auth.retention import run_refresh_token_retention_sweep
    from agentcore.db.models import RefreshToken
    from agentcore.db.repositories import CredentialsRepository, UserRepository
    from agentcore.security import hash_password, hash_refresh_token

    monkeypatch.setattr(settings, "refresh_token_retention_days", 7)
    monkeypatch.setattr(retention_mod, "async_session_factory", session_factory)

    async with session_factory() as session:
        user = await UserRepository(session).create(
            username=f"gc_{uuid4().hex[:8]}", display_name="GC"
        )
        await CredentialsRepository(session).create(
            user_id=user.user_id, password_hash=hash_password(_PW)
        )
        family = str(uuid4())
        live = RefreshToken(
            id=str(uuid4()),
            user_id=user.user_id,
            token_hash=hash_refresh_token(f"live-{uuid4().hex}"),
            token_family=family,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            client_aud="product",
            family_started_at=datetime.now(UTC),
            last_used_at=datetime.now(UTC),
        )
        old_rotated = RefreshToken(
            id=str(uuid4()),
            user_id=user.user_id,
            token_hash=hash_refresh_token(f"old-{uuid4().hex}"),
            token_family=family,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            client_aud="product",
            rotated_at=datetime.now(UTC) - timedelta(days=10),
            family_started_at=datetime.now(UTC) - timedelta(days=10),
            last_used_at=datetime.now(UTC) - timedelta(days=10),
        )
        session.add_all([live, old_rotated])
        await session.commit()
        live_id, old_id = live.id, old_rotated.id

    deleted = await run_refresh_token_retention_sweep()
    assert deleted >= 1

    async with session_factory() as session:
        assert await session.get(RefreshToken, live_id) is not None
        assert await session.get(RefreshToken, old_id) is None

