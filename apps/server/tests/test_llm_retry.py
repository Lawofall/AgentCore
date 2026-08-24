"""LLM provider retry policy: transient upstream / transport failures."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentcore.core.errors import (
    LLM_FAILURE_TRANSIENT,
    RETRY_AFTER_FROM_BACKOFF,
    RETRY_AFTER_FROM_HEADER,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
    llm_failure_class,
)
from agentcore.llm.errors import is_non_retryable_client_status, is_retryable_upstream_status
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.cooldown_gate import reset_cooldown_gate
from agentcore.llm.provider.openai_compatible import (
    _CONNECT_INITIAL_BACKOFF,
    _CONNECT_MAX_RETRIES,
    _INITIAL_BACKOFF,
    _MAX_RETRIES,
    _MAX_RETRY_AFTER,
    OpenAICompatibleProvider,
    _parse_retry_after,
    _rate_limit_should_retry,
    _retry_wait,
)
from agentcore.llm.provider.protocol import (
    TURN_CONNECT_INITIAL_BACKOFF,
    TURN_CONNECT_MAX_RETRIES,
    LLMMessage,
    LLMRequest,
)


@pytest.fixture(autouse=True)
def _reset_cooldown_gate():
    reset_cooldown_gate()
    yield
    reset_cooldown_gate()


def _ok_body() -> dict:
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "model": DEEPSEEK_V4_FLASH,
    }


def _sse_line(text: str = "hi") -> str:
    payload = {
        "choices": [{"delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload)}\n"


def _sse_reasoning(text: str = "think") -> str:
    payload = {
        "choices": [{"delta": {"reasoning_content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload)}\n"


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


def _req(scenario: str = "title") -> LLMRequest:
    """Default to a one-shot scenario — the fail-fast connect budget.

    ``chat`` / ``agent`` carry a whole turn and get the sustained connect chain
    (:func:`connect_retry_policy`), asserted separately below.
    """
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario=scenario,
    )


def test_retryable_status_helpers():
    assert is_retryable_upstream_status(502) is True
    assert is_retryable_upstream_status(503) is True
    assert is_retryable_upstream_status(400) is False
    assert is_non_retryable_client_status(400) is True
    assert is_non_retryable_client_status(401) is True
    assert is_non_retryable_client_status(502) is False


@pytest.mark.parametrize("code", [502, 503])
async def test_complete_retries_transient_5xx_then_succeeds(code, monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(code, content=b'{"error":"upstream"}')
        return httpx.Response(200, json=_ok_body())

    provider = await _mock_provider(handler)
    try:
        result = await provider.complete(_req())
        assert result.content == "ok"
        assert calls["n"] == 2
        assert sleeps == [_INITIAL_BACKOFF]
    finally:
        await provider.close()


async def test_complete_does_not_retry_400(monkeypatch):
    calls = {"n": 0}

    async def fake_sleep(sec: float) -> None:
        raise AssertionError("should not sleep on 400")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, content=b'{"error":{"message":"bad request"}}')

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert ei.value.retryable is False
        assert calls["n"] == 1
    finally:
        await provider.close()


_TEMP_REJECT_BODY = b'{"error":{"message":"invalid temperature: only 1 is allowed for this model"}}'


async def test_complete_omits_temperature_once_on_deprecated_400(monkeypatch):
    """Known temperature-reject 400 → strip temperature, retry once, succeed."""
    calls: list[dict] = []

    async def fake_sleep(sec: float) -> None:
        raise AssertionError("temperature omit retry must not sleep")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append(body)
        if "temperature" in body:
            return httpx.Response(400, content=_TEMP_REJECT_BODY)
        return httpx.Response(200, json=_ok_body())

    provider = await _mock_provider(handler)
    try:
        result = await provider.complete(_req())
        assert result.content == "ok"
        assert len(calls) == 2
        assert "temperature" in calls[0]
        assert "temperature" not in calls[1]
        assert calls[0]["model"] == calls[1]["model"]
    finally:
        await provider.close()


async def test_stream_omits_temperature_once_on_deprecated_400(monkeypatch):
    """Streaming path: same temperature-reject 400 → omit + one retry."""
    calls: list[dict] = []

    async def fake_sleep(sec: float) -> None:
        raise AssertionError("temperature omit retry must not sleep")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append(body)
        if "temperature" in body:
            return httpx.Response(400, content=_TEMP_REJECT_BODY)
        sse = _sse_line("ok") + "data: [DONE]\n"
        return httpx.Response(200, content=sse.encode())

    provider = await _mock_provider(handler)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert any(c.delta_content == "ok" for c in chunks)
        assert len(calls) == 2
        assert "temperature" in calls[0]
        assert "temperature" not in calls[1]
    finally:
        await provider.close()


async def test_complete_unrelated_400_still_no_retry(monkeypatch):
    """Other 400 bodies must not trigger the temperature omit path."""
    calls = {"n": 0}

    async def fake_sleep(sec: float) -> None:
        raise AssertionError("unrelated 400 must not sleep/retry")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            400, content=b'{"error":{"message":"max_tokens must be positive"}}'
        )

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMError) as ei:
            await provider.complete(_req())
        assert ei.value.retryable is False
        assert calls["n"] == 1
        assert "max_tokens must be positive" in ei.value.message
    finally:
        await provider.close()


async def test_complete_2xx_non_json_is_typed_llm_error_no_retry(monkeypatch):
    """LLM-01 A: 2xx HTML/non-JSON → LLMInvalidResponseError(retryable=False), no empty retry spin."""
    calls = {"n": 0}

    async def fake_sleep(sec: float) -> None:
        raise AssertionError("should not sleep on non-JSON 2xx")

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=b"<html><body>login</body></html>",
            headers={"content-type": "text/html"},
        )

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMInvalidResponseError) as ei:
            await provider.complete(_req())
        err = ei.value
        assert err.retryable is False
        assert "响应格式无效" in err.message
        assert isinstance(err.__cause__, json.JSONDecodeError)
        assert calls["n"] == 1
    finally:
        await provider.close()


async def test_list_models_2xx_non_json_is_typed_llm_error():
    """list_models mirrors complete: 2xx non-JSON → LLMInvalidResponseError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html>login</html>",
            headers={"content-type": "text/html"},
        )

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMInvalidResponseError) as ei:
            await provider.list_models()
        assert ei.value.retryable is False
        assert "模型列表响应格式无效" in ei.value.message
        assert isinstance(ei.value.__cause__, json.JSONDecodeError)
    finally:
        await provider.close()


