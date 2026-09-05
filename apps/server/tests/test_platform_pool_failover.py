"""Pre-commit 429 / CreditsError / 403 RegionError swap the pool member; AuthError does not."""

from __future__ import annotations

import json

import httpx
import pytest

from agentcore.config import settings
from agentcore.core.errors import (
    LLMAuthError,
    LLMError,
    LLMInsufficientBalanceError,
    LLMQuotaExceededError,
    LLMRateLimitError,
)
from agentcore.llm.errors import OPENCODE_REGION_PLATFORM_MESSAGE, upstream_client_error
from agentcore.llm.platform_pool import PlatformPoolMember, replace_platform_pool_snapshot
from agentcore.llm.platform_pool_state import get_pool_state_store
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.llm.resolve import platform_llm_credentials

_GO = "https://opencode.ai/zen/go/v1"
_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _member(*, cred_id: str, api_key: str) -> PlatformPoolMember:
    return PlatformPoolMember(
        id=cred_id,
        label=cred_id[:4],
        api_key=api_key,
        base_url=_GO,
        subscription_day=18,
        enabled=True,
    )


def _pool() -> None:
    replace_platform_pool_snapshot(
        (
            _member(cred_id=_A, api_key="sk-a"),
            _member(cred_id=_B, api_key="sk-b"),
        )
    )


def _go_429(*, limit_name: str = "5 hour") -> bytes:
    return json.dumps(
        {
            "type": "error",
            "error": {
                "type": "GoUsageLimitError",
                "message": "Go usage limit",
                "metadata": {"limitName": limit_name},
            },
        }
    ).encode()


def _go_401() -> bytes:
    return json.dumps(
        {"type": "error", "error": {"type": "AuthError", "message": "unauthorized"}}
    ).encode()


def _go_credits() -> bytes:
    return json.dumps(
        {
            "type": "error",
            "error": {"type": "CreditsError", "message": "Insufficient balance"},
        }
    ).encode()


def _go_monthly_limit() -> bytes:
    return json.dumps(
        {
            "type": "error",
            "error": {"type": "MonthlyLimitError", "message": "monthly cap"},
        }
    ).encode()


def _go_403_region() -> bytes:
    return json.dumps(
        {
            "type": "error",
            "error": {"type": "RegionError", "message": "opt-in required"},
        }
    ).encode()


def _passthrough_403() -> bytes:
    return json.dumps(
        {"error": {"message": "This model is not available in your region"}}
    ).encode()


def _ok_body() -> dict:
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "model": DEEPSEEK_V4_FLASH,
    }


def _sse_ok(text: str = "ok") -> bytes:
    payload = json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]})
    done = json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    return f"data: {payload}\n\ndata: {done}\n\ndata: [DONE]\n".encode()


def _req(scenario: str = "chat") -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario=scenario,
    )


def _patch_client_factory(monkeypatch, handler) -> None:
    def factory(**kwargs):
        return httpx.AsyncClient(
            base_url=str(kwargs.get("base_url") or _GO),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.outbound_async_client",
        factory,
    )


async def _platform_leaf() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(name="platform", api_key="sk-a", base_url=_GO)


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "")
    monkeypatch.setattr(settings, "platform_model_credentials", "")
    monkeypatch.setattr(settings, "platform_base_url", _GO)


async def test_stream_pre_commit_429_failsover_without_sleep(monkeypatch):
    _pool()
    seen: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append(auth)
        if auth == "Bearer sk-a":
            return httpx.Response(
                429,
                headers={"retry-after": "3600"},
                content=_go_429(),
            )
        return httpx.Response(200, content=_sse_ok("from-b"))

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        chunks = [c async for c in provider.stream(_req())]
        text = "".join(c.delta_content or "" for c in chunks)
        assert "from-b" in text
        assert seen == ["Bearer sk-a", "Bearer sk-b"]
        assert sleeps == []
        assert provider._api_key == "sk-b"
    finally:
        await provider.close()


