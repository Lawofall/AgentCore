"""Per-model platform credential overrides (运营中转「一 key 一模型」, 成本配额与计费 §〇·六 F3).

The platform catalog may list several models; each may carry its own api_key / base_url
(``PLATFORM_MODEL_CREDENTIALS``). ``platform_llm_credentials(model=…)`` is the single point
that resolves「which upstream key + base_url serves this model」, falling back to the shared
``platform_api_key`` / ``platform_base_url`` for any missing field or unlisted model.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.billing.gate import (
    preflight_llm_credentials,
    preflight_resolved_llm_credentials,
)
from agentcore.billing.preference import is_platform_available
from agentcore.config import settings
from agentcore.config.platform import parse_platform_model_credentials
from agentcore.core.errors import PlatformBillingUnavailableError
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.resolve import (
    ModelSelection,
    platform_llm_credentials,
    platform_wire_model,
    resolve_model_config,
)

_OVERRIDE = (
    '{"relay-b": {"api_key": "sk-relay-b-key", "base_url": "https://relay.example/openai/v1"}}'
)


def _user():
    """A quota-bearing user (all override columns None → inherit deployment defaults)."""
    return SimpleNamespace(
        user_id="u1",
        is_unlimited=False,
        quota_daily_tokens=None,
        quota_monthly_cost_cny=None,
        quota_daily_cost_cny=None,
        quota_daily_requests=None,
    )


# --- parse_platform_model_credentials ----------------------------------------


def test_parse_valid_json_keeps_only_nonblank_fields():
    parsed = parse_platform_model_credentials(
        '{"a": {"api_key": "k1", "base_url": "u1", "id": "go-relay"},'
        ' "b": {"api_key": "k2"},'
        ' "c": {"base_url": "u3"},'
        ' "d": {"api_key": "", "base_url": "  "},'
        ' "  ": {"api_key": "x"},'
        ' "e": "not-an-object",'
        ' "f": {"upstream_model": "glm-5.2"},'
        ' "g": {"api_key": "k3", "upstream_model": "  "}}'
    )
    assert parsed == {
        "a": {"api_key": "k1", "base_url": "u1", "id": "go-relay"},
        "b": {"api_key": "k2"},
        "c": {"base_url": "u3"},
        "f": {"upstream_model": "glm-5.2"},
        "g": {"api_key": "k3"},
    }


def test_parse_blank_and_malformed_degrade_to_empty():
    assert parse_platform_model_credentials("") == {}
    assert parse_platform_model_credentials("   ") == {}
    assert parse_platform_model_credentials("{not json}") == {}
    assert parse_platform_model_credentials('["list", "not", "object"]') == {}


# --- platform_llm_credentials(model=…) ---------------------------------------


def test_no_arg_call_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.api_key == "sk-default"
    assert creds.base_url == "https://default/v1"
    assert creds.default_model == "glm-5.2"
    assert creds.source == "platform"
    assert creds.platform_credential_id
    assert creds.platform_credential_id != "sk-default"
    assert creds.api_key[-4:] not in creds.platform_credential_id


def test_override_model_uses_its_own_key_and_base_url(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    creds = platform_llm_credentials(model="relay-b")
    assert creds is not None
    assert creds.api_key == "sk-relay-b-key"
    assert creds.base_url == "https://relay.example/openai/v1"
    # default_model is the requested model, not settings.platform_model.
    assert creds.default_model == "relay-b"
    assert creds.source == "platform"
    assert creds.platform_credential_id
    assert creds.platform_credential_id != creds.api_key
    assert creds.api_key[-4:] not in creds.platform_credential_id


def test_unlisted_model_falls_back_to_default_key(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    creds = platform_llm_credentials(model="glm-5.2")
    assert creds is not None
    # glm-5.2 has no override entry → shared default key/base_url, default_model=glm-5.2.
    assert creds.api_key == "sk-default"
    assert creds.base_url == "https://default/v1"
    assert creds.default_model == "glm-5.2"


def test_override_missing_base_url_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(
        settings, "platform_model_credentials", '{"m": {"api_key": "sk-m"}}'
    )
    creds = platform_llm_credentials(model="m")
    assert creds is not None
    assert creds.api_key == "sk-m"  # override key
    assert creds.base_url == "https://default/v1"  # base_url falls back


def test_platform_wire_model_uses_upstream_override(monkeypatch):
    monkeypatch.setattr(
        settings,
        "platform_model_credentials",
        '{"glm-5.2-alt": {"api_key": "sk-alt", "upstream_model": "glm-5.2"}}',
    )
    assert platform_wire_model("glm-5.2-alt") == "glm-5.2"
    assert platform_wire_model("glm-5.2") == "glm-5.2"  # no override → catalog id
    assert platform_wire_model("") == ""


def test_override_only_key_serves_model_when_default_absent(monkeypatch):
    """No shared default key, but the model's override has one → resolvable."""
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    assert platform_llm_credentials(model="relay-b") is not None
    # A model with neither an override key nor a default key stays None.
    assert platform_llm_credentials(model="glm-5.2") is None
    assert platform_llm_credentials() is None


