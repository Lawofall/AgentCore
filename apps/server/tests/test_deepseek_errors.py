"""Provider-level mapping of upstream HTTP errors raised during a turn."""

import json

import httpx
import pytest

from agentcore.core.errors import (
    InferenceTokenExpiredError,
    LLMAuthError,
    LLMError,
    LLMInsufficientBalanceError,
    LLMRateLimitError,
    LLMUpstreamError,
)
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import (
    OpenAICompatibleProvider,
    _reasoning_text,
)
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, ToolCall, ToolCallFunction


async def _mock_provider(
    handler, *, name: str = "test", base_url: str = "http://example.invalid/v1"
) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(name=name, api_key="k", base_url=base_url)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=base_url,
        transport=httpx.MockTransport(handler),
    )
    return provider


def _req() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
    )


async def test_complete_maps_402_to_insufficient_balance():
    provider = await _mock_provider(lambda request: httpx.Response(402))
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert "余额" in str(ei.value)
    finally:
        await provider.close()


async def test_stream_maps_402_to_insufficient_balance():
    provider = await _mock_provider(lambda request: httpx.Response(402))
    try:
        with pytest.raises(LLMInsufficientBalanceError):
            async for _ in provider.stream(_req()):
                pass
    finally:
        await provider.close()


# OpenCode Zen answers an exhausted account with 401 + CreditsError instead of 402.
# Verbatim upstream shape (workspace id redacted) — a bare 401 must stay an auth error.
_CREDITS_BODY = (
    b'{"type":"error","error":{"type":"CreditsError","message":'
    b'"Insufficient balance. Manage your billing here: '
    b'https://opencode.ai/workspace/wrk_test/billing"}}'
)


@pytest.mark.parametrize("code", [401, 403])
async def test_401_403_with_credits_body_is_balance_not_auth(code):
    provider = await _mock_provider(
        lambda request: httpx.Response(code, content=_CREDITS_BODY)
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert not isinstance(ei.value, LLMAuthError)
        assert "余额不足" in ei.value.message
        # Never route a balance failure back to「更新 Key」.
        assert "API Key 无效" not in ei.value.message
        assert ei.value.details.get("upstream_status") == code
        assert "Insufficient balance" in (ei.value.details.get("upstream_body_preview") or "")
    finally:
        await provider.close()


async def test_platform_balance_copy_offers_byok_exit_not_topup():
    """End users cannot top up the operator's account — copy must not tell them to."""
    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_CREDITS_BODY), name="platform"
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert "请充值" not in ei.value.message
        assert "自己的 API Key" in ei.value.message
        assert ei.value.details.get("credential_source") == "platform"
    finally:
        await provider.close()


async def test_probe_401_with_credits_body_is_balance():
    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_CREDITS_BODY)
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        assert "余额不足" in ei.value.message
        assert not isinstance(ei.value, LLMAuthError)
        assert ei.value.details.get("upstream_status") == 401
        assert "Insufficient balance" in (ei.value.details.get("upstream_body_preview") or "")
    finally:
        await provider.close()


async def test_probe_credits_error_is_credits_family_copy():
    from agentcore.llm.errors import OPENCODE_CREDITS_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_GO_NO_PAYMENT_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        assert ei.value.message == OPENCODE_CREDITS_MESSAGE
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


_AUTH_BODY = (
    b'{"error":{"message":"invalid api key","type":"authentication_error",'
    b'"code":"invalid_api_key"}}'
)

_GO_URL = "https://opencode.ai/zen/go/v1"
_ZEN_URL = "https://opencode.ai/zen/v1"
_GO_NO_PAYMENT_BODY = (
    b'{"type":"error","error":{"type":"CreditsError","message":'
    b'"No payment method. Add a payment method here: '
    b'https://opencode.ai/workspace/wrk_test/billing"}}'
)
_GO_UNKNOWN_CREDITS_BODY = (
    b'{"type":"error","error":{"type":"CreditsError","message":"Credits declined"}}'
)


@pytest.mark.parametrize("code", [401, 403])
async def test_probe_auth_attaches_upstream_status_not_llm_auth_error(code):
    """Connectivity probe must not upgrade 401/403 to LLMAuthError (Key 废)."""
    provider = await _mock_provider(lambda request: httpx.Response(code, content=_AUTH_BODY))
    try:
        with pytest.raises(LLMError) as ei:
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert ei.value.details.get("upstream_status") == code
        assert "invalid api key" in (ei.value.details.get("upstream_body_preview") or "")
    finally:
        await provider.close()


async def test_probe_403_model_not_allowed_is_client_error_not_key():
    body = b'{"error":{"message":"model not allowed","code":"model_not_allowed"}}'
    provider = await _mock_provider(lambda request: httpx.Response(403, content=body))
    try:
        with pytest.raises(LLMError) as ei:
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        assert not isinstance(ei.value, LLMAuthError)
        assert "API Key 无效" not in ei.value.message
        assert ei.value.details.get("upstream_status") == 403
        assert "model not allowed" in (ei.value.details.get("upstream_body_preview") or "")
    finally:
        await provider.close()


async def test_probe_401_logs_client_error_without_key():
    from structlog.testing import capture_logs

    secret = "sk-secret-key-xyz"
    provider = OpenAICompatibleProvider(
        name="test", api_key=secret, base_url="http://example.invalid/v1"
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://example.invalid/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    try:
        with capture_logs() as caps, pytest.raises(LLMError):
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        ev = next(c for c in caps if c.get("event") == "llm.client_error")
        assert ev["status_code"] == 401
        assert ev["provider"] == "test"
        assert "api_key" not in ev
        dumped = " ".join(str(v) for v in ev.values())
        assert secret not in dumped
    finally:
        await provider.close()


async def test_list_models_401_is_llm_auth_error():
    """GET /models 401 is a real Key failure — unlike probe, this stays LLMAuthError."""
    provider = await _mock_provider(lambda request: httpx.Response(401, content=_AUTH_BODY))
    try:
        with pytest.raises(LLMAuthError) as ei:
            await provider.list_models()
        assert ei.value.details.get("upstream_status") == 401
        assert "API Key" in ei.value.message
    finally:
        await provider.close()


async def test_list_models_401_with_credits_body_is_balance():
    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_CREDITS_BODY)
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError):
            await provider.list_models()
    finally:
        await provider.close()


def test_balance_detection_does_not_swallow_real_auth_failures():
    from agentcore.llm.errors import is_auth_rejection, is_balance_exhausted

    auth_body = b'{"error":{"message":"invalid api key","code":"invalid_api_key"}}'
    assert is_balance_exhausted(_CREDITS_BODY) is True
    assert is_balance_exhausted(auth_body) is False
    assert is_balance_exhausted(None) is False
    assert is_auth_rejection(401, _CREDITS_BODY) is False
    assert is_auth_rejection(401, auth_body) is True
    assert is_auth_rejection(401, None) is True


