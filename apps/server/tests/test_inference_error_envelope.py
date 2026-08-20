"""Sidecar→cloud /inference/ error contract: the envelope, not the HTTP status.

The proxy flattens its typed errors onto 402 / 429 / 502, so a leaf that classifies
by status mistranslates three faults at once: quota exhaustion → vendor throttling
(retried for ~a minute), missing BYOK key → empty wallet (充值 CTA), and any code at
all → ``LLM_ERROR`` on the 502 relay (no turn-level auth latch, no client CTA).
The direct-to-vendor hop must keep its status-based classification untouched.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agentcore.core.errors import (
    InferenceTokenExpiredError,
    LLMAuthError,
    LLMInsufficientBalanceError,
    LLMKeyRequiredError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    OurServiceUnavailableError,
)
from agentcore.llm.call_fence import ObservingLLMProvider
from agentcore.llm.errors import error_context_from, inference_envelope_error
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.llm.turn_auth_dead import (
    bind_turn_auth_dead,
    is_latchable_llm_death,
    is_turn_auth_dead,
    reset_turn_auth_dead,
)

_INFERENCE_BASE = "http://127.0.0.1:8000/v1/inference/v1"
_VENDOR_BASE = "http://example.invalid/v1"

# Verbatim copy the quota gate raises (conversation/quota.py, 成本配额 F6): the
# 「接入自己的 key」exit is the CTA, so it must survive the hop word for word.
_QUOTA_MESSAGE = (
    "本月额度已用完（约 ¥12.00 / ¥12.00），额度重置后可继续；"
    "测试需要可联系管理员提额，或接入自己的 key 继续。"
)
_KEY_MISSING_MESSAGE = "请先接入自己的 API Key，再发起对话。"
_KEY_INVALID_MESSAGE = "我的网关 API Key 无效或无权限，请更新后重试。"


def _envelope(code: str, message: str, context: dict | None = None) -> bytes:
    error: dict = {"code": code, "message": message}
    if context:
        error["context"] = context
    return json.dumps({"error": error}, ensure_ascii=False).encode()


def _req() -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")], model="test-model")


def _leaf(base_url: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(name="user", api_key="tok", base_url=base_url)


async def _mock_leaf(handler, *, base_url: str = _INFERENCE_BASE) -> OpenAICompatibleProvider:
    provider = _leaf(base_url)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=base_url, transport=httpx.MockTransport(handler)
    )
    return provider


# ── 1. Quota exhausted is not upstream throttling ─────────────────────────────


def test_quota_envelope_is_not_a_retryable_rate_limit():
    with pytest.raises(LLMQuotaExceededError) as ei:
        _leaf(_INFERENCE_BASE)._raise_for_status(
            429, 1.0, {}, body=_envelope("QUOTA_EXCEEDED", _QUOTA_MESSAGE), attempt=0
        )
    err = ei.value
    assert not isinstance(err, LLMRateLimitError)
    assert err.code == "QUOTA_EXCEEDED"
    assert err.retryable is False
    # CTA: the gate's own copy reaches the user — never「上游限流…请稍后再试」.
    assert err.message == _QUOTA_MESSAGE
    assert "上游限流" not in err.message


async def test_quota_exhausted_fails_on_the_first_call_without_burning_retries():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, content=_envelope("QUOTA_EXCEEDED", _QUOTA_MESSAGE))

    provider = await _mock_leaf(handler)
    try:
        with pytest.raises(LLMQuotaExceededError):
            await provider.complete(_req())
        # Status-keyed classification would spend 6 attempts / ~62s of backoff here.
        assert calls == 1
    finally:
        await provider.close()


async def test_quota_exhausted_stops_a_streamed_turn_immediately():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, content=_envelope("QUOTA_EXCEEDED", _QUOTA_MESSAGE))

    provider = await _mock_leaf(handler)
    try:
        with pytest.raises(LLMQuotaExceededError):
            async for _ in provider.stream(_req()):
                pass
        assert calls == 1
    finally:
        await provider.close()


def test_vendor_429_still_maps_to_a_retryable_rate_limit():
    """Direct hop: the vendor's status stays the only signal (Retry-After honoured)."""
    with pytest.raises(LLMRateLimitError) as ei:
        _leaf(_VENDOR_BASE)._raise_for_status(
            429, 1.0, {"retry-after": "7"}, body=None, attempt=0
        )
    assert ei.value.retryable is True
    assert ei.value.retry_after == 7


