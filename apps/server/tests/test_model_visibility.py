"""Account picker visibility: normalize refs; PUT replaces the set."""

from __future__ import annotations

from types import SimpleNamespace

from agentcore.llm.model_visibility import (
    MAX_HIDDEN_MODEL_REFS,
    byok_refs_from_override,
    byok_refs_from_slots,
    merge_enabled_picker_refs,
    normalize_hidden_model_refs,
    picker_ref_enabled,
)


def test_normalize_keeps_canonical_refs_and_drops_junk() -> None:
    assert normalize_hidden_model_refs(
        [
            "@platform/flash",
            "  @byok/p1/ep-1  ",
            "@platform/flash",
            "mention-not-a-ref",
            "@nope/x",
            12,
            "",
        ]
    ) == ["@platform/flash", "@byok/p1/ep-1"]


def test_normalize_caps_length() -> None:
    raw = [f"@platform/m{i}" for i in range(MAX_HIDDEN_MODEL_REFS + 40)]
    assert len(normalize_hidden_model_refs(raw)) == MAX_HIDDEN_MODEL_REFS


def test_normalize_empty_and_non_list() -> None:
    assert normalize_hidden_model_refs(None) == []
    assert normalize_hidden_model_refs("nope") == []


def test_merge_enabled_picker_refs_unions_byok_only() -> None:
    assert merge_enabled_picker_refs(
        ["@byok/p1/flash"],
        ["@byok/p1/flash", "@byok/p1/ep-1", "@platform/glm-5.2"],
    ) == ["@byok/p1/flash", "@byok/p1/ep-1"]


def test_byok_refs_from_slots_and_override() -> None:
    assert byok_refs_from_slots(
        {"origin": "byok", "model": "ep-1", "provider_id": "p1"},
        {"origin": "platform", "model": "glm-5.2", "provider_id": None},
        None,
    ) == ["@byok/p1/ep-1"]
    assert byok_refs_from_override(
        {
            "main": {"origin": "byok", "model": "flash", "provider_id": "p1"},
            "worker": {"origin": "byok", "model": "ep-1", "provider_id": "p1"},
        }
    ) == ["@byok/p1/flash", "@byok/p1/ep-1"]


def test_picker_membership_platform_opt_out_byok_opt_in() -> None:
    assert picker_ref_enabled("@platform/flash", "platform") is True
    assert (
        picker_ref_enabled(
            "@platform/flash", "platform", hidden_refs=["@platform/flash"]
        )
        is False
    )
    assert picker_ref_enabled("@byok/p1/glm-5.2", "byok") is False
    assert (
        picker_ref_enabled(
            "@byok/p1/glm-5.2",
            "byok",
            enabled_refs=["@byok/p1/glm-5.2"],
        )
        is True
    )
    assert (
        picker_ref_enabled(
            "@byok/p1/glm-5.2",
            "byok",
            hidden_refs=["@byok/p1/glm-5.2"],
            enabled_refs=["@byok/p1/glm-5.2"],
        )
        is False
    )


async def test_put_and_get_hidden_refs(monkeypatch) -> None:
    import httpx
    from httpx import ASGITransport

    from agentcore.api.dependencies import get_current_user, get_db, get_user_repo
    from agentcore.api.routes import model_catalog as route
    from agentcore.llm.catalog import ModelCatalog, ModelCatalogCurrent
    from agentcore.main import app

    stored_hidden: list[str] = []
    stored_enabled: list[str] = []

    class _Users:
        async def hidden_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return tuple(stored_hidden)

        async def enabled_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return tuple(stored_enabled)

        async def model_display_names(self, _user_id: str) -> dict[str, str]:
            return {}

        async def composer_model_override(self, _user_id: str) -> dict | None:
            return None

        async def set_picker_visibility(
            self,
            _user_id: str,
            *,
            hidden: list[str],
            enabled: list[str] | None,
        ) -> tuple[tuple[str, ...], tuple[str, ...]]:
            stored_hidden[:] = list(hidden)
            if enabled is not None:
                stored_enabled[:] = list(enabled)
            return tuple(stored_hidden), tuple(stored_enabled)

    async def _fake_catalog(*_a, **_k) -> ModelCatalog:
        return ModelCatalog(
            current=ModelCatalogCurrent(
                id="flash", origin="platform", provider_id=None
            ),
            byok_configured=False,
            models=[],
        )

    async def _fake_user():
        return SimpleNamespace(user_id="u1")

    async def _fake_db():
        yield None

    monkeypatch.setattr(route, "resolve_model_catalog", _fake_catalog)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_user_repo] = lambda: _Users()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            listed = await client.get("/v1/users/me/models")
            assert listed.status_code == 200, listed.text
            assert listed.json()["hidden_refs"] == []
            assert listed.json()["enabled_refs"] == []

            put = await client.put(
                "/v1/users/me/models/hidden",
                json={
                    "refs": [
                        "@platform/flash",
                        "ignore-me",
                        "@platform/flash",
                    ],
                    "enabled_refs": [
                        "@byok/p1/glm-5.2",
                        "ignore-me",
                    ],
                },
            )
            assert put.status_code == 200, put.text
            assert put.json()["refs"] == ["@platform/flash"]
            assert put.json()["enabled_refs"] == ["@byok/p1/glm-5.2"]

            listed = await client.get("/v1/users/me/models")
            assert listed.json()["hidden_refs"] == ["@platform/flash"]
            assert listed.json()["enabled_refs"] == ["@byok/p1/glm-5.2"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_user_repo, None)
