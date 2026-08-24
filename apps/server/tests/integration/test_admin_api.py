"""Integration tests for the admin console API (``/v1/admin/*``, real PG).

Covers 用户管理 P0 (the ``AdminUser`` gate, the account roster, role/status/quota
patches with tri-state quota semantics, the disable→access-revoked chain, and the
no-self-lockout guard), plus 全站用量看板 P1 (``/usage/summary`` cross-user
aggregation) and 系统状态 P2 (``/system`` read-only snapshot) — all end-to-end over
the full HTTP chain (cookies, DI, error mapping).
"""

from uuid import uuid4

from agentcore.config import settings
from agentcore.core.types import new_id
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    MessageRepository,
    TurnJournalRepository,
    TurnMetricsRepository,
    UserRepository,
)
from agentcore.llm.pricing import NANO_PER_CNY
from tests.integration.conftest import (
    client_platform_headers,
    login_admin,
    register_and_login,
)

_PW = "password123"
_DESKTOP = client_platform_headers()


async def _seed_spend(session_factory, *, user_id: str, total: int, role: str = "captain") -> None:
    """Seed one priced turn (one distinct message_id) for ``user_id`` into the
    ledger, landing in today's + this month's windows (created_at server-defaults
    to now). The write path itself is covered by test_cost_ledger.py."""
    async with session_factory() as session:
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=new_id(),
            message_id=new_id(),
            runs=[
                {
                    "run_id": new_id(),
                    "parent_run_id": None,
                    "agent_id": new_id(),
                    "role": role,
                    "model": "deepseek-v4-pro",
                    "tokens": {
                        "input": 100,
                        "output": 50,
                        "reasoning": 0,
                        "cache_hit": 60,
                        "cache_miss": 40,
                    },
                    "cost": {"input": 800, "cached": 100, "output": 200, "total": total},
                    "cost_total_nano": total,
                    "currency": "USD",
                    "rounds": 1,
                    "duration_ms": 500,
                }
            ],
        )


async def _seed_calls(
    session_factory,
    *,
    user_id: str,
    model: str,
    total: int,
    calls: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 50,
    estimated: int = 0,
) -> None:
    """Seed ``cost_calls`` rows (authority for per-model aggregates).

    Used by platform ``month_by_model`` and per-user ``recent_by_model`` — both
    must scan ``cost_calls``, never ``cost_events.model``.
    """
    async with session_factory() as session:
        await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=new_id(),
            message_id=new_id(),
            calls=[
                {
                    "call_id": new_id(),
                    "run_id": new_id(),
                    "parent_run_id": None,
                    "agent_id": new_id(),
                    "role": "captain",
                    "model": model,
                    "tokens": {
                        "input": input_tokens,
                        "output": output_tokens,
                        "reasoning": 0,
                        "cache_hit": 0,
                        "cache_miss": input_tokens,
                    },
                    "cost": {
                        "input": total // 2,
                        "cached": 0,
                        "output": total - total // 2,
                        "total": total,
                    },
                    "cost_total_nano": total,
                    "cost_estimated_nano": estimated,
                    "currency": "USD",
                    "duration_ms": 100,
                }
                for _ in range(calls)
            ],
        )


async def _seed_llm_key(
    session_factory,
    *,
    user_id: str,
    default_model: str = "deepseek-v4-pro",
    background_model: str | None = "deepseek-v4-flash",
) -> None:
    """Seed a BYOK provider + default 模型组合 (ciphertext stub — admin detail
    only reads main/background model names from the account default profile)."""
    from agentcore.db.repositories import (
        LlmModelProfileRepository,
        UserLlmProviderRepository,
        UserRepository,
    )

    async with session_factory() as session:
        provider = await UserLlmProviderRepository(session).create(
            user_id=user_id,
            label="DeepSeek",
            api_key_enc=b"test-cipher-not-a-real-key",
            default_model=default_model,
        )
        profile = await LlmModelProfileRepository(session).create(
            user_id=user_id,
            name="default",
            main_origin="byok",
            main_provider_id=provider.id,
            main_model=default_model,
            background_origin="byok" if background_model else None,
            background_provider_id=provider.id if background_model else None,
            background_model=background_model,
        )
        await UserRepository(session).set_default_model_profile(user_id, profile.id)


async def _seed_user(
    session_factory,
    username: str,
    *,
    role: str = "user",
    status: str = "active",
    registration_ip: str | None = None,
) -> str:
    async with session_factory() as session:
        user = await UserRepository(session).create(
            username=username,
            display_name=username,
            role=role,
            status=status,
            registration_ip=registration_ip,
        )
    return user.user_id


