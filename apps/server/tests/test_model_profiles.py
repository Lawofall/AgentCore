"""Unit tests for dynamic platform system presets (uuid5 projection)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.assembly.recipes import FULL, capability_preset_id
from agentcore.llm.model_profiles import (
    LlmModelProfileService,
    _capability_main_model_id,
    is_system_profile_id,
    platform_preset_id,
    resolve_system_preset_main,
    system_presets,
    system_profile_default_id,
)

# Former hardcoded product UUIDs — must NOT be recognized as system presets.
_LEGACY_SYSTEM_PROFILE_52 = "00000000-0000-4000-8000-000000000011"
_LEGACY_SYSTEM_PROFILE_GROK = "00000000-0000-4000-8000-000000000012"


def _glm_preset_id() -> str:
    return platform_preset_id("glm-5.2")


def test_platform_preset_id_is_stable_uuid5():
    expected = str(
        uuid.uuid5(uuid.NAMESPACE_URL, "agentcore:platform-preset:glm-5.2")
    )
    assert platform_preset_id("glm-5.2") == expected
    assert platform_preset_id("glm-5.2") == platform_preset_id("glm-5.2")
    assert platform_preset_id("glm-5.2") != platform_preset_id("grok-4.5")


def test_system_presets_project_from_listable_catalog(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2", "grok-4.5"],
    )
    presets = system_presets()
    assert list(presets.values()) == ["glm-5.2", "grok-4.5"]
    assert presets[_glm_preset_id()] == "glm-5.2"
    assert presets[platform_preset_id("grok-4.5")] == "grok-4.5"
    assert _glm_preset_id() in presets
    assert not is_system_profile_id(_glm_preset_id())
    assert is_system_profile_id(capability_preset_id(FULL))
    assert not is_system_profile_id(_LEGACY_SYSTEM_PROFILE_52)
    assert not is_system_profile_id(_LEGACY_SYSTEM_PROFILE_GROK)
    assert not is_system_profile_id("00000000-0000-4000-8000-000000000002")


def test_system_profile_default_prefers_platform_model(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["grok-4.5", "glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.settings.platform_model",
        "glm-5.2",
    )
    assert system_profile_default_id() == capability_preset_id(FULL)
    assert _capability_main_model_id() == "glm-5.2"


def test_system_profile_default_falls_to_first_when_platform_model_absent(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["grok-4.5", "glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.settings.platform_model",
        "not-in-list",
    )
    assert system_profile_default_id() == capability_preset_id(FULL)
    assert _capability_main_model_id() == "grok-4.5"


def test_resolve_system_preset_main_is_fixed(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    sel = resolve_system_preset_main(_glm_preset_id())
    assert sel.model == "glm-5.2"
    assert sel.origin == "platform"
    assert sel.provider_id is None


@pytest.mark.asyncio
async def test_list_profiles_hides_missing_catalog_models(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: [],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    assert [v.name for v in views] == ["极简", "轻量", "完整"]


@pytest.mark.asyncio
async def test_list_profiles_hides_system_when_platform_billing_off(monkeypatch):
    """byok + free-tier off: allowlist may still list glm-5.2 — presets must hide."""
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: False,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    assert [v.name for v in views] == ["极简", "轻量", "完整"]


@pytest.mark.asyncio
async def test_list_profiles_marks_default_when_present(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.settings.platform_model",
        "glm-5.2",
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    assert [v.name for v in views] == ["极简", "轻量", "完整"]
    assert views[2].id == capability_preset_id(FULL)
    assert views[2].is_default is True
    assert views[0].omit_factory_catalog is True
    assert views[1].omit_factory_catalog is True
    assert views[2].omit_factory_catalog is False


@pytest.mark.asyncio
async def test_list_profiles_projects_multiple_models(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2", "grok-4.5"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.settings.platform_model",
        "glm-5.2",
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    assert [v.name for v in views] == ["极简", "轻量", "完整"]
    assert views[2].is_default is True


@pytest.mark.asyncio
async def test_official_list_is_three_recipes_not_one_row_per_sku(monkeypatch):
    """Free and priced SKUs stay in the catalog. The official list does not grow with them."""
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["deepseek-v4-flash-free", "deepseek-v4-flash"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    assert [v.name for v in views] == ["极简", "轻量", "完整"]
    assert [v.recipe for v in views] == ["chat", "web", "full"]

    expanded = await svc.expand("u1", None)
    assert expanded.main.model == "deepseek-v4-flash-free"
    assert expanded.name == "完整"
    assert expanded.profile_id == capability_preset_id(FULL)


@pytest.mark.asyncio
async def test_expand_none_and_dangling_fall_back_to_platform_default(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.settings.platform_model",
        "glm-5.2",
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._repo.get = AsyncMock(return_value=None)  # type: ignore[method-assign]

    expanded = await svc.expand("u1", None)
    assert expanded.profile_id == system_profile_default_id()
    assert expanded.main.model == "glm-5.2"
    assert expanded.name == "完整"

    dangling = await svc.expand("u1", "00000000-0000-4000-8000-000000000002")
    assert dangling.profile_id == system_profile_default_id()
    assert dangling.main.model == "glm-5.2"


@pytest.mark.asyncio
async def test_expand_legacy_hardcoded_uuid_falls_back_to_default(monkeypatch):
    """Old …0011 / …0012 ids are not system presets → dangling → PLATFORM_MODEL preset."""
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.settings.platform_model",
        "glm-5.2",
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._repo.get = AsyncMock(return_value=None)  # type: ignore[method-assign]

    for legacy in (_LEGACY_SYSTEM_PROFILE_52, _LEGACY_SYSTEM_PROFILE_GROK):
        expanded = await svc.expand("u1", legacy)
        assert expanded.profile_id == system_profile_default_id()
        assert expanded.main.model == "glm-5.2"


@pytest.mark.asyncio
async def test_set_default_rejects_legacy_preset_id():
    """A leftover per-model preset id is not an assembly."""
    from agentcore.core.errors import NotFoundError

    svc = LlmModelProfileService(MagicMock())
    svc._repo.get = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(NotFoundError, match="模型组合不存在"):
        await svc.set_default("u1", _glm_preset_id())


@pytest.mark.asyncio
async def test_ensure_rejects_legacy_preset_id():
    from agentcore.core.errors import ValidationError

    svc = LlmModelProfileService(MagicMock())
    svc._repo.get = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValidationError, match="不存在"):
        await svc.ensure_profile_usable("u1", _glm_preset_id())


@pytest.mark.asyncio
async def test_list_marks_user_default_when_system_pin_dormant(monkeypatch):
    """A leftover per-model pin is not an assembly. Recipes stay listed; 完整 is the star."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: False,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    user_row = SimpleNamespace(
        id="user-combo-1",
        name="我的组合",
        kind="user",
        main_origin="byok",
        main_model="gpt-4o",
        main_provider_id="p1",
        worker_origin=None,
        worker_model=None,
        worker_provider_id=None,
        background_origin=None,
        background_model=None,
        background_provider_id=None,
        vision_origin=None,
        vision_model=None,
        vision_provider_id=None,
        reasoning_effort=None,
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=_glm_preset_id())  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[user_row])  # type: ignore[method-assign]
    svc._users.set_default_model_profile = AsyncMock()  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    assert [v.name for v in views] == ["极简", "轻量", "完整", "我的组合"]
    assert views[2].is_default is True
    assert views[3].id == "user-combo-1"
    assert views[3].is_default is False
    assert views[3].recipe is None
    svc._users.set_default_model_profile.assert_awaited()