async def test_complete_retries_connect_error_then_succeeds(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, json=_ok_body())

    provider = await _mock_provider(handler)
    try:
        result = await provider.complete(_req())
        assert result.content == "ok"
        assert calls["n"] == 2
        assert sleeps == [_CONNECT_INITIAL_BACKOFF]
    finally:
        await provider.close()


async def test_complete_connect_timeout_fails_fast(monkeypatch):
    """ConnectTimeout: only 1 retry (2 attempts) with 1s backoff — not the 5xx budget."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("connect timed out")

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMTimeoutError):
            await provider.complete(_req())
        assert calls["n"] == _CONNECT_MAX_RETRIES
        assert sleeps == [_CONNECT_INITIAL_BACKOFF]
        assert calls["n"] < _MAX_RETRIES
    finally:
        await provider.close()


@pytest.mark.parametrize("scenario", ["chat", "agent"])
async def test_connect_error_gets_sustained_budget_for_turn_scale(scenario, monkeypatch):
    """A dropped turn costs a whole run — connect failures retry with a real chain.

    Regression: a ~20s upstream outage killed a 4-worker delegate turn because the
    fail-fast budget (2 attempts / 1s flat) gave up inside 5 seconds.
    """
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("All connection attempts failed")

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMUpstreamError):
            await provider.complete(_req(scenario))
        assert calls["n"] == TURN_CONNECT_MAX_RETRIES
        # Exponential, not the flat 1s that used to compress 3 retries into ~5s.
        assert sleeps == [
            TURN_CONNECT_INITIAL_BACKOFF,
            TURN_CONNECT_INITIAL_BACKOFF * 2,
            TURN_CONNECT_INITIAL_BACKOFF * 4,
        ]
        assert sum(sleeps) > _CONNECT_INITIAL_BACKOFF * _CONNECT_MAX_RETRIES
    finally:
        await provider.close()


async def test_stream_connect_error_gets_sustained_budget_for_turn_scale(monkeypatch):
    """Same policy on the streaming path (workers stream; complete() is the one-shot path)."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < TURN_CONNECT_MAX_RETRIES:
            raise httpx.ConnectError("All connection attempts failed")
        body = _sse_line("recovered") + "data: [DONE]\n"
        return httpx.Response(200, content=body.encode())

    provider = await _mock_provider(handler)
    try:
        # Recovers on the last attempt the budget allows — the whole chain is usable.
        chunks = [c async for c in provider.stream(_req("agent"))]
        assert any(c.delta_content == "recovered" for c in chunks)
        assert calls["n"] == TURN_CONNECT_MAX_RETRIES
        assert sleeps == [
            TURN_CONNECT_INITIAL_BACKOFF,
            TURN_CONNECT_INITIAL_BACKOFF * 2,
            TURN_CONNECT_INITIAL_BACKOFF * 4,
        ]
    finally:
        await provider.close()


