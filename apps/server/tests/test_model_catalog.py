"""Model catalog + 会话级模型切换 (multi-BYOK-provider + platform catalog)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agentcore.llm import catalog
from agentcore.llm.catalog import (
    reset_discovery_cache_for_tests,
    resolve_model_catalog,
    validate_model_choice,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.resolve import (
    ModelSelection,
    resolve_conversation_model_selection,
    resolve_turn_model,
)

pytestmark = pytest.mark.anyio


# --- helpers -----------------------------------------------------------------


def _vendor_for_url(base_url: str) -> str:
    key = base_url.rstrip("/").lower()
    if "api.openai.com" in key:
        return "openai"
    if key.endswith("/zen/go/v1"):
        return "opencode_go"
    if "/zen/v1" in key:
        return "opencode_zen"
    if "openrouter.ai" in key:
        return "openrouter"
    if "deepseek.com" in key:
        return "deepseek"
    return "custom"


def _prov(
    pid: str,
    *,
    default_model: str = "deepseek-v4-flash",
    label: str = "P",
    base_url: str | None = None,
    vendor_id: str | None = None,
):
    resolved_url = base_url if base_url is not None else f"http://{pid}/v1"
    return SimpleNamespace(
        id=pid,
        user_id="u1",
        label=label,
        default_model=default_model,
        base_url=resolved_url,
        vendor_id=vendor_id or _vendor_for_url(resolved_url),
        api_key_enc=b"x",
        supports_tools=None,
        status="active",
    )


def _creds(row) -> LLMCredentials:
    return LLMCredentials(
        api_key="k",
        base_url=row.base_url,
        default_model=row.default_model,
        source="user",
        provider_id=row.id,
        vendor_id=getattr(row, "vendor_id", "custom"),
    )


def _mock_catalog(monkeypatch, *, providers, selection, discovered=None):
    """Patch the catalog's provider list / decrypt / discovery / account default."""
    monkeypatch.setattr(catalog, "list_user_providers", AsyncMock(return_value=providers))
    monkeypatch.setattr(catalog, "resolve_account_default_model", AsyncMock(return_value=selection))
    monkeypatch.setattr(catalog, "_decrypt_provider", lambda row, _uid: _creds(row))
    discovered = discovered or {}

    async def _disc(row, _creds):
        return discovered.get(row.id)

    monkeypatch.setattr(catalog, "_discover_provider_models", _disc)


# --- resolve_turn_model priority (unchanged skeleton) -------------------------


def test_resolve_turn_model_conversation_override_wins():
    creds = LLMCredentials(api_key="k", base_url="u", default_model="account-model")
    assert resolve_turn_model(creds, conversation_model="picked-model") == "picked-model"


def test_resolve_turn_model_blank_override_falls_back_to_default_model():
    creds = LLMCredentials(api_key="k", base_url="u", default_model="account-model")
    assert resolve_turn_model(creds, conversation_model=None) == "account-model"
    assert resolve_turn_model(creds, conversation_model="   ") == "account-model"


# --- provider GET /models discovery ------------------------------------------


def _mock_transport_provider(handler) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://up/v1")
    provider._client = httpx.AsyncClient(
        base_url="http://up/v1", transport=httpx.MockTransport(handler)
    )
    return provider


async def test_list_models_parses_dedupes_and_drops_blanks():
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={"data": [{"id": "m1"}, {"id": "m2"}, {"id": ""}, {"id": "m1"}, {"no": "id"}]},
        )

    ids = await _mock_transport_provider(_handler).list_models()
    assert ids == ["m1", "m2"]


def _fake_provider(*, ids: list[str] | None = None, error: Exception | None = None):
    class _Provider:
        async def list_models(self):
            if error is not None:
                raise error
            return list(ids or [])

        async def close(self):
            return None

    return _Provider()


async def test_discovery_caches_within_ttl_per_provider(monkeypatch):
    reset_discovery_cache_for_tests()
    row = _prov("prov-1")
    creds = _creds(row)
    calls = {"n": 0}

    def _build(_creds):
        calls["n"] += 1
        return _fake_provider(ids=["a", "b"])

    monkeypatch.setattr("agentcore.llm.factory.build_provider", _build)
    first = await catalog._discover_provider_models(row, creds)
    second = await catalog._discover_provider_models(row, creds)
    assert first == ["a", "b"]
    assert second == ["a", "b"]
    assert calls["n"] == 1  # cached by (provider_id, base_url)


# --- unified catalog ----------------------------------------------------------


async def test_catalog_with_key_mixes_byok_and_platform(monkeypatch):
    reset_discovery_cache_for_tests()
    row = _prov("prov-1", default_model="deepseek-v4-flash", label="DeepSeek")
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    # Platform listable requires curated CNY card (step1: glm-5.2 only among defaults).
    monkeypatch.setattr(catalog.settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(catalog.settings, "platform_models", "")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(model="deepseek-v4-flash", origin="byok", provider_id="prov-1"),
        discovered={"prov-1": ["deepseek-v4-flash", "some-endpoint-model"]},
    )
    cat = await resolve_model_catalog(None, "u1")
    assert cat.byok_configured is True
    assert cat.current.id == "deepseek-v4-flash"
    assert cat.current.origin == "byok"
    assert cat.current.provider_id == "prov-1"
    keys = {(m.id, m.origin, m.provider_id) for m in cat.models}
    assert ("deepseek-v4-flash", "byok", "prov-1") in keys
    assert ("glm-5.2", "platform", None) in keys
    assert ("some-endpoint-model", "byok", "prov-1") in keys
    # BYOK rows carry the provider label for UI grouping.
    byok = [m for m in cat.models if m.origin == "byok"]
    assert all(m.available and m.provider_label == "DeepSeek" for m in byok)


async def test_catalog_discovery_failed_keeps_vendor_presets(monkeypatch):
    """Matched preset: discovery None still lists the seed; probe default stays out."""
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-ds",
        default_model="deepseek-v4-flash",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(
            model="deepseek-v4-flash", origin="byok", provider_id="prov-ds"
        ),
        discovered={"prov-ds": None},
    )
    cat = await resolve_model_catalog(None, "u1")
    byok_ids = {m.id for m in cat.models if m.origin == "byok" and m.provider_id == "prov-ds"}
    assert byok_ids == {"deepseek-flash"}