@pytest.mark.asyncio
async def test_list_projects_locked_recipe_on_owned_row(monkeypatch):
    """The shelf folds this row into the tray chip while the key is still set."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    owned = SimpleNamespace(
        id="owned-chat",
        name="极简",
        kind="user",
        recipe="chat",
        main_origin="platform",
        main_model="glm-5.2",
        main_provider_id=None,
        worker_origin=None,
        worker_model=None,
        worker_provider_id=None,
        background_origin=None,
        background_model=None,
        background_provider_id=None,
        vision_origin=None,
        vision_model=None,
        vision_provider_id=None,
        reasoning_effort=None,
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value="owned-chat")  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[owned])  # type: ignore[method-assign]
    svc._capture_legacy_model_star = AsyncMock()  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    hit = next(view for view in views if view.id == "owned-chat")
    assert hit.recipe == "chat"
    assert hit.is_default is True


@pytest.mark.asyncio
async def test_expand_dormant_system_falls_to_byok_coherent(monkeypatch):
    """Unavailable system preset → BYOK selection; name/origin match (not GLM-5.2 + byok)."""
    from agentcore.llm.resolve import ModelSelection

    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: False,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles._provider_first_fallback",
        AsyncMock(
            return_value=ModelSelection(
                model="user-flash", origin="byok", provider_id="p1"
            )
        ),
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=_glm_preset_id())  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]
    svc._repo.get = AsyncMock(return_value=None)  # type: ignore[method-assign]

    expanded = await svc.expand("u1", None)
    assert expanded.main.origin == "byok"
    assert expanded.main.model == "user-flash"
    assert expanded.main.provider_id == "p1"
    assert expanded.kind == "implicit"
    assert expanded.vision is None
    assert "GLM-5.2" not in expanded.name
    assert "glm-5.2" not in expanded.name
    assert expanded.profile_id != _glm_preset_id()


@pytest.mark.asyncio
async def test_provider_first_fallback_uses_url_seed_not_stored_column(monkeypatch):
    """Leftover ``user_llm_providers.default_model`` is not a chat identity."""
    from agentcore.llm.model_profiles import _provider_first_fallback
    from agentcore.llm.profiles import PLATFORM_MODEL_FLASH

    row = SimpleNamespace(
        id="p1",
        base_url="https://api.openai.com/v1",
        default_model="stale-user-model",
    )
    monkeypatch.setattr(
        "agentcore.llm.resolve._default_chat_provider_row",
        AsyncMock(return_value=row),
    )
    sel = await _provider_first_fallback(MagicMock(), "u1")
    assert sel.model == "gpt-4o"
    assert sel.origin == "byok"
    assert sel.provider_id == "p1"

    custom = SimpleNamespace(
        id="p2",
        base_url="https://my-proxy.example/v1",
        default_model="stale-user-model",
    )
    monkeypatch.setattr(
        "agentcore.llm.resolve._default_chat_provider_row",
        AsyncMock(return_value=custom),
    )
    sel2 = await _provider_first_fallback(MagicMock(), "u1")
    assert sel2.model == PLATFORM_MODEL_FLASH
    assert sel2.origin == "byok"
    assert sel2.provider_id == "p2"


@pytest.mark.asyncio
async def test_expand_user_profile_includes_vision_slot(monkeypatch):
    """User combo with vision columns → expand surfaces vision; empty stays None."""
    from types import SimpleNamespace

    from agentcore.llm.resolve import ModelSelection

    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_catalog_visible",
        lambda: True,
    )

    async def _live(_session, _user_id, slot):
        return ModelSelection(
            model=slot.model, origin=slot.origin, provider_id=slot.provider_id
        )

    monkeypatch.setattr(
        "agentcore.llm.model_profiles._live_selection",
        _live,
    )

    row = SimpleNamespace(
        id="combo-v",
        name="识图组合",
        kind="user",
        main_origin="byok",
        main_model="gpt-4o",
        main_provider_id="p-main",
        worker_origin=None,
        worker_model=None,
        worker_provider_id=None,
        background_origin=None,
        background_model=None,
        background_provider_id=None,
        vision_origin="byok",
        vision_model="qwen-vl-max",
        vision_provider_id="p-vision",
        reasoning_effort=None,
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._repo.get = AsyncMock(return_value=row)  # type: ignore[method-assign]

    expanded = await svc.expand("u1", "combo-v")
    assert expanded.vision is not None
    assert expanded.vision.model == "qwen-vl-max"
    assert expanded.vision.origin == "byok"
    assert expanded.vision.provider_id == "p-vision"

    row_no_vision = SimpleNamespace(**{**row.__dict__, "vision_origin": None, "vision_model": None, "vision_provider_id": None})
    svc._repo.get = AsyncMock(return_value=row_no_vision)  # type: ignore[method-assign]
    expanded2 = await svc.expand("u1", "combo-v")
    assert expanded2.vision is None


@pytest.mark.asyncio
async def test_system_preset_view_vision_always_null(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    svc = LlmModelProfileService(MagicMock())
    view = svc._view_system(capability_preset_id(FULL), is_default=True)
    assert view.vision is None


@pytest.mark.asyncio
async def test_validate_slot_platform_requires_catalog_visible(monkeypatch):
    from agentcore.core.errors import ValidationError
    from agentcore.llm.model_profiles import ProfileSlot

    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: False,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    svc = LlmModelProfileService(MagicMock())
    with pytest.raises(ValidationError, match="不可用平台模型"):
        await svc._validate_slot(
            "u1",
            ProfileSlot(origin="platform", model="glm-5.2", provider_id=None),
            label="main",
        )


def _prov_row(**kwargs):
    defaults = {
        "id": "prov-1",
        "label": "OpenAI",
        "default_model": "gpt-4o",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _profile_db_row(**kwargs):
    defaults = {
        "id": "prof-1",
        "name": "combo",
        "kind": "user",
        "main_origin": "byok",
        "main_provider_id": "prov-1",
        "main_model": "gpt-4o",
        "worker_origin": None,
        "worker_provider_id": None,
        "worker_model": None,
        "background_origin": "byok",
        "background_provider_id": "prov-1",
        "background_model": "gpt5.6",
        "vision_origin": None,
        "vision_provider_id": None,
        "vision_model": None,
        "reasoning_effort": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class _ReachFake:
    def __init__(
        self,
        *,
        model_ids: list[str] | None = None,
        list_error: Exception | None = None,
        fail_models: set[str] | None = None,
    ) -> None:
        self._model_ids = model_ids
        self._list_error = list_error
        self._fail_models = fail_models or set()
        self.probe_models: list[str] = []

    async def list_models(self) -> list[str]:
        if self._list_error is not None:
            raise self._list_error
        assert self._model_ids is not None
        return list(self._model_ids)

    async def probe(self, *, model: str) -> None:
        from agentcore.core.errors import LLMError

        self.probe_models.append(model)
        if model in self._fail_models:
            raise LLMError(f"model {model} not found")

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_create_slot_warns_on_unreachable_byok_model_but_saves():
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.model_profiles import ProfileSlot

    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._providers = MagicMock()
    svc._providers.get = AsyncMock(return_value=_prov_row())
    svc._users = MagicMock()
    svc._users.set_default_model_profile = AsyncMock()
    created = _profile_db_row()
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=created)
    svc._repo.update = AsyncMock(return_value=created)

    fake = _ReachFake(model_ids=["gpt-4o"], fail_models={"gpt5.6"})
    creds = LLMCredentials(
        api_key="sk", base_url="https://x", default_model="gpt-4o", provider_id="prov-1"
    )
    with (
        patch(
            "agentcore.llm.resolve.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.factory.build_provider", return_value=fake),
    ):
        view = await svc.update_profile(
            "u1",
            "prof-1",
            main=ProfileSlot(origin="byok", model="gpt-4o", provider_id="prov-1"),
            background=ProfileSlot(
                origin="byok", model="gpt5.6", provider_id="prov-1"
            ),
            fields_set={"main", "background"},
        )
    assert view.id == "prof-1"
    assert view.background is not None
    assert view.background.model == "gpt5.6"
    assert len(view.warnings) == 1
    assert "gpt5.6" in view.warnings[0]
    assert "gpt5.6" in fake.probe_models


@pytest.mark.asyncio
async def test_create_slot_ark_ep_not_in_list_no_warning():
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.model_profiles import ProfileSlot

    ep = "ep-20240101000000-abcde"
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._providers = MagicMock()
    svc._providers.get = AsyncMock(return_value=_prov_row())
    svc._users = MagicMock()
    created = _profile_db_row(background_model=ep)
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=created)
    svc._repo.update = AsyncMock(return_value=created)

    fake = _ReachFake(model_ids=["gpt-4o", "doubao-pro"], fail_models=set())
    creds = LLMCredentials(
        api_key="sk", base_url="https://ark", default_model="gpt-4o", provider_id="prov-1"
    )
    with (
        patch(
            "agentcore.llm.resolve.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.factory.build_provider", return_value=fake),
    ):
        view = await svc.update_profile(
            "u1",
            "prof-1",
            main=ProfileSlot(origin="byok", model="gpt-4o", provider_id="prov-1"),
            background=ProfileSlot(origin="byok", model=ep, provider_id="prov-1"),
            fields_set={"main", "background"},
        )
    assert view.warnings == ()
    assert ep in fake.probe_models


@pytest.mark.asyncio
async def test_create_slot_list_fetch_failure_saves_without_warning():
    from agentcore.core.errors import LLMError
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.model_profiles import ProfileSlot

    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._providers = MagicMock()
    svc._providers.get = AsyncMock(return_value=_prov_row())
    svc._users = MagicMock()
    created = _profile_db_row(background_model="gpt5.6")
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=created)
    svc._repo.update = AsyncMock(return_value=created)

    fake = _ReachFake(list_error=LLMError("upstream /models 500"), fail_models={"gpt5.6"})
    creds = LLMCredentials(
        api_key="sk", base_url="https://x", default_model="gpt-4o", provider_id="prov-1"
    )
    with (
        patch(
            "agentcore.llm.resolve.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.factory.build_provider", return_value=fake),
    ):
        view = await svc.update_profile(
            "u1",
            "prof-1",
            main=ProfileSlot(origin="byok", model="gpt-4o", provider_id="prov-1"),
            background=ProfileSlot(
                origin="byok", model="gpt5.6", provider_id="prov-1"
            ),
            fields_set={"main", "background"},
        )
    assert view.id == "prof-1"
    assert view.warnings == ()
    assert fake.probe_models == []


@pytest.mark.asyncio
async def test_create_slot_rejects_alias_and_unsupported_effort():
    from agentcore.core.errors import ValidationError
    from agentcore.llm.model_profiles import ProfileSlot

    svc = LlmModelProfileService(MagicMock())
    svc._validate_slot = AsyncMock()  # type: ignore[method-assign]
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=_profile_db_row())
    svc._repo.update = AsyncMock()

    with pytest.raises(ValidationError, match="厂商档位"):
        await svc.update_profile(
            "u1",
            "prof-1",
            main=ProfileSlot(
                origin="byok", model="deepseek-v4-flash", provider_id="prov-1"
            ),
            reasoning_effort="medium",
            fields_set={"main", "reasoning_effort"},
        )
    with pytest.raises(ValidationError, match="不支持思考强度"):
        await svc.update_profile(
            "u1",
            "prof-1",
            main=ProfileSlot(origin="byok", model="gpt-4o", provider_id="prov-1"),
            reasoning_effort="low",
            fields_set={"main", "reasoning_effort"},
        )
    svc._repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_slot_persists_official_effort():
    from agentcore.llm.model_profiles import ProfileSlot

    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._validate_slot = AsyncMock()  # type: ignore[method-assign]
    svc._byok_reachability_warnings = AsyncMock(return_value=())  # type: ignore[method-assign]
    created = _profile_db_row(
        main_model="deepseek-v4-flash", reasoning_effort="low"
    )
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=created)
    svc._repo.update = AsyncMock(return_value=created)

    view = await svc.update_profile(
        "u1",
        "prof-1",
        main=ProfileSlot(
            origin="byok", model="deepseek-v4-flash", provider_id="prov-1"
        ),
        reasoning_effort="low",
        fields_set={"main", "reasoning_effort"},
    )
    assert svc._repo.update.await_args.kwargs["reasoning_effort"] == "low"
    assert view.reasoning_effort == "low"


@pytest.mark.asyncio
async def test_update_slot_snaps_effort_when_main_loses_spec():
    from agentcore.llm.model_profiles import ProfileSlot

    svc = LlmModelProfileService(MagicMock())
    svc._validate_slot = AsyncMock()  # type: ignore[method-assign]
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._byok_reachability_warnings = AsyncMock(return_value=())  # type: ignore[method-assign]
    row = _profile_db_row(
        main_model="deepseek-v4-flash", reasoning_effort="low"
    )
    updated = _profile_db_row(main_model="gpt-4o", reasoning_effort=None)
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=row)
    svc._repo.update = AsyncMock(return_value=updated)

    view = await svc.update_profile(
        "u1",
        "prof-1",
        main=ProfileSlot(origin="byok", model="gpt-4o", provider_id="prov-1"),
        fields_set={"main"},
    )
    assert svc._repo.update.await_args.kwargs["reasoning_effort"] is None
    assert view.reasoning_effort is None


@pytest.mark.asyncio
async def test_list_profiles_omits_hidden_recipe(monkeypatch):
    monkeypatch.setattr(
        "agentcore.llm.catalog.platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_billing_selectable",
        lambda: True,
    )
    monkeypatch.setattr(
        "agentcore.billing.preference.is_platform_available",
        lambda: True,
    )
    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset({"chat"}))  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]

    views = await svc.list_profiles("u1")
    assert [v.name for v in views] == ["轻量", "完整"]


@pytest.mark.asyncio
async def test_delete_starred_user_assembly_moves_star():
    kept = SimpleNamespace(id="kept", kind="user")
    svc = LlmModelProfileService(MagicMock())
    svc._repo.get = AsyncMock(return_value=SimpleNamespace(id="star", kind="user"))  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[SimpleNamespace(id="star", kind="user"), kept])  # type: ignore[method-assign]
    svc._repo.delete = AsyncMock(return_value=True)  # type: ignore[method-assign]
    svc._default_id = AsyncMock(return_value="star")  # type: ignore[method-assign]
    svc._users.set_default_model_profile = AsyncMock()  # type: ignore[method-assign]
    with patch(
        "agentcore.db.repositories.ConversationRepository"
    ) as repo_cls:
        repo_cls.return_value.reassign_model_profile_refs = AsyncMock()
        await svc.delete_profile("u1", "star")
    svc._users.set_default_model_profile.assert_awaited_with("u1", "kept")
    repo_cls.return_value.reassign_model_profile_refs.assert_awaited_with(
        "u1", "star", to_profile_id="kept"
    )
    svc._repo.delete.assert_awaited_with("star", user_id="u1")


@pytest.mark.asyncio
async def test_delete_system_preset_leaves_the_tray():
    from agentcore.assembly.recipes import CHAT, capability_preset_id

    preset_id = capability_preset_id(CHAT)
    svc = LlmModelProfileService(MagicMock())
    svc._users.hide_capability_recipe = AsyncMock()  # type: ignore[method-assign]
    svc._default_id = AsyncMock(return_value=preset_id)  # type: ignore[method-assign]
    svc._repo.delete = AsyncMock()  # type: ignore[method-assign]
    svc._repo.list_for_user = AsyncMock(return_value=[])  # type: ignore[method-assign]
    svc._hidden_recipe_keys = AsyncMock(return_value=frozenset({"chat"}))  # type: ignore[method-assign]
    svc._users.set_default_model_profile = AsyncMock()  # type: ignore[method-assign]
    svc._materialize_preset = AsyncMock(return_value="owned-next")  # type: ignore[method-assign]
    with patch(
        "agentcore.db.repositories.ConversationRepository"
    ) as repo_cls:
        repo_cls.return_value.reassign_model_profile_refs = AsyncMock()
        await svc.delete_profile("u1", preset_id)
    svc._users.hide_capability_recipe.assert_awaited_with("u1", "chat")
    svc._users.set_default_model_profile.assert_awaited_with("u1", "owned-next")
    svc._materialize_preset.assert_awaited()
    assert svc._materialize_preset.await_args.args[1] != preset_id
    assert svc._materialize_preset.await_args.kwargs["avoid_id"] == preset_id
    svc._repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_context_budget_persists_shorter_step_and_snaps():
    from agentcore.core.errors import ValidationError
    from agentcore.llm.model_profiles import ProfileSlot

    svc = LlmModelProfileService(MagicMock())
    svc._default_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._validate_slot = AsyncMock()  # type: ignore[method-assign]
    svc._byok_reachability_warnings = AsyncMock(return_value=())  # type: ignore[method-assign]
    stored = _profile_db_row(
        main_model="deepseek-v4-flash", context_budget=128_000
    )
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=stored)
    svc._repo.update = AsyncMock(return_value=stored)

    view = await svc.update_profile(
        "u1",
        "prof-1",
        main=ProfileSlot(
            origin="byok", model="deepseek-v4-flash", provider_id="prov-1"
        ),
        context_budget=128_000,
        fields_set={"main", "context_budget"},
    )
    assert svc._repo.update.await_args.kwargs["context_budget"] == 128_000
    assert view.context_budget == 128_000

    with pytest.raises(ValidationError, match="其中一档"):
        await svc.update_profile(
            "u1",
            "prof-1",
            context_budget=100_000,
            fields_set={"context_budget"},
        )

    narrower = _profile_db_row(
        main_model="deepseek-v4-flash-free", context_budget=None
    )
    svc._repo.get = AsyncMock(
        return_value=_profile_db_row(
            main_model="deepseek-v4-flash", context_budget=512_000
        )
    )
    svc._repo.update = AsyncMock(return_value=narrower)
    await svc.update_profile(
        "u1",
        "prof-1",
        main=ProfileSlot(
            origin="byok", model="deepseek-v4-flash-free", provider_id="prov-1"
        ),
        fields_set={"main"},
    )
    assert svc._repo.update.await_args.kwargs["context_budget"] is None