@pytest.mark.parametrize("code", [401, 403])
async def test_credits_error_on_go_is_credits_family_not_quota(code):
    from agentcore.llm.errors import OPENCODE_CREDITS_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(code, content=_CREDITS_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert ei.value.message == OPENCODE_CREDITS_MESSAGE
        assert "Use balance" not in ei.value.message
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMRateLimitError)
        assert ei.value.details.get("upstream_status") == code
    finally:
        await provider.close()


@pytest.mark.parametrize("code", [401, 403])
async def test_credits_error_no_payment_is_same_family_copy(code):
    from agentcore.llm.errors import OPENCODE_CREDITS_MESSAGE, is_balance_exhausted

    assert is_balance_exhausted(_GO_NO_PAYMENT_BODY) is True
    provider = await _mock_provider(
        lambda request: httpx.Response(code, content=_GO_NO_PAYMENT_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert ei.value.message == OPENCODE_CREDITS_MESSAGE
        assert "Use balance" not in ei.value.message
        assert not isinstance(ei.value, LLMAuthError)
    finally:
        await provider.close()


async def test_credits_error_unknown_message_still_credits_family():
    from agentcore.llm.errors import OPENCODE_CREDITS_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_GO_UNKNOWN_CREDITS_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert ei.value.message == OPENCODE_CREDITS_MESSAGE
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


async def test_zen_credits_error_uses_same_family_copy():
    from agentcore.llm.errors import OPENCODE_CREDITS_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_CREDITS_BODY),
        base_url=_ZEN_URL,
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert ei.value.message == OPENCODE_CREDITS_MESSAGE
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


async def test_platform_go_credits_keeps_platform_copy():
    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_CREDITS_BODY),
        name="platform",
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert "请充值" not in ei.value.message
        assert "自己的 API Key" in ei.value.message
        assert "Use balance" not in ei.value.message
        assert ei.value.details.get("credential_source") == "platform"
    finally:
        await provider.close()