async def _seed_refresh_token(
    session_factory,
    *,
    user_id: str,
    ip: str | None = None,
    platform: str = "desktop",
) -> str:
    """Insert one active refresh-token tip so admin IP filter / sessions can see it."""
    from datetime import UTC, datetime, timedelta

    from agentcore.db.repositories.auth import RefreshTokenRepository

    async with session_factory() as session:
        row = await RefreshTokenRepository(session).create(
            user_id=user_id,
            token_hash=f"hash-{new_id()}",
            token_family=new_id(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            client_platform=platform,
            user_agent="AgentCoreTest/1.0",
            ip=ip,
        )
    return row.token_family


async def _soft_delete_user(session_factory, user_id: str) -> None:
    """注销 a seeded account (the self-service deletion path) so the admin-side
    tombstone behavior can be asserted."""
    async with session_factory() as session:
        await UserRepository(session).soft_delete(user_id)


# --- the AdminUser gate ---


async def test_admin_users_require_auth(client):
    assert (await client.get("/v1/admin/users")).status_code == 401
    assert (await client.patch("/v1/admin/users/anyone", json={"role": "admin"})).status_code == 401


async def test_non_admin_cannot_access_admin_users(client):
    await register_and_login(client, "regular")
    me = (await client.get("/v1/auth/me")).json()["id"]

    assert (await client.get("/v1/admin/users")).status_code == 403
    # a non-admin can't even self-escalate: the gate rejects before the service runs
    assert (await client.patch(f"/v1/admin/users/{me}", json={"role": "admin"})).status_code == 403


# --- roster: listing, filter, pagination ---


async def test_admin_lists_roster_with_quota_fields(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    await _seed_user(session_factory, "alice")
    await _seed_user(session_factory, "bob")

    r = await client.get("/v1/admin/users")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3  # admin + alice + bob
    assert body["page"] == 1 and body["page_size"] == 20
    assert {"admin", "alice", "bob"} <= {u["username"] for u in body["data"]}
    # rows carry the admin-only account + quota fields (richer than the self-view)
    row = body["data"][0]
    for key in ("status", "is_unlimited", "quota_daily_tokens", "role", "created_at"):
        assert key in row


async def test_admin_roster_filter_and_pagination(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    await _seed_user(session_factory, "alice")
    await _seed_user(session_factory, "alicia")
    await _seed_user(session_factory, "bob")

    r = await client.get("/v1/admin/users", params={"q": "alic"})
    body = r.json()
    assert body["total"] == 2
    assert {u["username"] for u in body["data"]} == {"alice", "alicia"}

    r = await client.get("/v1/admin/users", params={"page_size": 2})
    body = r.json()
    assert body["page_size"] == 2 and len(body["data"]) == 2 and body["total"] == 4


async def test_admin_roster_hides_deleted_by_default(client, make_admin, session_factory):
    """注销 (soft-deleted, anonymized) accounts are tombstones: excluded from the
    roster (and its total) by default, surfaced only with ``include_deleted`` — and
    when surfaced they carry the ``deleted_at`` flag + anonymized username."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    await _seed_user(session_factory, "alice")
    gone = await _seed_user(session_factory, "zombie")
    await _soft_delete_user(session_factory, gone)

    # Default roster: live accounts only (admin + alice); the tombstone is hidden.
    r = await client.get("/v1/admin/users")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert {u["username"] for u in body["data"]} == {username, "alice"}
    assert all(u["deleted_at"] is None for u in body["data"])

    # Audit view: the tombstone reappears — anonymized (deleted_<id>), flagged.
    r = await client.get("/v1/admin/users", params={"include_deleted": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    tomb = next(u for u in body["data"] if u["id"] == gone)
    assert tomb["username"] == f"deleted_{gone}"
    assert tomb["deleted_at"] is not None
    assert tomb["status"] == "disabled"


# --- roster: cumulative cost + sort + role/status filters ---


async def test_admin_roster_carries_cost_and_sorts_by_spend(client, make_admin, session_factory):
    """Each roster row carries its all-time spend (``cost_total``), and ``sort=cost``
    orders by it; the response ships the FX rate for ¥ display. A never-spent account
    reads 0 (LEFT JOIN onto the ledger)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    bob = await _seed_user(session_factory, "bob")
    # alice outspends bob over two turns; the admin never spent (→ 0).
    await _seed_spend(session_factory, user_id=alice, total=5000)
    await _seed_spend(session_factory, user_id=alice, total=3000)
    await _seed_spend(session_factory, user_id=bob, total=1000)

    # Default sort still carries cost_total per row (nano-CNY; no FX field).
    body = (await client.get("/v1/admin/users")).json()
    assert "cny_per_usd" not in body
    costs = {u["id"]: u["cost_total"] for u in body["data"]}
    assert costs[alice] == 8000
    assert costs[bob] == 1000
    admin_id = next(u["id"] for u in body["data"] if u["username"] == username)
    assert costs[admin_id] == 0  # never spent

    # sort=cost desc: biggest spender first; the zero-spend admin sinks to the end.
    desc = (await client.get("/v1/admin/users", params={"sort": "cost", "order": "desc"})).json()[
        "data"
    ]
    assert [u["id"] for u in desc][:2] == [alice, bob]
    assert desc[-1]["cost_total"] == 0

    # sort=cost asc: mirror — biggest spender last.
    asc = (await client.get("/v1/admin/users", params={"sort": "cost", "order": "asc"})).json()[
        "data"
    ]
    assert [u["id"] for u in asc][-2:] == [bob, alice]


async def test_admin_roster_sorts_by_created_at_order(client, make_admin, session_factory):
    """``order`` flips the default ``created_at`` sort: desc is newest-first, asc is
    oldest-first. The admin is seeded first, so it leads asc and trails desc."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    await _seed_user(session_factory, "alice")
    await _seed_user(session_factory, "bob")

    desc = (await client.get("/v1/admin/users")).json()["data"]
    asc = (await client.get("/v1/admin/users", params={"order": "asc"})).json()["data"]
    assert desc[-1]["username"] == username  # oldest account trails newest-first
    assert asc[0]["username"] == username  # …and leads oldest-first


async def test_admin_roster_filters_by_role_and_status(client, make_admin, session_factory):
    """``role`` / ``status`` pin those dimensions, AND-combined with each other."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    await _seed_user(session_factory, "alice")  # user / active
    await _seed_user(session_factory, "carol", role="admin")
    await _seed_user(session_factory, "dave", status="disabled")  # user / disabled

    # role=admin → the make_admin account + carol.
    admins = (await client.get("/v1/admin/users", params={"role": "admin"})).json()
    assert {u["username"] for u in admins["data"]} == {username, "carol"}
    assert admins["total"] == 2

    # role=user → alice + dave (the plain users, regardless of status).
    plain = (await client.get("/v1/admin/users", params={"role": "user"})).json()
    assert {u["username"] for u in plain["data"]} == {"alice", "dave"}

    # status=disabled → only dave.
    disabled = (await client.get("/v1/admin/users", params={"status": "disabled"})).json()
    assert {u["username"] for u in disabled["data"]} == {"dave"}
    assert disabled["total"] == 1

    # AND-combined: role=user & status=active → alice alone.
    combo = (
        await client.get("/v1/admin/users", params={"role": "user", "status": "active"})
    ).json()
    assert {u["username"] for u in combo["data"]} == {"alice"}


async def test_admin_roster_filters_by_ip_and_registration_time(
    client, make_admin, session_factory
):
    """``ip`` matches registration_ip OR any refresh_tokens.ip; ``since``/``until``
    bound created_at. Register path writes registration_ip via get_client_ip."""
    from datetime import UTC, datetime, timedelta

    username, password = await make_admin()
    await login_admin(client, username, password)

    # registration_ip match
    await _seed_user(session_factory, "reg_ip", registration_ip="198.51.100.7")
    # login-IP-only match (no registration_ip)
    login_only = await _seed_user(session_factory, "login_ip")
    await _seed_refresh_token(session_factory, user_id=login_only, ip="198.51.100.7")
    # unrelated
    await _seed_user(session_factory, "other_ip", registration_ip="203.0.113.9")

    by_ip = (await client.get("/v1/admin/users", params={"ip": "198.51.100.7"})).json()
    assert {u["username"] for u in by_ip["data"]} == {"reg_ip", "login_ip"}
    assert by_ip["total"] == 2
    assert all("registration_ip" in u for u in by_ip["data"])

    # Registration via HTTP writes the peer IP (TestClient → typically "testclient").
    r = await client.post(
        "/v1/auth/register",
        json={"username": "fresh_reg", "password": _PW},
    )
    assert r.status_code == 201, r.text
    roster = (await client.get("/v1/admin/users", params={"q": "fresh_reg"})).json()
    assert roster["total"] == 1
    assert roster["data"][0]["registration_ip"]  # non-empty peer IP

    # since / until bound created_at.
    past_until = (datetime.now(UTC) - timedelta(days=3650)).isoformat()
    assert (await client.get("/v1/admin/users", params={"until": past_until})).json()[
        "total"
    ] == 0
    future_since = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert (await client.get("/v1/admin/users", params={"since": future_since})).json()[
        "total"
    ] == 0
    # since=far past includes the freshly registered account.
    past_since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    recent = (
        await client.get(
            "/v1/admin/users", params={"q": "fresh_reg", "since": past_since}
        )
    ).json()
    assert recent["total"] == 1


async def test_admin_roster_rejects_invalid_filter_params(client, make_admin):
    """The enum-shaped query params are validated at the edge (422), never silently
    coerced to a wrong filter."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    for params in (
        {"role": "superuser"},
        {"status": "frozen"},
        {"sort": "username"},
        {"order": "sideways"},
    ):
        r = await client.get("/v1/admin/users", params=params)
        assert r.status_code == 422, (params, r.text)


# --- role / status / quota patches ---


async def test_admin_changes_role(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    uid = await _seed_user(session_factory, "alice")

    r = await client.patch(f"/v1/admin/users/{uid}", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    r = await client.patch(f"/v1/admin/users/{uid}", json={"role": "user"})
    assert r.json()["role"] == "user"


async def test_admin_disable_revokes_target_access(client, new_client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    async with new_client() as target:
        await register_and_login(target, "victim")
        uid = (await target.get("/v1/auth/me")).json()["id"]
        assert (await target.get("/v1/auth/me")).status_code == 200

        r = await client.patch(f"/v1/admin/users/{uid}", json={"status": "disabled"})
        assert r.status_code == 200 and r.json()["status"] == "disabled"

        # the disabled account is refused on its very next request (status re-checked)
        assert (await target.get("/v1/auth/me")).status_code == 401


# --- password reset (重置密码) ---


async def test_admin_resets_user_password(client, new_client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    async with new_client() as target:
        await register_and_login(target, "forgetful")
        uid = (await target.get("/v1/auth/me")).json()["id"]

        r = await client.post(f"/v1/admin/users/{uid}/reset-password")
        assert r.status_code == 200, r.text
        temp = r.json()["temporary_password"]
        assert len(temp) >= 8

        # every pre-reset session is revoked — the old refresh token is dead
        assert (await target.post("/v1/auth/refresh")).status_code == 401

    # the old password no longer logs in; the one-off temp password does
    async with new_client() as fresh:
        assert (
            await fresh.post(
                "/v1/auth/login",
                json={"username": "forgetful", "password": _PW},
                headers=_DESKTOP,
            )
        ).status_code == 401
        assert (
            await fresh.post(
                "/v1/auth/login",
                json={"username": "forgetful", "password": temp},
                headers=_DESKTOP,
            )
        ).status_code == 200
        me = (await fresh.get("/v1/auth/me")).json()
        assert me["password_must_change"] is True


async def test_reset_password_unknown_user_404(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    assert (await client.post(f"/v1/admin/users/{uuid4()}/reset-password")).status_code == 404


async def test_reset_password_requires_admin(client):
    await register_and_login(client, "plainuser")
    me = (await client.get("/v1/auth/me")).json()["id"]
    # even targeting self, the role gate refuses a non-admin before the service runs
    assert (await client.post(f"/v1/admin/users/{me}/reset-password")).status_code == 403


# --- set password (设置密码) ---

_CUSTOM_PW = "custompass99"


async def test_admin_sets_user_password(client, new_client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    async with new_client() as target:
        await register_and_login(target, "settarget")
        uid = (await target.get("/v1/auth/me")).json()["id"]

        r = await client.post(
            f"/v1/admin/users/{uid}/set-password",
            json={"new_password": _CUSTOM_PW},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"

        assert (await target.post("/v1/auth/refresh")).status_code == 401

    async with new_client() as fresh:
        assert (
            await fresh.post(
                "/v1/auth/login",
                json={"username": "settarget", "password": _PW},
                headers=_DESKTOP,
            )
        ).status_code == 401
        assert (
            await fresh.post(
                "/v1/auth/login",
                json={"username": "settarget", "password": _CUSTOM_PW},
                headers=_DESKTOP,
            )
        ).status_code == 200
        me = (await fresh.get("/v1/auth/me")).json()
        assert me["password_must_change"] is True


async def test_set_password_force_change_false(client, new_client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    async with new_client() as target:
        await register_and_login(target, "permuser")
        uid = (await target.get("/v1/auth/me")).json()["id"]

        r = await client.post(
            f"/v1/admin/users/{uid}/set-password",
            json={"new_password": _CUSTOM_PW, "force_change": False},
        )
        assert r.status_code == 200, r.text

    async with new_client() as fresh:
        assert (
            await fresh.post(
                "/v1/auth/login",
                json={"username": "permuser", "password": _CUSTOM_PW},
                headers=_DESKTOP,
            )
        ).status_code == 200
        me = (await fresh.get("/v1/auth/me")).json()
        assert me["password_must_change"] is False


async def test_set_password_weak_rejected(client, new_client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    async with new_client() as target:
        await register_and_login(target, "weaktarget")
        uid = (await target.get("/v1/auth/me")).json()["id"]

    assert (
        await client.post(
            f"/v1/admin/users/{uid}/set-password",
            json={"new_password": "short"},
        )
    ).status_code == 422


async def test_set_password_unknown_user_404(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    assert (
        await client.post(
            f"/v1/admin/users/{uuid4()}/set-password",
            json={"new_password": _CUSTOM_PW},
        )
    ).status_code == 404


async def test_set_password_requires_admin(client):
    await register_and_login(client, "plainuser2")
    me = (await client.get("/v1/auth/me")).json()["id"]
    assert (
        await client.post(
            f"/v1/admin/users/{me}/set-password",
            json={"new_password": _CUSTOM_PW},
        )
    ).status_code == 403


# --- 注销账号 (admin-initiated deletion, 用户管理 强操作) ---


async def test_admin_deletes_user_anonymizes_and_cascades(client, make_admin, session_factory):
    """DELETE 注销s an account: anonymizes + disables it (returns the tombstone with
    ``deleted_at``), drops it from the default roster + the system tallies, and
    cascades cross-domain cleanup (the user's conversations are soft-deleted)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    uid = await _seed_user(session_factory, "alice")
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(user_id=uid, title="留念")
    conv_id = conv.id

    r = await client.delete(f"/v1/admin/users/{uid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_at"] is not None
    assert body["username"] == f"deleted_{uid}"
    assert body["status"] == "disabled"

    # Default roster hides the tombstone; system total drops to the live count (admin).
    roster = (await client.get("/v1/admin/users")).json()
    assert uid not in {u["id"] for u in roster["data"]}
    assert (await client.get("/v1/admin/system")).json()["users_total"] == 1

    # Cross-domain cascade: the account's conversation was soft-deleted.
    async with session_factory() as session:
        assert await ConversationRepository(session).get_by_id_unscoped(conv_id) is None


async def test_admin_cannot_delete_self(client, make_admin):
    """No self-lockout: an admin can't 注销 their own account (keeps ≥1 active admin)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    me = (await client.get("/v1/auth/me")).json()["id"]

    assert (await client.delete(f"/v1/admin/users/{me}")).status_code == 422
    # untouched: still present in the roster
    roster = (await client.get("/v1/admin/users")).json()
    assert me in {u["id"] for u in roster["data"]}


async def test_admin_delete_unknown_user_404(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    assert (await client.delete(f"/v1/admin/users/{uuid4()}")).status_code == 404


async def test_delete_user_requires_admin(client):
    # unauthenticated → 401 (the gate rejects before any lookup)
    assert (await client.delete(f"/v1/admin/users/{uuid4()}")).status_code == 401
    # a logged-in non-admin → 403, even targeting their own account
    await register_and_login(client, "regular_delu")
    me = (await client.get("/v1/auth/me")).json()["id"]
    assert (await client.delete(f"/v1/admin/users/{me}")).status_code == 403


async def test_admin_sets_then_clears_quota(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    uid = await _seed_user(session_factory, "alice")

    r = await client.patch(
        f"/v1/admin/users/{uid}",
        json={
            "is_unlimited": True,
            "quota_daily_tokens": 1000,
            "quota_monthly_cost_cny": 5.5,
            "quota_daily_requests": 50,
        },
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["is_unlimited"] is True
    assert b["quota_daily_tokens"] == 1000
    assert b["quota_monthly_cost_cny"] == 5.5
    assert b["quota_daily_requests"] == 50

    # explicit null clears one override (inherit global); untouched fields persist
    r = await client.patch(f"/v1/admin/users/{uid}", json={"quota_daily_tokens": None})
    b = r.json()
    assert b["quota_daily_tokens"] is None
    assert b["quota_daily_requests"] == 50


# --- guards & validation ---


async def test_admin_cannot_self_demote_or_self_disable(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    me = (await client.get("/v1/auth/me")).json()["id"]

    assert (await client.patch(f"/v1/admin/users/{me}", json={"role": "user"})).status_code == 422
    assert (
        await client.patch(f"/v1/admin/users/{me}", json={"status": "disabled"})
    ).status_code == 422
    # a harmless self-patch (own quota) is still allowed
    r = await client.patch(f"/v1/admin/users/{me}", json={"quota_daily_tokens": 999})
    assert r.status_code == 200 and r.json()["quota_daily_tokens"] == 999


async def test_admin_update_unknown_user_404(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    r = await client.patch(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000",
        json={"role": "admin"},
    )
    assert r.status_code == 404


async def test_admin_rejects_invalid_values(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    uid = await _seed_user(session_factory, "alice")

    assert (
        await client.patch(f"/v1/admin/users/{uid}", json={"role": "superuser"})
    ).status_code == 422
    assert (
        await client.patch(f"/v1/admin/users/{uid}", json={"quota_daily_tokens": -5})
    ).status_code == 422


# --- 全站用量看板 (P1) + 系统状态 (P2) gate ---


async def test_admin_usage_and_system_require_auth(client):
    assert (await client.get("/v1/admin/usage/summary")).status_code == 401
    assert (await client.get("/v1/admin/system")).status_code == 401


async def test_non_admin_cannot_access_usage_or_system(client):
    await register_and_login(client, "regular2")
    assert (await client.get("/v1/admin/usage/summary")).status_code == 403
    assert (await client.get("/v1/admin/system")).status_code == 403


# --- 全站用量看板: cross-user aggregation ---


async def test_admin_usage_summary_aggregates_across_users(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    bob = await _seed_user(session_factory, "bob")

    # alice outspends bob over two turns; bob has one. The admin itself never spent,
    # so it must be absent from the by-user payroll (>0 only).
    await _seed_spend(session_factory, user_id=alice, total=5000)
    await _seed_spend(session_factory, user_id=alice, total=3000)
    await _seed_spend(session_factory, user_id=bob, total=1000)

    r = await client.get("/v1/admin/usage/summary")
    assert r.status_code == 200, r.text
    b = r.json()

    # Platform totals span *every* account; all spend is "now" → today == month.
    assert b["today"]["cost"]["total"] == 9000
    assert b["month"]["cost"]["total"] == 9000
    assert b["today"]["requests"] == 3  # three distinct message_ids

    # Top spenders by user, spend-desc: alice (8000, 2 turns) before bob (1000, 1).
    by_user = b["month_by_user"]
    assert [u["user_id"] for u in by_user] == [alice, bob]
    assert by_user[0]["username"] == "alice"
    assert by_user[0]["cost_total"] == 8000
    assert by_user[0]["turns"] == 2
    assert by_user[1]["cost_total"] == 1000

    # The 7-day trend is a fixed-length series; today carries the whole spend.
    trend = b["recent_daily_cost"]
    assert len(trend) == 7
    assert trend[-1]["cost_total"] == 9000
    assert sum(p["cost_total"] for p in trend) == 9000
    assert b["billing_mode"] == settings.billing_mode


async def test_admin_usage_summary_splits_month_by_model(client, make_admin, session_factory):
    """全站看板 splits this month's spend by model from ``cost_calls`` (GROUP BY model),
    never ``cost_events.model`` — multi-model runs would otherwise mis-attribute."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    bob = await _seed_user(session_factory, "bob")

    # cost_events alone must NOT produce month_by_model rows (wrong source).
    await _seed_spend(session_factory, user_id=alice, total=9999)

    # Two models across two accounts; pro outspends flash; tokens sum per model.
    await _seed_calls(
        session_factory,
        user_id=alice,
        model="deepseek-v4-pro",
        total=5000,
        input_tokens=100,
        output_tokens=100,
    )
    await _seed_calls(
        session_factory,
        user_id=bob,
        model="deepseek-v4-pro",
        total=3000,
        input_tokens=50,
        output_tokens=50,
    )
    await _seed_calls(
        session_factory,
        user_id=alice,
        model="deepseek-v4-flash",
        total=400,
        input_tokens=25,
        output_tokens=25,
    )
    await _seed_calls(
        session_factory,
        user_id=bob,
        model="deepseek-v4-flash",
        total=200,
        input_tokens=15,
        output_tokens=15,
        estimated=50,
    )

    r = await client.get("/v1/admin/usage/summary")
    assert r.status_code == 200, r.text
    rows = r.json()["month_by_model"]

    assert [row["model"] for row in rows] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    by_model = {row["model"]: row for row in rows}
    assert by_model["deepseek-v4-pro"] == {
        "model": "deepseek-v4-pro",
        "calls": 2,
        "tokens_total": 300,
        "cost_total": 8000,
        "cost_estimated_total": 0,
    }
    assert by_model["deepseek-v4-flash"] == {
        "model": "deepseek-v4-flash",
        "calls": 2,
        "tokens_total": 80,
        "cost_total": 600,
        "cost_estimated_total": 50,
    }


async def test_admin_usage_summary_empty_is_zero(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    r = await client.get("/v1/admin/usage/summary")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["today"]["cost"]["total"] == 0
    assert b["month_by_user"] == []
    assert b["month_by_model"] == []
    assert [p["cost_total"] for p in b["recent_daily_cost"]] == [0] * 7


# --- 系统状态: read-only deployment snapshot ---


async def test_admin_system_status_reports_config_health_and_counts(
    client, make_admin, session_factory
):
    username, password = await make_admin()
    await login_admin(client, username, password)
    await _seed_user(session_factory, "alice")
    await _seed_user(session_factory, "carol", role="admin")
    await _seed_user(session_factory, "dave", status="disabled")

    r = await client.get("/v1/admin/system")
    assert r.status_code == 200, r.text
    b = r.json()

    # Config snapshot (deploy-time settings, surfaced read-only).
    assert b["billing_mode"] == settings.billing_mode
    assert "cny_per_usd" not in b
    assert b["quota"]["daily_tokens"] == settings.quota_daily_tokens
    assert b["quota"]["daily_requests"] == settings.quota_daily_requests
    assert b["quota"]["monthly_cost_nano"] == int(settings.quota_monthly_cost_cny * NANO_PER_CNY)
    # Health + provenance: the request itself proves the DB is reachable.
    assert b["database_ok"] is True
    assert isinstance(b["version"], str) and b["version"]
    # Account tallies: admin + alice + carol(admin) + dave(disabled) = 4 total;
    # active = admin + alice + carol = 3; admins = admin + carol = 2.
    assert b["users_total"] == 4
    assert b["users_active"] == 3
    assert b["admins"] == 2


async def test_admin_system_counts_exclude_deleted(client, make_admin, session_factory):
    """注销 accounts drop out of every system tally — they're anonymized tombstones,
    not part of the live population (so ``total`` no longer over-counts them)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    await _seed_user(session_factory, "alice")
    gone = await _seed_user(session_factory, "zombie")
    await _soft_delete_user(session_factory, gone)

    r = await client.get("/v1/admin/system")
    assert r.status_code == 200, r.text
    b = r.json()
    # Live = admin + alice (zombie soft-deleted → excluded from total *and* active).
    assert b["users_total"] == 2
    assert b["users_active"] == 2
    assert b["admins"] == 1


# --- 运营观测看板 (观测, P1) ---


async def _seed_turn(
    session_factory,
    *,
    user_id: str,
    status: str = "ok",
    finish_reason: str = "stop",
    error: str | None = None,
    rounds: int = 1,
    duration_ms: int = 500,
    delegated: bool = False,
    workers: int = 0,
    input_tokens: int = 100,
    output_tokens: int = 50,
    boundary_yields: int = 0,
    scope_signals: int = 0,
    revises: int = 0,
    escalations: int = 0,
    mode: str = "cloud",
) -> None:
    """Seed one turn_metrics row for ``user_id`` landing in today's window
    (created_at server-defaults to now). The write path is exercised end-to-end by
    the conversation service; here it seeds the dashboard's read side directly."""
    async with session_factory() as session:
        await TurnMetricsRepository(session).record(
            turn_id=new_id(),
            conversation_id=new_id(),
            user_id=user_id,
            trace_id=uuid4().hex,
            agent_id="CEO",
            kind="turn",
            mode=mode,
            status=status,
            finish_reason=finish_reason,
            error=error,
            rounds=rounds,
            duration_ms=duration_ms,
            delegated=delegated,
            workers=workers,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            boundary_yields=boundary_yields,
            scope_signals=scope_signals,
            revises=revises,
            escalations=escalations,
        )


async def test_admin_observability_requires_auth(client):
    assert (await client.get("/v1/admin/observability/summary")).status_code == 401
    assert (
        await client.get(f"/v1/admin/observability/conversations/{new_id()}")
    ).status_code == 401
    assert (
        await client.get(
            f"/v1/admin/observability/conversations/{new_id()}/messages/{new_id()}/final-state"
        )
    ).status_code == 401


async def test_non_admin_cannot_access_observability(client):
    await register_and_login(client, "regular_obs")
    assert (await client.get("/v1/admin/observability/summary")).status_code == 403
    assert (
        await client.get(f"/v1/admin/observability/conversations/{new_id()}")
    ).status_code == 403
    assert (
        await client.get(
            f"/v1/admin/observability/conversations/{new_id()}/messages/{new_id()}/final-state"
        )
    ).status_code == 403


async def test_admin_observability_surfaces_collab_quality(client, make_admin, session_factory):
    """学·度量 §2.5: the health window surfaces 协作质量 — 首计划存活率 over delegated turns plus
    raw scope / revise / escalation sums — so the operator面 sees the same 方向盘 as offline."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    user = await _seed_user(session_factory, "collab")

    # 3 delegated turns: 2 ran the first plan clean (boundary_yields==0); 1 needed a mid-course
    # replan (boundary_yields=1), drifted (scope_signals=2), took 1 revise + 3 escalations.
    await _seed_turn(session_factory, user_id=user, delegated=True, workers=2)
    await _seed_turn(session_factory, user_id=user, delegated=True, workers=1)
    await _seed_turn(
        session_factory,
        user_id=user,
        delegated=True,
        workers=2,
        boundary_yields=1,
        scope_signals=2,
        revises=1,
        escalations=3,
    )
    # A non-delegated turn: excluded from the 首计划存活 denominator, doesn't add scope signals.
    await _seed_turn(session_factory, user_id=user)

    today = (await client.get("/v1/admin/observability/summary")).json()["today"]
    assert today["delegated_turns"] == 3
    # 首计划存活率: 2 of 3 delegated turns had boundary_yields == 0.
    assert today["first_plan_survival_rate"] == 2 / 3
    assert today["scope_signals"] == 2
    assert today["revises"] == 1
    assert today["escalations"] == 3


async def test_admin_observability_summary_aggregates(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    bob = await _seed_user(session_factory, "bob")

    # 4 turns total across two users: 3 ok + 1 error; one ok turn delegated.
    await _seed_turn(session_factory, user_id=alice, delegated=True, workers=2)
    await _seed_turn(session_factory, user_id=alice)
    await _seed_turn(
        session_factory,
        user_id=alice,
        status="error",
        finish_reason="error",
        error="boom",
        rounds=3,
        duration_ms=800,
        output_tokens=0,
    )
    await _seed_turn(session_factory, user_id=bob)

    r = await client.get("/v1/admin/observability/summary")
    assert r.status_code == 200, r.text
    b = r.json()

    # today health spans every account; all turns are "now" → today == week.
    today = b["today"]
    assert today["turns"] == 4
    assert today["errors"] == 1
    assert today["error_rate"] == 0.25
    assert today["delegated_turns"] == 1
    assert today["delegated_rate"] == 0.25
    # rounds: (1 + 1 + 3 + 1) / 4 = 1.5; token SUM over mode=cloud (all 4 here).
    assert today["avg_rounds"] == 1.5
    assert today["input_tokens"] == 400
    assert today["output_tokens"] == 150
    assert today["p95_duration_ms"] > 0
    assert b["week"]["turns"] == 4

    # 近期错误 feed: the one errored turn, with its drill-down join keys.
    errs = b["recent_errors"]
    assert len(errs) == 1
    assert errs[0]["status"] == "error"
    assert errs[0]["finish_reason"] == "error"
    assert errs[0]["error"] == "boom"
    assert errs[0]["trace_id"] and len(errs[0]["trace_id"]) == 32

    # 7-day trend is a fixed-length series; today carries all 4 turns / 1 error.
    trend = b["recent_daily"]
    assert len(trend) == 7
    assert trend[-1]["turns"] == 4
    assert trend[-1]["errors"] == 1
    assert sum(p["turns"] for p in trend) == 4


async def test_admin_observability_token_sums_skip_local_mode(
    client, make_admin, session_factory
):
    """Health tokens count mode=cloud only; local rows still sit in the turn count."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    user = await _seed_user(session_factory, "tokmix")

    await _seed_turn(session_factory, user_id=user, input_tokens=100, output_tokens=50)
    await _seed_turn(
        session_factory,
        user_id=user,
        mode="local",
        input_tokens=0,
        output_tokens=0,
    )

    r = await client.get("/v1/admin/observability/summary")
    assert r.status_code == 200, r.text
    today = r.json()["today"]
    assert today["turns"] == 2
    assert today["input_tokens"] == 100
    assert today["output_tokens"] == 50


async def test_admin_observability_summary_empty_is_zero(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)

    r = await client.get("/v1/admin/observability/summary")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["today"]["turns"] == 0
    assert b["today"]["errors"] == 0
    assert b["today"]["error_rate"] == 0.0
    assert b["today"]["p95_duration_ms"] == 0
    assert b["recent_errors"] == []
    assert [p["turns"] for p in b["recent_daily"]] == [0] * 7


# --- 会话复盘 (观测 P2): timeline merge by trace_id / message_id ---


async def _seed_conversation_with_turn(
    session_factory,
    *,
    user_id: str,
    status: str = "ok",
    error: str | None = None,
    cost_nano: int = 4200,
) -> tuple[str, str]:
    """Seed one conversation as the three turn sources the 复盘 merges: a user
    prompt + an assistant reply sharing a ``trace_id``, a ``turn_metrics`` row on
    that trace, and a ``cost_events`` row on the reply's ``message_id``. Returns
    ``(conversation_id, assistant_message_id)`` for the assertions."""
    trace_id = uuid4().hex
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(user_id=user_id, title="复盘会话")
        # Production stamps trace_id only on the assistant reply; the user prompt's
        # is NULL — so a trace overlays exactly one message in the replay.
        await MessageRepository(session).create(
            conversation_id=conv.id,
            role="user",
            content="帮我做个东西",
        )
        assistant = await MessageRepository(session).create(
            conversation_id=conv.id,
            role="assistant",
            content="好的，已完成" if status == "ok" else "出错了",
            trace_id=trace_id,
        )
        await TurnMetricsRepository(session).record(
            turn_id=assistant.id,
            conversation_id=conv.id,
            user_id=user_id,
            trace_id=trace_id,
            agent_id="CEO",
            kind="turn",
            status=status,
            finish_reason="error" if status == "error" else "stop",
            error=error,
            rounds=2,
            duration_ms=700,
            delegated=True,
            workers=1,
            input_tokens=120,
            output_tokens=60,
        )
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            runs=[
                {
                    "run_id": new_id(),
                    "parent_run_id": None,
                    "agent_id": new_id(),
                    "role": "captain",
                    "model": "deepseek-v4-pro",
                    "tokens": {
                        "input": 120,
                        "output": 60,
                        "reasoning": 0,
                        "cache_hit": 0,
                        "cache_miss": 120,
                    },
                    "cost": {
                        "input": 0,
                        "cached": 0,
                        "output": 0,
                        "total": cost_nano,
                    },
                    "cost_total_nano": cost_nano,
                    "currency": "USD",
                    "rounds": 2,
                    "duration_ms": 700,
                }
            ],
            trace_id=trace_id,
        )
        # Call authority for models / credential_source overlays (message + trace join).
        await CostEventRepository(session).record_calls(
            user_id=user_id,
            conversation_id=conv.id,
            message_id=assistant.id,
            trace_id=trace_id,
            calls=[
                {
                    "call_id": new_id(),
                    "run_id": new_id(),
                    "parent_run_id": None,
                    "agent_id": new_id(),
                    "role": "captain",
                    "model": "deepseek-v4-pro",
                    "tokens": {
                        "input": 120,
                        "output": 60,
                        "reasoning": 0,
                        "cache_hit": 0,
                        "cache_miss": 120,
                    },
                    "cost": {
                        "input": 0,
                        "cached": 0,
                        "output": 0,
                        "total": cost_nano,
                        "credential_source": "platform",
                        "pricing_source": "curated",
                    },
                    "cost_total_nano": cost_nano,
                    "cost_estimated_nano": 0,
                    "currency": "USD",
                    "duration_ms": 700,
                }
            ],
        )
        # The turn's execution journal (keyed by the assistant message id) — the
        # source the 复盘 projects tool/LLM spans from.
        await TurnJournalRepository(session).record(
            turn_id=assistant.id,
            conversation_id=conv.id,
            trace_id=trace_id,
            entries=[
                {
                    "kind": "llm_call",
                    "payload": {
                        "run_id": "r1",
                        "round_idx": 0,
                        "finish_reason": "tool_calls",
                        "usage": {"input": 120, "output": 60},
                    },
                    "ts": None,
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "run_id": "r1",
                        "tool_call_id": "tc1",
                        "name": "read_file",
                        "arguments": '{"path": "a.py"}',
                        "result": "file body",
                        "success": True,
                    },
                    "ts": None,
                },
            ],
        )
    return conv.id, assistant.id


async def test_admin_conversation_replay_merges_timeline(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    conv_id, assistant_id = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="error", error="boom", cost_nano=4200
    )

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    b = r.json()

    # Conversation header carries the (cross-user) owner identity + title.
    assert b["conversation"]["id"] == conv_id
    assert b["conversation"]["title"] == "复盘会话"
    assert b["conversation"]["user_id"] == alice
    assert b["conversation"]["username"] == "alice"

    # Rollup over the conversation's traced turns (nano-CNY; no FX field).
    assert b["turns"] == 1
    assert b["errors"] == 1
    assert b["cost_total"] == 4200
    assert "cny_per_usd" not in b

    # Timeline is oldest-first: the user prompt, then the assistant reply.
    msgs = b["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    user_msg, assistant_msg = msgs

    # The user message has no turn overlay (no metrics, no spend, no spans).
    assert user_msg["metrics"] is None
    assert user_msg["cost_total"] == 0
    assert user_msg["spans"] == []
    assert user_msg["origin"] is None
    assert user_msg["harvest_kind"] is None
    assert user_msg["attachments"] == []
    assert user_msg["agent_mentions"] == []

    # The assistant message merges its turn telemetry (by trace_id) + spend
    # (by message_id) onto the thread row.
    assert assistant_msg["id"] == assistant_id
    assert assistant_msg["cost_total"] == 4200
    assert assistant_msg["models"] == ["deepseek-v4-pro"]
    assert assistant_msg["credential_source"] == "platform"
    # Session profile pin may be null; expand still yields a display name.
    assert "model_profile_id" in b["conversation"]
    assert isinstance(b["conversation"]["model_profile_name"], str)
    assert b["conversation"]["model_profile_name"]
    m = assistant_msg["metrics"]
    assert m is not None
    assert m["status"] == "error"
    assert m["finish_reason"] == "error"
    assert m["error"] == "boom"
    assert m["rounds"] == 2
    assert m["delegated"] is True and m["workers"] == 1
    assert m["trace_id"] == assistant_msg["trace_id"]

    # User prompt has no ledger overlay.
    assert user_msg["models"] == []
    assert user_msg["credential_source"] is None

    # Execution spans projected from turn_journal (llm_call + tool_call), in order.
    spans = assistant_msg["spans"]
    assert [s["kind"] for s in spans] == ["llm", "tool"]
    assert spans[0]["round_idx"] == 0
    assert spans[0]["finish_reason"] == "tool_calls"
    assert spans[0]["output_tokens"] == 60
    assert spans[1]["name"] == "read_file"
    assert spans[1]["success"] is True
    assert "a.py" in spans[1]["args_preview"]
    assert spans[1]["result_preview"] == "file body"
    # Plain tool journal (no team surface) → empty runs list.
    assert assistant_msg["runs"] == []
    assert assistant_msg["runs_payload"] is None
    assert assistant_msg["projected"] is None
    assert assistant_msg["has_final_state"] is False


async def test_admin_conversation_replay_projects_execution_harvest_origin(
    client, make_admin, session_factory
):
    """Synthetic harvest closing rows expose origin (not ordinary user prompts)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice_harvest")
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=alice, title="收口复盘"
        )
        conv_id = conv.id
        await MessageRepository(session).create(
            conversation_id=conv_id,
            role="user",
            content="【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
            metadata={
                "origin": "execution_harvest",
                "harvest_kind": "cancelled",
                "execution_id": "exec-harvest-1",
            },
        )
        await MessageRepository(session).create(
            conversation_id=conv_id,
            role="assistant",
            content="按已完成部分收尾。",
            trace_id=uuid4().hex,
        )

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    harvest, assistant = msgs
    assert harvest["origin"] == "execution_harvest"
    assert harvest["harvest_kind"] == "cancelled"
    assert assistant["origin"] is None
    assert assistant["harvest_kind"] is None


async def test_admin_conversation_replay_surfaces_user_attachments_and_mentions(
    client, make_admin, session_factory
):
    """User-row chips reuse MessageDetail shapes (metadata only, no file text)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice_replay_chips")
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=alice, title="附件复盘"
        )
        conv_id = conv.id
        await MessageRepository(session).create(
            conversation_id=conv_id,
            role="user",
            content="看这个",
            attachments=[
                {
                    "name": "brief.pdf",
                    "path": "inbox/brief.pdf",
                    "truncated": False,
                    "kind": "file",
                    "workspace_path": "attachments/brief.pdf",
                    "size_bytes": 2048,
                    "thumb_path": None,
                    "binary": True,
                    "text": "SHOULD_NOT_SHIP",
                }
            ],
            agent_mentions=[{"agent_id": "researcher", "role": "研究员"}],
        )
        await MessageRepository(session).create(
            conversation_id=conv_id,
            role="assistant",
            content="收到",
            trace_id=uuid4().hex,
        )

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    user_msg, assistant_msg = r.json()["messages"]
    assert user_msg["role"] == "user"
    assert user_msg["attachments"] == [
        {
            "name": "brief.pdf",
            "path": "inbox/brief.pdf",
            "truncated": False,
            "kind": "file",
            "workspace_path": "attachments/brief.pdf",
            "conversation_id": None,
            "size_bytes": 2048,
            "thumb_path": None,
            "binary": True,
        }
    ]
    assert "text" not in user_msg["attachments"][0]
    assert user_msg["agent_mentions"] == [{"agent_id": "researcher", "role": "研究员"}]
    assert assistant_msg["attachments"] == []
    assert assistant_msg["agent_mentions"] == []


async def test_admin_conversation_replay_projects_multi_agent_runs(
    client, make_admin, session_factory
):
    """Multi-agent turn_journal projects lightweight ReplayRun (tree + full content)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    trace_id = uuid4().hex
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=alice, title="多Agent复盘"
        )
        conv_id = conv.id
        await MessageRepository(session).create(
            conversation_id=conv_id, role="user", content="组队"
        )
        assistant = await MessageRepository(session).create(
            conversation_id=conv_id,
            role="assistant",
            content="已安排",
            trace_id=trace_id,
        )
        await TurnMetricsRepository(session).record(
            turn_id=new_id(),
            conversation_id=conv_id,
            user_id=alice,
            trace_id=trace_id,
            agent_id="CEO",
            kind="turn",
            status="ok",
            finish_reason="end_turn",
            error=None,
            rounds=2,
            duration_ms=900,
            delegated=True,
            workers=1,
            input_tokens=50,
            output_tokens=40,
        )
        await TurnJournalRepository(session).record(
            turn_id=assistant.id,
            conversation_id=conv_id,
            trace_id=trace_id,
            entries=[
                {
                    "kind": "run_plan",
                    "payload": {
                        "execution_id": "e1",
                        "plan_type": "multi_agent",
                        "agents": [{"id": "w1", "role": "研究员", "thinking": True}],
                        "runs": [
                            {
                                "id": "r1",
                                "agent_id": "w1",
                                "task": "调研",
                                "depends_on": [],
                            }
                        ],
                    },
                    "ts": "t0",
                },
                {
                    "kind": "run_started",
                    "payload": {
                        "run_id": "r1",
                        "agent_id": "w1",
                        "kind": "agent",
                        "parent_run_id": "cap",
                    },
                    "ts": "t1",
                },
                {
                    "kind": "message_final",
                    "payload": {
                        "run_id": "r1",
                        "phase": "completed",
                        "content": "队员交付全文",
                        "reasoning": "",
                    },
                    "ts": None,
                },
                {
                    "kind": "run_completed",
                    "payload": {
                        "run_id": "r1",
                        "agent_id": "w1",
                        "output_summary": "调研完成",
                        "role": "member",
                        "debrief": {"summary": "调研完成", "key_points": ["A"]},
                    },
                    "ts": "t2",
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "run_id": "r1",
                        "tool_call_id": "tc1",
                        "name": "web_search",
                        "arguments": "{}",
                        "result": "hit",
                        "success": True,
                    },
                    "ts": None,
                },
                {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
            ],
        )

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    assistant_msg = next(m for m in r.json()["messages"] if m["role"] == "assistant")
    runs = assistant_msg["runs"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == "r1"
    assert runs[0]["agent_id"] == "w1"
    assert runs[0]["task"] == "调研"
    assert runs[0]["content"] == "队员交付全文"
    assert runs[0]["output_summary"] == "调研完成"
    assert runs[0]["parent_run_id"] == "cap"
    assert runs[0]["status"] == "completed"
    assert any(s["run_id"] == "r1" and s["kind"] == "tool" for s in assistant_msg["spans"])
    # List stays summary-sized: heavy pair is on-demand, not inlined.
    assert assistant_msg["runs_payload"] is None
    assert assistant_msg["projected"] is None
    assert assistant_msg["has_final_state"] is True

    final = await client.get(
        f"/v1/admin/observability/conversations/{conv_id}/messages/{assistant_msg['id']}/final-state"
    )
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["message_id"] == assistant_msg["id"]
    # User-end / conformance homology: projected is project_turn(events).
    # turn_end → runs_payload.finish_reason (not a message_end event), so
    # projected.status stays the fold-of-events value — do not splice a fake end.
    assert body["projected"] is not None
    assert body["projected"]["runs"][0]["id"] == "r1"
    assert body["runs_payload"] is not None
    assert body["runs_payload"]["finish_reason"] == "end_turn"
    assert any(e["type"] == "run_plan" for e in body["runs_payload"]["events"])


async def test_admin_conversation_replay_surfaces_textless_error_turn(
    client, make_admin, session_factory
):
    """A turn that errored before persisting any assistant reply has a turn_metrics
    row but no message to ride. The replay must still surface it (as a bare turn
    marker) so a 复盘 never hides the failure."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    trace_id = uuid4().hex
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(user_id=alice, title="空回合")
        conv_id = conv.id
        # Only the user prompt is persisted (no assistant reply for this failed turn).
        await MessageRepository(session).create(
            conversation_id=conv_id, role="user", content="炸一下"
        )
        await TurnMetricsRepository(session).record(
            turn_id=new_id(),
            conversation_id=conv_id,
            user_id=alice,
            trace_id=trace_id,
            agent_id="CEO",
            kind="turn",
            status="error",
            finish_reason="error",
            error="early boom",
            rounds=0,
            duration_ms=120,
            delegated=False,
            workers=0,
            input_tokens=10,
            output_tokens=0,
        )

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["turns"] == 1 and b["errors"] == 1

    # Timeline: the user prompt, then a synthetic turn marker (no body, carries the
    # failed turn's metrics) — newest after, since metrics is recorded post-prompt.
    msgs = b["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    marker = msgs[1]
    assert marker["content"] is None
    assert marker["metrics"]["status"] == "error"
    assert marker["metrics"]["error"] == "early boom"
    assert marker["trace_id"] == trace_id
    assert marker["reasoning_content"] is None


async def test_admin_conversation_replay_surfaces_reasoning_content(
    client, make_admin, session_factory
):
    """Assistant rows expose messages.reasoning_content; user / bare markers null."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice_reasoning")
    plain_trace = uuid4().hex
    journal_trace = uuid4().hex
    plain_reasoning = "先想清楚再回答"
    journal_reasoning = "工具链前的思考"
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=alice, title="思考复盘"
        )
        conv_id = conv.id
        await MessageRepository(session).create(
            conversation_id=conv_id,
            role="user",
            content="纯聊一句",
        )
        plain_assistant = await MessageRepository(session).create(
            conversation_id=conv_id,
            role="assistant",
            content="纯聊回复",
            reasoning_content=plain_reasoning,
            trace_id=plain_trace,
        )
        await TurnMetricsRepository(session).record(
            turn_id=plain_assistant.id,
            conversation_id=conv_id,
            user_id=alice,
            trace_id=plain_trace,
            agent_id="CEO",
            kind="turn",
            status="ok",
            finish_reason="stop",
            error=None,
            rounds=1,
            duration_ms=200,
            delegated=False,
            workers=0,
            input_tokens=10,
            output_tokens=5,
        )
        await MessageRepository(session).create(
            conversation_id=conv_id,
            role="user",
            content="带工具一句",
        )
        journal_assistant = await MessageRepository(session).create(
            conversation_id=conv_id,
            role="assistant",
            content="工具回复",
            reasoning_content=journal_reasoning,
            trace_id=journal_trace,
        )
        await TurnMetricsRepository(session).record(
            turn_id=journal_assistant.id,
            conversation_id=conv_id,
            user_id=alice,
            trace_id=journal_trace,
            agent_id="CEO",
            kind="turn",
            status="ok",
            finish_reason="tool_calls",
            error=None,
            rounds=2,
            duration_ms=400,
            delegated=False,
            workers=0,
            input_tokens=20,
            output_tokens=10,
        )
        await TurnJournalRepository(session).record(
            turn_id=journal_assistant.id,
            conversation_id=conv_id,
            trace_id=journal_trace,
            entries=[
                {
                    "kind": "llm_call",
                    "payload": {
                        "run_id": "r1",
                        "round_idx": 0,
                        "finish_reason": "tool_calls",
                        "usage": {"input": 20, "output": 10},
                    },
                    "ts": None,
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "run_id": "r1",
                        "tool_call_id": "tc1",
                        "name": "read_file",
                        "arguments": '{"path": "x.py"}',
                        "result": "ok",
                        "success": True,
                    },
                    "ts": None,
                },
            ],
        )

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

    plain_user, plain_asst, journal_user, journal_asst = msgs
    assert plain_user["reasoning_content"] is None
    assert plain_asst["reasoning_content"] == plain_reasoning
    assert plain_asst["has_final_state"] is False
    assert plain_asst["spans"] == []

    assert journal_user["reasoning_content"] is None
    assert journal_asst["reasoning_content"] == journal_reasoning
    assert journal_asst["spans"]
    assert journal_asst["has_final_state"] is False
    assert journal_asst["runs_payload"] is None
    assert journal_asst["projected"] is None


async def test_admin_conversation_replay_unknown_404(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    r = await client.get(f"/v1/admin/observability/conversations/{new_id()}")
    assert r.status_code == 404
    missing = await client.get(
        f"/v1/admin/observability/conversations/{new_id()}/messages/{new_id()}/final-state"
    )
    assert missing.status_code == 404


async def test_admin_replay_turn_final_state_scopes_to_conversation(
    client, make_admin, session_factory
):
    """Final-state is conversation-scoped: foreign message ids 404, user rows are empty."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice_final_state")
    conv_a, assistant_a = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok"
    )
    conv_b, _assistant_b = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok"
    )

    list_a = await client.get(f"/v1/admin/observability/conversations/{conv_a}")
    assert list_a.status_code == 200, list_a.text
    user_id = next(m["id"] for m in list_a.json()["messages"] if m["role"] == "user")

    crossed = await client.get(
        f"/v1/admin/observability/conversations/{conv_b}/messages/{assistant_a}/final-state"
    )
    assert crossed.status_code == 404

    user_row = await client.get(
        f"/v1/admin/observability/conversations/{conv_a}/messages/{user_id}/final-state"
    )
    assert user_row.status_code == 200, user_row.text
    assert user_row.json()["runs_payload"] is None
    assert user_row.json()["projected"] is None
    assert user_row.json()["message_id"] == user_id



async def test_admin_conversation_replay_keeps_latest_window(
    client, make_admin, session_factory, monkeypatch
):
    """Hard cap must drop the oldest side, not the newest (ops triage)."""
    from datetime import UTC, datetime, timedelta

    from agentcore.api.routes.admin import observability as observability_mod
    from agentcore.db.models import Message

    monkeypatch.setattr(observability_mod, "_REPLAY_MAX_MESSAGES", 2)
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice_replay_window")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=alice, title="长会话复盘"
        )
        conv_id = conv.id
        for i, text in enumerate(("oldest", "mid", "newest")):
            session.add(
                Message(
                    id=new_id(),
                    conversation_id=conv_id,
                    role="user",
                    content=text,
                    created_at=base + timedelta(minutes=i),
                )
            )
        await session.commit()

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_more_before"] is True
    texts = [m["content"] for m in body["messages"]]
    assert texts == ["mid", "newest"]
    assert "oldest" not in texts


