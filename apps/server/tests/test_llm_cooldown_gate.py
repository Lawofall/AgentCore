"""Shared 429 cooldown gate: one upstream failure, later callers refuse immediately.

Replays the production cascade (worker 429 → worker remount 429 → CEO 429) at the
leaf: after the first 429, sibling complete()s on the same provider+credential
return the rate-limit signal without sleeping or minting a second real 429.
"""

from __future__ import annotations

import httpx
import pytest

from agentcore.core.errors import (
    LLM_FAILURE_TERMINAL,
    LLM_FAILURE_TRANSIENT,
    RETRY_AFTER_FROM_BACKOFF,
    RETRY_AFTER_FROM_HEADER,
    LLMAuthError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    llm_failure_class,
    upstream_rate_limit_error,
)
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.cooldown_gate import (
    arm_cooldown,
    clear_cooldown,
    cooldown_key,
    cooldown_remaining,
    cooldown_slot_key,
    peek_cooldown,
    reset_cooldown_gate,
    silent_cooldown_seconds,
)
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest


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


def _req(scenario: str = "chat") -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario=scenario,
    )


async def _mock_provider(
    handler, *, name: str = "user", api_key: str = "k"
) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(
        name=name, api_key=api_key, base_url="http://example.invalid/v1"
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://example.invalid/v1",
        transport=httpx.MockTransport(handler),
    )
    return provider


# ---- gate unit ----------------------------------------------------------------


def test_cooldown_key_is_stable_and_hides_the_secret():
    a = cooldown_key("user", "sk-secret", "https://api.example/v1")
    b = cooldown_key("user", "sk-secret", "https://api.example/v1")
    c = cooldown_key("user", "sk-other", "https://api.example/v1")
    assert a == b
    assert a != c
    assert "sk-secret" not in a


def test_chat_and_agent_share_a_lane_title_and_compaction_do_not():
    from agentcore.llm.provider.cooldown_gate import cooldown_lane

    key = cooldown_key("user", "k", "http://x")
    assert cooldown_lane("chat") == cooldown_lane("agent") == "turn"
    assert cooldown_slot_key(key, "chat") == cooldown_slot_key(key, "agent")
    assert cooldown_slot_key(key, "title") != cooldown_slot_key(key, "compaction")
    assert cooldown_slot_key(key, "title") != cooldown_slot_key(key, "chat")


def test_arm_extends_never_shortens(monkeypatch):
    ticks = {"t": 0.0}
    monkeypatch.setattr(
        "agentcore.llm.provider.cooldown_gate.time.monotonic", lambda: ticks["t"]
    )
    key = cooldown_key("user", "k", "http://x")
    arm_cooldown(key, 2.0, RETRY_AFTER_FROM_BACKOFF)
    first = peek_cooldown(key)
    assert first is not None
    arm_cooldown(key, 1.0, RETRY_AFTER_FROM_HEADER)
    assert peek_cooldown(key) == first
    arm_cooldown(key, 5.0, RETRY_AFTER_FROM_HEADER)
    later = peek_cooldown(key)
    assert later is not None
    assert later.seconds == 5.0
    assert later.source == RETRY_AFTER_FROM_HEADER


def test_remaining_expires_lazily(monkeypatch):
    ticks = {"t": 10.0}
    monkeypatch.setattr(
        "agentcore.llm.provider.cooldown_gate.time.monotonic", lambda: ticks["t"]
    )
    key = cooldown_key("user", "k", "http://x")
    arm_cooldown(key, 2.0, RETRY_AFTER_FROM_BACKOFF)
    assert cooldown_remaining(key) == 2.0
    ticks["t"] = 12.0
    assert cooldown_remaining(key) == 0.0
    assert peek_cooldown(key) is None


def test_silent_cooldown_env_override(monkeypatch):
    monkeypatch.setenv("AGENTCORE_LLM_SILENT_COOLDOWN_SECONDS", "7.5")
    assert silent_cooldown_seconds() == 7.5
    monkeypatch.setenv("AGENTCORE_LLM_SILENT_COOLDOWN_SECONDS", "nope")
    assert silent_cooldown_seconds() == 10.0