# --- is_platform_available ---------------------------------------------------


def test_is_platform_available_default_key(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    assert is_platform_available() is True


def test_is_platform_available_override_only_key(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    assert is_platform_available() is True


def test_is_platform_available_none(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    assert is_platform_available() is False
    # An override that carries only a base_url (no key) is not "available".
    monkeypatch.setattr(
        settings, "platform_model_credentials", '{"m": {"base_url": "https://x/v1"}}'
    )
    assert is_platform_available() is False


# --- gate 503 honors override-only availability -------------------------------


@pytest.mark.asyncio
async def test_gate_platform_unavailable_when_no_key_anywhere(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    with pytest.raises(PlatformBillingUnavailableError):
        await preflight_llm_credentials(
            session=MagicMock(),
            user=SimpleNamespace(user_id="u1"),
            cost_repo=MagicMock(),
            byok_missing_message="missing",
            model_origin="platform",
        )


@pytest.mark.asyncio
async def test_gate_platform_available_via_override_key(monkeypatch):
    """Default key empty but an override carries a key → gate proceeds to quota (no 503)."""
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    monkeypatch.setattr(settings, "billing_mode", "platform")
    with patch("agentcore.billing.gate.enforce_quota", AsyncMock()) as enforce:
        result = await preflight_llm_credentials(
            session=MagicMock(),
            user=_user(),
            cost_repo=MagicMock(),
            byok_missing_message="missing",
            model_origin="platform",
        )
    assert result is None  # platform path (per-model creds resolved at the call site)
    enforce.assert_awaited_once()


@pytest.mark.asyncio
async def test_preflight_resolved_platform_returns_per_model_creds(monkeypatch):
    """standing/workflows helper: platform origin → per-model platform credentials."""
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    monkeypatch.setattr(settings, "billing_mode", "platform")
    with patch("agentcore.billing.gate.enforce_quota", AsyncMock()):
        result = await preflight_resolved_llm_credentials(
            session=MagicMock(),
            user=_user(),
            cost_repo=MagicMock(),
            byok_missing_message="missing",
            selection=ModelSelection(model="relay-b", origin="platform", provider_id=None),
        )
    assert result is not None
    assert result.source == "platform"
    assert result.api_key == "sk-relay-b-key"


@pytest.mark.asyncio
async def test_preflight_resolved_byok_returns_gate_creds(monkeypatch):
    """standing/workflows helper: byok origin → gate credentials unchanged."""
    byok = LLMCredentials(
        api_key="sk-user",
        base_url="https://example/v1",
        default_model="gpt-test",
        source="user",
        provider_id="prov-1",
    )
    with patch(
        "agentcore.billing.gate.preflight_llm_credentials",
        AsyncMock(return_value=byok),
    ) as gate:
        result = await preflight_resolved_llm_credentials(
            session=MagicMock(),
            user=_user(),
            cost_repo=MagicMock(),
            byok_missing_message="missing",
            selection=ModelSelection(
                model="gpt-test", origin="byok", provider_id="prov-1"
            ),
        )
    assert result is byok
    gate.assert_awaited_once()


# --- resolve_model_config platform branch resolves per-model -----------------


def _mock_keyless(monkeypatch):
    """A keyless account: no BYOK provider → resolve falls to the platform key."""
    monkeypatch.setattr(
        "agentcore.db.repositories.UserRepository",
        lambda _s: SimpleNamespace(
            get_by_id=AsyncMock(
                return_value=SimpleNamespace(user_id="u1", default_chat_provider_id=None)
            )
        ),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.UserLlmProviderRepository",
        lambda _s: SimpleNamespace(
            get=AsyncMock(return_value=None),
            first_for_user=AsyncMock(return_value=None),
            count_for_user=AsyncMock(return_value=0),
        ),
    )
    # Account default is now profile-expand (not users.default_chat_*); stub the
    # selection so MagicMock sessions don't enter LlmModelProfileService → DB.
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=ModelSelection(
                model="relay-b", origin="platform", provider_id=None
            )
        ),
    )
    # Keyless ⇒ no user combination either: a system preset, every slot follow-null.
    from agentcore.llm.model_profiles import ExpandedProfile

    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand",
        AsyncMock(
            return_value=ExpandedProfile(
                profile_id="preset",
                name="平台预置",
                kind="system",
                main=ModelSelection(
                    model="relay-b", origin="platform", provider_id=None
                ),
            )
        ),
    )


@pytest.mark.asyncio
async def test_resolve_model_config_platform_chat_uses_per_model_key(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "relay-b")
    monkeypatch.setattr(settings, "platform_background_model", "")
    monkeypatch.setattr(settings, "billing_mode", "platform")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    _mock_keyless(monkeypatch)
    cfg = await resolve_model_config(MagicMock(), "u1", "chat")
    assert cfg is not None
    assert cfg.source == "platform"
    assert cfg.model == "relay-b"
    assert cfg.api_key == "sk-relay-b-key"
    assert cfg.base_url == "https://relay.example/openai/v1"


@pytest.mark.asyncio
async def test_resolve_model_config_platform_background_downgrade_resolves_that_model(
    monkeypatch,
):
    """Background purpose 降档 to platform_background_model → its per-model key is used."""
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_background_model", "relay-b")
    monkeypatch.setattr(settings, "billing_mode", "platform")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)
    _mock_keyless(monkeypatch)
    cfg = await resolve_model_config(MagicMock(), "u1", "title")
    assert cfg is not None
    assert cfg.source == "platform"
    assert cfg.model == "relay-b"  # downgraded model name
    assert cfg.api_key == "sk-relay-b-key"  # resolved for the downgraded model