async def test_admin_conversation_replay_includes_soft_deleted(
    client, make_admin, session_factory
):
    """Roster defaults to include tombstones; replay must not 404 those rows."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice_replay_deleted")
    conv_id, _ = await _seed_conversation_with_turn(session_factory, user_id=alice)

    async with session_factory() as session:
        ok = await ConversationRepository(session).soft_delete(conv_id, user_id=alice)
        assert ok is True

    roster = await client.get("/v1/admin/conversations")
    assert roster.status_code == 200, roster.text
    assert any(row["id"] == conv_id for row in roster.json()["data"])

    r = await client.get(f"/v1/admin/observability/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conversation"]["id"] == conv_id
    assert body["conversation"]["deleted_at"] is not None
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assistant_id = next(m["id"] for m in body["messages"] if m["role"] == "assistant")
    final = await client.get(
        f"/v1/admin/observability/conversations/{conv_id}/messages/{assistant_id}/final-state"
    )
    assert final.status_code == 200, final.text


# --- 用户详情下钻 (用户管理 P0 drill-down) ---


async def test_admin_user_detail_requires_auth(client):
    assert (await client.get(f"/v1/admin/users/{new_id()}/detail")).status_code == 401


async def test_non_admin_cannot_access_user_detail(client):
    await register_and_login(client, "regular_detail")
    me = (await client.get("/v1/auth/me")).json()["id"]
    assert (await client.get(f"/v1/admin/users/{me}/detail")).status_code == 403


async def test_admin_user_detail_unknown_404(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    r = await client.get("/v1/admin/users/00000000-0000-0000-0000-000000000000/detail")
    assert r.status_code == 404


async def test_admin_user_detail_composes_account_view(client, make_admin, session_factory):
    """The drill-down stitches one account's record + its own usage (today/month/
    trend/by-model) + recent conversations (with message counts) + recent turns —
    all scoped to that account (another user's spend/turns never leak in)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    bob = await _seed_user(session_factory, "bob")

    # alice: one priced captain turn + a full conversation (user+assistant msgs, a
    # turn_metrics row, spend, journal). bob gets his own spend + conversation to
    # prove the detail is user-scoped (bob's numbers must not bleed into alice's).
    await _seed_spend(session_factory, user_id=alice, total=7000, role="captain")
    conv_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok", cost_nano=4200
    )
    await _seed_spend(session_factory, user_id=bob, total=99999)
    await _seed_conversation_with_turn(session_factory, user_id=bob)

    r = await client.get(f"/v1/admin/users/{alice}/detail")
    assert r.status_code == 200, r.text
    b = r.json()

    # Profile: the admin-rich account record.
    assert b["user"]["id"] == alice
    assert b["user"]["username"] == "alice"

    # Usage scoped to alice: two priced turns (7000 + 4200 = 11200); all "now" so
    # today == month, two distinct message_ids → requests == 2. bob's 99999 absent.
    assert b["today"]["cost"]["total"] == 11200
    assert b["month"]["cost"]["total"] == 11200
    assert b["today"]["requests"] == 2

    # 7-day trend: fixed length, today carries all of alice's spend.
    assert len(b["recent_daily_cost"]) == 7
    assert b["recent_daily_cost"][-1]["cost_total"] == 11200

    # Recent conversations: only alice's, with batched message count (user+asst = 2).
    convs = b["conversations"]
    assert [c["id"] for c in convs] == [conv_id]
    assert convs[0]["title"] == "复盘会话"
    assert convs[0]["messages"] == 2

    # Recent activity: only alice's traced turn (bob's excluded), drillable by conv id.
    turns = b["recent_turns"]
    assert len(turns) == 1
    assert turns[0]["conversation_id"] == conv_id
    assert turns[0]["status"] == "ok"

    assert "cny_per_usd" not in b
    assert b["billing_mode"] == settings.billing_mode
    # No BYOK → model names empty; conversation fixture seeds one cost_calls row.
    assert b["default_model"] is None
    assert b["background_model"] is None
    assert b["recent_by_model"] == [
        {
            "model": "deepseek-v4-pro",
            "calls": 1,
            "tokens_total": 180,
            "cost_total": 4200,
            "cost_estimated_total": 0,
        }
    ]
    # 加强可查: registration_ip + sessions (empty when no refresh tips).
    assert "registration_ip" in b["user"]
    assert b["sessions"] == []