async def test_credits_error_on_402_still_credits_family_not_quota():
    from agentcore.llm.errors import OPENCODE_CREDITS_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(402, content=_CREDITS_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert ei.value.message == OPENCODE_CREDITS_MESSAGE
        assert "Use balance" not in ei.value.message
        assert ei.value.details.get("upstream_status") == 402
    finally:
        await provider.close()


# 2026-08-18 production: Go + deepseek-v4-flash, workspace id redacted.
_REGION_WS = "https://opencode.ai/workspace/wrk_test/go"
_REGION_BODY = (
    b'{"type":"error","error":{"type":"RegionError","message":'
    b'"The latest version of this model is only available hosted in China '
    b'and requires explicit opt in: https://opencode.ai/workspace/wrk_test/go"}}'
)
_REGION_BODY_NO_URL = (
    b'{"type":"error","error":{"type":"RegionError","message":'
    b'"The latest version of this model is only available hosted in China '
    b'and requires explicit opt in"}}'
)
_REGION_FREE_TEXT_NO_TYPE = (
    b'{"error":{"message":"hosted in China and requires explicit opt in: '
    b'https://opencode.ai/workspace/wrk_test/go"}}'
)


def test_region_error_classifies_on_structured_type_not_free_text():
    from agentcore.llm.errors import (
        is_auth_rejection,
        is_balance_exhausted,
        is_opencode_region_error,
        opencode_structured_error_type,
        opencode_typed_client_error,
    )

    assert opencode_structured_error_type(_REGION_BODY) == "regionerror"
    assert is_opencode_region_error(_REGION_BODY) is True
    assert is_opencode_region_error(_REGION_BODY_NO_URL) is True
    assert is_opencode_region_error(_CREDITS_BODY) is False
    assert is_opencode_region_error(_REGION_FREE_TEXT_NO_TYPE) is False
    assert is_opencode_region_error(None) is False
    assert is_balance_exhausted(_REGION_BODY) is False
    assert is_auth_rejection(401, _REGION_BODY) is False
    assert is_auth_rejection(403, _REGION_BODY) is False
    # Unknown type: extension slot returns the value, but no product copy.
    unknown = b'{"error":{"type":"SomethingNewError","message":"nope"}}'
    assert opencode_structured_error_type(unknown) == "somethingnewerror"
    assert (
        opencode_typed_client_error(unknown, status=400, platform=False)
        is None
    )


def test_region_product_message_forks_by_billing_leaf():
    from agentcore.llm.errors import (
        OPENCODE_REGION_BYOK_MESSAGE,
        OPENCODE_REGION_PLATFORM_MESSAGE,
        opencode_region_product_message,
    )

    byok = opencode_region_product_message(_REGION_BODY, platform=False)
    assert byok.startswith(OPENCODE_REGION_BYOK_MESSAGE)
    assert _REGION_WS in byok
    assert "hosted in China" not in byok
    platform = opencode_region_product_message(_REGION_BODY, platform=True)
    assert platform == OPENCODE_REGION_PLATFORM_MESSAGE
    assert "opencode.ai/workspace" not in platform
    assert "wrk_" not in platform
    no_url = opencode_region_product_message(_REGION_BODY_NO_URL, platform=False)
    assert no_url == OPENCODE_REGION_BYOK_MESSAGE
    assert "opencode.ai/workspace" not in no_url


@pytest.mark.parametrize("code", [400, 401, 403])
async def test_go_region_error_is_opt_in_copy_not_balance_or_auth(code):
    from agentcore.llm.errors import OPENCODE_REGION_BYOK_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(code, content=_REGION_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert ei.value.message.startswith(OPENCODE_REGION_BYOK_MESSAGE)
        assert _REGION_WS in ei.value.message
        assert "余额" not in ei.value.message
        assert "请充值" not in ei.value.message
        assert "API Key 无效" not in ei.value.message
        assert "hosted in China" not in ei.value.message
        assert ei.value.details.get("upstream_status") == code
    finally:
        await provider.close()


@pytest.mark.parametrize("code", [400, 401, 403])
async def test_platform_region_error_never_leaks_workspace(code):
    from agentcore.llm.errors import OPENCODE_REGION_PLATFORM_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(code, content=_REGION_BODY),
        name="platform",
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert ei.value.message == OPENCODE_REGION_PLATFORM_MESSAGE
        assert "opencode.ai/workspace" not in ei.value.message
        assert "wrk_" not in ei.value.message
        assert "余额" not in ei.value.message
        assert "请充值" not in ei.value.message
        assert "API Key 无效" not in ei.value.message
        assert "hosted in China" not in ei.value.message
    finally:
        await provider.close()


async def test_platform_region_error_leak_safe_even_off_go_url():
    """Platform must never echo the operator workspace URL, whatever the base_url."""
    from agentcore.llm.errors import OPENCODE_REGION_PLATFORM_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_REGION_BODY),
        name="platform",
        base_url="http://example.invalid/v1",
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert ei.value.message == OPENCODE_REGION_PLATFORM_MESSAGE
        assert "opencode.ai/workspace" not in ei.value.message
        assert "wrk_" not in ei.value.message
    finally:
        await provider.close()


async def test_region_error_on_unknown_endpoint_still_uses_type_table():
    """OpenCode class names are unique — RegionError copy is not Go-URL-gated."""
    from agentcore.llm.errors import OPENCODE_REGION_BYOK_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(403, content=_REGION_BODY),
        base_url="http://example.invalid/v1",
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert ei.value.message.startswith(OPENCODE_REGION_BYOK_MESSAGE)
        assert "API Key 无效" not in ei.value.message
        assert "余额不足" not in ei.value.message
    finally:
        await provider.close()


@pytest.mark.parametrize("code", [400, 401])
async def test_probe_go_region_error_is_opt_in_copy(code):
    from agentcore.llm.errors import OPENCODE_REGION_BYOK_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(code, content=_REGION_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.probe(model=DEEPSEEK_V4_FLASH)
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert ei.value.message.startswith(OPENCODE_REGION_BYOK_MESSAGE)
        assert _REGION_WS in ei.value.message
    finally:
        await provider.close()


async def test_list_models_go_region_error_is_not_auth():
    from agentcore.llm.errors import OPENCODE_REGION_BYOK_MESSAGE

    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_REGION_BODY),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.list_models()
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert ei.value.message.startswith(OPENCODE_REGION_BYOK_MESSAGE)
        assert _REGION_WS in ei.value.message
    finally:
        await provider.close()


async def test_probe_and_list_models_platform_region_never_leaks_workspace():
    from agentcore.llm.errors import OPENCODE_REGION_PLATFORM_MESSAGE

    async def _assert_platform(factory):
        provider = await _mock_provider(
            lambda request: httpx.Response(401, content=_REGION_BODY),
            name="platform",
            base_url=_GO_URL,
        )
        try:
            with pytest.raises(LLMError) as ei:
                await factory(provider)
            assert ei.value.message == OPENCODE_REGION_PLATFORM_MESSAGE
            assert "opencode.ai/workspace" not in ei.value.message
            assert "wrk_" not in ei.value.message
        finally:
            await provider.close()

    await _assert_platform(lambda p: p.probe(model=DEEPSEEK_V4_FLASH))
    await _assert_platform(lambda p: p.list_models())


def _oc(kind: str, message: str = "x", metadata: dict | None = None) -> bytes:
    err: dict = {"type": kind, "message": message}
    if metadata is not None:
        err["metadata"] = metadata
    return json.dumps({"type": "error", "error": err}).encode()


def test_opencode_type_table_covers_proven_kinds_only():
    from agentcore.llm.errors import OPENCODE_TYPED_KINDS, opencode_structured_error_type

    proven = {
        "RegionError": "regionerror",
        "AuthError": "autherror",
        "CreditsError": "creditserror",
        "MonthlyLimitError": "monthlylimiterror",
        "UserLimitError": "userlimiterror",
        "ModelError": "modelerror",
        "RateLimitError": "ratelimiterror",
        "FreeUsageLimitError": "freeusagelimiterror",
        "GoUsageLimitError": "gousagelimiterror",
        "BlackUsageLimitError": "blackusagelimiterror",
        "error": "error",
    }
    assert set(proven.values()) == set(OPENCODE_TYPED_KINDS)
    for raw, lowered in proven.items():
        assert opencode_structured_error_type(_oc(raw)) == lowered
    # Different envelope — not nested error.type.
    router = b'{"type":"Router.Unavailable","modelID":"gpt-5.6-luna"}'
    assert opencode_structured_error_type(router) is None
    assert "router.unavailable" not in OPENCODE_TYPED_KINDS


def test_passthrough_region_403_is_not_regionerror():
    from agentcore.llm.errors import (
        is_auth_rejection,
        is_opencode_region_error,
        opencode_typed_client_error,
    )

    body = b'{"error":{"message":"This model is not available in your region"}}'
    assert is_opencode_region_error(body) is False
    assert opencode_typed_client_error(body, status=403, platform=False) is None
    assert is_auth_rejection(403, body) is False


async def test_passthrough_region_403_does_not_offer_china_opt_in():
    from agentcore.llm.errors import OPENCODE_REGION_BYOK_MESSAGE

    body = b'{"error":{"message":"This model is not available in your region"}}'
    provider = await _mock_provider(
        lambda request: httpx.Response(403, content=body),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert not isinstance(ei.value, LLMAuthError)
        assert OPENCODE_REGION_BYOK_MESSAGE not in ei.value.message
        assert "中国区" not in ei.value.message
    finally:
        await provider.close()


async def test_router_unavailable_is_not_on_the_type_table():
    body = b'{"type":"Router.Unavailable","modelID":"gpt-5.6-luna"}'
    provider = await _mock_provider(lambda request: httpx.Response(500, content=body))
    try:
        with pytest.raises(LLMUpstreamError) as ei:
            provider._raise_for_status(500, 1.0, httpx.Headers(), body=body)
        assert "中国区" not in ei.value.message
        assert "Use balance" not in ei.value.message
        assert "API Key 无效" not in ei.value.message
        assert "上游模型服务暂时不可用" in ei.value.message
    finally:
        await provider.close()


async def test_autherror_401_is_key_invalid():
    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_oc("AuthError", "invalid key")),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMAuthError) as ei:
            await provider.complete(_req())
        assert "API Key 无效" in ei.value.message
        assert "余额" not in ei.value.message
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


@pytest.mark.parametrize(
    ("kind", "byok"),
    [
        ("MonthlyLimitError", "OPENCODE_MONTHLY_LIMIT_MESSAGE"),
        ("UserLimitError", "OPENCODE_USER_LIMIT_MESSAGE"),
        ("ModelError", "OPENCODE_MODEL_UNAVAILABLE_MESSAGE"),
    ],
)
async def test_opencode_401_limit_and_model_types(kind, byok):
    from agentcore.llm import errors as errmod

    copy = getattr(errmod, byok)
    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=_oc(kind)),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert not isinstance(ei.value, LLMAuthError)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert not isinstance(ei.value, LLMRateLimitError)
        assert ei.value.message == copy
        assert "余额" not in ei.value.message
        assert "API Key 无效" not in ei.value.message
    finally:
        await provider.close()