async def test_catalog_discovery_unions_with_vendor_presets(monkeypatch):
    """Discovery ∪ seed; official DeepSeek retired aliases are omitted."""
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-ds",
        default_model="deepseek-v4-flash",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1/",  # alias + trailing slash
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(
            model="deepseek-v4-flash", origin="byok", provider_id="prov-ds"
        ),
        discovered={
            "prov-ds": [
                "deepseek-v4-flash",
                "deepseek-v4-pro",
                "endpoint-only-model",
            ]
        },
    )
    cat = await resolve_model_catalog(None, "u1")
    byok_ids = {m.id for m in cat.models if m.origin == "byok" and m.provider_id == "prov-ds"}
    assert "deepseek-flash" in byok_ids
    assert "endpoint-only-model" in byok_ids
    assert "deepseek-v4-flash" not in byok_ids
    assert "deepseek-v4-pro" not in byok_ids
    assert "deepseek-v4.1-flash-expires-on-0910" not in byok_ids


async def test_catalog_custom_base_url_has_no_presets(monkeypatch):
    """Unknown base_url: discovery only (no vendor preset, no stored default)."""
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-custom",
        default_model="my-default",
        label="Custom",
        base_url="https://my-proxy.example/v1",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(model="my-default", origin="byok", provider_id="prov-custom"),
        discovered={"prov-custom": None},
    )
    cat = await resolve_model_catalog(None, "u1")
    byok_ids = [m.id for m in cat.models if m.origin == "byok"]
    assert byok_ids == []


async def test_catalog_synthesizes_enabled_refs_missing_from_discovery(monkeypatch):
    """Hand-filled BYOK ids the upstream list omitted become custom catalog rows."""
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-custom",
        default_model="claude-sonnet",
        label="Relay",
        base_url="https://relay.example/v1",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(
            model="claude-sonnet", origin="byok", provider_id="prov-custom"
        ),
        discovered={"prov-custom": ["claude-sonnet"]},
    )
    cat = await resolve_model_catalog(
        None,
        "u1",
        enabled_refs=[
            "@byok/prov-custom/deepseek-v4.1-flash",
            "@byok/prov-custom/claude-sonnet",
            "@byok/gone/ghost",
            "@platform/glm-5.2",
        ],
    )
    byok = [m for m in cat.models if m.origin == "byok"]
    flash = next(m for m in byok if m.id == "deepseek-v4.1-flash")
    sonnet = next(m for m in byok if m.id == "claude-sonnet")
    assert flash.custom is True
    assert flash.provider_id == "prov-custom"
    assert sonnet.custom is False
    assert all(m.id != "ghost" for m in byok)


async def test_catalog_same_model_id_under_two_providers(monkeypatch):
    """同一模型 id 允许同时出现在多个服务商下 — (id, origin, provider_id) is the key."""
    reset_discovery_cache_for_tests()
    a = _prov("provA", label="Ark")
    b = _prov("provB", label="Kimi")
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[a, b],
        selection=ModelSelection(model="deepseek-v4-flash", origin="byok", provider_id="provA"),
        discovered={"provA": ["shared-model"], "provB": ["shared-model"]},
    )
    cat = await resolve_model_catalog(None, "u1")
    keys = {(m.id, m.origin, m.provider_id) for m in cat.models}
    assert ("shared-model", "byok", "provA") in keys
    assert ("shared-model", "byok", "provB") in keys
    shared = [m for m in cat.models if m.id == "shared-model"]
    assert {m.provider_label for m in shared} == {"Ark", "Kimi"}


async def test_catalog_keyless_platform_on_hides_guide_rows(monkeypatch):
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    monkeypatch.setattr(catalog.settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(catalog.settings, "platform_models", "")
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(model="glm-5.2", origin="platform", provider_id=None),
    )
    cat = await resolve_model_catalog(None, "u1")
    assert cat.byok_configured is False
    assert cat.current.origin == "platform"
    keys = {(m.id, m.origin) for m in cat.models}
    assert ("glm-5.2", "platform") in keys
    assert all(m.origin == "platform" and m.available for m in cat.models)
    glm = next(m for m in cat.models if m.id == "glm-5.2")
    assert glm.display_name == "GLM-5.2"
    assert glm.vendor == "智谱 AI"
    assert glm.badge is None


async def test_catalog_keyless_platform_off_returns_empty(monkeypatch):
    """Keyless + no platform subsidy: empty catalog (UI shows an empty state, no guide rows)."""
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(model="deepseek-v4-flash", origin="byok", provider_id=None),
    )
    cat = await resolve_model_catalog(None, "u1")
    assert cat.byok_configured is False
    assert cat.models == []


async def test_catalog_dormant_hides_platform_despite_key(monkeypatch):
    """byok + PLATFORM_API_KEY still set → no platform catalog rows."""
    reset_discovery_cache_for_tests()
    row = _prov("provA", label="DeepSeek")
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    monkeypatch.setattr(catalog.settings, "platform_model", "deepseek-v4-flash")
    monkeypatch.setattr(catalog.settings, "platform_models", "glm-5.2,relay-b")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(model="deepseek-v4-flash", origin="byok", provider_id="provA"),
        discovered={"provA": ["deepseek-v4-flash"]},
    )
    cat = await resolve_model_catalog(None, "u1")
    assert all(m.origin == "byok" for m in cat.models)
    assert not any(m.origin == "platform" for m in cat.models)


async def test_catalog_platform_allowlist_drives_rows(monkeypatch):
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    monkeypatch.setattr(catalog.settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(
        catalog.settings,
        "platform_models",
        "glm-5.2, doubao/doubao-seed-2-1-turbo-260628 , glm-5.2",
    )
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(model="glm-5.2", origin="platform", provider_id=None),
    )
    cat = await resolve_model_catalog(None, "u1")
    platform_ids = [m.id for m in cat.models if m.origin == "platform"]
    assert platform_ids == ["glm-5.2", "doubao/doubao-seed-2-1-turbo-260628"]
    assert all(m.available and m.price is not None for m in cat.models if m.origin == "platform")
    assert all(m.price.get("currency") == "CNY" for m in cat.models if m.origin == "platform")
    assert all(m.price.get("multiplier") == "1" for m in cat.models if m.origin == "platform")