async def test_admin_user_detail_includes_sessions(
    client, make_admin, session_factory
):
    """Detail surfaces active refresh-token sessions (ip/ua/platform/times)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(
        session_factory, "alice_sess", registration_ip="203.0.113.50"
    )
    fam = await _seed_refresh_token(
        session_factory, user_id=alice, ip="203.0.113.51", platform="mobile"
    )

    r = await client.get(f"/v1/admin/users/{alice}/detail")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["user"]["registration_ip"] == "203.0.113.50"
    assert len(b["sessions"]) == 1
    sess = b["sessions"][0]
    assert sess["id"] == fam
    assert sess["ip"] == "203.0.113.51"
    assert sess["platform"] == "mobile"
    assert sess["user_agent"] == "AgentCoreTest/1.0"
    assert sess["current"] is False
    assert sess["created_at"]
    assert sess["last_used_at"]


async def test_admin_user_detail_exposes_models_and_by_model_usage(
    client, make_admin, session_factory
):
    """User detail surfaces configured model names (never the key) + 30d by-model
    payroll from ``cost_calls``."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice_models")
    bob = await _seed_user(session_factory, "bob_models")

    await _seed_llm_key(
        session_factory,
        user_id=alice,
        default_model="deepseek-v4-pro",
        background_model="deepseek-v4-flash",
    )
    await _seed_calls(
        session_factory,
        user_id=alice,
        model="deepseek-v4-pro",
        total=3500,
        calls=2,
        input_tokens=75,
        output_tokens=75,
    )
    await _seed_calls(
        session_factory,
        user_id=alice,
        model="deepseek-v4-flash",
        total=500,
        input_tokens=25,
        output_tokens=25,
    )
    # bob's calls must not appear in alice's by-model table.
    await _seed_calls(session_factory, user_id=bob, model="deepseek-v4-pro", total=99999)

    r = await client.get(f"/v1/admin/users/{alice}/detail")
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["default_model"] == "deepseek-v4-pro"
    assert b["background_model"] == "deepseek-v4-flash"
    # Response must never leak any key ciphertext / plaintext fields.
    assert "api_key" not in b
    assert "api_key_enc" not in b
    assert "base_url" not in b

    rows = b["recent_by_model"]
    assert [row["model"] for row in rows] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    by_model = {row["model"]: row for row in rows}
    assert by_model["deepseek-v4-pro"]["calls"] == 2
    assert by_model["deepseek-v4-pro"]["cost_total"] == 7000
    assert by_model["deepseek-v4-pro"]["tokens_total"] == 300
    assert by_model["deepseek-v4-flash"]["calls"] == 1
    assert by_model["deepseek-v4-flash"]["cost_total"] == 500
    assert by_model["deepseek-v4-flash"]["tokens_total"] == 50


