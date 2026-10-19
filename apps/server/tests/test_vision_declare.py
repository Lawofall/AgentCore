"""BYOK vision declare: normalize, reject platform, overlay catalog vision."""

from __future__ import annotations

from types import SimpleNamespace

from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.image_accept import catalog_vision_tags, model_accepts_images
from agentcore.llm.vision_declare import (
    MAX_VISION_ACCEPT_REFS,
    VisionDeclareCapError,
    VisionDeclareRefError,
    apply_vision_accept,
    normalize_vision_accept_refs,
    vision_declared,
)


def test_normalize_keeps_byok_drops_platform() -> None:
    assert normalize_vision_accept_refs(
        ["@byok/p1/my-vl", "@platform/glm-5.3", "not-a-ref", "@byok/p1/my-vl", 12]
    ) == ["@byok/p1/my-vl"]


def test_apply_upsert_and_clear() -> None:
    ref, stored, mapping = apply_vision_accept([], "@byok/p1/my-vl", True)
    assert ref == "@byok/p1/my-vl"
    assert stored is True
    assert mapping == ["@byok/p1/my-vl"]
    ref, stored, mapping = apply_vision_accept(mapping, "@byok/p1/my-vl", False)
    assert stored is False
    assert mapping == []


def test_apply_rejects_platform_and_junk() -> None:
    try:
        apply_vision_accept([], "@platform/flash", True)
        raise AssertionError("expected platform reject")
    except VisionDeclareRefError as exc:
        assert exc.code == "platform"
    try:
        apply_vision_accept([], "flash", True)
        raise AssertionError("expected invalid reject")
    except VisionDeclareRefError as exc:
        assert exc.code == "invalid"


def test_apply_caps_new_keys() -> None:
    current = [f"@byok/p1/m{i}" for i in range(MAX_VISION_ACCEPT_REFS)]
    try:
        apply_vision_accept(current, "@byok/p1/extra", True)
        raise AssertionError("expected cap")
    except VisionDeclareCapError:
        pass
    ref, stored, mapping = apply_vision_accept(current, "@byok/p1/m0", True)
    assert ref == "@byok/p1/m0"
    assert stored is True
    assert len(mapping) == MAX_VISION_ACCEPT_REFS


def test_vision_declared_only_matching_byok() -> None:
    creds = LLMCredentials(
        api_key="k",
        base_url="https://example.test/v1",
        default_model="my-vl",
        source="user",
        provider_id="p1",
    )
    refs = ["@byok/p1/my-vl"]
    assert vision_declared("my-vl", creds, refs) is True
    assert vision_declared("other", creds, refs) is False
    platform = LLMCredentials(
        api_key="k",
        base_url="https://example.test/v1",
        default_model="my-vl",
        source="platform",
        provider_id="p1",
    )
    assert vision_declared("my-vl", platform, refs) is False


def test_catalog_overlay_declared_unknown_id() -> None:
    tags = catalog_vision_tags("my-vl", ["tools"], declared=True)
    assert "vision" in tags
    assert "tools" in tags
    assert model_accepts_images("my-vl") is False
    assert model_accepts_images("my-vl", declared=True) is True


async def test_put_and_get_vision_accept(monkeypatch) -> None:
    import httpx
    from httpx import ASGITransport

    from agentcore.api.dependencies import get_current_user, get_db, get_user_repo
    from agentcore.api.routes import model_catalog as route
    from agentcore.llm.catalog import (
        ModelCatalog,
        ModelCatalogCurrent,
        ModelCatalogEntry,
    )
    from agentcore.main import app

    stored: list[str] = []

    class _Users:
        async def hidden_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def enabled_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def model_display_names(self, _user_id: str) -> dict[str, str]:
            return {}

        async def composer_model_override(self, _user_id: str) -> dict | None:
            return None

        async def vision_accept_refs(self, _user_id: str) -> tuple[str, ...]:
            return tuple(stored)

        async def set_vision_accept(
            self, _user_id: str, ref: str, accept: bool
        ) -> tuple[str, bool]:
            canonical, stored_flag, mapping = apply_vision_accept(stored, ref, accept)
            stored.clear()
            stored.extend(mapping)
            return canonical, stored_flag

    async def _fake_catalog(*_a, **_k) -> ModelCatalog:
        return ModelCatalog(
            current=ModelCatalogCurrent(
                id="my-vl", origin="byok", provider_id="p1"
            ),
            byok_configured=True,
            models=[
                ModelCatalogEntry(
                    id="my-vl",
                    origin="byok",
                    display_name="My VL",
                    vendor="其他",
                    provider_id="p1",
                    provider_label="Custom",
                    capabilities=["tools"],
                ),
            ],
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
            row = next(m for m in listed.json()["models"] if m["id"] == "my-vl")
            assert "vision" not in row["capabilities"]

            put = await client.put(
                "/v1/users/me/models/vision-accept",
                json={"ref": "@byok/p1/my-vl", "accept": True},
            )
            assert put.status_code == 200, put.text
            assert put.json() == {"ref": "@byok/p1/my-vl", "accept": True}

            listed = await client.get("/v1/users/me/models")
            row = next(m for m in listed.json()["models"] if m["id"] == "my-vl")
            assert "vision" in row["capabilities"]

            denied = await client.put(
                "/v1/users/me/models/vision-accept",
                json={"ref": "@platform/glm-5.3", "accept": True},
            )
            assert denied.status_code == 400

            cleared = await client.put(
                "/v1/users/me/models/vision-accept",
                json={"ref": "@byok/p1/my-vl", "accept": False},
            )
            assert cleared.status_code == 200
            assert cleared.json()["accept"] is False
            listed = await client.get("/v1/users/me/models")
            row = next(m for m in listed.json()["models"] if m["id"] == "my-vl")
            assert "vision" not in row["capabilities"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_user_repo, None)