async def test_catalog_first_wave_platform_sell_cards(monkeypatch):
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    monkeypatch.setattr(catalog.settings, "platform_model", "deepseek-v4.1-flash")
    monkeypatch.setattr(
        catalog.settings,
        "platform_models",
        "deepseek-v4.1-flash,glm-5.3,glm-5.3-flash,kimi-k3",
    )
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(
            model="deepseek-v4.1-flash", origin="platform", provider_id=None
        ),
    )
    cat = await resolve_model_catalog(None, "u1")
    platform = {m.id: m for m in cat.models if m.origin == "platform"}
    assert list(platform) == [
        "deepseek-v4.1-flash",
        "glm-5.3",
        "glm-5.3-flash",
        "kimi-k3",
    ]
    assert platform["deepseek-v4.1-flash"].price["multiplier"] == "0.25"
    assert platform["deepseek-v4.1-flash"].price["quota_multiplier"] == "1"
    glm = platform["glm-5.3"]
    assert glm.display_name == "GLM-5.3"
    assert glm.price["multiplier"] == "2"
    assert glm.price["quota_multiplier"] == "58"
    assert glm.price["cache_miss"] == "16"
    assert glm.price["output"] == "56"
    flash = platform["glm-5.3-flash"]
    assert flash.display_name == "GLM-5.3 Flash"
    assert flash.price["multiplier"] == "2"
    assert flash.price["quota_multiplier"] == "6"
    assert flash.price["cache_miss"] == "1.6"
    assert flash.price["output"] == "5.6"
    assert "vision" in flash.capabilities
    assert "vision" not in glm.capabilities
    kimi = platform["kimi-k3"]
    assert kimi.display_name == "Kimi K3"
    assert kimi.price["multiplier"] == "2"
    assert kimi.price["quota_multiplier"] == "192"
    assert kimi.price["cache_miss"] == "40"
    assert kimi.price["output"] == "200"
    assert "vision" in kimi.capabilities


async def test_catalog_platform_allowlist_marks_off_protocol_unselectable(monkeypatch):
    """Allowlist off-protocol ids list as unavailable; priced platform rows stay selectable."""
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    monkeypatch.setattr(catalog.settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(
        catalog.settings,
        "platform_models",
        "glm-5.2,grok-4.5,union-alpha,grok-4.5-fast",
    )
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(model="glm-5.2", origin="platform", provider_id=None),
    )
    cat = await resolve_model_catalog(None, "u1")
    platform = {m.id: m for m in cat.models if m.origin == "platform"}

    glm = platform["glm-5.2"]
    assert glm.available is True
    assert glm.unavailable_reason is None

    grok = platform["grok-4.5"]
    assert grok.available is False
    assert grok.unavailable_reason is not None
    assert grok.unavailable_reason.code == "upstream_protocol_unsupported"
    assert grok.unavailable_reason.required_protocol == "openai_responses"

    claude = platform["union-alpha"]
    assert claude.available is False
    assert claude.unavailable_reason is not None
    assert claude.unavailable_reason.code == "upstream_protocol_unsupported"
    assert claude.unavailable_reason.required_protocol == "anthropic_messages"

    # Family grok-* is off-protocol even when unpriced — listed grey, not dropped.
    fast = platform["grok-4.5-fast"]
    assert fast.available is False
    assert fast.unavailable_reason is not None
    assert fast.unavailable_reason.required_protocol == "openai_responses"
    assert catalog.is_platform_listable("glm-5.2") is True
    assert catalog.is_platform_listable("grok-4.5") is False

    assert await validate_model_choice(None, "u1", "grok-4.5", "platform") is False
    assert await validate_model_choice(None, "u1", "union-alpha", "platform") is False
    assert await validate_model_choice(None, "u1", "glm-5.2", "platform") is True


async def test_catalog_priced_claude_gpt_are_selectable(monkeypatch):
    """Priced Claude / GPT on the allowlist are selectable; unpriced grok stays grey."""
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    monkeypatch.setattr(catalog.settings, "platform_model", "deepseek-v4.1-flash")
    monkeypatch.setattr(
        catalog.settings,
        "platform_models",
        (
            "deepseek-v4.1-flash,claude-opus-4-6,claude-opus-5-5,"
            "claude-fable-5.1,gpt-6-astra,gpt-6.1-sol,grok-4.5"
        ),
    )
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(
            model="deepseek-v4.1-flash", origin="platform", provider_id=None
        ),
    )
    cat = await resolve_model_catalog(None, "u1")
    platform = {m.id: m for m in cat.models if m.origin == "platform"}

    flash = platform["deepseek-v4.1-flash"]
    assert flash.available is True
    assert flash.price["quota_multiplier"] == "1"

    opus = platform["claude-opus-5-5"]
    assert opus.available is True
    assert opus.unavailable_reason is None
    assert opus.display_name == "Claude Opus 5.5"
    assert opus.price["multiplier"] == "0.3"
    assert opus.price["quota_multiplier"] == "41"
    assert opus.price["cache_miss"] == "8.64"
    assert opus.price["output"] == "43.2"
    assert "vision" in opus.capabilities

    opus46 = platform["claude-opus-4-6"]
    assert opus46.available is True
    assert opus46.price["quota_multiplier"] == "52"
    assert opus46.price["cache_miss"] == "10.8"

    fable = platform["claude-fable-5.1"]
    assert fable.available is True
    assert fable.price["quota_multiplier"] == "104"
    assert fable.price["cache_miss"] == "21.6"
    assert fable.price["cache_hit"] == "0.54"

    astra = platform["gpt-6-astra"]
    assert astra.available is True
    assert astra.price["multiplier"] == "0.18"
    assert astra.price["quota_multiplier"] == "62"
    assert astra.price["cache_miss"] == "12.96"

    sol = platform["gpt-6.1-sol"]
    assert sol.available is True
    assert sol.price["quota_multiplier"] == "12"
    assert sol.price["cache_miss"] == "2.592"

    grok = platform["grok-4.5"]
    assert grok.available is False
    assert grok.unavailable_reason is not None
    assert grok.price is None or grok.price.get("quota_multiplier") is None

    assert catalog.is_platform_listable("claude-opus-5-5") is True
    assert catalog.is_platform_listable("gpt-6-astra") is True
    assert catalog.is_platform_listable("grok-4.5") is False
    assert await validate_model_choice(None, "u1", "claude-opus-5-5", "platform") is True
    assert await validate_model_choice(None, "u1", "gpt-6.1-sol", "platform") is True
    assert await validate_model_choice(None, "u1", "grok-4.5", "platform") is False


async def test_catalog_excludes_models_without_curated_pricing(monkeypatch):
    """缺 curated 价卡 → 不上架（不进目录），不只 warning 仍 available。"""
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    monkeypatch.setattr(catalog.settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(
        catalog.settings,
        "platform_models",
        "glm-5.2,totally-unknown-relay-model",
    )
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(model="glm-5.2", origin="platform", provider_id=None),
    )
    cat = await resolve_model_catalog(None, "u1")
    platform_ids = [m.id for m in cat.models if m.origin == "platform"]
    assert platform_ids == ["glm-5.2"]
    assert "totally-unknown-relay-model" not in platform_ids


# --- OpenCode off-protocol catalog (/responses grey; /messages selectable) ---


def _byok_off_protocol_row(mid: str, models: list):
    hits = [m for m in models if m.id == mid and m.origin == "byok"]
    assert len(hits) == 1, mid
    return hits[0]


