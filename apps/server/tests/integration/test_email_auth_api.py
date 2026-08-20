"""HTTP contract for register-verify / password-reset / email-verify (real PG)."""

from types import SimpleNamespace

import pytest

from agentcore.api.dependencies import get_email_sender
from agentcore.config import settings
from agentcore.main import app
from tests.integration.conftest import client_platform_headers, register_and_login

_PW = "password123"
_DESKTOP = client_platform_headers()


class RecordingMailer:
    def __init__(self) -> None:
        self.sends: list[SimpleNamespace] = []

    async def send_verification_code(self, *, to, purpose, code, ttl_seconds):
        self.sends.append(
            SimpleNamespace(to=to, purpose=purpose, code=code, ttl_seconds=ttl_seconds)
        )

    @property
    def last_code(self) -> str:
        return self.sends[-1].code


@pytest.fixture
def mailer():
    sender = RecordingMailer()
    app.dependency_overrides[get_email_sender] = lambda: sender
    yield sender
    app.dependency_overrides.pop(get_email_sender, None)


async def test_register_send_code_and_verify(client, mailer):
    r = await client.post(
        "/v1/auth/register/send-code",
        json={
            "password": _PW,
            "email": "ada@example.com",
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "accepted"
    assert body["expires_in"] == settings.email_code_ttl_seconds
    assert len(mailer.sends) == 1

    r = await client.post(
        "/v1/auth/register/verify",
        json={"email": "ada@example.com", "code": mailer.last_code},
    )
    assert r.status_code == 201, r.text
    user = r.json()
    assert user["username"].startswith("user_")
    assert "@" not in user["username"]
    assert user["username"] != "ada"
    assert not user["username"].startswith("ada")
    assert user["display_name"] == user["username"]
    assert user["email"] == "ada@example.com"
    assert user["email_verified_at"]

    r = await client.post(
        "/v1/auth/login",
        json={"username": "ada@example.com", "password": _PW},
        headers=_DESKTOP,
    )
    assert r.status_code == 200
    me = (await client.get("/v1/auth/me")).json()
    assert me["email_verified_at"]


async def test_register_send_code_rejects_taken_email_before_mail(client, mailer):
    await register_and_login(client, "taken")
    await client.patch("/v1/auth/me", json={"email": "taken@example.com"})
    r = await client.post(
        "/v1/auth/register/send-code",
        json={"password": _PW, "email": "taken@example.com"},
    )
    assert r.status_code == 422
    assert mailer.sends == []


async def test_register_send_code_rejects_weak_password_before_mail(client, mailer):
    r = await client.post(
        "/v1/auth/register/send-code",
        json={"password": "short", "email": "w@example.com"},
    )
    assert r.status_code == 422
    assert mailer.sends == []


async def test_password_forgot_unknown_email_is_202(client, mailer):
    r = await client.post("/v1/auth/password/forgot", json={"email": "ghost@example.com"})
    assert r.status_code == 202
    assert r.json()["status"] == "accepted"
    assert mailer.sends == []


async def test_password_reset_flow(client, mailer):
    await register_and_login(client, "gia")
    await client.patch("/v1/auth/me", json={"email": "gia@example.com"})
    r = await client.post("/v1/auth/password/forgot", json={"email": "gia@example.com"})
    assert r.status_code == 202
    assert mailer.sends

    r = await client.post(
        "/v1/auth/password/reset",
        json={
            "email": "gia@example.com",
            "code": mailer.last_code,
            "new_password": "newpass99",
        },
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/v1/auth/login",
        json={"username": "gia", "password": "newpass99"},
        headers=_DESKTOP,
    )
    assert r.status_code == 200


async def test_logged_in_email_verify(client, mailer):
    await register_and_login(client, "hal")
    r = await client.post("/v1/auth/email/send-code", json={"email": "hal@example.com"})
    assert r.status_code == 202, r.text
    r = await client.post(
        "/v1/auth/email/verify",
        json={"email": "hal@example.com", "code": mailer.last_code},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "hal@example.com"
    assert body["email_verified_at"]


async def test_legacy_register_gone_in_http(client, monkeypatch):
    monkeypatch.setattr(settings, "legacy_register_enabled", False)
    r = await client.post(
        "/v1/auth/register",
        json={"username": "closed", "password": _PW},
    )
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "GONE"


async def test_register_verify_with_display_name(client, mailer):
    r = await client.post(
        "/v1/auth/register/send-code",
        json={"password": _PW, "email": "nick@example.com"},
    )
    assert r.status_code == 202
    r = await client.post(
        "/v1/auth/register/verify",
        json={
            "email": "nick@example.com",
            "code": mailer.last_code,
            "display_name": "Nick Chen",
        },
    )
    assert r.status_code == 201, r.text
    user = r.json()
    assert user["username"].startswith("user_")
    assert user["display_name"] == "Nick Chen"


async def test_login_username_case_insensitive(client, mailer):
    await client.post(
        "/v1/auth/register/send-code",
        json={"password": _PW, "email": "case@example.com"},
    )
    r = await client.post(
        "/v1/auth/register/verify",
        json={"email": "case@example.com", "code": mailer.last_code},
    )
    assert r.status_code == 201
    await client.post(
        "/v1/auth/login",
        json={"username": "case@example.com", "password": _PW},
        headers=_DESKTOP,
    )
    r = await client.patch("/v1/auth/me", json={"username": "caseuser"})
    assert r.status_code == 200
    r = await client.post(
        "/v1/auth/login",
        json={"username": "CaseUser", "password": _PW},
        headers=_DESKTOP,
    )
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "caseuser"


async def test_patch_username_claim_and_search(client, mailer):
    await client.post(
        "/v1/auth/register/send-code",
        json={"password": _PW, "email": "search@example.com"},
    )
    r = await client.post(
        "/v1/auth/register/verify",
        json={"email": "search@example.com", "code": mailer.last_code},
    )
    user_id = r.json()["id"]
    await client.post(
        "/v1/auth/login",
        json={"username": "search@example.com", "password": _PW},
        headers=_DESKTOP,
    )
    r = await client.patch("/v1/auth/me", json={"username": "searchable"})
    assert r.status_code == 200
    assert r.json()["username"] == "searchable"
    r = await client.get("/v1/messages/users/search", params={"q": "searchable"})
    assert r.status_code == 200
    assert r.json()["total"] == 0
    await register_and_login(client, "finder")
    r = await client.get("/v1/messages/users/search", params={"q": "searchable"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["data"][0]["username"] == "searchable"
    r = await client.get("/v1/messages/users/search", params={"q": user_id})
    assert r.json()["total"] == 1
    assert r.json()["data"][0]["id"] == user_id