@pytest.mark.parametrize("kind", ["MonthlyLimitError", "UserLimitError", "ModelError"])
async def test_platform_401_limit_and_model_never_leaks_workspace(kind):
    from agentcore.llm.errors import (
        OPENCODE_PLATFORM_MODEL_MESSAGE,
        OPENCODE_PLATFORM_USAGE_MESSAGE,
    )

    body = _oc(kind, metadata={"workspace": "wrk_secret", "limitName": "month"})
    provider = await _mock_provider(
        lambda request: httpx.Response(401, content=body),
        name="platform",
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        expected = (
            OPENCODE_PLATFORM_MODEL_MESSAGE
            if kind == "ModelError"
            else OPENCODE_PLATFORM_USAGE_MESSAGE
        )
        assert ei.value.message == expected
        assert "opencode.ai/workspace" not in ei.value.message
        assert "wrk_" not in ei.value.message
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


def _raise_429(provider, body: bytes, headers=None):
    provider._raise_for_status(
        429,
        1.0,
        httpx.Headers(headers or {"retry-after": "2"}),
        body=body,
    )


async def test_go_usage_limit_without_limit_name_uses_stem_copy():
    from agentcore.llm.errors import OPENCODE_GO_QUOTA_MESSAGE

    body = _oc("GoUsageLimitError", "Go usage limit")
    provider = await _mock_provider(lambda request: httpx.Response(429, content=body))
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            _raise_429(provider, body)
        assert ei.value.message == OPENCODE_GO_QUOTA_MESSAGE
    finally:
        await provider.close()


async def test_go_usage_limit_429_is_quota_copy_not_balance():
    from agentcore.llm.errors import is_balance_exhausted

    body = _oc(
        "GoUsageLimitError",
        "Go usage limit",
        metadata={"workspace": "wrk_secret", "limitName": "5h"},
    )
    assert is_balance_exhausted(body) is False
    provider = await _mock_provider(
        lambda request: httpx.Response(429, content=body),
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            _raise_429(provider, body)
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert not isinstance(ei.value, LLMAuthError)
        assert ei.value.message.startswith("OpenCode Go 订阅配额已用尽（5h）")
        assert "Use balance" in ei.value.message
        assert "wrk_" not in ei.value.message
        assert "余额不足" not in ei.value.message
    finally:
        await provider.close()


async def test_platform_go_usage_limit_429_never_leaks_workspace():
    from agentcore.llm.errors import OPENCODE_PLATFORM_USAGE_MESSAGE, is_auth_rejection

    body = _oc(
        "GoUsageLimitError",
        "Go usage limit",
        metadata={"workspace": "wrk_secret", "limitName": "5h"},
    )
    assert is_auth_rejection(401, body) is False
    provider = await _mock_provider(
        lambda request: httpx.Response(429, content=body),
        name="platform",
        base_url=_GO_URL,
    )
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            _raise_429(provider, body)
        assert ei.value.message == OPENCODE_PLATFORM_USAGE_MESSAGE
        assert "opencode.ai/workspace" not in ei.value.message
        assert "wrk_" not in ei.value.message
        assert "Use balance" not in ei.value.message
        assert "5h" not in ei.value.message
    finally:
        await provider.close()


async def test_free_usage_limit_429_is_not_balance():
    from agentcore.llm.errors import OPENCODE_FREE_USAGE_MESSAGE

    body = _oc("FreeUsageLimitError", "free usage")
    provider = await _mock_provider(
        lambda request: httpx.Response(429, content=body),
        base_url=_ZEN_URL,
    )
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            _raise_429(provider, body)
        assert ei.value.message == OPENCODE_FREE_USAGE_MESSAGE
        assert not isinstance(ei.value, LLMInsufficientBalanceError)
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


async def test_rate_limit_and_black_usage_keep_existing_429_copy():
    for kind in ("RateLimitError", "BlackUsageLimitError"):
        body = _oc(kind, "slow down")
        provider = await _mock_provider(
            lambda request, b=body: httpx.Response(429, content=b),
            base_url=_GO_URL,
        )
        try:
            with pytest.raises(LLMRateLimitError) as ei:
                _raise_429(provider, body)
            assert "上游限流" in ei.value.message
            assert "Use balance" not in ei.value.message
            assert "余额" not in ei.value.message
        finally:
            await provider.close()


async def test_opencode_500_literal_error_uses_existing_5xx_copy():
    body = _oc("error", "Internal server error")
    provider = await _mock_provider(lambda request: httpx.Response(500, content=body))
    try:
        with pytest.raises(LLMUpstreamError) as ei:
            provider._raise_for_status(500, 1.0, httpx.Headers(), body=body)
        assert "上游模型服务暂时不可用" in ei.value.message
        assert "Internal server error" not in ei.value.message
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


async def test_deepseek_402_without_opencode_type_still_asks_to_topup():
    """Non-OpenCode vendors must not pick up the CreditsError family copy."""
    provider = await _mock_provider(lambda request: httpx.Response(402))
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert "余额不足" in ei.value.message
        assert "请充值" in ei.value.message
        assert "OpenCode" not in ei.value.message
        assert "Use balance" not in ei.value.message
    finally:
        await provider.close()


async def test_bare_401_still_invalid_key():
    provider = await _mock_provider(lambda request: httpx.Response(401))
    try:
        with pytest.raises(LLMAuthError) as ei:
            await provider.complete(_req())
        assert "API Key 无效" in ei.value.message
        assert "OpenCode" not in ei.value.message
    finally:
        await provider.close()


@pytest.mark.parametrize("code", [401, 403])
async def test_complete_maps_401_403_to_auth_error(code):
    body = b'{"error":{"message":"invalid api key","type":"authentication_error","code":"invalid_api_key"}}'
    provider = await _mock_provider(lambda request: httpx.Response(code, content=body))
    try:
        with pytest.raises(LLMAuthError) as ei:
            await provider.complete(_req())
        assert "DeepSeek" not in ei.value.message
        assert "invalid api key" not in ei.value.message
        assert "设置 · 服务商" not in ei.value.message
        assert "请更新后重试" in ei.value.message
        assert ei.value.details.get("upstream_status") == code
        assert "invalid api key" in (ei.value.details.get("upstream_body_preview") or "")
    finally:
        await provider.close()


@pytest.mark.parametrize("code", [401, 403])
async def test_inference_proxy_401_maps_to_inference_token_expired(code):
    """Sidecar→cloud /inference/ base_url: JWT rejection ≠ BYOK key invalid."""
    body = b'{"error":{"message":"Invalid or expired inference token"}}'
    provider = OpenAICompatibleProvider(
        name="user",
        api_key="tok",
        base_url="http://127.0.0.1:8000/v1/inference/v1",
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000/v1/inference/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(code, content=body)),
    )
    try:
        with pytest.raises(InferenceTokenExpiredError) as ei:
            await provider.complete(_req())
        assert ei.value.code == "INFERENCE_TOKEN_EXPIRED"
        assert ei.value.retryable is True
        assert "推理凭证" in ei.value.message
        assert ei.value.details.get("upstream_status") == code
    finally:
        await provider.close()


async def test_complete_maps_key_expired_to_auth_error_with_upstream_in_preview():
    """BYOK expired: product face; upstream / CC Switch only in preview."""
    body = json.dumps(
        {
            "error": {
                "message": "This API key has expired. 请访问本站查看 CC Switch 配置教程。",
                "type": "invalid_request_error",
                "code": "key_expired",
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    provider = await _mock_provider(lambda request: httpx.Response(401, content=body))
    try:
        with pytest.raises(LLMAuthError) as ei:
            await provider.complete(_req())
        assert "CC Switch" not in ei.value.message
        assert "expired" not in ei.value.message.lower()
        assert "设置 · 服务商" not in ei.value.message
        assert "请更新后重试" in ei.value.message
        assert ei.value.details.get("upstream_status") == 401
        assert "expired" in (ei.value.details.get("upstream_body_preview") or "").lower()
    finally:
        await provider.close()


async def test_byok_auth_uses_product_copy_not_upstream_gateway_text():
    """BYOK 401 key_revoked (案 9db7bd04): no `user ` prefix / CC Switch on face."""
    body = json.dumps(
        {
            "error": {
                "message": "This API key has been revoked. 请访问本站查看 CC Switch 配置教程。",
                "type": "invalid_request_error",
                "code": "key_revoked",
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    provider = OpenAICompatibleProvider(
        name="user", api_key="k", base_url="http://example.invalid/v1"
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://example.invalid/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, content=body)),
    )
    try:
        with pytest.raises(LLMAuthError) as ei:
            await provider.complete(_req())
        assert "CC Switch" not in ei.value.message
        assert not ei.value.message.startswith("user ")
        assert "revoked" not in ei.value.message.lower()
        assert "当前模型" not in ei.value.message
        assert "服务商" in ei.value.message
        assert "设置 · 服务商" not in ei.value.message
        assert "请更新后重试" in ei.value.message
        assert ei.value.details.get("upstream_status") == 401
        assert "revoked" in (ei.value.details.get("upstream_body_preview") or "").lower()
        assert ei.value.details.get("credential_source") == "user"
    finally:
        await provider.close()


async def test_byok_timeout_uses_display_name_not_log_source(monkeypatch):
    """User-facing timeout must not leak credentials.source ``user``."""
    from agentcore.core.errors import LLMTimeoutError
    from agentcore.llm.call_fence import unwrap_provider
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.factory import build_provider

    provider = build_provider(
        LLMCredentials(
            api_key="k",
            base_url="http://example.invalid/v1",
            default_model="m",
            source="user",
            label="我的网关",
        )
    )
    leaf = unwrap_provider(provider)
    assert leaf.name == "user"
    assert leaf.display_name == "我的网关"

    async def boom(*_a, **_k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(leaf, "_can_retry_attempt", lambda *_a, **_k: False)
    leaf._client = httpx.AsyncClient(base_url="http://example.invalid/v1")
    monkeypatch.setattr(leaf._client, "post", boom)
    try:
        with pytest.raises(LLMTimeoutError) as ei:
            await leaf.complete(_req())
        assert "我的网关" in ei.value.message
        assert "user" not in ei.value.message
    finally:
        await provider.close()


def test_platform_leaf_display_name_is_fixed(monkeypatch):
    from agentcore.llm.provider.platform import PlatformProvider

    monkeypatch.setattr(
        "agentcore.llm.resolve.platform_llm_credentials",
        lambda model=None: None,
    )
    leaf = PlatformProvider()
    assert leaf.name == "platform"
    assert leaf.display_name == "平台"
    with pytest.raises(LLMError) as ei:
        leaf._leaf_for("")
    assert "平台模型" in ei.value.message
    assert "platform" not in ei.value.message


async def test_platform_auth_uses_product_copy_not_upstream_gateway_text():
    """Platform 401 must not echo upstream gateway help (e.g. CC Switch)."""
    body = json.dumps(
        {
            "error": {
                "message": "This API key has been revoked. 请访问本站查看 CC Switch 配置教程。",
                "type": "invalid_request_error",
                "code": "key_revoked",
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    provider = OpenAICompatibleProvider(
        name="platform", api_key="k", base_url="http://example.invalid/v1"
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://example.invalid/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, content=body)),
    )
    try:
        with pytest.raises(LLMAuthError) as ei:
            await provider.complete(_req())
        assert "CC Switch" not in ei.value.message
        assert "platform " not in ei.value.message
        assert "平台模型暂时不可用" in ei.value.message
        assert ei.value.details.get("upstream_status") == 401
        assert "revoked" in (ei.value.details.get("upstream_body_preview") or "").lower()
    finally:
        await provider.close()


async def test_llm_auth_error_platform_default_message():
    err = LLMAuthError(provider_name="platform")
    assert "平台模型暂时不可用" in err.message
    assert "platform" not in err.message
    assert "CC Switch" not in err.message
    assert "设置 · 服务商" not in err.message


async def test_complete_maps_model_not_allowed_403_to_client_error_not_auth():
    body = json.dumps(
        {
            "error": {
                "message": "模型 ID 配置不正确。",
                "type": "invalid_request_error",
                "code": "model_not_allowed",
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    provider = await _mock_provider(lambda request: httpx.Response(403, content=body))
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert not isinstance(ei.value, LLMAuthError)
        assert "模型 ID" in ei.value.message
        assert ei.value.details.get("upstream_status") == 403
    finally:
        await provider.close()


async def test_complete_maps_500_with_context():
    provider = await _mock_provider(
        lambda request: httpx.Response(500, content=b'{"error":"boom"}')
    )
    try:
        with pytest.raises(LLMUpstreamError) as ei:
            await provider.complete(_req())
        assert ei.value.details.get("upstream_status") == 500
        assert "boom" in (ei.value.details.get("upstream_body_preview") or "")
    finally:
        await provider.close()


async def test_complete_maps_400_to_llm_error_with_upstream_body():
    body = (
        b'{"error":{"message":"The `reasoning_content` in the thinking mode '
        b'must be passed back to the API."}}'
    )
    provider = await _mock_provider(lambda request: httpx.Response(400, content=body))
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert ei.value.retryable is False
        assert "reasoning_content" in ei.value.message
        assert ei.value.details.get("upstream_status") == 400
        assert "reasoning_content" in (ei.value.details.get("upstream_body_preview") or "")
    finally:
        await provider.close()


async def test_stream_maps_400_to_llm_error():
    body = b'{"error":{"message":"bad request"}}'
    provider = await _mock_provider(lambda request: httpx.Response(400, content=body))
    try:
        with pytest.raises(LLMError) as ei:
            async for _ in provider.stream(_req()):
                pass
        assert "bad request" in ei.value.message
    finally:
        await provider.close()


def test_build_payload_echoes_reasoning_content_for_tool_turns():
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[
            LLMMessage(role="user", content="go"),
            LLMMessage(
                role="assistant",
                content="",
                reasoning_content="chain",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        function=ToolCallFunction(name="search", arguments="{}"),
                    )
                ],
            ),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="tc2",
                        function=ToolCallFunction(name="read", arguments="{}"),
                    )
                ],
            ),
        ],
        model=DEEPSEEK_V4_FLASH,
    )
    payload = provider._build_payload(req, stream=True)
    assistant_msgs = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert assistant_msgs[0]["reasoning_content"] == "chain"
    assert assistant_msgs[1]["reasoning_content"] == ""


def test_build_payload_clean_openai_for_non_deepseek_tool_turns():
    """Non-DeepSeek models must not leak DeepSeek reasoning_content; empty
    assistant tool turns still carry content:\"\" (clean OpenAI form)."""
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[
            LLMMessage(role="user", content="go"),
            LLMMessage(
                role="assistant",
                content=None,
                reasoning_content="should not be sent",
                tool_calls=[
                    ToolCall(
                        id="tooluse_abc",
                        function=ToolCallFunction(name="consult", arguments='{"name":"x"}'),
                    )
                ],
            ),
            LLMMessage(role="tool", content="skill body", tool_call_id="tooluse_abc"),
        ],
        model="gpt-4o",
    )
    payload = provider._build_payload(req, stream=True)
    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant["content"] == ""
    assert "reasoning_content" not in assistant
    assert assistant["tool_calls"][0]["id"] == "tooluse_abc"


def test_build_payload_disables_thinking_for_deepseek_v4_background():
    """Title/memory one-shots must send thinking.disabled — otherwise V4's default
    thinking eats a tight max_tokens budget and the sidebar falls back to raw input."""
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        max_tokens=64,
        thinking=False,
        scenario="title",
    )
    payload = provider._build_payload(req, stream=False)
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload

    # Non-DeepSeek / non-Hy3 models must not get the thinking-type field.
    other = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="gpt-4o",
        thinking=False,
        scenario="title",
    )
    assert "thinking" not in provider._build_payload(other, stream=False)

    # None = explicit enabled (do not omit: OpenCode Go treats omit as off).
    default = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
    )
    default_payload = provider._build_payload(default, stream=False)
    assert default_payload["thinking"] == {"type": "enabled"}
    assert default_payload["reasoning_effort"] == "high"


@pytest.mark.parametrize(
    "base_url",
    (
        "https://api.deepseek.com",
        "https://opencode.ai/zen/go/v1",
        "https://opencode.ai/zen/v1",
    ),
)
def test_build_payload_sends_thinking_enabled_on_v4_chat_across_gateways(base_url: str):
    """Chat/CEO path must write thinking.enabled — OpenCode Go omit = off."""
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url=base_url)
    chat = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        thinking=True,
        scenario="chat",
    )
    chat_payload = provider._build_payload(chat, stream=True)
    assert chat_payload["thinking"] == {"type": "enabled"}
    assert chat_payload["reasoning_effort"] == "high"
    omitted = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
    )
    omitted_payload = provider._build_payload(omitted, stream=True)
    assert omitted_payload["thinking"] == {"type": "enabled"}
    assert omitted_payload["reasoning_effort"] == "high"


def test_reasoning_text_reads_openai_aliases():
    assert _reasoning_text({"reasoning_content": "官方"}) == "官方"
    assert _reasoning_text({"reasoning": "别名"}) == "别名"
    assert _reasoning_text({"reasoning_text": "第三键"}) == "第三键"
    assert _reasoning_text({"reasoning_content": "", "reasoning": "回落"}) == "回落"
    assert _reasoning_text({"reasoning": 1}) is None
    assert _reasoning_text({}) is None


@pytest.mark.parametrize("model", ["hy3", "hy3-preview", "tokenhub/hy3", "tokenhub/hy3-preview"])
def test_build_payload_echoes_reasoning_content_for_hy3_tool_turns(model: str):
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[
            LLMMessage(role="user", content="go"),
            LLMMessage(
                role="assistant",
                content="",
                reasoning_content="chain",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        function=ToolCallFunction(name="search", arguments="{}"),
                    )
                ],
            ),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="tc2",
                        function=ToolCallFunction(name="read", arguments="{}"),
                    )
                ],
            ),
        ],
        model=model,
    )
    payload = provider._build_payload(req, stream=True)
    assistant_msgs = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert assistant_msgs[0]["reasoning_content"] == "chain"
    assert assistant_msgs[1]["reasoning_content"] == ""