async def test_opencode_go_catalog_lists_responses_ids_selectable(monkeypatch):
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-go",
        default_model="deepseek-v4.1-flash",
        label="OpenCode Go",
        base_url="https://opencode.ai/zen/go/v1",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(
            model="deepseek-v4.1-flash", origin="byok", provider_id="prov-go"
        ),
        discovered={
            "prov-go": [
                "deepseek-v4.1-flash",
                "deepseek-v4-flash",
                "grok-4.5",
                "gpt-5.6-luna",
                "minimax-m2.7",
                "qwen3.7-max",
                "union-alpha",
                "claude-haiku-4-5",
                "claude-haiku-9",
                "grok-9",
            ]
        },
    )
    cat = await resolve_model_catalog(None, "u1")
    grok = _byok_off_protocol_row("grok-4.5", cat.models)
    assert grok.available is True
    assert grok.unavailable_reason is None

    luna = _byok_off_protocol_row("gpt-5.6-luna", cat.models)
    assert luna.available is True
    assert luna.unavailable_reason is None

    minimax = _byok_off_protocol_row("minimax-m2.7", cat.models)
    assert minimax.available is True
    assert minimax.unavailable_reason is None

    qwen = _byok_off_protocol_row("qwen3.7-max", cat.models)
    assert qwen.available is True
    assert qwen.unavailable_reason is None

    union = _byok_off_protocol_row("union-alpha", cat.models)
    assert union.available is True
    assert union.unavailable_reason is None

    haiku = _byok_off_protocol_row("claude-haiku-4-5", cat.models)
    assert haiku.available is True
    assert haiku.unavailable_reason is None

    new_haiku = _byok_off_protocol_row("claude-haiku-9", cat.models)
    assert new_haiku.available is True
    assert new_haiku.unavailable_reason is None

    new_grok = _byok_off_protocol_row("grok-9", cat.models)
    assert new_grok.available is True
    assert new_grok.unavailable_reason is None

    flash = _byok_off_protocol_row("deepseek-v4.1-flash", cat.models)
    assert flash.available is True
    assert flash.unavailable_reason is None
    glm = _byok_off_protocol_row("glm-5.3", cat.models)
    assert glm.available is True
    assert glm.unavailable_reason is None

    assert (
        await validate_model_choice(None, "u1", "grok-4.5", "byok", "prov-go") is True
    )
    assert (
        await validate_model_choice(None, "u1", "union-alpha", "byok", "prov-go") is True
    )
    assert (
        await validate_model_choice(None, "u1", "minimax-m2.7", "byok", "prov-go")
        is True
    )
    assert (
        await validate_model_choice(None, "u1", "deepseek-v4.1-flash", "byok", "prov-go")
        is True
    )


async def test_opencode_go_hides_official_deepseek_flash_from_picker(monkeypatch):
    """Go discovery lists official ``deepseek-flash`` and retired V4 ids; new picker omits them."""
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-go",
        default_model="deepseek-v4.1-flash",
        label="OpenCode Go",
        base_url="https://opencode.ai/zen/go/v1",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(
            model="deepseek-v4.1-flash", origin="byok", provider_id="prov-go"
        ),
        discovered={
            "prov-go": [
                "deepseek-flash",
                "deepseek-v4.1-flash",
                "deepseek-v4-flash",
                "deepseek-v4-pro",
            ]
        },
    )
    cat = await resolve_model_catalog(None, "u1")
    byok_ids = {m.id for m in cat.models if m.origin == "byok" and m.provider_id == "prov-go"}
    assert "deepseek-flash" not in byok_ids
    assert "deepseek-v4-flash" not in byok_ids
    assert "deepseek-v4-pro" not in byok_ids
    assert "deepseek-v4.1-flash" in byok_ids
    assert (
        await validate_model_choice(None, "u1", "deepseek-flash", "byok", "prov-go")
        is False
    )


async def test_opencode_zen_catalog_lists_grok_selectable(monkeypatch):
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-zen",
        default_model="deepseek-v4-flash",
        label="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1/",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(
            model="deepseek-v4-flash", origin="byok", provider_id="prov-zen"
        ),
        discovered={"prov-zen": ["kimi-k2.6", "grok-4.5", "claude-haiku-4-5"]},
    )
    cat = await resolve_model_catalog(None, "u1")
    grok = _byok_off_protocol_row("grok-4.5", cat.models)
    assert grok.available is True
    assert grok.unavailable_reason is None
    haiku = _byok_off_protocol_row("claude-haiku-4-5", cat.models)
    assert haiku.available is True
    assert haiku.unavailable_reason is None
    kimi = _byok_off_protocol_row("kimi-k2.6", cat.models)
    assert kimi.available is True
    assert kimi.unavailable_reason is None
    assert (
        await validate_model_choice(None, "u1", "grok-4.5", "byok", "prov-zen") is True
    )
    assert (
        await validate_model_choice(None, "u1", "claude-haiku-4-5", "byok", "prov-zen")
        is True
    )


async def test_official_openai_catalog_drops_non_chat_ids(monkeypatch):
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-oai",
        default_model="gpt-4o",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(model="gpt-4o", origin="byok", provider_id="prov-oai"),
        discovered={
            "prov-oai": [
                "gpt-4o",
                "gpt-5",
                "whisper-1",
                "tts-1",
                "dall-e-3",
                "text-embedding-3-small",
                "gpt-4o-mini-tts",
            ]
        },
    )
    cat = await resolve_model_catalog(None, "u1")
    ids = {m.id for m in cat.models if m.origin == "byok" and m.provider_id == "prov-oai"}
    assert "gpt-4o" in ids
    assert "gpt-5" in ids
    assert "gpt-4.1" in ids
    assert "whisper-1" not in ids
    assert "tts-1" not in ids
    assert "dall-e-3" not in ids
    assert "text-embedding-3-small" not in ids
    assert "gpt-4o-mini-tts" not in ids
    gpt5 = _byok_off_protocol_row("gpt-5", cat.models)
    assert gpt5.available is True
    assert gpt5.unavailable_reason is None


async def test_openai_vendor_relay_url_keeps_openai_seed(monkeypatch):
    """Saved OpenAI identity uses the OpenAI seed even when base_url is a relay."""
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-relay-oai",
        default_model="gpt-4o",
        label="OpenAI",
        base_url="https://api.apistokens.com",
        vendor_id="openai",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(
            model="gpt-4o", origin="byok", provider_id="prov-relay-oai"
        ),
        discovered={
            "prov-relay-oai": ["gpt-4o", "whisper-1", "gpt-6.1-sol"],
        },
    )
    cat = await resolve_model_catalog(None, "u1")
    ids = {
        m.id
        for m in cat.models
        if m.origin == "byok" and m.provider_id == "prov-relay-oai"
    }
    assert "gpt-4o" in ids
    assert "gpt-6.1-sol" in ids
    assert "whisper-1" not in ids
    gpt = _byok_off_protocol_row("gpt-4o", cat.models)
    assert gpt.available is True
    assert gpt.unavailable_reason is None


