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
                id="claude-haiku-4-5",
                origin="byok",
                display_name="Claude Haiku 4.5",
                vendor="Anthropic",
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
    assert grok["custom"] is False
    assert grok["available"] is False
    assert grok["unavailable_reason"] == {
        "code": "upstream_protocol_unsupported",
        "required_protocol": "openai_responses",
    }
    haiku = next(m for m in dumped["models"] if m["id"] == "claude-haiku-4-5")
    assert haiku["unavailable_reason"] == {
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

    from agentcore.api.dependencies import get_current_user, get_db, get_user_repo
    from agentcore.main import app

    async def _fake_catalog(*_a, **_k) -> ModelCatalog:
        return _catalog_with_off_protocol()

    async def _fake_user():
        return SimpleNamespace(user_id="u1")

    async def _fake_db():
        yield None

    class _Users:
        async def hidden_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def enabled_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def model_display_names(self, _user_id: str) -> dict[str, str]:
            return {}

        async def composer_model_override(self, _user_id: str) -> dict | None:
            return None

    monkeypatch.setattr(route, "resolve_model_catalog", _fake_catalog)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_user_repo] = lambda: _Users()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/v1/users/me/models")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["hidden_refs"] == []
        assert body["enabled_refs"] == []
        grok = next(m for m in body["models"] if m["id"] == "grok-4.5")
        assert grok["available"] is False
        assert grok["unavailable_reason"] == {
            "code": "upstream_protocol_unsupported",
            "required_protocol": "openai_responses",
        }
        haiku = next(m for m in body["models"] if m["id"] == "claude-haiku-4-5")
        assert haiku["unavailable_reason"]["required_protocol"] == (
            "anthropic_messages"
        )
        kimi = next(m for m in body["models"] if m["id"] == "kimi-k2.5")
        assert kimi["available"] is True
        assert kimi["unavailable_reason"] is None
        assert body["composer_model"] is None
        assert body["composer_model_knobs"] == {}
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_user_repo, None)


async def test_list_user_models_returns_knobs_by_model(monkeypatch) -> None:
    import httpx
    from httpx import ASGITransport

    from agentcore.api.dependencies import get_current_user, get_db, get_user_repo
    from agentcore.main import app

    async def _fake_catalog(*_a, **_k) -> ModelCatalog:
        return _catalog_with_off_protocol()

    async def _fake_user():
        return SimpleNamespace(user_id="u1")

    async def _fake_db():
        yield None

    class _Users:
        async def hidden_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def enabled_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def model_display_names(self, _user_id: str) -> dict[str, str]:
            return {}

        async def composer_model_override(self, _user_id: str) -> dict | None:
            return {
                "main": {
                    "origin": "byok",
                    "provider_id": "p1",
                    "model": "kimi-k2.5",
                },
                "worker": None,
                "reasoning_effort": None,
                "context_budget": 128000,
                "knobs_by_model": {
                    "@byok/p1/kimi-k2.5": {
                        "reasoning_effort": None,
                        "context_budget": 128000,
                    },
                    "@platform/glm-5.2": {
                        "reasoning_effort": "high",
                        "context_budget": None,
                    },
                },
            }

    monkeypatch.setattr(route, "resolve_model_catalog", _fake_catalog)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_user_repo] = lambda: _Users()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/v1/users/me/models")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["composer_model"]["main"]["model"] == "kimi-k2.5"
        assert "knobs_by_model" not in body["composer_model"]
        assert body["composer_model_knobs"]["@byok/p1/kimi-k2.5"] == {
            "reasoning_effort": None,
            "context_budget": 128000,
        }
        assert body["composer_model_knobs"]["@platform/glm-5.2"]["reasoning_effort"] == (
            "high"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_user_repo, None)


async def test_put_composer_model_persists_complete_snapshot(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import httpx
    from httpx import ASGITransport

    from agentcore.api.dependencies import get_current_user, get_db, get_user_repo
    from agentcore.main import app

    stored: dict = {}
    enabled: list[str] = []

    class _Users:
        async def enable_picker_refs(self, _user_id: str, refs):
            enabled.extend(refs)
            return tuple(dict.fromkeys(enabled))

        async def composer_model_override(self, _user_id: str) -> dict | None:
            return {
                "main": {
                    "origin": "platform",
                    "provider_id": None,
                    "model": "glm-5.2",
                },
                "worker": None,
                "reasoning_effort": "high",
                "context_budget": None,
                "knobs_by_model": {
                    "@platform/glm-5.2": {
                        "reasoning_effort": "high",
                        "context_budget": None,
                    }
                },
            }

        async def set_composer_model_override(self, _user_id: str, payload: dict):
            stored["payload"] = payload
            return payload

    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.normalize_conversation_override",
        AsyncMock(
            return_value={
                "main": {
                    "origin": "byok",
                    "provider_id": "p1",
                    "model": "deepseek-v4-flash",
                },
                "worker": None,
                "reasoning_effort": "low",
                "context_budget": None,
            }
        ),
    )

    async def _fake_user():
        return SimpleNamespace(user_id="u1")

    async def _fake_db():
        yield None

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_user_repo] = lambda: _Users()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.put(
                "/v1/users/me/models/composer",
                json={
                    "main": {
                        "origin": "byok",
                        "provider_id": "p1",
                        "model": "deepseek-v4-flash",
                    },
                    "worker": None,
                    "reasoning_effort": "low",
                    "context_budget": None,
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reasoning_effort"] == "low"
        assert "knobs_by_model" not in body
        assert stored["payload"]["reasoning_effort"] == "low"
        assert stored["payload"]["worker"] is None
        assert stored["payload"]["knobs_by_model"]["@platform/glm-5.2"][
            "reasoning_effort"
        ] == "high"
        assert stored["payload"]["knobs_by_model"]["@byok/p1/deepseek-v4-flash"] == {
            "reasoning_effort": "low",
            "context_budget": None,
        }
        assert enabled == ["@byok/p1/deepseek-v4-flash"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_user_repo, None)