@pytest.mark.parametrize("model", ["hy3", "hy3-preview"])
def test_build_payload_thinking_switch_for_hy3(model: str):
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    disabled = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        thinking=False,
        scenario="title",
    )
    assert provider._build_payload(disabled, stream=False)["thinking"] == {"type": "disabled"}

    enabled = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        thinking=True,
    )
    enabled_payload = provider._build_payload(enabled, stream=False)
    assert enabled_payload["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in enabled_payload

    default = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
    )
    default_payload = provider._build_payload(default, stream=False)
    assert default_payload["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in default_payload


def test_build_payload_hy_siblings_do_not_get_hy3_dialect():
    """Other TokenHub hy-* ids must stay clean OpenAI (no reasoning_content / thinking)."""
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[
            LLMMessage(
                role="assistant",
                content=None,
                reasoning_content="should not leak",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        function=ToolCallFunction(name="search", arguments="{}"),
                    )
                ],
            ),
        ],
        model="hy-chat",
        thinking=False,
    )
    payload = provider._build_payload(req, stream=False)
    assistant = payload["messages"][0]
    assert "reasoning_content" not in assistant
    assert "thinking" not in payload


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-5",
        "platform/claude-opus-5",
        "claude-opus-4-7",
        "claude-opus-4.8",
        "anthropic/claude-fable-5",
        "claude-mythos-5",
    ],
)
def test_build_payload_omits_temperature_for_restricted_models(model: str):
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        temperature=0.7,
    )
    payload = provider._build_payload(req, stream=False)
    assert "temperature" not in payload
    assert payload["model"] == model