async def test_non_opencode_relay_grok_stays_selectable(monkeypatch):
    """Non-OpenCode relay grok-4.5 is chat/completions — off-protocol gate must not hit it."""
    reset_discovery_cache_for_tests()
    row = _prov(
        "prov-relay",
        default_model="glm-5.2",
        label="Custom relay",
        base_url="https://relay.example/openai/v1",
    )
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[row],
        selection=ModelSelection(model="glm-5.2", origin="byok", provider_id="prov-relay"),
        discovered={"prov-relay": ["grok-4.5"]},
    )
    cat = await resolve_model_catalog(None, "u1")
    grok = _byok_off_protocol_row("grok-4.5", cat.models)
    assert grok.available is True
    assert grok.unavailable_reason is None
    assert (
        await validate_model_choice(None, "u1", "grok-4.5", "byok", "prov-relay") is True
    )


def test_catalog_off_protocol_filter_uses_preset_singleton():
    """Seed exclusion and catalog filter must read the same off_protocol_kind."""
    from agentcore.llm.byok_provider_presets import off_protocol_kind as preset_fn

    assert catalog.off_protocol_kind is preset_fn
    # BYOK speaks /messages and /responses; family ids are selectable on OpenCode.
    assert catalog._off_protocol_reason("grok-4.5-fast", "https://opencode.ai/zen/go/v1") is None
    assert catalog._off_protocol_reason("grok-4.5", "https://opencode.ai/zen/go/v1") is None
    assert catalog._off_protocol_reason("grok-4.5", "https://relay.example/openai/v1") is None
    assert catalog._off_protocol_reason("union-alpha", "https://opencode.ai/zen/v1") is None
    assert catalog._off_protocol_reason("claude-haiku-4-5", "https://opencode.ai/zen/go/v1") is None
    assert catalog._off_protocol_reason("claude-haiku-9", "https://opencode.ai/zen/go/v1") is None
    assert catalog._off_protocol_reason("union-alpha", "https://openrouter.ai/api/v1") is None
    assert catalog._off_protocol_reason("anthropic/claude-sonnet-4", "https://opencode.ai/zen/v1") is None
    # Platform path: same function object, no endpoint gate (see _platform_entry).
    assert catalog._off_protocol_unavailable("grok-4.5") is not None
    assert catalog._off_protocol_unavailable("grok-4.5-fast") is not None
    assert catalog._off_protocol_unavailable("union-alpha") is not None
    assert (
        catalog._off_protocol_unavailable("union-alpha").required_protocol
        == "anthropic_messages"
    )
    assert (
        catalog._off_protocol_unavailable("grok-4.5").required_protocol
        == preset_fn("grok-4.5")
    )
    assert catalog._off_protocol_unavailable("claude-opus-4-6") is None
    assert catalog._off_protocol_unavailable("claude-opus-5-5") is None
    assert catalog._off_protocol_unavailable("claude-fable-5.1") is None
    assert catalog._off_protocol_unavailable("gpt-6-astra") is None
    assert catalog._off_protocol_unavailable("gpt-6.1-sol") is None


# --- validate_model_choice ----------------------------------------------------


async def test_validate_platform_allowlist_membership(monkeypatch):
    monkeypatch.setattr(catalog.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(catalog.settings, "billing_mode", "platform")
    # Only curated-CNY ids list；无卡的 allowlist 成员不上架 / 不可选。
    monkeypatch.setattr(
        catalog.settings,
        "platform_models",
        "glm-5.2,doubao/doubao-seed-2-1-turbo-260628,gpt-4o",
    )
    _mock_catalog(
        monkeypatch,
        providers=[],
        selection=ModelSelection(model="glm-5.2", origin="platform", provider_id=None),
    )
    assert await validate_model_choice(None, "u1", "glm-5.2", "platform") is True
    assert (
        await validate_model_choice(
            None, "u1", "doubao/doubao-seed-2-1-turbo-260628", "platform"
        )
        is True
    )
    assert await validate_model_choice(None, "u1", "gpt-4o", "platform") is False
    assert await validate_model_choice(None, "u1", "deepseek-v4-flash", "platform") is False


async def test_validate_model_choice_is_provider_scoped(monkeypatch):
    """The same model id under a different provider is a different (invalid) choice."""
    reset_discovery_cache_for_tests()
    a = _prov("provA")
    b = _prov("provB")
    monkeypatch.setattr(catalog.settings, "platform_api_key", "")
    monkeypatch.setattr(catalog.settings, "billing_mode", "byok")
    _mock_catalog(
        monkeypatch,
        providers=[a, b],
        selection=ModelSelection(model="deepseek-v4-flash", origin="byok", provider_id="provA"),
        discovered={"provA": ["shared-model"], "provB": ["other-model"]},
    )
    assert await validate_model_choice(None, "u1", "shared-model", "byok", "provA") is True
    # shared-model is not under provB → rejected when that provider is specified.
    assert await validate_model_choice(None, "u1", "shared-model", "byok", "provB") is False
    # byok choice with no provider specified never matches a provider-tagged row.
    assert await validate_model_choice(None, "u1", "shared-model", "byok", None) is False


def test_has_curated_pricing_flags_uncurated():
    from agentcore.llm.pricing import has_curated_pricing

    assert not has_curated_pricing("gpt-4o")
    assert has_curated_pricing("deepseek-v4-flash")
    assert has_curated_pricing("deepseek-v4.1-flash")
    assert not has_curated_pricing("deepseek-v4-pro")
    assert has_curated_pricing("glm-5.2")
    assert has_curated_pricing("glm-5.3")
    assert has_curated_pricing("glm-5.3-flash")
    assert has_curated_pricing("kimi-k3")
    assert has_curated_pricing("claude-opus-4-6")
    assert has_curated_pricing("claude-opus-5-5")
    assert has_curated_pricing("claude-fable-5.1")
    assert has_curated_pricing("gpt-6-astra")
    assert has_curated_pricing("gpt-6.1-sol")
    assert not has_curated_pricing("grok-4.5")
    assert has_curated_pricing("doubao/doubao-seed-2-1-turbo-260628")
    assert not has_curated_pricing("totally-unknown-relay-model")


# --- resolve_conversation_model_selection ------------------------------------


def _mock_resolve_repos(monkeypatch, *, user=None, provider_by_id=None, first=None, count=0):
    provider_by_id = provider_by_id or {}
    monkeypatch.setattr(
        "agentcore.db.repositories.UserRepository",
        lambda _s: SimpleNamespace(get_by_id=AsyncMock(return_value=user)),
    )

    async def _get(pid, *, user_id):
        return provider_by_id.get(pid)

    monkeypatch.setattr(
        "agentcore.db.repositories.UserLlmProviderRepository",
        lambda _s: SimpleNamespace(
            get=_get,
            first_for_user=AsyncMock(return_value=first),
            count_for_user=AsyncMock(return_value=count),
        ),
    )


async def test_platform_selection_passes_through_to_turn(monkeypatch):
    from agentcore.conversation.common import resolve_turn_profiles
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    expanded = ExpandedProfile(
        profile_id="sys",
        name="GLM-5.2",
        kind="system",
        main=ModelSelection(model="deepseek-v4-pro", origin="platform", provider_id=None),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=expanded),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand",
        AsyncMock(return_value=expanded),
    )
    conv = SimpleNamespace(assembly_id="sys")

    sel = await resolve_conversation_model_selection(None, conv, "u1")
    assert sel.model == "deepseek-v4-pro"
    assert sel.origin == "platform"
    assert sel.provider_id is None

    profiles = await resolve_turn_profiles(None, conv, "u1", credentials=None)
    assert profiles.model == "deepseek-v4-pro"