async def test_complete_read_timeout_keeps_full_retry_budget(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out")

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMTimeoutError):
            await provider.complete(_req())
        assert calls["n"] == _MAX_RETRIES
        assert sleeps == [_INITIAL_BACKOFF, _INITIAL_BACKOFF * 2]
    finally:
        await provider.close()


async def test_complete_exhausts_retries_on_persistent_502(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, content=b'{"error":"bad gateway"}')

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMUpstreamError) as ei:
            await provider.complete(_req())
        assert calls["n"] == _MAX_RETRIES
        assert sleeps == [_INITIAL_BACKOFF, _INITIAL_BACKOFF * 2]
        assert ei.value.details.get("retry_attempts") == _MAX_RETRIES
    finally:
        await provider.close()


async def test_stream_retries_502_before_any_sse_line(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, content=b'{"error":"bad gateway"}')
        body = _sse_line("done") + "data: [DONE]\n"
        return httpx.Response(200, content=body.encode())

    provider = await _mock_provider(handler)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert any(c.delta_content == "done" for c in chunks)
        assert calls["n"] == 2
        assert sleeps == [_INITIAL_BACKOFF]
    finally:
        await provider.close()


def test_parse_retry_after_seconds_and_fallbacks():
    # Delta-seconds (the DeepSeek case) parses straight through (raw; sleep clamps later).
    assert _parse_retry_after("120", 2.0).seconds == 120.0
    assert _parse_retry_after(" 5 ", 2.0).seconds == 5.0
    # audit 01 F9: absent / blank / malformed must fall back to backoff, never raise
    # (a raised ValueError used to escape the retry path and surface as a generic 502).
    # Whose number that is stays visible — see test_llm_retry_after_provenance.py.
    assert _parse_retry_after(None, 2.0).seconds == 2.0
    assert _parse_retry_after("", 2.0).seconds == 2.0
    assert _parse_retry_after("not-a-date", 2.0).seconds == 2.0


def test_parse_retry_after_http_date():
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    # An HTTP-date value (RFC 7231) resolves to a positive delta, not a ValueError.
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=60))
    delta = _parse_retry_after(future, 2.0).seconds
    assert 0 < delta <= 60
    # A past HTTP-date has a non-positive delta → fall back to backoff.
    past = format_datetime(datetime.now(UTC) - timedelta(seconds=60))
    assert _parse_retry_after(past, 2.0).seconds == 2.0


