"""Platform-paid calls stamp which credential funded them (logs + cost_calls).

``credential_source`` is only user/platform/vendor. The pool-member id is a
stable alias or hash of (api_key, base_url) — never key plaintext / last-4 —
and must not appear on user-visible SSE error context.
"""

from __future__ import annotations

from structlog.testing import capture_logs

from agentcore.core.errors import LLMUpstreamError
from agentcore.core.log_context import clear_log_context, get_log_value
from agentcore.llm.credentials import (
    LLMCredentials,
    bind_credential_pricing_context,
    derive_platform_credential_id,
    hash_platform_credential_id,
    sanitize_platform_credential_alias,
)
from agentcore.llm.errors import error_context_from
from agentcore.llm.observability import log_llm_call, log_llm_call_failed
from agentcore.llm.pricing import calculate_cost
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.llm.resolve import platform_llm_credentials
from agentcore.runtime.costing import priced_call_cost


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=2, cache_miss_tokens=10)


def setup_function() -> None:
    clear_log_context()


def teardown_function() -> None:
    clear_log_context()


def test_hash_is_stable_and_not_the_key():
    key = "sk-abcdefghijklmnopqrstuvwxyz012345"
    url = "https://opencode.ai/zen/go/v1"
    a = hash_platform_credential_id(key, url)
    b = hash_platform_credential_id(key, url)
    assert a == b
    assert a.startswith("pk_")
    assert key not in a
    assert a != key[-4:]
    other = hash_platform_credential_id(key + "-other", url)
    assert other != a
    assert hash_platform_credential_id(key, url + "/x") != a


def test_sanitize_rejects_key_shaped_aliases():
    key = "sk-abcdefghijklmnopqrstuvwxyz012345"
    assert sanitize_platform_credential_alias("go-1", api_key=key) == "go-1"
    assert sanitize_platform_credential_alias("sk-secret", api_key=key) is None
    assert sanitize_platform_credential_alias(key, api_key=key) is None
    assert sanitize_platform_credential_alias(key[-4:], api_key=key) is None
    assert sanitize_platform_credential_alias("has space", api_key=key) is None
    assert sanitize_platform_credential_alias("", api_key=key) is None


def test_derive_uses_env_alias_for_default_pair(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "platform_api_key", "sk-default-key")
    monkeypatch.setattr(settings, "platform_base_url", "https://go.example/v1")
    monkeypatch.setattr(settings, "platform_credential_id", "go-1")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    assert derive_platform_credential_id("sk-default-key", "https://go.example/v1") == "go-1"
    # Different pair → hash, not the default alias.
    other = derive_platform_credential_id("sk-other", "https://go.example/v1")
    assert other != "go-1"
    assert other.startswith("pk_")


def test_derive_uses_per_model_json_id(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "platform_api_key", "sk-default-key")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_credential_id", "go-default")
    monkeypatch.setattr(
        settings,
        "platform_model_credentials",
        '{"relay-b": {"api_key": "sk-relay-b", "base_url": "https://relay/v1", "id": "go-2"}}',
    )
    assert derive_platform_credential_id("sk-relay-b", "https://relay/v1") == "go-2"
    assert derive_platform_credential_id("sk-default-key", "https://default/v1") == "go-default"


def test_invalid_alias_falls_back_to_hash(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "platform_api_key", "sk-default-key")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_credential_id", "sk-looks-like-a-key")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    cid = derive_platform_credential_id("sk-default-key", "https://default/v1")
    assert cid.startswith("pk_")
    assert cid != "sk-looks-like-a-key"


