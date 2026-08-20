"""HTTP-level checks that do not need PostgreSQL."""

import pytest
from httpx import ASGITransport, AsyncClient

from agentcore.api.schemas.auth import RegisterSendCodeRequest
from agentcore.config import settings
from agentcore.core.error_codes import ErrorCode
from agentcore.main import app


@pytest.mark.asyncio
async def test_legacy_register_returns_410_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "legacy_register_enabled", False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            "/v1/auth/register",
            json={"username": "goneuser", "password": "password123"},
        )
    assert r.status_code == 410
    body = r.json()["error"]
    assert body["code"] == ErrorCode.GONE
    assert "send-code" in body["message"]


def test_register_send_code_ignores_client_username_and_display_name():
    body = RegisterSendCodeRequest.model_validate(
        {
            "password": "password123",
            "email": "ada@example.com",
            "username": "picked",
            "display_name": "Ada",
        }
    )
    dumped = body.model_dump()
    assert dumped == {"password": "password123", "email": "ada@example.com"}


@pytest.mark.asyncio
async def test_legacy_register_stays_gone_when_debug_on(monkeypatch):
    monkeypatch.setattr(settings, "legacy_register_enabled", False)
    monkeypatch.setattr(settings, "debug", True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            "/v1/auth/register",
            json={"username": "debuguser", "password": "password123"},
        )
    assert r.status_code == 410


def test_register_verify_accepts_optional_display_name():
    from agentcore.api.schemas.auth import RegisterVerifyRequest

    body = RegisterVerifyRequest.model_validate(
        {
            "email": "ada@example.com",
            "code": "123456",
            "display_name": "Ada",
        }
    )
    assert body.display_name == "Ada"