def test_retry_wait_honors_small_retry_after_and_ignores_absurd():
    # Interactive budgets: honor modest Retry-After; absurd values must not
    # become wait_sec if somehow slept (helper still clamps), and must refuse retry.
    assert _retry_wait(5.0, 2.0) == 5.0
    assert _retry_wait(None, 2.0) == 2.0
    wait = _retry_wait(3600.0, 2.0)
    assert wait == 2.0
    assert wait <= _MAX_RETRY_AFTER
    assert _retry_wait(_MAX_RETRY_AFTER, 2.0) == _MAX_RETRY_AFTER
    assert _rate_limit_should_retry(5.0) is True
    assert _rate_limit_should_retry(None) is True
    assert _rate_limit_should_retry(_MAX_RETRY_AFTER) is True
    assert _rate_limit_should_retry(3600.0) is False
    # Provenance omitted: old ceiling-only gate, even for a turn-scale scenario.
    assert (
        _rate_limit_should_retry(5.0, scenario="chat", attempt=0) is True
    )
    assert (
        _rate_limit_should_retry(
            2.0,
            scenario="chat",
            cooldown_source=RETRY_AFTER_FROM_BACKOFF,
            attempt=0,
        )
        is False
    )
    assert (
        _rate_limit_should_retry(
            2.0,
            scenario="agent",
            cooldown_source=RETRY_AFTER_FROM_HEADER,
            attempt=0,
        )
        is True
    )
    assert (
        _rate_limit_should_retry(
            2.0,
            scenario="chat",
            cooldown_source=RETRY_AFTER_FROM_HEADER,
            attempt=1,
        )
        is True
    )
    assert (
        _rate_limit_should_retry(
            8.0,
            scenario="chat",
            cooldown_source=RETRY_AFTER_FROM_HEADER,
            attempt=0,
        )
        is True
    )
    assert (
        _rate_limit_should_retry(
            16.0,
            scenario="chat",
            cooldown_source=RETRY_AFTER_FROM_HEADER,
            attempt=0,
        )
        is False
    )
    assert (
        _rate_limit_should_retry(
            2.0,
            scenario="title",
            cooldown_source=RETRY_AFTER_FROM_BACKOFF,
            attempt=0,
        )
        is True
    )


async def test_complete_rate_limit_absurd_retry_after_fails_immediately(monkeypatch):
    """Retry-After: 3600 → no blind backoff chain; raise on first 429."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"retry-after": "3600"},
            content=b'{"error":"rate_limited"}',
        )

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            await provider.complete(_req())
        assert ei.value.retry_after == 3600.0
        assert calls["n"] == 1
        assert sleeps == []
    finally:
        await provider.close()


async def test_complete_rate_limit_honors_short_retry_after_chain(monkeypatch):
    """2→4→8 Retry-After chain must be waited (not abandoned at MAX_RETRIES=3)."""
    from agentcore.llm.provider.openai_compatible import _RATE_LIMIT_MAX_RETRIES

    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Three 429s then success — needs RATE_LIMIT_MAX_RETRIES > MAX_RETRIES.
        if calls["n"] <= 3:
            return httpx.Response(
                429,
                headers={"retry-after": str(2 ** calls["n"])},
                content=b'{"error":"rate_limited"}',
            )
        return httpx.Response(200, json=_ok_body())

    provider = await _mock_provider(handler)
    try:
        result = await provider.complete(_req())
        assert result.content == "ok"
        assert calls["n"] == 4
        assert sleeps == [2.0, 4.0, 8.0]
        assert _RATE_LIMIT_MAX_RETRIES > _MAX_RETRIES
    finally:
        await provider.close()


@pytest.mark.parametrize("scenario", ["chat", "agent"])
async def test_turn_scale_headerless_429_fails_immediately(scenario, monkeypatch):
    """Interactive header-less 429: return the signal, do not sit local backoff."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, content=b'{"error":"rate_limited"}')
        return httpx.Response(200, json=_ok_body())

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            await provider.complete(_req(scenario))
        assert ei.value.retryable is False
        assert llm_failure_class(ei.value) == LLM_FAILURE_TRANSIENT
        assert calls["n"] == 1
        assert sleeps == []
    finally:
        await provider.close()


