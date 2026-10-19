"""ProviderRouter prefix routing, openai_compatible stream error path, BYOK resolve.

No real network: duck-typed leaf providers + httpx MockTransport + mocked DB/encryptor.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentcore.config import settings
from agentcore.core.errors import LLMAuthError, LLMError
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    TokenUsage,
)
from agentcore.llm.provider.router import ProviderRouter
from agentcore.llm.resolve import (
    platform_llm_credentials,
    resolve_model_config,
    resolve_turn_model,
    resolve_user_llm_credentials,
)
from agentcore.security.keys import KeyEncryptor

_MASTER_KEY = "a" * 64


# --- ProviderRouter -----------------------------------------------------------


class _LeafProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.complete_models: list[str] = []
        self.stream_models: list[str] = []
        self.closed = False

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.complete_models.append(request.model)
        return LLMResponse(
            content=f"{self.name}:{request.model}",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            model=request.model,
            finish_reason="stop",
        )

    async def stream(self, request: LLMRequest):
        self.stream_models.append(request.model)
        yield LLMChunk(delta_content=f"{self.name}:{request.model}")

    def clone(self) -> _LeafProvider:
        return _LeafProvider(self.name)

    async def close(self) -> None:
        self.closed = True


def _req(model: str) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
    )


async def test_router_complete_strips_known_prefix():
    default = _LeafProvider("default")
    vendor = _LeafProvider("vendor")
    router = ProviderRouter(default=default, providers={"vendor": vendor})

    result = await router.complete(_req("vendor/gpt-mini"))
    assert result.content == "vendor:gpt-mini"
    assert vendor.complete_models == ["gpt-mini"]
    assert default.complete_models == []


async def test_router_stream_strips_known_prefix():
    default = _LeafProvider("default")
    vendor = _LeafProvider("vendor")
    router = ProviderRouter(default=default, providers={"vendor": vendor})

    chunks = [c async for c in router.stream(_req("vendor/flash-2"))]
    assert [c.delta_content for c in chunks] == ["vendor:flash-2"]
    assert vendor.stream_models == ["flash-2"]
    assert default.stream_models == []


async def test_router_unknown_prefix_keeps_full_model_on_default():
    default = _LeafProvider("default")
    router = ProviderRouter(default=default, providers={"vendor": _LeafProvider("vendor")})

    result = await router.complete(_req("other/model-x"))
    assert result.content == "default:other/model-x"
    assert default.complete_models == ["other/model-x"]


async def test_router_empty_rest_after_prefix_falls_to_default():
    """``vendor/`` (empty rest) must not route to the vendor leaf."""
    default = _LeafProvider("default")
    vendor = _LeafProvider("vendor")
    router = ProviderRouter(default=default, providers={"vendor": vendor})

    result = await router.complete(_req("vendor/"))
    assert result.content == "default:vendor/"
    assert default.complete_models == ["vendor/"]
    assert vendor.complete_models == []


async def test_router_no_prefix_uses_default():
    default = _LeafProvider("default")
    router = ProviderRouter(default=default, providers={"vendor": _LeafProvider("vendor")})

    result = await router.complete(_req("plain-model"))
    assert result.content == "default:plain-model"
    assert "vendor" in router.available_prefixes


async def test_router_clone_and_close_are_independent():
    default = _LeafProvider("default")
    vendor = _LeafProvider("vendor")
    router = ProviderRouter(default=default, providers={"vendor": vendor})
    cloned = router.clone()

    await cloned.close()
    assert default.closed is False
    assert vendor.closed is False
    # Cloned leaves are distinct instances that did close.
    assert isinstance(cloned._default, _LeafProvider)  # noqa: SLF001
    assert cloned._default.closed is True  # noqa: SLF001


# --- openai_compatible stream error path --------------------------------------


async def _mock_provider(handler) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://example.invalid/v1",
        transport=httpx.MockTransport(handler),
    )
    return provider


def _sse_ok(text: str = "hi") -> bytes:
    payload = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
    return (f"data: {json.dumps(payload)}\ndata: [DONE]\n").encode()


async def test_stream_happy_path_yields_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse_ok("streamed"))

    provider = await _mock_provider(handler)
    try:
        chunks = [c async for c in provider.stream(_req(DEEPSEEK_V4_FLASH))]
        assert any(c.delta_content == "streamed" for c in chunks)
    finally:
        await provider.close()


async def test_stream_401_fails_fast_without_retry(monkeypatch):
    calls = {"n": 0}

    async def fake_sleep(_sec: float) -> None:
        raise AssertionError("auth failures must not sleep/retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, content=b'{"error":"unauthorized"}')

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMAuthError):
            async for _ in provider.stream(_req(DEEPSEEK_V4_FLASH)):
                pass
        assert calls["n"] == 1
    finally:
        await provider.close()


async def test_stream_400_fails_fast_without_retry(monkeypatch):
    calls = {"n": 0}

    async def fake_sleep(_sec: float) -> None:
        raise AssertionError("client 400 must not sleep/retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, content=b'{"error":{"message":"bad request"}}')

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError) as ei:
            async for _ in provider.stream(_req(DEEPSEEK_V4_FLASH)):
                pass
        assert ei.value.retryable is False
        assert calls["n"] == 1
    finally:
        await provider.close()


# --- BYOK / platform resolve --------------------------------------------------


def test_platform_llm_credentials_none_without_key(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    assert platform_llm_credentials() is None


def test_platform_llm_credentials_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-plat")
    monkeypatch.setattr(settings, "platform_base_url", "https://api.example/v1")
    monkeypatch.setattr(settings, "platform_model", "plat-model")
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.api_key == "sk-plat"
    assert creds.default_model == "plat-model"
    assert creds.source == "platform"


def test_resolve_turn_model_fallbacks():
    assert resolve_turn_model(None) == settings.platform_model
    creds = LLMCredentials(
        api_key="k",
        base_url="https://x",
        default_model="user-model",
        source="user",
    )
    assert resolve_turn_model(creds) == "user-model"


def _provider_row(**kw):
    defaults = {
        "id": "prov-1",
        "user_id": "u1",
        "label": "DeepSeek",
        "base_url": "https://byok.example/v1",
        "default_model": "byok-flash",
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _mock_provider_get(monkeypatch, row):
    monkeypatch.setattr(
        "agentcore.db.repositories.UserLlmProviderRepository",
        lambda _s: SimpleNamespace(get=AsyncMock(return_value=row)),
    )


async def test_resolve_provider_credentials_decrypts_row(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)
    enc = KeyEncryptor(_MASTER_KEY)
    cipher = enc.encrypt(b"sk-user-secret")
    _mock_provider_get(monkeypatch, _provider_row(api_key_enc=cipher))
    creds = await resolve_user_llm_credentials(MagicMock(), "u1", provider_id="prov-1")
    assert creds is not None
    assert creds.api_key == "sk-user-secret"
    assert creds.base_url == "https://byok.example/v1"
    assert creds.default_model == ""
    assert creds.provider_id == "prov-1"
    assert creds.source == "user"
    assert creds.label == "DeepSeek"


async def test_resolve_provider_credentials_empty_label_is_none(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)
    enc = KeyEncryptor(_MASTER_KEY)
    cipher = enc.encrypt(b"sk-user-secret")
    _mock_provider_get(monkeypatch, _provider_row(api_key_enc=cipher, label="  "))
    creds = await resolve_user_llm_credentials(MagicMock(), "u1", provider_id="prov-1")
    assert creds is not None
    assert creds.label is None
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)
    _mock_provider_get(monkeypatch, None)
    assert await resolve_user_llm_credentials(MagicMock(), "u1", provider_id="p") is None


async def test_resolve_provider_credentials_no_encryptor_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "")
    _mock_provider_get(monkeypatch, _provider_row(api_key_enc=b"cipher"))
    assert await resolve_user_llm_credentials(MagicMock(), "u1", provider_id="prov-1") is None


async def test_resolve_provider_credentials_malformed_encryptor_returns_none(monkeypatch):
    """Malformed ENCRYPTION_KEY must not raise binascii through the BYOK resolve path."""
    monkeypatch.setattr(settings, "encryption_key", "not-a-valid-hex-key")
    _mock_provider_get(monkeypatch, _provider_row(api_key_enc=b"cipher"))
    assert await resolve_user_llm_credentials(MagicMock(), "u1", provider_id="prov-1") is None


async def test_resolve_provider_credentials_bad_cipher_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)
    _mock_provider_get(monkeypatch, _provider_row(api_key_enc=b"not-a-valid-gcm-blob"))
    assert await resolve_user_llm_credentials(MagicMock(), "u1", provider_id="prov-1") is None


async def test_resolve_model_config_byok_ignores_background_slot(monkeypatch):
    """Catalog closed: title follows main, leftover background slot is ignored."""
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    monkeypatch.setattr(settings, "platform_api_key", "")
    user_creds = LLMCredentials(
        api_key="sk-user",
        base_url="https://user.example/v1",
        default_model="user-flash",
        source="user",
        provider_id="prov-1",
    )
    expanded = ExpandedProfile(
        profile_id="p",
        name="当前配置",
        kind="user",
        main=ModelSelection(model="user-flash", origin="byok", provider_id="prov-1"),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand",
        AsyncMock(return_value=expanded),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation_id",
        AsyncMock(return_value=expanded),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.UserLlmProviderRepository",
        lambda _s: SimpleNamespace(
            get=AsyncMock(return_value=_provider_row(default_model="user-flash")),
            first_for_user=AsyncMock(return_value=_provider_row()),
        ),
    )
    monkeypatch.setattr("agentcore.llm.resolve._decrypt_provider", lambda _r, _u: user_creds)
    chat = await resolve_model_config(MagicMock(), "u1", "chat")
    title = await resolve_model_config(MagicMock(), "u1", "title")
    assert chat is not None and chat.source == "byok" and chat.model == "user-flash"
    assert title is not None and title.source == "byok" and title.model == "user-flash"
    assert chat.api_key == "sk-user"
    assert chat.provider_id == "prov-1"


def _mock_background_account(monkeypatch):
    """Platform key live + visible, on a BYOK account whose combo has a chat main.

    Fixture models are curated-CNY listable ids (glm-5.2 / deepseek-v4-flash).
    """
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    monkeypatch.setattr(settings, "billing_mode", "platform")
    monkeypatch.setattr(settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_background_model", "deepseek-v4-flash")
    expanded = ExpandedProfile(
        profile_id="p",
        name="当前配置",
        kind="user",
        main=ModelSelection(model="user-flash", origin="byok", provider_id="prov-1"),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand",
        AsyncMock(return_value=expanded),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation_id",
        AsyncMock(return_value=expanded),
    )
    row = _provider_row(default_model="user-flash")
    monkeypatch.setattr(
        "agentcore.db.repositories.UserLlmProviderRepository",
        lambda _s: SimpleNamespace(
            get=AsyncMock(return_value=row),
            first_for_user=AsyncMock(return_value=row),
        ),
    )
    user_creds = LLMCredentials(
        api_key="sk-user",
        base_url="https://user.example/v1",
        default_model="user-flash",
        source="user",
        provider_id="prov-1",
    )
    monkeypatch.setattr("agentcore.llm.resolve._decrypt_provider", lambda _r, _u: user_creds)


async def test_resolve_model_config_title_catalog_open_pins_chrome(
    monkeypatch,
):
    """代付开着：标题钉 PLATFORM_BACKGROUND_MODEL，不读主模型、不读后台槽。"""
    _mock_background_account(monkeypatch)
    title = await resolve_model_config(MagicMock(), "u1", "title")
    assert title is not None
    assert title.source == "platform"
    assert title.model == "deepseek-v4-flash"
    assert title.api_key == "sk-platform"


async def test_resolve_model_config_compaction_follows_main_not_chrome(
    monkeypatch,
):
    """压缩跟这场主模型，不走平台标题档，也不读后台槽。"""
    _mock_background_account(monkeypatch)
    cfg = await resolve_model_config(MagicMock(), "u1", "compaction")
    assert cfg is not None
    assert cfg.source == "byok"
    assert cfg.model == "user-flash"
    assert cfg.api_key == "sk-user"
    assert cfg.provider_id == "prov-1"


async def test_resolve_model_config_title_dormant_follows_conversation_main(
    monkeypatch,
):
    """代付关掉：标题跟这场主模型，不读账号星标、不读后台槽。"""
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    _mock_background_account(monkeypatch)
    monkeypatch.setattr(settings, "billing_mode", "byok")
    conversation = ExpandedProfile(
        profile_id="conv",
        name="这场",
        kind="user",
        main=ModelSelection(model="conv-main", origin="byok", provider_id="prov-1"),
    )
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand_for_conversation_id",
        AsyncMock(return_value=conversation),
    )
    title = await resolve_model_config(
        MagicMock(), "u1", "title", conversation_id="c1"
    )
    assert title is not None
    assert title.source == "byok"
    assert title.model == "conv-main"
    assert title.api_key == "sk-user"


async def test_resolve_model_config_title_platform_main_still_uses_chrome(
    monkeypatch,
):
    """主模型是平台 GLM 时，标题仍钉后台 Flash，不跟主模型。"""
    from agentcore.llm.model_profiles import ExpandedProfile
    from agentcore.llm.resolve import ModelSelection

    _mock_background_account(monkeypatch)
    monkeypatch.setattr(
        "agentcore.llm.model_profiles.LlmModelProfileService.expand",
        AsyncMock(
            return_value=ExpandedProfile(
                profile_id="p",
                name="平台主模型",
                kind="user",
                main=ModelSelection(model="glm-5.2", origin="platform", provider_id=None),
            )
        ),
    )
    title = await resolve_model_config(MagicMock(), "u1", "title")
    assert title is not None
    assert title.source == "platform"
    assert title.model == "deepseek-v4-flash"
    assert title.api_key == "sk-platform"


@pytest.mark.parametrize("purpose", ["title", "compaction"])
async def test_background_user_fallback_keeps_chat_model_not_platform_background(
    monkeypatch, purpose
):
    """User-key fallback must send the account chat model, not the Go catalog id.

    ``PLATFORM_BACKGROUND_MODEL`` stays on the platform-key path. A dormant deploy
    (and a platform-auth fallback while the catalog is still open) both land here.
    """
    from agentcore.llm.resolve import resolve_background_user_fallback

    _mock_background_account(monkeypatch)
    monkeypatch.setattr(settings, "platform_background_model", "deepseek-v4.1-flash")
    cfg = await resolve_background_user_fallback(MagicMock(), "u1", purpose)
    assert cfg is not None
    assert cfg.source == "byok"
    assert cfg.model == "user-flash"
    assert cfg.api_key == "sk-user"
    assert cfg.provider_id == "prov-1"


async def test_resolve_model_config_background_dormant_keeps_chat_model(monkeypatch):
    """``billing_mode=byok`` title resolve uses the account chat model, not Go's id."""
    _mock_background_account(monkeypatch)
    monkeypatch.setattr(settings, "billing_mode", "byok")
    monkeypatch.setattr(settings, "platform_background_model", "deepseek-v4.1-flash")
    title = await resolve_model_config(MagicMock(), "u1", "title")
    assert title is not None
    assert title.source == "byok"
    assert title.model == "user-flash"
    assert title.api_key == "sk-user"