async def test_complete_pre_commit_429_failsover(monkeypatch):
    _pool()
    seen: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append(auth)
        if auth == "Bearer sk-a":
            return httpx.Response(
                429,
                headers={"retry-after": "3600"},
                content=_go_429(),
            )
        return httpx.Response(200, json=_ok_body())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        result = await provider.complete(_req("title"))
        assert result.content == "ok"
        assert seen == ["Bearer sk-a", "Bearer sk-b"]
        assert sleeps == []
    finally:
        await provider.close()


async def test_401_does_not_failover_to_the_next_key(monkeypatch):
    _pool()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(401, content=_go_401())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        with pytest.raises(LLMAuthError):
            await provider.complete(_req())
        assert seen == ["Bearer sk-a"]
        later = OpenAICompatibleProvider(name="platform", api_key="sk-a", base_url=_GO)
        try:
            # A is blocked: pick for a new leaf still starts from snapshot order,
            # but resolve skips A. This leaf is frozen on sk-a; a fresh resolve
            # would land on B — pin that this *request* never hopped.
            picked = platform_llm_credentials()
            assert picked is not None
            assert picked.api_key == "sk-b"
        finally:
            await later.close()
    finally:
        await provider.close()


async def test_complete_pre_commit_creditserror_failsover(monkeypatch):
    _pool()
    seen: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append(auth)
        if auth == "Bearer sk-a":
            return httpx.Response(401, content=_go_credits())
        return httpx.Response(200, json=_ok_body())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        result = await provider.complete(_req("title"))
        assert result.content == "ok"
        assert seen == ["Bearer sk-a", "Bearer sk-b"]
        assert sleeps == []
        exhausted = get_pool_state_store().get(_A)
        assert exhausted is not None
        assert exhausted.status == "exhausted"
        assert exhausted.source == "creditserror"
        picked = platform_llm_credentials()
        assert picked is not None
        assert picked.api_key == "sk-b"
    finally:
        await provider.close()


async def test_complete_pre_commit_monthlylimit_failsover(monkeypatch):
    _pool()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append(auth)
        if auth == "Bearer sk-a":
            return httpx.Response(401, content=_go_monthly_limit())
        return httpx.Response(200, json=_ok_body())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        result = await provider.complete(_req("title"))
        assert result.content == "ok"
        assert seen == ["Bearer sk-a", "Bearer sk-b"]
        exhausted = get_pool_state_store().get(_A)
        assert exhausted is not None
        assert exhausted.status == "exhausted"
        assert exhausted.source == "monthlylimiterror"
    finally:
        await provider.close()


async def test_creditserror_on_last_member_raises_insufficient_balance(monkeypatch):
    _pool()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=_go_credits())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        with pytest.raises(LLMInsufficientBalanceError):
            await provider.complete(_req("title"))
        assert get_pool_state_store().get(_A) is not None
        assert get_pool_state_store().get(_B) is not None
        assert platform_llm_credentials() is not None  # last-resort still returns a member
    finally:
        await provider.close()


async def test_complete_pre_commit_403_regionerror_failsover(monkeypatch):
    _pool()
    seen: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append(auth)
        if auth == "Bearer sk-a":
            return httpx.Response(403, content=_go_403_region())
        return httpx.Response(200, json=_ok_body())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        result = await provider.complete(_req("title"))
        assert result.content == "ok"
        assert seen == ["Bearer sk-a", "Bearer sk-b"]
        assert sleeps == []
        blocked = get_pool_state_store().get(_A)
        assert blocked is not None
        assert blocked.status == "blocked"
        assert blocked.source == "regionerror"
        picked = platform_llm_credentials()
        assert picked is not None
        assert picked.api_key == "sk-b"
    finally:
        await provider.close()


async def test_stream_pre_commit_403_regionerror_failsover(monkeypatch):
    _pool()
    seen: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append(auth)
        if auth == "Bearer sk-a":
            return httpx.Response(403, content=_go_403_region())
        return httpx.Response(200, content=_sse_ok("from-b"))

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        chunks = [c async for c in provider.stream(_req())]
        text = "".join(c.delta_content or "" for c in chunks)
        assert "from-b" in text
        assert seen == ["Bearer sk-a", "Bearer sk-b"]
        assert sleeps == []
        assert provider._api_key == "sk-b"
        blocked = get_pool_state_store().get(_A)
        assert blocked is not None
        assert blocked.status == "blocked"
        assert blocked.source == "regionerror"
    finally:
        await provider.close()