@pytest.mark.parametrize("model", ["kimi-k3", "kimi-k2.5", "kimi-k2.6"])
def test_build_payload_omits_temperature_for_kimi_leaf(model: str):
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        temperature=0.7,
    )
    payload = provider._build_payload(req, stream=False)
    assert "temperature" not in payload


def test_build_payload_keeps_temperature_for_ordinary_models():
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    for model in (
        "gpt-4o",
        "deepseek-v4-flash",
        "claude-opus-4-20250514",
        "hy3",
        "moonshot-v1-128k",
        "k3",
    ):
        req = LLMRequest(
            messages=[LLMMessage(role="user", content="hi")],
            model=model,
            temperature=0.3,
        )
        payload = provider._build_payload(req, stream=False)
        assert payload["temperature"] == 0.3, model


def test_balance_and_auth_errors_are_not_retryable():
    assert LLMInsufficientBalanceError().retryable is False
    assert LLMAuthError().retryable is False


def test_inference_token_expired_is_retryable():
    err = InferenceTokenExpiredError()
    assert err.retryable is True
    assert err.code == "INFERENCE_TOKEN_EXPIRED"
    assert "推理凭证" in err.message


def test_upstream_error_is_retryable():
    err = LLMUpstreamError("test", upstream_status=502)
    assert err.retryable is True