@pytest.mark.parametrize("scenario", ["chat", "agent"])
async def test_turn_scale_short_retry_after_sits_out_until_budget(scenario, monkeypatch):
    """Attested Retry-After ≤ silent threshold: wait in-place until the 429 budget."""
    from agentcore.llm.provider.openai_compatible import _RATE_LIMIT_MAX_RETRIES

    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"retry-after": "2"},
            content=b'{"error":"rate_limited"}',
        )

    provider = await _mock_provider(handler)
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            await provider.complete(_req(scenario))
        assert ei.value.retryable is False
        assert llm_failure_class(ei.value) == LLM_FAILURE_TRANSIENT
        assert calls["n"] == _RATE_LIMIT_MAX_RETRIES
        assert sleeps == [2.0] * (_RATE_LIMIT_MAX_RETRIES - 1)
    finally:
        await provider.close()


@pytest.mark.parametrize("scenario", ["chat", "agent"])
async def test_turn_scale_short_retry_after_recovers_after_one_wait(scenario, monkeypatch):
    """A single short header is waited; the next attempt may succeed."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "2"},
                content=b'{"error":"rate_limited"}',
            )
        return httpx.Response(200, json=_ok_body())

    provider = await _mock_provider(handler)
    try:
        result = await provider.complete(_req(scenario))
        assert result.content == "ok"
        assert calls["n"] == 2
        assert sleeps == [2.0]
    finally:
        await provider.close()


async def test_stream_does_not_retry_after_committed_content():
    """Content delta commits the stream: disconnect yields aborted, no retry."""
    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )

    async def line_iter():
        yield _sse_line("partial")
        raise httpx.ReadError("peer closed connection")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.aread = AsyncMock(return_value=b"")
    mock_response.aiter_lines = line_iter
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    provider._client.stream = MagicMock(return_value=mock_response)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert [c.delta_content for c in chunks if c.delta_content] == ["partial"]
        assert chunks[-1].aborted is True
        provider._client.stream.assert_called_once()
    finally:
        await provider.close()


async def test_stream_retries_after_reasoning_only_disconnect(monkeypatch):
    """Reasoning deltas do not commit: RemoteProtocolError → transparent retry."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )

    async def first_lines():
        yield _sse_reasoning("step1")
        yield _sse_reasoning("step2")
        raise httpx.RemoteProtocolError("peer closed connection")

    async def second_lines():
        yield _sse_reasoning("fresh")
        yield _sse_line("final answer")
        yield "data: [DONE]\n"

    def make_response(line_iter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.aread = AsyncMock(return_value=b"")
        mock_response.aiter_lines = line_iter
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        return mock_response

    responses = [make_response(first_lines), make_response(second_lines)]

    def stream_side_effect(*_a, **_kw):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    provider._client.stream = MagicMock(side_effect=stream_side_effect)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert calls["n"] == 2
        assert sleeps == [_INITIAL_BACKOFF]
        assert any(c.stream_reset for c in chunks)
        assert [c.delta_reasoning for c in chunks if c.delta_reasoning] == [
            "step1",
            "step2",
            "fresh",
        ]
        assert [c.delta_content for c in chunks if c.delta_content] == ["final answer"]
        assert not any(c.aborted for c in chunks)
    finally:
        await provider.close()


def _sse_finish(reason: str) -> str:
    payload = {"choices": [{"delta": {}, "finish_reason": reason}]}
    return f"data: {json.dumps(payload)}\n"


def _stream_response(line_iter):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.aread = AsyncMock(return_value=b"")
    mock_response.aiter_lines = line_iter
    mock_response.aclose = AsyncMock()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    return mock_response


async def test_stream_retries_after_reasoning_only_clean_eof(monkeypatch):
    """Reasoning then clean SSE end (no finish_reason) is incomplete, not silent_empty."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )

    async def first_lines():
        yield _sse_reasoning("必须")
        yield _sse_reasoning("先写")
        yield "data: [DONE]\n"

    async def second_lines():
        yield _sse_reasoning("fresh")
        yield _sse_line("final answer")
        yield _sse_finish("stop")
        yield "data: [DONE]\n"

    responses = [_stream_response(first_lines), _stream_response(second_lines)]

    def stream_side_effect(*_a, **_kw):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    provider._client.stream = MagicMock(side_effect=stream_side_effect)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert calls["n"] == 2
        assert sleeps == [_INITIAL_BACKOFF]
        assert any(c.stream_reset for c in chunks)
        assert [c.delta_content for c in chunks if c.delta_content] == ["final answer"]
        assert not any(c.empty_diagnosis for c in chunks)
        assert not any(c.aborted for c in chunks)
    finally:
        await provider.close()


async def test_stream_does_not_retry_reasoning_only_with_stop():
    """finish_reason=stop after thinking-only is a real silent empty — do not retry."""
    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )

    async def lines():
        yield _sse_reasoning("think")
        yield _sse_finish("stop")
        yield "data: [DONE]\n"

    provider._client.stream = MagicMock(return_value=_stream_response(lines))
    try:
        chunks = [c async for c in provider.stream(_req())]
        provider._client.stream.assert_called_once()
        assert [c.empty_diagnosis for c in chunks if c.empty_diagnosis] == ["silent_empty"]
        assert not any(c.stream_reset for c in chunks)
    finally:
        await provider.close()


async def test_stream_reasoning_only_incomplete_exhausts_to_empty(monkeypatch):
    """Retries stay pre-commit; exhausted incomplete thinking still surfaces silent_empty."""
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )

    async def incomplete_lines():
        yield _sse_reasoning("碎片")
        yield "data: [DONE]\n"

    def stream_side_effect(*_a, **_kw):
        calls["n"] += 1
        return _stream_response(incomplete_lines)

    provider._client.stream = MagicMock(side_effect=stream_side_effect)
    try:
        chunks = [c async for c in provider.stream(_req())]
        assert calls["n"] == _MAX_RETRIES
        assert len(sleeps) == _MAX_RETRIES - 1
        assert [c.empty_diagnosis for c in chunks if c.empty_diagnosis] == ["silent_empty"]
    finally:
        await provider.close()


def _http_error_from_cancel() -> httpx.ConnectError:
    req = httpx.Request("POST", "http://example.invalid/v1/chat/completions")
    err = httpx.ConnectError("wrapped-cancel", request=req)
    err.__cause__ = asyncio.CancelledError()
    return err


async def test_complete_wrapped_cancel_does_not_retry(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )
    calls = {"n": 0}

    async def boom(*_a, **_kw):
        calls["n"] += 1
        raise _http_error_from_cancel()

    provider._client.post = boom
    try:
        with pytest.raises(asyncio.CancelledError):
            await provider.complete(_req())
        assert calls["n"] == 1
        assert sleeps == []
    finally:
        await provider.close()


async def test_stream_cancel_aborts_in_flight_and_does_not_retry():
    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )
    hang = asyncio.Event()
    responses: list = []

    async def hanging_lines():
        await hang.wait()
        yield "data: [DONE]\n"

    def stream_side_effect(*_a, **_kw):
        resp = _stream_response(hanging_lines)
        responses.append(resp)
        return resp

    provider._client.stream = MagicMock(side_effect=stream_side_effect)

    async def consume() -> None:
        async for _ in provider.stream(_req()):
            pass

    task = asyncio.create_task(consume())
    try:
        deadline = asyncio.get_running_loop().time() + 0.5
        while not responses:
            if asyncio.get_running_loop().time() > deadline:
                pytest.fail("stream never opened")
            await asyncio.sleep(0)
        assert len(responses) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(responses) == 1
        assert responses[0].aclose.await_count >= 1
    finally:
        hang.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await provider.close()


async def test_stream_wrapped_cancel_does_not_retry(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr("agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep)

    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://example.invalid/v1"
    )
    calls = {"n": 0}

    async def boom_lines():
        raise _http_error_from_cancel()
        yield "data: [DONE]\n"  # pragma: no cover

    def stream_side_effect(*_a, **_kw):
        calls["n"] += 1
        return _stream_response(boom_lines)

    provider._client.stream = MagicMock(side_effect=stream_side_effect)
    try:
        with pytest.raises(asyncio.CancelledError):
            async for _ in provider.stream(_req()):
                pass
        assert calls["n"] == 1
        assert sleeps == []
    finally:
        await provider.close()
