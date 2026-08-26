"""Route-layer tests for ``GET /v1/users/me/models``.

Catalog assembly lives in ``test_model_catalog.py``; this file only asserts the REST
mapping (structured ``unavailable_reason`` on the wire).
"""

from __future__ import annotations

from types import SimpleNamespace

from agentcore.api.routes import model_catalog as route
from agentcore.llm.catalog import (
    ModelCatalog,
    ModelCatalogCurrent,
    ModelCatalogEntry,
    ModelUnavailableReason,
)


def _catalog_with_off_protocol() -> ModelCatalog:
    return ModelCatalog(
        current=ModelCatalogCurrent(
            id="kimi-k2.5", origin="byok", provider_id="p1"
        ),
        byok_configured=True,
        models=[
            ModelCatalogEntry(
                id="grok-4.5",
                origin="byok",
                display_name="Grok 4.5",
                vendor="xAI",
                available=False,
                provider_id="p1",
                provider_label="OpenCode Go",
                unavailable_reason=ModelUnavailableReason(
                    code="upstream_protocol_unsupported",
                    required_protocol="openai_responses",
                ),
            ),
            ModelCatalogEntry(
                id="minimax-m2.7",
                origin="byok",
                display_name="MiniMax M2.7",
                vendor="MiniMax",
                available=False,
                provider_id="p1",
                provider_label="OpenCode Go",
                unavailable_reason=ModelUnavailableReason(
                    code="upstream_protocol_unsupported",
                    required_protocol="anthropic_messages",
                ),
            ),
            ModelCatalogEntry(
                id="kimi-k2.5",
                origin="byok",
                display_name="Kimi K2.5",
                vendor="Moonshot",
                available=True,
                provider_id="p1",
                provider_label="OpenCode Go",
            ),
        ],
    )


def test_to_response_includes_unavailable_reason() -> None:
    dumped = route._to_response(_catalog_with_off_protocol()).model_dump()
    assert dumped["current"]["ref"] == "@byok/p1/kimi-k2.5"
    grok = next(m for m in dumped["models"] if m["id"] == "grok-4.5")
    assert grok["ref"] == "@byok/p1/grok-4.5"
    assert grok["available"] is False
    assert grok["unavailable_reason"] == {
        "code": "upstream_protocol_unsupported",
        "required_protocol": "openai_responses",
    }
    minimax = next(m for m in dumped["models"] if m["id"] == "minimax-m2.7")
    assert minimax["unavailable_reason"] == {
        "code": "upstream_protocol_unsupported",
        "required_protocol": "anthropic_messages",
    }
    kimi = next(m for m in dumped["models"] if m["id"] == "kimi-k2.5")
    assert kimi["available"] is True
    assert kimi["unavailable_reason"] is None


async def test_list_user_models_includes_unavailable_reason(monkeypatch) -> None:
    """GET /v1/users/me/models carries structured unavailability, not copy."""
    import httpx
    from httpx import ASGITransport

    from agentcore.api.dependencies import get_current_user, get_db
    from agentcore.main import app

    async def _fake_catalog(_session, _user_id: str) -> ModelCatalog:
        return _catalog_with_off_protocol()

    async def _fake_user():
        return SimpleNamespace(user_id="u1")

    async def _fake_db():
        yield None

    monkeypatch.setattr(route, "resolve_model_catalog", _fake_catalog)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/v1/users/me/models")
        assert response.status_code == 200, response.text
        body = response.json()
        grok = next(m for m in body["models"] if m["id"] == "grok-4.5")
        assert grok["available"] is False
        assert grok["unavailable_reason"] == {
            "code": "upstream_protocol_unsupported",
            "required_protocol": "openai_responses",
        }
        minimax = next(m for m in body["models"] if m["id"] == "minimax-m2.7")
        assert minimax["unavailable_reason"]["required_protocol"] == (
            "anthropic_messages"
        )
        kimi = next(m for m in body["models"] if m["id"] == "kimi-k2.5")
        assert kimi["available"] is True
        assert kimi["unavailable_reason"] is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