# ---- failure class (Wave reads this, not retryable) ---------------------------


def test_rate_limit_is_transient_even_after_leaf_exhausts():
    err = upstream_rate_limit_error(
        2.0, credential_source="user", retry_after_source=RETRY_AFTER_FROM_BACKOFF
    )
    assert isinstance(err, LLMRateLimitError)
    assert err.retryable is True
    assert llm_failure_class(err) == LLM_FAILURE_TRANSIENT
    err.retryable = False
    assert llm_failure_class(err) == LLM_FAILURE_TRANSIENT


def test_dated_quota_face_is_transient_auth_is_terminal():
    dated = upstream_rate_limit_error(
        46_440.0,
        credential_source="platform",
        retry_after_source=RETRY_AFTER_FROM_HEADER,
    )
    assert isinstance(dated, LLMQuotaExceededError)
    assert dated.retryable is False
    assert llm_failure_class(dated) == LLM_FAILURE_TRANSIENT
    auth = LLMAuthError(provider_name="user")
    assert llm_failure_class(auth) == LLM_FAILURE_TERMINAL
    assert llm_failure_class(RuntimeError("nope")) == LLM_FAILURE_TERMINAL


# ---- cascade: one real 429 ----------------------------------------------------


async def test_triple_slam_is_one_upstream_429(monkeypatch):
    """trace 933d81fe…: three sequential callers, one real 429.

    First complete hits a header-less 429 and returns immediately (no local
    backoff sit). The two later callers (worker remount / CEO) see the armed
    slot and raise without sleeping or hitting upstream.
    """
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep
    )

    status: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not status:
            status.append(429)
            return httpx.Response(429, content=b'{"error":"rate_limited"}')
        status.append(200)
        return httpx.Response(200, json=_ok_body())

    worker = await _mock_provider(handler, api_key="shared-key")
    worker_retry = await _mock_provider(handler, api_key="shared-key")
    ceo = await _mock_provider(handler, api_key="shared-key")
    try:
        with pytest.raises(LLMRateLimitError) as first_err:
            await worker.complete(_req("agent"))
        assert first_err.value.retryable is False
        with pytest.raises(LLMRateLimitError) as second_err:
            await worker_retry.complete(_req("agent"))
        with pytest.raises(LLMRateLimitError) as third_err:
            await ceo.complete(_req("chat"))
        assert second_err.value.retryable is False
        assert third_err.value.retryable is False
        assert llm_failure_class(second_err.value) == LLM_FAILURE_TRANSIENT
        assert status.count(429) == 1
        assert status == [429]
        assert sleeps == []
    finally:
        await worker.close()
        await worker_retry.close()
        await ceo.close()


async def test_later_callers_refuse_an_armed_slot_without_sleeping(monkeypatch):
    """Once a 2s slot is armed, new complete()s return immediately and do not hit 429."""
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_ok_body())

    first = await _mock_provider(handler, api_key="shared-key")
    second = await _mock_provider(handler, api_key="shared-key")
    try:
        arm_cooldown(cooldown_slot_key(first._cooldown_key, "agent"), 2.0, RETRY_AFTER_FROM_BACKOFF)
        with pytest.raises(LLMRateLimitError) as first_err:
            await first.complete(_req("agent"))
        with pytest.raises(LLMRateLimitError) as second_err:
            await second.complete(_req("chat"))
        assert first_err.value.retryable is False
        assert second_err.value.retryable is False
        assert llm_failure_class(first_err.value) == LLM_FAILURE_TRANSIENT
        assert calls["n"] == 0
        assert sleeps == []
    finally:
        await first.close()
        await second.close()


async def test_in_place_retry_clears_the_gate_on_success(monkeypatch):
    """Attested short Retry-After: sit once, succeed, leave no leftover slot."""
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep
    )
    calls = {"n": 0}

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
    other = await _mock_provider(handler)
    try:
        result = await provider.complete(_req("chat"))
        assert result.content == "ok"
        assert calls["n"] == 2
        assert sleeps == [2.0]
        assert peek_cooldown(cooldown_slot_key(provider._cooldown_key, "chat")) is None
        later = await other.complete(_req("chat"))
        assert later.content == "ok"
        assert calls["n"] == 3
    finally:
        await provider.close()
        await other.close()