def test_platform_llm_credentials_stamps_id(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "platform_api_key", "sk-default-key")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_model", "glm-5.2")
    monkeypatch.setattr(settings, "platform_credential_id", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    creds = platform_llm_credentials()
    assert creds is not None
    assert creds.platform_credential_id
    assert creds.platform_credential_id.startswith("pk_")
    assert creds.api_key not in creds.platform_credential_id
    same = platform_llm_credentials(model="glm-5.2")
    assert same is not None
    assert same.platform_credential_id == creds.platform_credential_id


def test_bind_platform_sets_ambient_id_byok_clears_it(monkeypatch):
    from agentcore.config import settings

    clear_log_context()
    monkeypatch.setattr(settings, "platform_api_key", "sk-default-key")
    monkeypatch.setattr(settings, "platform_base_url", "https://default/v1")
    monkeypatch.setattr(settings, "platform_credential_id", "go-1")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    platform = LLMCredentials(
        api_key="sk-default-key",
        base_url="https://default/v1",
        source="platform",
        platform_credential_id="go-1",
    )
    bind_credential_pricing_context(platform)
    assert get_log_value("credential_source") == "platform"
    assert get_log_value("platform_credential_id") == "go-1"

    byok = LLMCredentials(
        api_key="sk-user",
        base_url="https://api.deepseek.com",
        source="user",
        provider_id="prov-1",
    )
    bind_credential_pricing_context(byok)
    assert get_log_value("credential_source") == "user"
    assert get_log_value("platform_credential_id") == ""
    assert get_log_value("provider_id") == "prov-1"
    clear_log_context()


def test_log_llm_call_includes_id_only_on_platform_path():
    clear_log_context()
    bind_credential_pricing_context(
        LLMCredentials(
            api_key="sk-default-key",
            base_url="https://default/v1",
            source="platform",
            platform_credential_id="go-1",
        )
    )
    usage = _usage()
    with capture_logs() as caps:
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=usage,
            finish_reason="stop",
            latency_ms=1,
            stream=False,
        )
    call = next(c for c in caps if c.get("event") == "llm.call")
    assert call["platform_credential_id"] == "go-1"
    priced = calculate_cost(DEEPSEEK_V4_FLASH, usage, credential_source="platform")
    assert priced.credential_source == "platform"

    bind_credential_pricing_context(
        LLMCredentials(api_key="sk-user", base_url="https://x", source="user")
    )
    with capture_logs() as caps:
        log_llm_call(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            usage=usage,
            finish_reason="stop",
            latency_ms=1,
            stream=False,
            credential_source="user",
        )
        log_llm_call_failed(
            scenario="chat",
            model=DEEPSEEK_V4_FLASH,
            latency_ms=1,
            error="boom",
            stream=False,
        )
    call = next(c for c in caps if c.get("event") == "llm.call")
    failed = next(c for c in caps if c.get("event") == "llm.call_failed")
    assert "platform_credential_id" not in call
    assert "platform_credential_id" not in failed
    clear_log_context()


def test_priced_call_stamps_column_on_platform_not_byok():
    clear_log_context()
    bind_credential_pricing_context(
        LLMCredentials(
            api_key="sk-default-key",
            base_url="https://default/v1",
            source="platform",
            platform_credential_id="go-1",
        )
    )
    platform = priced_call_cost(
        model=DEEPSEEK_V4_FLASH,
        usage=_usage(),
        role="captain",
        credential_source="platform",
        call_id="call_p",
    )
    assert platform.platform_credential_id == "go-1"
    assert platform.cost["credential_source"] == "platform"
    # Money path unchanged.
    assert platform.cost_total_nano > 0
    assert platform.cost_estimated_nano == 0

    byok = priced_call_cost(
        model=DEEPSEEK_V4_FLASH,
        usage=_usage(),
        role="captain",
        credential_source="user",
        call_id="call_u",
    )
    assert byok.platform_credential_id is None
    assert byok.cost_total_nano == 0
    assert byok.cost_estimated_nano > 0
    clear_log_context()


def test_error_context_omits_platform_credential_id_even_if_details_has_it():
    err = LLMUpstreamError(
        "上游模型服务暂时不可用（503），请稍后再试",
        upstream_status=503,
        credential_source="platform",
    )
    err.details["platform_credential_id"] = "go-1"
    ctx = error_context_from(err) or {}
    assert ctx.get("credential_source") == "platform"
    assert "platform_credential_id" not in ctx
    assert "go-1" not in str(ctx)