def test_vendor_hop_ignores_a_lookalike_envelope():
    """A vendor echoing our code shape must not reach our typed leaf errors."""
    with pytest.raises(LLMRateLimitError) as ei:
        _leaf(_VENDOR_BASE)._raise_for_status(
            429, 1.0, {}, body=_envelope("QUOTA_EXCEEDED", _QUOTA_MESSAGE), attempt=0
        )
    assert ei.value.retryable is True


# ── 2. Missing key is not an empty wallet ─────────────────────────────────────


def test_key_required_envelope_is_not_insufficient_balance():
    with pytest.raises(LLMKeyRequiredError) as ei:
        _leaf(_INFERENCE_BASE)._raise_for_status(
            402, 1.0, {}, body=_envelope("LLM_KEY_REQUIRED", _KEY_MISSING_MESSAGE), attempt=0
        )
    err = ei.value
    assert not isinstance(err, LLMInsufficientBalanceError)
    assert err.code == "LLM_KEY_REQUIRED"
    assert err.retryable is False
    assert err.message == _KEY_MISSING_MESSAGE
    assert "充值" not in err.message
    assert "余额" not in err.message
    # No account exists yet, so the turn must not short-circuit as「余额不足」.
    assert is_latchable_llm_death(err) is False


async def test_key_required_does_not_retry_and_keeps_the_key_config_cta():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(402, content=_envelope("LLM_KEY_REQUIRED", _KEY_MISSING_MESSAGE))

    provider = await _mock_leaf(handler)
    try:
        with pytest.raises(LLMKeyRequiredError) as ei:
            await provider.complete(_req())
        assert ei.value.code == "LLM_KEY_REQUIRED"
        assert calls == 1
    finally:
        await provider.close()


async def test_vendor_402_still_maps_to_insufficient_balance():
    """Direct hop: a bare vendor 402 keeps meaning「key 有效但余额不足」."""
    provider = await _mock_leaf(lambda request: httpx.Response(402), base_url=_VENDOR_BASE)
    try:
        with pytest.raises(LLMInsufficientBalanceError) as ei:
            await provider.complete(_req())
        assert "余额不足" in ei.value.message
    finally:
        await provider.close()


# ── 3. The code survives the 502 relay (turn-level auth latch + client CTA) ───


def _key_invalid_body() -> bytes:
    """What the proxy answers when the cloud leaf's BYOK key is rejected upstream."""
    return _envelope(
        "LLM_KEY_INVALID",
        _KEY_INVALID_MESSAGE,
        {
            "upstream_status": 401,
            "upstream_body_preview": '{"error":{"code":"invalid_api_key"}}',
            "retry_attempts": 0,
            "credential_source": "user",
        },
    )


def test_key_invalid_code_survives_the_proxy_502():
    with pytest.raises(LLMAuthError) as ei:
        _leaf(_INFERENCE_BASE)._raise_for_status(
            502,
            1.0,
            {"x-upstream-retried": "3"},
            body=_key_invalid_body(),
            attempt=0,
        )
    err = ei.value
    assert err.code == "LLM_KEY_INVALID"
    assert err.retryable is False
    assert err.message == _KEY_INVALID_MESSAGE
    # Diagnostics keep the vendor's real status, not the relay's 502.
    assert err.details.get("upstream_status") == 401
    ctx = error_context_from(err)
    assert ctx is not None
    assert ctx.get("credential_source") == "user"