async def test_resolve_turn_profiles_no_worker_slot_follows_main(monkeypatch):
    """worker 空槽 → model_overrides 无 agent；CEO/worker 同 model。"""
    from agentcore.conversation.common import resolve_turn_profiles
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    expanded = ExpandedProfile(
        profile_id="p",
        name="当前配置",
        kind="user",
        main=ModelSelection(model="main-model", origin="byok", provider_id="p1"),
        worker=None,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=expanded),
    )
    conv = SimpleNamespace(assembly_id="p")
    creds = LLMCredentials(api_key="sk", base_url="https://x", default_model="main-model")
    profiles = await resolve_turn_profiles(None, conv, "u1", credentials=creds)
    assert profiles.model == "main-model"
    assert "agent" not in profiles.model_overrides
    assert profiles.model_for("agent") == "main-model"
    assert profiles.agent_provider_id is None


async def test_resolve_turn_profiles_worker_slot_overrides_agent(monkeypatch):
    """配了 worker 槽 → model_for(agent)=worker；model_for(chat) 仍为主模型。"""
    from agentcore.conversation.common import resolve_turn_profiles
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    expanded = ExpandedProfile(
        profile_id="p",
        name="当前配置",
        kind="user",
        main=ModelSelection(model="ceo-pro", origin="byok", provider_id="p1"),
        worker=ModelSelection(model="worker-flash", origin="byok", provider_id="p2"),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=expanded),
    )
    conv = SimpleNamespace(assembly_id="p")
    creds = LLMCredentials(api_key="sk", base_url="https://x", default_model="ceo-pro")
    profiles = await resolve_turn_profiles(None, conv, "u1", credentials=creds)
    assert profiles.model == "ceo-pro"
    assert profiles.model_for("chat") == "ceo-pro"
    assert profiles.model_for("agent") == "worker-flash"
    assert profiles.agent_provider_id == "p2"
    assert profiles.route_model_for("agent") == "p2/worker-flash"


async def test_resolve_turn_profiles_worker_platform_with_byok_main(monkeypatch):
    """主 byok、worker platform → agent_provider_id=platform 哨兵。"""
    from agentcore.conversation.common import resolve_turn_profiles
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL
    from agentcore.llm.resolve import ModelSelection

    expanded = ExpandedProfile(
        profile_id="p",
        name="当前配置",
        kind="user",
        main=ModelSelection(model="ceo-pro", origin="byok", provider_id="p1"),
        worker=ModelSelection(
            model="deepseek-v4-flash", origin="platform", provider_id=None
        ),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=expanded),
    )
    conv = SimpleNamespace(assembly_id="p")
    creds = LLMCredentials(
        api_key="sk", base_url="https://x", default_model="ceo-pro", provider_id="p1"
    )
    profiles = await resolve_turn_profiles(None, conv, "u1", credentials=creds)
    assert profiles.model_for("agent") == "deepseek-v4-flash"
    assert profiles.agent_provider_id == PLATFORM_PROVIDER_SENTINEL
    assert profiles.route_model_for("agent", turn_provider_id="p1") == (
        f"{PLATFORM_PROVIDER_SENTINEL}/deepseek-v4-flash"
    )


async def test_resolve_account_default_from_profile(monkeypatch):
    """账号默认组合展开为主槽。"""
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection, resolve_account_default_model

    expanded = ExpandedProfile(
        profile_id="p",
        name="当前配置",
        kind="user",
        main=ModelSelection(model="deepseek-v4-pro", origin="platform", provider_id=None),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand",
        AsyncMock(return_value=expanded),
    )
    sel = await resolve_account_default_model(None, "u1")
    assert sel.origin == "platform"
    assert sel.provider_id is None
    assert sel.model == "deepseek-v4-pro"


async def test_resolve_turn_profiles_dangling_worker_follows_main(monkeypatch):
    """worker 槽展开为 None（删服务商后）→ 跟随主模型。"""
    from agentcore.conversation.common import resolve_turn_profiles
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    expanded = ExpandedProfile(
        profile_id="p",
        name="当前配置",
        kind="user",
        main=ModelSelection(model="ceo-pro", origin="byok", provider_id="p1"),
        worker=None,
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=expanded),
    )
    conv = SimpleNamespace(assembly_id="p")
    creds = LLMCredentials(api_key="sk", base_url="https://x", default_model="ceo-pro")
    profiles = await resolve_turn_profiles(None, conv, "u1", credentials=creds)
    assert profiles.model_for("agent") == "ceo-pro"
    assert "agent" not in profiles.model_overrides


async def test_conversation_profile_pin_expands_main(monkeypatch):
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    expanded = ExpandedProfile(
        profile_id="imp",
        name="会话覆盖",
        kind="implicit",
        main=ModelSelection(model="picked", origin="byok", provider_id="p1"),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=expanded),
    )
    conv = SimpleNamespace(assembly_id="imp")
    sel = await resolve_conversation_model_selection(None, conv, "u1")
    assert sel.model == "picked"
    assert sel.origin == "byok"
    assert sel.provider_id == "p1"


async def test_dangling_profile_falls_back_via_expand(monkeypatch):
    """展开侧对悬空 pin 回落到账号/系统默认。"""
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    fallback = ExpandedProfile(
        profile_id="bal",
        name="GLM-5.2",
        kind="system",
        main=ModelSelection(model="acct-model", origin="byok", provider_id="p2"),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=fallback),
    )
    conv = SimpleNamespace(assembly_id="dead")
    sel = await resolve_conversation_model_selection(None, conv, "u1")
    assert sel.model == "acct-model"
    assert sel.provider_id == "p2"