# --- 控制台概览 (landing dashboard) ---


async def test_admin_overview_requires_auth(client):
    assert (await client.get("/v1/admin/overview")).status_code == 401


async def test_non_admin_cannot_access_overview(client):
    await register_and_login(client, "regular_ov")
    assert (await client.get("/v1/admin/overview")).status_code == 403


async def test_admin_overview_aggregates_today(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    bob = await _seed_user(session_factory, "bob")

    # Turns across two users: alice 2 (1 error) + bob 1 → 3 turns, 1 error, 2 active
    # users (the admin took no turn, so it's not "active"). Spend lands today.
    await _seed_turn(session_factory, user_id=alice)
    await _seed_turn(
        session_factory,
        user_id=alice,
        status="error",
        finish_reason="error",
        error="boom",
    )
    await _seed_turn(session_factory, user_id=bob)
    await _seed_spend(session_factory, user_id=alice, total=5000)
    await _seed_spend(session_factory, user_id=bob, total=1000)

    r = await client.get("/v1/admin/overview")
    assert r.status_code == 200, r.text
    b = r.json()

    # 今日 pulse: distinct active users + turn health + cost.
    assert b["active_users_today"] == 2
    assert b["today"]["turns"] == 3
    assert b["today"]["errors"] == 1
    assert abs(b["today"]["error_rate"] - 1 / 3) < 0.001
    assert b["cost_today"]["total"] == 6000

    # Account tallies: admin + alice + bob = 3 total, all active, 1 admin.
    assert b["users_total"] == 3
    assert b["users_active"] == 3
    assert b["admins"] == 1

    # 7-day trends: fixed length, today carries it all.
    assert len(b["recent_daily_cost"]) == 7
    assert b["recent_daily_cost"][-1]["cost_total"] == 6000
    assert len(b["recent_daily_turns"]) == 7
    assert b["recent_daily_turns"][-1]["turns"] == 3
    assert b["recent_daily_turns"][-1]["errors"] == 1

    # Deployment health + the short recent-errors feed.
    assert b["database_ok"] is True
    assert len(b["recent_errors"]) == 1
    assert b["recent_errors"][0]["error"] == "boom"
    assert b["billing_mode"] == settings.billing_mode


async def test_admin_overview_empty_is_zero(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    r = await client.get("/v1/admin/overview")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["active_users_today"] == 0
    assert b["today"]["turns"] == 0
    assert b["cost_today"]["total"] == 0
    assert b["recent_errors"] == []
    assert [p["turns"] for p in b["recent_daily_turns"]] == [0] * 7


# --- 对话名册 (platform conversation roster + turn feed) ---


async def test_admin_conversations_requires_auth(client):
    assert (await client.get("/v1/admin/conversations")).status_code == 401
    assert (await client.get("/v1/admin/conversations/turns")).status_code == 401


async def test_non_admin_cannot_access_conversations(client):
    await register_and_login(client, "regular_conv")
    assert (await client.get("/v1/admin/conversations")).status_code == 403
    assert (await client.get("/v1/admin/conversations/turns")).status_code == 403


async def test_admin_list_conversations_roster(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    bob = await _seed_user(session_factory, "bob")

    ok_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok", cost_nano=3000
    )
    err_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=bob, status="error", error="boom", cost_nano=1000
    )

    r = await client.get("/v1/admin/conversations")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["total"] == 2
    assert len(b["data"]) == 2
    by_id = {row["id"]: row for row in b["data"]}
    assert by_id[ok_id]["username"] == "alice"
    assert by_id[ok_id]["turns"] == 1
    assert by_id[ok_id]["errors"] == 0
    assert by_id[ok_id]["messages"] == 2
    assert by_id[ok_id]["cost_total"] == 3000
    assert by_id[ok_id]["delegated_turns"] == 1
    assert by_id[ok_id]["workers"] == 1
    assert by_id[err_id]["errors"] == 1
    assert by_id[err_id]["cost_total"] == 1000
    assert by_id[err_id]["delegated_turns"] == 1
    assert "cny_per_usd" not in b


async def test_admin_list_conversations_filters_has_delegated(
    client, make_admin, session_factory
):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    # Seed helper always records delegated=True; add a plain single-agent turn too.
    multi_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok"
    )
    trace_id = uuid4().hex
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=alice, title="单Agent"
        )
        solo_id = conv.id
        await MessageRepository(session).create(
            conversation_id=solo_id, role="user", content="hi"
        )
        await MessageRepository(session).create(
            conversation_id=solo_id,
            role="assistant",
            content="yo",
            trace_id=trace_id,
        )
        await TurnMetricsRepository(session).record(
            turn_id=new_id(),
            conversation_id=solo_id,
            user_id=alice,
            trace_id=trace_id,
            agent_id="CEO",
            kind="turn",
            status="ok",
            finish_reason="end_turn",
            error=None,
            rounds=1,
            duration_ms=100,
            delegated=False,
            workers=0,
            input_tokens=10,
            output_tokens=5,
        )

    r = await client.get("/v1/admin/conversations", params={"has_delegated": "true"})
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()["data"]}
    assert multi_id in ids
    assert solo_id not in ids

    r2 = await client.get(
        "/v1/admin/conversations/turns", params={"delegated": "true"}
    )
    assert r2.status_code == 200, r2.text
    turn_ids = {row["conversation_id"] for row in r2.json()["data"]}
    assert multi_id in turn_ids
    assert solo_id not in turn_ids