def test_platform_credential_source_survives_for_the_cta_split():
    with pytest.raises(LLMAuthError) as ei:
        _leaf(_INFERENCE_BASE)._raise_for_status(
            502,
            1.0,
            {},
            body=_envelope(
                "LLM_KEY_INVALID",
                "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
                {"credential_source": "platform"},
            ),
            attempt=0,
        )
    assert ei.value.details.get("credential_source") == "platform"


async def test_key_invalid_latches_the_turn_so_the_fan_out_stops():
    """Every worker used to re-hit the same bad key and re-run its retry budget."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            502, content=_key_invalid_body(), headers={"x-upstream-retried": "3"}
        )

    leaf = await _mock_leaf(handler)
    observed = ObservingLLMProvider(leaf)
    token = bind_turn_auth_dead()
    try:
        with pytest.raises(LLMAuthError) as first:
            await observed.complete(_req())
        assert first.value.code == "LLM_KEY_INVALID"
        assert is_turn_auth_dead("user") is True

        with pytest.raises(LLMAuthError) as sibling:
            await observed.complete(_req())
        assert sibling.value.details.get("short_circuited") is True
        assert sibling.value.details.get("credential_source") == "user"
        assert calls == 1
    finally:
        reset_turn_auth_dead(token)
        await leaf.close()


def test_balance_envelope_keeps_its_code_across_the_relay():
    with pytest.raises(LLMInsufficientBalanceError) as ei:
        _leaf(_INFERENCE_BASE)._raise_for_status(
            502,
            1.0,
            {"x-upstream-retried": "3"},
            body=_envelope(
                "LLM_INSUFFICIENT_BALANCE",
                "我的网关 API Key 有效，但账户余额不足，请充值后重试。",
                {"credential_source": "user"},
            ),
            attempt=0,
        )
    assert ei.value.code == "LLM_INSUFFICIENT_BALANCE"
    assert is_latchable_llm_death(ei.value) is True


def test_relayed_rate_limit_keeps_its_code_and_retry_after():
    with pytest.raises(LLMRateLimitError) as ei:
        _leaf(_INFERENCE_BASE)._raise_for_status(
            502,
            1.0,
            {"x-upstream-retried": "3"},
            body=_envelope(
                "LLM_RATE_LIMIT",
                "上游限流，暂时无法继续本回合。请约 9 秒后再试。",
                {"retry_after": 9.0},
            ),
            attempt=0,
        )
    err = ei.value
    assert err.code == "LLM_RATE_LIMIT"
    assert err.retry_after == 9.0
    # The cloud already exhausted its own 429 chain — do not run a second one.
    assert err.retryable is False


# ── Fallbacks: codes nobody branches on stay on the existing paths ────────────


def test_unmapped_envelope_code_falls_back_to_the_our_service_face():
    with pytest.raises(OurServiceUnavailableError) as ei:
        _leaf(_INFERENCE_BASE)._raise_for_status(
            503,
            1.0,
            {},
            body=_envelope("DATABASE_UNAVAILABLE", "AgentCore 服务暂时不可用，请稍后重试"),
            attempt=0,
        )
    assert ei.value.code == "DATABASE_UNAVAILABLE"


def test_inference_jwt_401_still_maps_to_token_expired():
    """No envelope of ours (or an auth one): the JWT remint path stays in charge."""
    with pytest.raises(InferenceTokenExpiredError):
        _leaf(_INFERENCE_BASE)._raise_for_status(
            401, 1.0, {}, body=b'{"error":{"message":"Invalid or expired inference token"}}'
        )


def test_envelope_error_ignores_bodies_that_are_not_ours():
    assert inference_envelope_error(status=429, body=None) is None
    assert inference_envelope_error(status=429, body=b"<html>gateway</html>") is None
    assert (
        inference_envelope_error(
            status=401, body=b'{"error":{"code":"invalid_api_key","message":"bad key"}}'
        )
        is None
    )
    # Catalogued but nothing branches on it → status heuristics keep the case.
    assert inference_envelope_error(status=422, body=_envelope("VALIDATION_ERROR", "x")) is None