async def test_different_credentials_do_not_share_a_gate(monkeypatch):
    async def fake_sleep(sec: float) -> None:
        return None

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep
    )
    hits = {"a": 0, "b": 0}

    def handler_a(request: httpx.Request) -> httpx.Response:
        hits["a"] += 1
        return httpx.Response(429, content=b'{"error":"rate_limited"}')

    def handler_b(request: httpx.Request) -> httpx.Response:
        hits["b"] += 1
        return httpx.Response(200, json=_ok_body())

    throttled = await _mock_provider(handler_a, api_key="key-a")
    other = await _mock_provider(handler_b, api_key="key-b")
    try:
        with pytest.raises(LLMRateLimitError):
            await throttled.complete(_req("chat"))
        result = await other.complete(_req("chat"))
        assert result.content == "ok"
        assert hits["b"] == 1
    finally:
        await throttled.close()
        await other.close()


async def test_long_cooldown_does_not_silent_wait_and_blocks_siblings(monkeypatch):
    """Hour-scale Retry-After: first call fails immediately; siblings refuse without HTTP."""
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"retry-after": "3600"},
            content=b'{"error":"rate_limited"}',
        )

    first = await _mock_provider(handler, api_key="shared-key")
    sibling = await _mock_provider(handler, api_key="shared-key")
    try:
        with pytest.raises(LLMRateLimitError) as first_err:
            await first.complete(_req("chat"))
        assert first_err.value.retryable is False
        assert calls["n"] == 1
        assert sleeps == []
        with pytest.raises(LLMRateLimitError) as sibling_err:
            await sibling.complete(_req("chat"))
        assert sibling_err.value.retryable is False
        assert llm_failure_class(sibling_err.value) == LLM_FAILURE_TRANSIENT
        # Sibling never hit upstream — the gate refused.
        assert calls["n"] == 1
        assert sleeps == []
    finally:
        await first.close()
        await sibling.close()


async def test_title_day_reset_does_not_block_compaction(monkeypatch):
    """Title Retry-After of ~4.8 days must not make compaction refuse before probing."""
    sleeps: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "416526"},
                content=b'{"error":"rate_limited"}',
            )
        return httpx.Response(200, json=_ok_body())

    title = await _mock_provider(handler, api_key="shared-key")
    fold = await _mock_provider(handler, api_key="shared-key")
    try:
        with pytest.raises(LLMRateLimitError):
            await title.complete(_req("title"))
        assert calls["n"] == 1
        result = await fold.complete(_req("compaction"))
        assert result.content == "ok"
        assert calls["n"] == 2
        assert sleeps == []
        with pytest.raises(LLMRateLimitError):
            await title.complete(_req("title"))
        assert calls["n"] == 2
    finally:
        await title.close()
        await fold.close()


async def test_compaction_probes_even_when_platform_member_is_cooling(monkeypatch):
    """Turn-scale still consults pool remaining; compaction does not inherit it."""
    monkeypatch.setattr(
        OpenAICompatibleProvider,
        "_platform_account_remaining",
        lambda self: 416526.0,
    )

    async def fake_sleep(sec: float) -> None:
        return None

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep", fake_sleep
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_ok_body())

    fold = await _mock_provider(handler, api_key="shared-key")
    chat = await _mock_provider(handler, api_key="shared-key")
    try:
        result = await fold.complete(_req("compaction"))
        assert result.content == "ok"
        assert calls["n"] == 1
        with pytest.raises(LLMRateLimitError):
            await chat.complete(_req("chat"))
        assert calls["n"] == 1
    finally:
        await fold.close()
        await chat.close()


def test_reset_drops_every_slot():
    key = cooldown_key("user", "k", "http://x")
    arm_cooldown(key, 30.0, RETRY_AFTER_FROM_BACKOFF)
    assert peek_cooldown(key) is not None
    reset_cooldown_gate()
    assert peek_cooldown(key) is None
    clear_cooldown(key)  # no-op