def test_client_error_message_404_model_not_base_url():
    from agentcore.llm.errors import client_error_message

    body = b'{"error":{"message":"Not found the model x","code":"resource_not_found"}}'
    msg = client_error_message("DeepSeek", 404, body)
    assert "Not found the model x" in msg
    assert "base_url" not in msg
    assert "默认模型" in msg


def test_client_error_message_404_empty_body_blames_base_url():
    from agentcore.llm.errors import client_error_message

    msg = client_error_message("DeepSeek", 404, b"")
    assert "base_url" in msg
    assert "默认模型" not in msg


def test_client_error_message_404_path_with_unrelated_message():
    from agentcore.llm.errors import client_error_message

    body = b'{"error":{"message":"No route matched"}}'
    msg = client_error_message("网关", 404, body)
    assert "No route matched" in msg
    # Unrelated 404 with a body: surface upstream text, do not invent base_url blame
    # unless body is empty (path-style guess).
    assert msg.startswith("网关 ")


def test_client_error_message_temperature_deprecated_product_copy():
    from agentcore.llm.errors import client_error_message, is_temperature_deprecated

    product = "平台 当前模型不接受 temperature 参数，请重试或更换模型"
    anthropic_body = (
        b'{"error":{"message":"user `temperature` is deprecated for this model. '
        b'(request id: req_abc)"}}'
    )
    assert is_temperature_deprecated(anthropic_body) is True
    msg = client_error_message("平台", 400, anthropic_body)
    assert msg == product
    assert "request id" not in msg
    assert "`temperature` is deprecated" not in msg

    moonshot_body = b'{"error":{"message":"invalid temperature: only 1 is allowed"}}'
    assert is_temperature_deprecated(moonshot_body) is True
    msg = client_error_message("平台", 400, moonshot_body)
    assert msg == product
    assert "invalid temperature" not in msg
    assert "only 1 is allowed" not in msg

    unrelated = b'{"error":{"message":"max_tokens must be positive"}}'
    assert is_temperature_deprecated(unrelated) is False


def test_client_error_message_context_overflow_product_copy_aa519_shape():
    """⑦A: aa519-style upstream wall must not reach the user bubble."""
    from agentcore.llm.errors import CONTEXT_OVERFLOW_PRODUCT, client_error_message

    body = (
        b'{"error":{"message":"user This model\'s maximum context length is 1048576 '
        b'tokens. However, you requested 1108450 tokens in the messages, '
        b'Please reduce the length of the messages.","code":"invalid_request_error"}}'
    )
    msg = client_error_message("DeepSeek", 400, body)
    assert msg == CONTEXT_OVERFLOW_PRODUCT
    assert "maximum context length" not in msg
    assert "1108450" not in msg
    assert "1048576" not in msg


def test_client_error_message_context_overflow_by_code():
    from agentcore.llm.errors import (
        CONTEXT_OVERFLOW_PRODUCT,
        client_error_message,
        is_context_overflow,
    )

    body = b'{"error":{"message":"too long","code":"context_length_exceeded"}}'
    assert is_context_overflow(body) is True
    msg = client_error_message("平台", 400, body)
    assert msg == CONTEXT_OVERFLOW_PRODUCT
    assert "too long" not in msg


def test_client_error_message_413_is_context_overflow_product_copy():
    from agentcore.llm.errors import CONTEXT_OVERFLOW_PRODUCT, client_error_message

    msg = client_error_message("平台", 413, b"")
    assert msg == CONTEXT_OVERFLOW_PRODUCT


def test_client_error_message_400_unrelated_still_passthrough():
    """Unclassified 400 still echoes the upstream message (not rewritten)."""
    from agentcore.llm.errors import client_error_message

    body = b'{"error":{"message":"max_tokens too large"}}'
    msg = client_error_message("平台", 400, body)
    assert msg == "平台 max_tokens too large"


def _go_tool_schema_body(
    *, reason: str | None = "tool_count_limit", json_code: str | None = None
) -> bytes:
    reason_bit = f" ({reason})" if reason else ""
    msg = (
        "Error from provider (Console Go): Upstream request failed: "
        f"[unsupported_tool_schema] The tool schema is not supported{reason_bit}."
    )
    err: dict = {"message": msg}
    if json_code is not None:
        err["code"] = json_code
    return json.dumps({"error": err}).encode()


def _assert_honest_tool_schema_copy(msg: str) -> None:
    assert "上游服务端拒绝了本次请求" in msg
    assert "入口即被拒绝" in msg
    assert "未消耗 token" in msg
    assert "未产生费用" in msg
    assert "自动" not in msg
    assert "裁剪" not in msg
    assert "重试" not in msg
    assert "unsupported_tool_schema" not in msg
    assert "tool_count_limit" not in msg
    assert "unsupported_keyword" not in msg
    assert "The tool schema is not supported" not in msg


def test_client_error_message_400_unknown_bracket_code_still_passthrough():
    """Unknown vendor bracket codes keep the unclassified 400 passthrough."""
    from agentcore.llm.errors import client_error_message

    body = (
        b'{"error":{"message":"Upstream request failed: [some_other_code] nope."}}'
    )
    msg = client_error_message("平台", 400, body)
    assert msg == "平台 Upstream request failed: [some_other_code] nope."


