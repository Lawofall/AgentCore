"""Admin platform-credential pool CRUD (encrypted at rest, no plaintext in responses)."""

import time
from uuid import uuid4

from agentcore.config import settings
from agentcore.llm.platform_pool import pick_enabled_platform_pool_member
from agentcore.llm.platform_pool_state import AccountRecord, get_pool_state_store
from agentcore.llm.resolve import platform_llm_credentials
from agentcore.security.keys import KeyEncryptor
from tests.integration.conftest import login_admin

_GO = "https://opencode.ai/zen/go/v1"
_MASTER_KEY = "ab" * 32


def _body(**kw) -> dict:
    data = {
        "label": "Go-A",
        "api_key": "sk-pool-secret-aaaa",
        "base_url": _GO,
        "subscription_day": 18,
        "enabled": True,
    }
    data.update(kw)
    return data


async def test_platform_credentials_require_admin(client):
    assert (await client.get("/v1/admin/platform-credentials")).status_code == 401
    r = await client.post("/v1/admin/platform-credentials", json=_body())
    assert r.status_code == 401
    r = await client.post("/v1/admin/platform-credentials/x/clear-runtime")
    assert r.status_code == 401


async def test_pool_crud_disable_and_env_fallback(
    client, make_admin, monkeypatch
):
    monkeypatch.setattr(settings, "platform_api_key", "sk-env-fallback")
    monkeypatch.setattr(settings, "platform_base_url", "https://env.example/v1")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    username, password = await make_admin()
    await login_admin(client, username, password)

    empty = await client.get("/v1/admin/platform-credentials")
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["data"] == []
    assert body["fallback"] == "env"
    env_creds = platform_llm_credentials()
    assert env_creds is not None
    assert env_creds.api_key == "sk-env-fallback"

    created = await client.post("/v1/admin/platform-credentials", json=_body())
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["label"] == "Go-A"
    assert row["base_url"] == _GO
    assert row["subscription_day"] == 18
    assert row["enabled"] is True
    assert row["masked_key"] == "••••aaaa"
    assert "api_key" not in row
    assert row["tool_surface_limits"] == {
        "max_tools": None,
        "max_properties_total": None,
        "max_properties_per_tool": None,
    }
    assert "sk-pool-secret" not in created.text
    cred_id = row["id"]

    listed = await client.get("/v1/admin/platform-credentials")
    assert listed.json()["fallback"] == "pool"
    listed_row = listed.json()["data"][0]
    assert listed_row["picked"] is True
    assert listed_row["same_as_env"] is False
    picked = platform_llm_credentials()
    assert picked is not None
    assert picked.api_key == "sk-pool-secret-aaaa"
    assert picked.base_url == _GO
    assert picked.platform_credential_id == cred_id
    assert pick_enabled_platform_pool_member() is not None

    patched = await client.patch(
        f"/v1/admin/platform-credentials/{cred_id}",
        json={"enabled": False},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False
    assert (await client.get("/v1/admin/platform-credentials")).json()["fallback"] == "env"
    after_disable = platform_llm_credentials()
    assert after_disable is not None
    assert after_disable.api_key == "sk-env-fallback"

    deleted = await client.delete(f"/v1/admin/platform-credentials/{cred_id}")
    assert deleted.status_code == 200, deleted.text
    assert (await client.get("/v1/admin/platform-credentials")).json()["data"] == []


async def test_create_does_not_reuse_global_base_url(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    r = await client.post(
        "/v1/admin/platform-credentials",
        json={
            "label": "missing-url",
            "api_key": "sk-x",
            "base_url": "",
            "subscription_day": 1,
        },
    )
    assert r.status_code == 422


async def test_stored_ciphertext_roundtrips_with_key_encryptor(
    client, make_admin, session_factory
):
    username, password = await make_admin()
    await login_admin(client, username, password)
    created = await client.post("/v1/admin/platform-credentials", json=_body())
    assert created.status_code == 201, created.text
    cred_id = created.json()["id"]

    from sqlalchemy import select

    from agentcore.db.models.platform import PlatformCredential

    async with session_factory() as session:
        row = (
            await session.execute(
                select(PlatformCredential).where(PlatformCredential.id == cred_id)
            )
        ).scalar_one()
        assert b"sk-pool-secret" not in row.api_key_enc
        plain = KeyEncryptor(_MASTER_KEY).decrypt(row.api_key_enc).decode()
        assert plain == "sk-pool-secret-aaaa"


async def test_list_includes_pool_runtime_state(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    created = await client.post("/v1/admin/platform-credentials", json=_body())
    assert created.status_code == 201, created.text
    cred_id = created.json()["id"]
    assert created.json()["status"] == "healthy"
    assert created.json()["recovery_at"] is None
    assert created.json()["limit_name"] is None

    listed = await client.get("/v1/admin/platform-credentials")
    assert listed.status_code == 200, listed.text
    row = listed.json()["data"][0]
    assert row["id"] == cred_id
    assert row["status"] == "healthy"
    assert row["recovery_at"] is None
    assert row["limit_name"] is None

    recovery = time.time() + 3600.0
    get_pool_state_store().set(
        cred_id,
        AccountRecord(
            status="cooling",
            recovery_at=recovery,
            limit_name="5 hour",
            source="retry_after",
        ),
    )
    listed = await client.get("/v1/admin/platform-credentials")
    row = listed.json()["data"][0]
    assert row["status"] == "cooling"
    assert row["limit_name"] == "5 hour"
    assert row["recovery_at"] is not None

    get_pool_state_store().set(
        cred_id,
        AccountRecord(
            status="blocked",
            recovery_at=None,
            limit_name=None,
            source="upstream_401",
        ),
    )
    listed = await client.get("/v1/admin/platform-credentials")
    row = listed.json()["data"][0]
    assert row["status"] == "blocked"
    assert row["recovery_at"] is None


async def test_clear_runtime_unblocks_and_audits(client, make_admin):
    username, password = await make_admin()
    await login_admin(client, username, password)
    created = await client.post("/v1/admin/platform-credentials", json=_body())
    assert created.status_code == 201, created.text
    cred_id = created.json()["id"]
    get_pool_state_store().set(
        cred_id,
        AccountRecord(
            status="blocked",
            recovery_at=None,
            limit_name=None,
            source="upstream_401",
        ),
    )

    missing = await client.post(
        f"/v1/admin/platform-credentials/{uuid4()}/clear-runtime"
    )
    assert missing.status_code == 404

    cleared = await client.post(
        f"/v1/admin/platform-credentials/{cred_id}/clear-runtime"
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["status"] == "healthy"
    assert get_pool_state_store().get(cred_id) is None

    listed = await client.get("/v1/admin/platform-credentials")
    assert listed.json()["data"][0]["status"] == "healthy"

    logs = await client.get(
        "/v1/admin/audit-logs",
        params={"action": "platform_credential.clear_runtime"},
    )
    assert logs.status_code == 200, logs.text
    row = next(x for x in logs.json()["data"] if x["target_id"] == cred_id)
    assert row["target_type"] == "platform_credential"
    assert row["detail"]["cleared_status"] == "blocked"
    assert row["actor_username"] == username


async def test_tool_surface_limits_roundtrip_and_clear(client, make_admin, monkeypatch):
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    username, password = await make_admin()
    await login_admin(client, username, password)

    created = await client.post(
        "/v1/admin/platform-credentials",
        json=_body(tool_surface_limits={"max_tools": 16, "max_properties_total": 40}),
    )
    assert created.status_code == 201, created.text
    limits = created.json()["tool_surface_limits"]
    assert limits["max_tools"] == 16
    assert limits["max_properties_total"] == 40
    assert limits["max_properties_per_tool"] is None
    cred_id = created.json()["id"]

    from agentcore.llm.platform_pool_scheduler import member_for_credentials
    from agentcore.llm.resolve import platform_llm_credentials

    picked = platform_llm_credentials()
    assert picked is not None
    member = member_for_credentials(picked.api_key, picked.base_url)
    assert member is not None
    assert member.tool_surface_limits.max_tools == 16
    assert member.tool_surface_limits.max_properties_total == 40

    unknown = await client.patch(
        f"/v1/admin/platform-credentials/{cred_id}",
        json={"tool_surface_limits": {"max_tokens": 8}},
    )
    assert unknown.status_code == 422

    cleared = await client.patch(
        f"/v1/admin/platform-credentials/{cred_id}",
        json={"tool_surface_limits": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["tool_surface_limits"] == {
        "max_tools": None,
        "max_properties_total": None,
        "max_properties_per_tool": None,
    }
    picked = platform_llm_credentials()
    assert picked is not None
    member = member_for_credentials(picked.api_key, picked.base_url)
    assert member is not None
    assert member.tool_surface_limits.is_unrestricted()


async def test_tool_surface_limits_migration_up_and_down(session_factory):
    """Alembic add-column can drop and re-apply on a live Postgres schema."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    from agentcore.db.migrations.versions.c2f9a1e4b7d8_platform_cred_tool_surface_limits import (
        downgrade,
        upgrade,
    )

    def _run_upgrade(sync_conn) -> None:
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx):
            upgrade()

    def _run_downgrade(sync_conn) -> None:
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx):
            downgrade()

    def _col_names(sync_conn) -> set[str]:
        return {c["name"] for c in inspect(sync_conn).get_columns("platform_credentials")}

    async with session_factory() as session:
        conn = await session.connection()
        names = await conn.run_sync(_col_names)
        assert "tool_surface_limits" in names  # create_all already applied ORM
        await conn.run_sync(_run_downgrade)
        names = await conn.run_sync(_col_names)
        assert "tool_surface_limits" not in names
        await conn.run_sync(_run_upgrade)
        names = await conn.run_sync(_col_names)
        assert "tool_surface_limits" in names
        await conn.run_sync(_run_downgrade)
        await conn.run_sync(_run_upgrade)
        names = await conn.run_sync(_col_names)
        assert "tool_surface_limits" in names
        await session.commit()