# --- conversation PATCH (crud) -----------------------------------------------


async def test_patch_conversation_persists_assembly_id(monkeypatch):
    from datetime import datetime

    from agentcore.api.routes.conversations import crud
    from agentcore.api.schemas import UpdateConversationRequest

    written: dict = {}
    conv = SimpleNamespace(
        id="c1",
        title="t",
        updated_at=datetime.now(),
        created_at=datetime.now(),
        message_count=0,
        folder_id=None,
        local_container_root_id=None,
        pinned=False,
        archived=False,
        permission_preset="workspace",
        deep_research_auto=False,
        assembly_id=None,
    )

    class _Repo:
        _session = None

        async def get_by_id(self, _cid, *, user_id):
            return conv

        async def preference_flags_for(self, _user_id, _ids):
            return {}

        async def set_model_profile(self, _cid, assembly_id, *, user_id):
            written["assembly_id"] = assembly_id
            conv.assembly_id = assembly_id
            return conv

    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.ensure_profile_usable",
        AsyncMock(return_value="prof-1"),
    )
    monkeypatch.setattr(crud, "_require_conversation_write", AsyncMock())
    body = UpdateConversationRequest(assembly_id="prof-1")
    result = await crud.update_conversation(
        "c1", body, SimpleNamespace(user_id="u1"), repo=_Repo()
    )
    assert written == {"assembly_id": "prof-1"}
    assert result.assembly_id == "prof-1"


async def test_patch_conversation_persists_model_override(monkeypatch):
    from datetime import datetime

    from agentcore.api.routes.conversations import crud
    from agentcore.api.schemas import ConversationModelOverride, UpdateConversationRequest
    from agentcore.api.schemas.llm_model_profiles import ModelProfileSlot

    written: dict = {}
    conv = SimpleNamespace(
        id="c1",
        title="t",
        updated_at=datetime.now(),
        created_at=datetime.now(),
        message_count=0,
        folder_id=None,
        local_container_root_id=None,
        pinned=False,
        archived=False,
        permission_preset="workspace",
        deep_research_auto=False,
        assembly_id="asm-1",
        model_override=None,
    )
    payload = {
        "main": {"origin": "byok", "provider_id": "p1", "model": "chat-main"},
    }

    class _Repo:
        _session = None

        async def get_by_id(self, _cid, *, user_id):
            return conv

        async def preference_flags_for(self, _user_id, _ids):
            return {}

        async def set_model_override(self, _cid, model_override, *, user_id):
            written["model_override"] = model_override
            conv.model_override = model_override
            return conv

    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.normalize_conversation_override",
        AsyncMock(return_value=payload),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.UserRepository.enable_picker_refs",
        AsyncMock(return_value=()),
    )
    monkeypatch.setattr(crud, "_require_conversation_write", AsyncMock())
    body = UpdateConversationRequest(
        model_override=ConversationModelOverride(
            main=ModelProfileSlot(origin="byok", provider_id="p1", model="chat-main")
        )
    )
    result = await crud.update_conversation(
        "c1", body, SimpleNamespace(user_id="u1"), repo=_Repo()
    )
    assert written == {"model_override": payload}
    assert result.model_override is not None
    assert result.model_override.main.model == "chat-main"


async def test_patch_conversation_null_repins_account_default(monkeypatch):
    from datetime import datetime

    from agentcore.api.routes.conversations import crud
    from agentcore.api.schemas import UpdateConversationRequest

    conv = SimpleNamespace(
        id="c1",
        title="t",
        updated_at=datetime.now(),
        created_at=datetime.now(),
        message_count=0,
        folder_id=None,
        local_container_root_id=None,
        pinned=False,
        archived=False,
        permission_preset="workspace",
        deep_research_auto=False,
        assembly_id="prof-1",
    )
    written: dict = {}

    class _Repo:
        _session = None

        async def get_by_id(self, _cid, *, user_id):
            return conv

        async def preference_flags_for(self, _user_id, _ids):
            return {}

        async def set_model_profile(self, _cid, assembly_id, *, user_id):
            written["assembly_id"] = assembly_id
            conv.assembly_id = assembly_id
            return conv

    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.snapshot_default_profile_id",
        AsyncMock(return_value="sys-default"),
    )
    monkeypatch.setattr(crud, "_require_conversation_write", AsyncMock())
    body = UpdateConversationRequest(assembly_id=None)
    # Explicit null is in model_fields_set.
    assert "assembly_id" in body.model_fields_set
    result = await crud.update_conversation(
        "c1", body, SimpleNamespace(user_id="u1"), repo=_Repo()
    )
    assert written == {"assembly_id": "sys-default"}
    assert result.assembly_id == "sys-default"


async def test_patch_conversation_persists_delegate_mode(monkeypatch):
    from datetime import datetime

    from agentcore.api.routes.conversations import crud
    from agentcore.api.schemas import UpdateConversationRequest

    written: dict = {}
    conv = SimpleNamespace(
        id="c1",
        title="t",
        updated_at=datetime.now(),
        created_at=datetime.now(),
        message_count=0,
        folder_id=None,
        local_container_root_id=None,
        pinned=False,
        archived=False,
        permission_preset="workspace",
        deep_research_auto=False,
        assembly_id="asm-1",
        delegate_mode=False,
    )

    class _Repo:
        _session = None

        async def get_by_id(self, _cid, *, user_id):
            return conv

        async def preference_flags_for(self, _user_id, _ids):
            return {}

        async def set_delegate_mode(self, _cid, enabled, *, user_id):
            written["delegate_mode"] = enabled
            conv.delegate_mode = enabled
            return conv

    monkeypatch.setattr(crud, "_require_conversation_write", AsyncMock())
    body = UpdateConversationRequest(delegate_mode=True)
    result = await crud.update_conversation(
        "c1", body, SimpleNamespace(user_id="u1"), repo=_Repo()
    )
    assert written == {"delegate_mode": True}
    assert result.delegate_mode is True