async def test_passthrough_403_without_regionerror_type_does_not_failover(
    monkeypatch,
):
    _pool()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(403, content=_passthrough_403())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        with pytest.raises(LLMError):
            await provider.complete(_req())
        assert seen == ["Bearer sk-a"]
        assert get_pool_state_store().get(_A) is None
    finally:
        await provider.close()


async def test_stream_post_commit_does_not_switch(monkeypatch):
    _pool()
    _patch_client_factory(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"unused"),
    )
    provider = await _platform_leaf()

    async def line_iter():
        payload = {
            "choices": [{"delta": {"content": "partial"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(payload)}"
        raise LLMRateLimitError(retry_after=3600)

    from unittest.mock import AsyncMock, MagicMock

    mock_cm = MagicMock()
    mock_cm.status_code = 200
    mock_cm.headers = {}
    mock_cm.aread = AsyncMock(return_value=b"")
    mock_cm.aiter_lines = line_iter
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    provider._client.stream = MagicMock(return_value=mock_cm)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert [c.delta_content for c in chunks if c.delta_content] == ["partial"]
        assert chunks[-1].aborted is True
        assert provider._api_key == "sk-a"
        provider._client.stream.assert_called_once()
    finally:
        await provider.close()


async def test_stream_post_commit_403_does_not_switch(monkeypatch):
    _pool()
    _patch_client_factory(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"unused"),
    )
    provider = await _platform_leaf()

    async def line_iter():
        payload = {
            "choices": [{"delta": {"content": "partial"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(payload)}"
        raise upstream_client_error(
            OPENCODE_REGION_PLATFORM_MESSAGE,
            status=403,
            body=_go_403_region(),
        )

    from unittest.mock import AsyncMock, MagicMock

    mock_cm = MagicMock()
    mock_cm.status_code = 200
    mock_cm.headers = {}
    mock_cm.aread = AsyncMock(return_value=b"")
    mock_cm.aiter_lines = line_iter
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    provider._client.stream = MagicMock(return_value=mock_cm)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert [c.delta_content for c in chunks if c.delta_content] == ["partial"]
        assert chunks[-1].aborted is True
        assert provider._api_key == "sk-a"
        provider._client.stream.assert_called_once()
    finally:
        await provider.close()


async def test_full_pool_429_is_honest_quota_cta(monkeypatch):
    _pool()
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "3600"},
            content=_go_429(limit_name="monthly"),
        )

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        with pytest.raises((LLMQuotaExceededError, LLMRateLimitError)) as ei:
            await provider.complete(_req("chat"))
        assert "自己的 API Key" in ei.value.message
        assert sleeps == []
    finally:
        await provider.close()


async def test_regionerror_on_last_schedulable_unblocks_quota_cta(monkeypatch):
    """A 403-blocked leftover must not stay the only pick while others cool."""
    from agentcore.core.errors import RETRY_AFTER_FROM_HEADER
    from agentcore.llm.platform_pool_scheduler import record_platform_rate_limit

    _pool()
    record_platform_rate_limit(
        api_key="sk-b",
        base_url=_GO,
        retry_after_seconds=3600,
        retry_after_source=RETRY_AFTER_FROM_HEADER,
        limit_name="5 hour",
    )
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=_go_403_region())

    _patch_client_factory(monkeypatch, handler)
    provider = await _platform_leaf()
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert ei.value.message == OPENCODE_REGION_PLATFORM_MESSAGE
        assert not isinstance(
            ei.value, (LLMAuthError, LLMRateLimitError, LLMQuotaExceededError)
        )
        blocked = get_pool_state_store().get(_A)
        assert blocked is not None
        assert blocked.status == "blocked"
    finally:
        await provider.close()

    picked = platform_llm_credentials()
    assert picked is not None
    assert picked.api_key == "sk-b"
    later = OpenAICompatibleProvider(
        name="platform", api_key=picked.api_key, base_url=_GO
    )
    try:
        with pytest.raises((LLMQuotaExceededError, LLMRateLimitError)) as ei:
            await later.complete(_req("chat"))
        assert "自己的 API Key" in ei.value.message
        assert sleeps == []
    finally:
        await later.close()