async def test_admin_list_conversations_filters_has_errors(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    ok_id, _ = await _seed_conversation_with_turn(session_factory, user_id=alice, status="ok")
    err_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="error", error="boom"
    )

    r = await client.get("/v1/admin/conversations", params={"has_errors": "true"})
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()["data"]}
    assert err_id in ids
    assert ok_id not in ids


async def test_admin_list_conversations_sort_by_cost(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    cheap_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok", cost_nano=1000
    )
    expensive_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok", cost_nano=9000
    )

    r = await client.get(
        "/v1/admin/conversations",
        params={"sort": "cost", "order": "desc", "user_id": alice},
    )
    assert r.status_code == 200, r.text
    ids = [row["id"] for row in r.json()["data"]]
    assert ids.index(expensive_id) < ids.index(cheap_id)


async def test_admin_list_conversations_sort_by_delegated(
    client, make_admin, session_factory
):
    """``sort=delegated`` orders by multi-agent turn count (roster 委派列)."""
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    multi_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="ok"
    )
    # Plain single-agent conversation (delegated_turns = 0).
    trace_id = uuid4().hex
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(
            user_id=alice, title="单Agent排序"
        )
        solo_id = conv.id
        await MessageRepository(session).create(
            conversation_id=solo_id, role="user", content="hi"
        )
        await MessageRepository(session).create(
            conversation_id=solo_id,
            role="assistant",
            content="yo",
            trace_id=trace_id,
        )
        await TurnMetricsRepository(session).record(
            turn_id=new_id(),
            conversation_id=solo_id,
            user_id=alice,
            trace_id=trace_id,
            agent_id="CEO",
            kind="turn",
            status="ok",
            finish_reason="end_turn",
            error=None,
            rounds=1,
            duration_ms=100,
            delegated=False,
            workers=0,
            input_tokens=10,
            output_tokens=5,
        )

    r = await client.get(
        "/v1/admin/conversations",
        params={"sort": "delegated", "order": "desc", "user_id": alice},
    )
    assert r.status_code == 200, r.text
    ids = [row["id"] for row in r.json()["data"]]
    assert ids.index(multi_id) < ids.index(solo_id)


async def test_admin_list_conversation_turns_feed(client, make_admin, session_factory):
    username, password = await make_admin()
    await login_admin(client, username, password)
    alice = await _seed_user(session_factory, "alice")
    conv_id, _ = await _seed_conversation_with_turn(
        session_factory, user_id=alice, status="error", error="boom"
    )

    r = await client.get("/v1/admin/conversations/turns", params={"status": "error"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["total"] >= 1
    row = next(x for x in b["data"] if x["conversation_id"] == conv_id)
    assert row["conversation_title"] == "复盘会话"
    assert row["username"] == "alice"
    assert row["status"] == "error"
    assert row["error"] == "boom"
    assert row["models"] == ["deepseek-v4-pro"]
    assert row["credential_source"] == "platform"