async def test_create_conversation_snapshots_account_default(monkeypatch):
    from datetime import datetime

    from agentcore.api.routes.conversations import crud
    from agentcore.api.schemas import CreateConversationRequest

    written: dict = {}
    conv = SimpleNamespace(
        id="c-new",
        title="",
        updated_at=datetime.now(),
        created_at=datetime.now(),
        message_count=0,
        folder_id=None,
        local_container_root_id=None,
        pinned=False,
        archived=False,
        permission_axes={"boundary": "folder"},
        deep_research_auto=False,
        assembly_id="sys-default",
        compaction_summary=None,
        compacted_through=None,
    )

    class _Repo:
        _session = object()

        async def create(self, **kwargs):
            written.update(kwargs)
            conv.assembly_id = kwargs.get("assembly_id")
            return conv

    monkeypatch.setattr(
        "agentcore.api.routes.conversations.crud.default_permission_axes_for_user",
        AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"boundary": "folder"})),
    )
    monkeypatch.setattr(
        "agentcore.tools.switchboard.disabled_tools_for_user",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.snapshot_default_profile_id",
        AsyncMock(return_value="sys-default"),
    )
    body = CreateConversationRequest()
    result = await crud.create_conversation(
        body, SimpleNamespace(user_id="u1"), repo=_Repo(), folder_repo=SimpleNamespace()
    )
    assert written["assembly_id"] == "sys-default"
    assert result.assembly_id == "sys-default"


# --- inference proxy authoritative re-resolution ------------------------------


async def test_inference_proxy_uses_conversation_profile(monkeypatch):
    from agentcore.api.routes import inference
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    creds = LLMCredentials(
        api_key="sk", base_url="https://api.deepseek.com", default_model="account-model"
    )
    seen: dict = {}

    async def _fake_preflight(**kw):
        seen["origin"] = kw["model_origin"]
        seen["provider_id"] = kw["provider_id"]
        return creds

    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", _fake_preflight)
    expanded = ExpandedProfile(
        profile_id="imp",
        name="会话",
        kind="implicit",
        main=ModelSelection(model="picked-model", origin="byok", provider_id="p1"),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation",
        AsyncMock(return_value=expanded),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand",
        AsyncMock(return_value=expanded),
    )

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, cid, *, user_id):
            return SimpleNamespace(id=cid, assembly_id="imp")

    monkeypatch.setattr("agentcore.db.repositories.ConversationRepository", _ConvRepo)
    cfg = await inference._resolve_inference_credentials(
        None,
        None,
        SimpleNamespace(user_id="u1"),
        conversation_id="c1",
    )
    assert seen["origin"] == "byok"
    assert seen["provider_id"] == "p1"
    assert cfg.model == "picked-model"
    assert cfg.source == "byok"


# --- P2-B: catalog as sole platform 上架 fact source ---------------------------


def test_platform_listable_public_api_and_visibility_gate(monkeypatch):
    """Public 上架 helpers: listable vs visible share one conjunction."""
    monkeypatch.setattr(
        catalog,
        "platform_listable_model_ids",
        lambda: ["glm-5.2", "grok-4.5"],
    )
    monkeypatch.setattr(catalog, "platform_catalog_visible", lambda: True)
    assert catalog.is_platform_listable("glm-5.2")
    assert not catalog.is_platform_listable("not-listed")
    assert catalog.visible_platform_listable_model_ids() == ["glm-5.2", "grok-4.5"]
    assert catalog.platform_model_display_name("glm-5.2") == "GLM-5.2"

    monkeypatch.setattr(catalog, "platform_catalog_visible", lambda: False)
    assert catalog.visible_platform_listable_model_ids() == []
    # Recognition set (no gate) still listable:
    assert catalog.is_platform_listable("grok-4.5")


def test_system_preset_display_name_matches_catalog_enrichment(monkeypatch):
    """Profiles derive combo names via catalog — same enrichment path as catalog rows."""
    from agentcore.llm.model_profiles import (
        _system_preset_display_name,
        platform_preset_id,
        system_presets,
    )

    monkeypatch.setattr(
        catalog,
        "platform_listable_model_ids",
        lambda: ["glm-5.2"],
    )
    presets = system_presets()
    assert presets[platform_preset_id("glm-5.2")] == "glm-5.2"
    assert _system_preset_display_name("glm-5.2") == catalog.platform_model_display_name(
        "glm-5.2"
    )


def test_platform_model_label_folds_badge_into_one_string():
    """Lone-label surfaces need the badge — (display_name, badge) is the unique key."""
    assert catalog.platform_model_display_name("deepseek-v4-flash-free") == (
        catalog.platform_model_display_name("deepseek-v4-flash")
    )
    assert catalog.platform_model_label("deepseek-v4-flash-free") == (
        "DeepSeek V4 Flash · 免费额度"
    )
    # No curated badge → unchanged (no dangling separator).
    assert catalog.platform_model_label("deepseek-v4-flash") == "DeepSeek V4 Flash"
    assert catalog.platform_model_label("glm-5.2") == "GLM-5.2"
    assert catalog.platform_model_label("deepseek-v4-flash-free") != (
        catalog.platform_model_label("deepseek-v4-flash")
    )


def test_platform_catalog_row_does_not_stamp_platform_badge():
    flash = catalog._entry(
        "deepseek-v4-flash",
        origin="platform",
        available=True,
        credential_source="platform",
    )
    assert flash.display_name == "DeepSeek V4 Flash"
    assert flash.vendor == "DeepSeek"
    assert flash.badge is None
    assert flash.id == "deepseek-v4-flash"

    free = catalog._entry(
        "deepseek-v4-flash-free",
        origin="platform",
        available=True,
        credential_source="platform",
    )
    assert free.display_name == "DeepSeek V4 Flash"
    assert free.badge == "免费额度"


def test_catalog_entry_stamps_vendor_reasoning_effort():
    flash = catalog._entry(
        "deepseek-v4-flash",
        origin="platform",
        available=True,
        credential_source="platform",
    )
    assert flash.reasoning_effort is not None
    assert flash.reasoning_effort.options == ("low", "high", "max")
    assert flash.reasoning_effort.default == "high"

    gpt = catalog._entry(
        "gpt-4o",
        origin="byok",
        available=True,
        credential_source="user",
        provider_id="p1",
        provider_label="OpenAI",
    )
    assert gpt.reasoning_effort is None

    gpt5 = catalog._entry(
        "gpt-5",
        origin="byok",
        available=True,
        credential_source="user",
        provider_id="p1",
        provider_label="OpenAI",
    )
    assert gpt5.reasoning_effort is not None
    assert gpt5.reasoning_effort.options == ("minimal", "low", "medium", "high")
    assert gpt5.reasoning_effort.default == "medium"

    astra = catalog._entry(
        "gpt-6-astra",
        origin="byok",
        available=True,
        credential_source="user",
        provider_id="p1",
        provider_label="OpenAI",
    )
    assert astra.reasoning_effort is not None
    assert astra.reasoning_effort.options == ("low", "medium", "high")
    assert astra.reasoning_effort.default == "medium"

    hy3 = catalog._entry(
        "hy3",
        origin="byok",
        available=True,
        credential_source="user",
        provider_id="p1",
        provider_label="Hy",
    )
    assert hy3.reasoning_effort is None