def test_client_error_message_unsupported_tool_schema_splits_subreasons():
    from agentcore.llm.errors import (
        UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE,
        UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE,
        UNSUPPORTED_TOOL_SCHEMA_MESSAGE,
        client_error_message,
        is_unsupported_tool_schema,
    )

    count_body = _go_tool_schema_body(reason="tool_count_limit")
    assert is_unsupported_tool_schema(count_body) is True
    count_msg = client_error_message("平台", 400, count_body)
    assert count_msg == UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE
    assert "工具数量或规模" in count_msg
    _assert_honest_tool_schema_copy(count_msg)

    keyword_body = _go_tool_schema_body(reason="unsupported_keyword")
    keyword_msg = client_error_message("OpenCode", 400, keyword_body)
    assert keyword_msg == UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE
    assert "不支持的字段" in keyword_msg
    _assert_honest_tool_schema_copy(keyword_msg)

    generic_body = _go_tool_schema_body(reason=None)
    generic_msg = client_error_message("平台", 400, generic_body)
    assert generic_msg == UNSUPPORTED_TOOL_SCHEMA_MESSAGE
    _assert_honest_tool_schema_copy(generic_msg)


def test_vendor_code_prefers_message_brackets_over_generic_json_code():
    from agentcore.llm.errors import (
        UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE,
        client_error_message,
        is_unsupported_tool_schema,
    )

    body = _go_tool_schema_body(
        reason="unsupported_keyword", json_code="invalid_request_error"
    )
    assert is_unsupported_tool_schema(body) is True
    assert (
        client_error_message("平台", 400, body)
        == UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE
    )


def test_vendor_code_falls_back_to_json_error_code_without_brackets():
    from agentcore.llm.errors import (
        UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE,
        client_error_message,
        is_unsupported_tool_schema,
    )

    body = json.dumps(
        {
            "error": {
                "code": "unsupported_tool_schema",
                "message": "The tool schema is not supported (tool_count_limit).",
            }
        }
    ).encode()
    assert is_unsupported_tool_schema(body) is True
    assert (
        client_error_message("平台", 400, body)
        == UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE
    )


def _tool_schema_req(*, n_tools: int = 3, scenario: str = "agent") -> LLMRequest:
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for i in range(n_tools)
    ]
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        tools=tools,
        scenario=scenario,
    )


async def test_complete_maps_opencode_tool_schema_400_to_zh_and_locator_context():
    from agentcore.llm.errors import (
        UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE,
        error_context_from,
    )
    from agentcore.runtime.events.payloads.chat import ErrorPayload

    body = _go_tool_schema_body(reason="tool_count_limit")
    provider = await _mock_provider(lambda request: httpx.Response(400, content=body))
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_tool_schema_req(n_tools=12, scenario="agent"))
        err = ei.value
        assert err.retryable is False
        assert err.message == UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE
        _assert_honest_tool_schema_copy(err.message)
        ctx = error_context_from(err)
        assert ctx is not None
        assert ctx.get("upstream_status") == 400
        assert "unsupported_tool_schema" in (ctx.get("upstream_body_preview") or "")
        assert ctx.get("vendor_code") == "unsupported_tool_schema"
        assert ctx.get("model") == DEEPSEEK_V4_FLASH
        assert ctx.get("profile") == "agent"
        assert ctx.get("tool_count") == 12
        assert "credential_source" not in ctx
        assert "platform_credential_id" not in ctx
        assert "credential_id" not in ctx
        ErrorPayload.model_validate(
            {"code": err.code, "message": err.message, "context": ctx}
        )
    finally:
        await provider.close()


async def test_stream_maps_opencode_tool_schema_keyword_400_to_zh():
    from agentcore.llm.errors import UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE

    body = _go_tool_schema_body(reason="unsupported_keyword")
    provider = await _mock_provider(lambda request: httpx.Response(400, content=body))
    try:
        with pytest.raises(LLMError) as ei:
            async for _ in provider.stream(_tool_schema_req(n_tools=2, scenario="chat")):
                pass
        assert ei.value.message == UNSUPPORTED_TOOL_SCHEMA_KEYWORD_MESSAGE
        assert ei.value.details.get("vendor_code") == "unsupported_tool_schema"
        assert ei.value.details.get("profile") == "chat"
        assert ei.value.details.get("tool_count") == 2
    finally:
        await provider.close()


def test_inference_hop_preserves_tool_schema_locator_without_credentials():
    from agentcore.llm.errors import (
        UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE,
        error_context_from,
    )

    envelope = json.dumps(
        {
            "error": {
                "code": "LLM_ERROR",
                "message": UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE,
                "context": {
                    "upstream_status": 400,
                    "upstream_body_preview": "[unsupported_tool_schema] (tool_count_limit)",
                    "vendor_code": "unsupported_tool_schema",
                    "model": DEEPSEEK_V4_FLASH,
                    "profile": "agent",
                    "tool_count": 12,
                    "credential_source": "platform",
                    "platform_credential_id": "go-1",
                },
            }
        },
        ensure_ascii=False,
    ).encode()
    leaf = OpenAICompatibleProvider(
        name="user",
        api_key="tok",
        base_url="http://127.0.0.1:8000/v1/inference/v1",
    )
    with pytest.raises(LLMUpstreamError) as ei:
        leaf._raise_for_status(502, 1.0, {}, body=envelope, attempt=0)
    err = ei.value
    assert err.message == UNSUPPORTED_TOOL_SCHEMA_COUNT_MESSAGE
    ctx = error_context_from(err) or {}
    assert ctx.get("vendor_code") == "unsupported_tool_schema"
    assert ctx.get("model") == DEEPSEEK_V4_FLASH
    assert ctx.get("profile") == "agent"
    assert ctx.get("tool_count") == 12
    assert ctx.get("upstream_status") == 400
    assert "credential_source" not in ctx
    assert "platform_credential_id" not in ctx
    assert "go-1" not in str(ctx)


async def test_probe_maps_dns_failure_to_public_endpoint_copy():
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno -2] Name or service not known")

    provider = await _mock_provider(_handler)
    with pytest.raises(LLMError) as ei:
        await provider.probe(model=DEEPSEEK_V4_FLASH)
    msg = str(ei.value)
    assert "域名无法解析" in msg
    assert "公网可达" in msg
    assert "Errno -2" not in msg


async def test_list_models_maps_dns_failure_to_public_endpoint_copy():
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno -2] Name or service not known")

    provider = await _mock_provider(_handler)
    with pytest.raises(LLMError) as ei:
        await provider.list_models()
    msg = str(ei.value)
    assert "域名无法解析" in msg
    assert "公网可达" in msg


async def test_probe_maps_generic_connect_failure_without_raw_errno():
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    provider = await _mock_provider(_handler)
    with pytest.raises(LLMError) as ei:
        await provider.probe(model=DEEPSEEK_V4_FLASH)
    msg = str(ei.value)
    assert "端点不可达" in msg
    assert "公网访问" in msg
    assert "Connection refused" not in msg
