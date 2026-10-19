"""Account BYOK display-name aliases: normalize, reject platform, overlay on GET."""

from __future__ import annotations

from types import SimpleNamespace

from agentcore.llm.model_display_names import (
    MAX_MODEL_DISPLAY_NAMES,
    DisplayNameCapError,
    DisplayNameRefError,
    apply_model_display_name,
    normalize_display_name,
    normalize_model_display_names,
    overlay_display_name,
)


def test_normalize_display_name_collapses_and_caps() -> None:
    assert normalize_display_name("  公司   Flash  ") == "公司 Flash"
    assert normalize_display_name("   ") is None
    assert normalize_display_name(None) is None
    assert len(normalize_display_name("x" * 80) or "") == 64


def test_normalize_map_keeps_byok_and_drops_platform() -> None:
    assert normalize_model_display_names(
        {
            "@byok/p1/flash": "  公司  ",
            "@platform/flash": "平台别名",
            "not-a-ref": "x",
            12: "nope",
        }
    ) == {"@byok/p1/flash": "公司"}


def test_apply_upsert_and_delete() -> None:
    ref, name, mapping = apply_model_display_name({}, "@byok/p1/flash", "公司")
    assert ref == "@byok/p1/flash"
    assert name == "公司"
    assert mapping == {"@byok/p1/flash": "公司"}
    ref, name, mapping = apply_model_display_name(mapping, "@byok/p1/flash", "  ")
    assert name is None
    assert mapping == {}


def test_apply_rejects_platform_and_junk() -> None:
    try:
        apply_model_display_name({}, "@platform/flash", "x")
        raise AssertionError("expected platform reject")
    except DisplayNameRefError as exc:
        assert exc.code == "platform"
    try:
        apply_model_display_name({}, "flash", "x")
        raise AssertionError("expected invalid reject")
    except DisplayNameRefError as exc:
        assert exc.code == "invalid"


def test_apply_caps_new_keys() -> None:
    current = {f"@byok/p1/m{i}": f"n{i}" for i in range(MAX_MODEL_DISPLAY_NAMES)}
    try:
        apply_model_display_name(current, "@byok/p1/extra", "x")
        raise AssertionError("expected cap")
    except DisplayNameCapError:
        pass
    ref, name, mapping = apply_model_display_name(
        current, "@byok/p1/m0", "renamed"
    )
    assert ref == "@byok/p1/m0"
    assert name == "renamed"
    assert mapping["@byok/p1/m0"] == "renamed"
    assert len(mapping) == MAX_MODEL_DISPLAY_NAMES


def test_overlay_only_byok() -> None:
    names = {"@byok/p1/flash": "公司", "@platform/flash": "忽略"}
    assert (
        overlay_display_name(
            origin="byok",
            ref="@byok/p1/flash",
            display_name="DeepSeek V4 Flash",
            names=names,
        )
        == "公司"
    )
    assert (
        overlay_display_name(
            origin="platform",
            ref="@platform/flash",
            display_name="DeepSeek V4 Flash",
            names=names,
        )
        == "DeepSeek V4 Flash"
    )


async def test_put_and_get_display_name(monkeypatch) -> None:
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

    stored: dict[str, str] = {}

    class _Users:
        async def hidden_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def enabled_model_refs(self, _user_id: str) -> tuple[str, ...]:
            return ()

        async def model_display_names(self, _user_id: str) -> dict[str, str]:
            return dict(stored)

        async def composer_model_override(self, _user_id: str) -> dict | None:
            return None

        async def set_model_display_name(
            self, _user_id: str, ref: str, display_name: str | None
        ) -> tuple[str, str | None]:
            canonical, name, mapping = apply_model_display_name(
                stored, ref, display_name
            )
            stored.clear()
            stored.update(mapping)
            return canonical, name

    async def _fake_catalog(*_a, **_k) -> ModelCatalog:
        return ModelCatalog(
            current=ModelCatalogCurrent(
                id="flash", origin="byok", provider_id="p1"
            ),
            byok_configured=True,
            models=[
                ModelCatalogEntry(
                    id="flash",
                    origin="byok",
                    display_name="DeepSeek V4 Flash",
                    vendor="DeepSeek",
                    provider_id="p1",
                    provider_label="DeepSeek",
                ),
                ModelCatalogEntry(
                    id="glm",
                    origin="platform",
                    display_name="GLM-5.3",
                    vendor="智谱 AI",
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
            flash = next(m for m in listed.json()["models"] if m["id"] == "flash")
            assert flash["display_name"] == "DeepSeek V4 Flash"

            put = await client.put(
                "/v1/users/me/models/display-names",
                json={"ref": "@byok/p1/flash", "display_name": "  公司 Flash  "},
            )
            assert put.status_code == 200, put.text
            assert put.json() == {
                "ref": "@byok/p1/flash",
                "display_name": "公司 Flash",
            }

            listed = await client.get("/v1/users/me/models")
            flash = next(m for m in listed.json()["models"] if m["id"] == "flash")
            glm = next(m for m in listed.json()["models"] if m["id"] == "glm")
            assert flash["display_name"] == "公司 Flash"
            assert glm["display_name"] == "GLM-5.3"
            assert glm["badge"] is None

            denied = await client.put(
                "/v1/users/me/models/display-names",
                json={"ref": "@platform/glm", "display_name": "不可"},
            )
            assert denied.status_code == 400

            cleared = await client.put(
                "/v1/users/me/models/display-names",
                json={"ref": "@byok/p1/flash", "display_name": ""},
            )
            assert cleared.status_code == 200
            assert cleared.json()["display_name"] is None
            listed = await client.get("/v1/users/me/models")
            flash = next(m for m in listed.json()["models"] if m["id"] == "flash")
            assert flash["display_name"] == "DeepSeek V4 Flash"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_user_repo, None)