# --- PlatformProvider: per-request model → key (辩论跨模型 / F3) --------------


@pytest.mark.asyncio
async def test_platform_provider_uses_per_model_key(monkeypatch):
    """同一 leaf 上 glm-5.2 与 relay-b 必须打到各自的 key（回归：冻死第二 key → 默认模型 403）。"""
    from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
    from agentcore.llm.provider.platform import PlatformProvider
    from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, LLMResponse

    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_model_credentials", _OVERRIDE)

    seen: list[tuple[str, str]] = []

    async def _capture_complete(self, request):  # noqa: ANN001
        seen.append((request.model, self._api_key))
        return LLMResponse(content="ok", model=request.model)

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", _capture_complete)
    provider = PlatformProvider()
    msgs = [LLMMessage(role="user", content="hi")]
    await provider.complete(LLMRequest(messages=msgs, model="glm-5.2"))
    await provider.complete(LLMRequest(messages=msgs, model="relay-b"))
    assert seen == [("glm-5.2", "sk-default"), ("relay-b", "sk-relay-b-key")]
    await provider.close()


@pytest.mark.asyncio
async def test_platform_provider_rewrites_upstream_model(monkeypatch):
    """Catalog id stays for lookup; leaf HTTP model is remapped via upstream_model."""
    from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
    from agentcore.llm.provider.platform import PlatformProvider
    from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, LLMResponse

    override = (
        '{"glm-5.2-alt": {"api_key": "sk-alt", "base_url": "https://relay.example/v1",'
        ' "upstream_model": "glm-5.2"}}'
    )
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model_credentials", override)

    seen: list[tuple[str, str]] = []

    async def _capture_complete(self, request):  # noqa: ANN001
        seen.append((request.model, self._api_key))
        return LLMResponse(content="ok", model=request.model)

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", _capture_complete)
    provider = PlatformProvider()
    msgs = [LLMMessage(role="user", content="hi")]
    await provider.complete(LLMRequest(messages=msgs, model="glm-5.2-alt"))
    assert seen == [("glm-5.2", "sk-alt")]
    await provider.close()


@pytest.mark.asyncio
async def test_build_provider_platform_source_is_resolving_leaf(monkeypatch):
    from agentcore.llm.call_fence import unwrap_provider
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.factory import build_provider
    from agentcore.llm.provider.platform import PlatformProvider

    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    provider = build_provider(
        LLMCredentials(
            api_key="sk-ignored-frozen",
            base_url="https://ignored/v1",
            default_model="glm-5.2",
            source="platform",
        )
    )
    assert isinstance(unwrap_provider(provider), PlatformProvider)


@pytest.mark.asyncio
async def test_ensure_debate_route_extras_platform_per_model(monkeypatch):
    """正方 glm-5.2 + 反方 deepseek-v4-flash：router ``platform/…`` 各自用对 key。

    Both ids have curated CNY cards (内测只上架有卡模型). Override key is on Flash.
    """
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.factory import build_router
    from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
    from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, LLMResponse
    from agentcore.runtime.debate.models import ModelIdentity, ensure_debate_route_extras

    flash_override = (
        '{"deepseek-v4-flash": {"api_key": "sk-flash-key",'
        ' "base_url": "https://relay.example/openai/v1"}}'
    )
    monkeypatch.setattr(settings, "billing_mode", "platform")
    monkeypatch.setattr(settings, "platform_api_key", "sk-default")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_model_credentials", flash_override)

    seen: list[tuple[str, str]] = []

    async def _capture_complete(self, request):  # noqa: ANN001
        seen.append((request.model, self._api_key))
        return LLMResponse(content="ok", model=request.model)

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", _capture_complete)

    # Turn main is BYOK — debate must inject platform extras (the production shape).
    router = build_router(
        LLMCredentials(
            api_key="sk-byok",
            base_url="https://byok.example/v1",
            default_model="deepseek-v4-flash",
            source="user",
            provider_id="prov-ds",
        )
    )
    await ensure_debate_route_extras(
        router,
        [
            ModelIdentity(model="glm-5.2", origin="platform"),
            ModelIdentity(model="deepseek-v4-flash", origin="platform"),
        ],
    )
    assert "platform" in router.available_prefixes
    msgs = [LLMMessage(role="user", content="hi")]
    await router.complete(LLMRequest(messages=msgs, model="platform/glm-5.2"))
    await router.complete(LLMRequest(messages=msgs, model="platform/deepseek-v4-flash"))
    assert seen == [("glm-5.2", "sk-default"), ("deepseek-v4-flash", "sk-flash-key")]
    await router.close()
