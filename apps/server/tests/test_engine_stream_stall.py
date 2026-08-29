"""流式停滞闸 (stall gate): a stalled LLM stream must fail FAST + observably instead of
freezing the whole turn (the 辩论回合卡死 root cause).

Pins ``stream_llm_round``'s per-chunk IDLE ceiling:
- a post-commit stall (content already streamed) returns ``aborted`` with the partial
  kept — same salvage contract as a mid-stream disconnect;
- a pre-commit stall (no content / tool_call yet) is **retryable** (aligned with the
  provider's pre-commit transparent retry; reasoning does not commit). After retries /
  wall-clock budget are exhausted it raises ``LLMTimeoutError``;
- a healthy trickle (gaps < idle) completes normally regardless of TOTAL duration;
- disabling the gate (0) never trips.
"""

import asyncio

import pytest
from structlog.testing import capture_logs

from agentcore.config import settings
from agentcore.core.errors import LLMTimeoutError
from agentcore.core.log_context import bind_log_context, clear_log_context
from agentcore.llm.provider.protocol import (
    MAX_RETRIES,
    LLMChunk,
    LLMMessage,
    LLMRequest,
)
from agentcore.runtime.engine import stream as stream_mod
from agentcore.runtime.engine.stream import stream_llm_round


def _request() -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")], model="m")


class _StallProvider:
    """Yields ``pre`` chunks, then goes silent (sleeps) past the idle ceiling."""

    def __init__(self, pre: list[LLMChunk], stall: float) -> None:
        self._pre = pre
        self._stall = stall
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.calls += 1
        for chunk in self._pre:
            yield chunk
        await asyncio.sleep(self._stall)  # no further chunk within the idle window
        yield LLMChunk(delta_content="late")


class _RecoveringStallProvider:
    """Stalls pre-commit on early attempts, then succeeds — pins transparent retry."""

    def __init__(self, fail_attempts: int, stall: float) -> None:
        self._fail_attempts = fail_attempts
        self._stall = stall
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.calls += 1
        yield LLMChunk(delta_reasoning="thinking…")
        if self.calls <= self._fail_attempts:
            await asyncio.sleep(self._stall)
            yield LLMChunk(delta_content="late")
            return
        yield LLMChunk(delta_content="recovered")


class _TrickleProvider:
    """Yields chunks with a gap SHORTER than the ceiling — a healthy long stream."""

    def __init__(self, n: int, gap: float) -> None:
        self._n = n
        self._gap = gap

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        for i in range(self._n):
            await asyncio.sleep(self._gap)
            yield LLMChunk(delta_content=str(i))


class _KeepaliveThenStallProvider:
    """Empty keepalive chunks (< idle) then silence — pins stall gate vs noop resets."""

    def __init__(self, *, keepalive_gap: float, stall: float, pre_content: str = "partial") -> None:
        self._keepalive_gap = keepalive_gap
        self._stall = stall
        self._pre = pre_content
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.calls += 1
        yield LLMChunk(delta_content=self._pre)
        # Simulate upstream noop SSE frames (empty delta) that must not reset idle.
        for _ in range(5):
            await asyncio.sleep(self._keepalive_gap)
            yield LLMChunk()
        await asyncio.sleep(self._stall)
        yield LLMChunk(delta_content="late")


async def test_committed_stall_returns_aborted_partial(monkeypatch):
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.05)
    seen: list[str] = []
    provider = _StallProvider([LLMChunk(delta_content="thinking…")], stall=1.0)

    clear_log_context()
    bind_log_context(credential_source="platform", provider_id="prov-stall")
    try:
        with capture_logs() as caps:
            result = await stream_llm_round(provider, _request(), seen.append, lambda _d: None)
    finally:
        clear_log_context()
    assert result.aborted is True
    assert result.content == "thinking…"
    assert seen == ["thinking…"]
    assert provider.calls == 1  # post-commit: salvage, no retry
    stalled = next(c for c in caps if c.get("event") == "llm.stream_stalled")
    assert stalled["model"] == "m"
    assert stalled["credential_source"] == "platform"
    assert stalled["provider_id"] == "prov-stall"
    assert "base_url" not in stalled


async def test_uncommitted_stall_retries_then_raises(monkeypatch):
    """Pre-commit stall retries (aligned with ``MAX_RETRIES``), then raises timeout."""
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.05)
    # Generous budget so attempt count (not wall-clock) is what exhausts retries.
    monkeypatch.setattr(stream_mod, "_STALL_BUDGET_IDLE_MULTIPLIER", 100.0)
    monkeypatch.setattr(stream_mod, "INITIAL_BACKOFF", 0.0)
    monkeypatch.setattr(stream_mod, "BACKOFF_MULTIPLIER", 1.0)
    seen: list[str] = []
    resets: list[str] = []
    provider = _StallProvider([LLMChunk(delta_reasoning="…")], stall=1.0)
    with pytest.raises(LLMTimeoutError):
        await stream_llm_round(
            provider,
            _request(),
            seen.append,
            seen.append,
            on_reset=resets.append,
        )
    assert provider.calls == MAX_RETRIES
    # First attempt emits reasoning; each retry resets live view then re-emits.
    assert seen == ["…"] * MAX_RETRIES
    # Stream-layer resets carry reason=retry — fold only clears the draft.
    assert resets == ["retry"] * (MAX_RETRIES - 1)


async def test_uncommitted_stall_retries_under_production_budget(monkeypatch):
    """Production multiplier=2 must still allow one transparent retry after a full idle window.

    Pins the budget-edge bug: ``remaining < idle`` after the first window used to
    kill the retry (overshoot of ms/TTFT). Window accounting keeps attempt 0 + 1.
    """
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.05)
    assert stream_mod._STALL_BUDGET_IDLE_MULTIPLIER == 2.0
    monkeypatch.setattr(stream_mod, "INITIAL_BACKOFF", 0.0)
    monkeypatch.setattr(stream_mod, "BACKOFF_MULTIPLIER", 1.0)
    content_seen: list[str] = []
    reasoning_seen: list[str] = []
    resets: list[str] = []
    provider = _RecoveringStallProvider(fail_attempts=1, stall=1.0)
    result = await stream_llm_round(
        provider,
        _request(),
        content_seen.append,
        reasoning_seen.append,
        on_reset=resets.append,
    )
    assert result.aborted is False
    assert result.content == "recovered"
    assert provider.calls == 2
    assert resets == ["retry"]
    assert content_seen == ["recovered"]


async def test_uncommitted_stall_production_budget_caps_at_two_windows(monkeypatch):
    """multiplier=2 exhausts after two stall windows (not full MAX_RETRIES=3)."""
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.05)
    assert stream_mod._STALL_BUDGET_IDLE_MULTIPLIER == 2.0
    monkeypatch.setattr(stream_mod, "INITIAL_BACKOFF", 0.0)
    monkeypatch.setattr(stream_mod, "BACKOFF_MULTIPLIER", 1.0)
    seen: list[str] = []
    resets: list[str] = []
    provider = _StallProvider([LLMChunk(delta_reasoning="…")], stall=1.0)
    with pytest.raises(LLMTimeoutError):
        await stream_llm_round(
            provider,
            _request(),
            seen.append,
            seen.append,
            on_reset=resets.append,
        )
    assert provider.calls == 2
    assert resets == ["retry"]


async def test_call_retried_still_opens_next_stream(monkeypatch):
    """After ``call_retried``, the next attempt must stream — no wall-clock abort at loop top."""
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.05)
    # Tight wall budget that would trip mid-backoff if loop-top 收口 were reintroduced.
    monkeypatch.setattr(stream_mod, "_STALL_BUDGET_IDLE_MULTIPLIER", 2.0)
    monkeypatch.setattr(stream_mod, "INITIAL_BACKOFF", 0.08)
    monkeypatch.setattr(stream_mod, "BACKOFF_MULTIPLIER", 1.0)
    content_seen: list[str] = []
    provider = _RecoveringStallProvider(fail_attempts=1, stall=1.0)
    with capture_logs() as caps:
        result = await stream_llm_round(
            provider,
            _request(),
            content_seen.append,
            lambda _d: None,
        )
    assert any(c.get("event") == "llm.call_retried" for c in caps)
    assert provider.calls == 2
    assert result.content == "recovered"
    assert content_seen == ["recovered"]


async def test_uncommitted_stall_recovers_on_retry(monkeypatch):
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.05)
    monkeypatch.setattr(stream_mod, "_STALL_BUDGET_IDLE_MULTIPLIER", 100.0)
    monkeypatch.setattr(stream_mod, "INITIAL_BACKOFF", 0.0)
    monkeypatch.setattr(stream_mod, "BACKOFF_MULTIPLIER", 1.0)
    content_seen: list[str] = []
    reasoning_seen: list[str] = []
    resets: list[str] = []
    provider = _RecoveringStallProvider(fail_attempts=1, stall=1.0)
    result = await stream_llm_round(
        provider,
        _request(),
        content_seen.append,
        reasoning_seen.append,
        on_reset=resets.append,
    )
    assert result.aborted is False
    assert result.content == "recovered"
    assert provider.calls == 2
    assert resets == ["retry"]
    assert content_seen == ["recovered"]


async def test_healthy_trickle_completes(monkeypatch):
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.2)
    seen: list[str] = []
    provider = _TrickleProvider(n=4, gap=0.02)  # each gap well under the 0.2s ceiling
    result = await stream_llm_round(provider, _request(), seen.append, lambda _d: None)
    assert result.content == "0123"
    assert seen == ["0", "1", "2", "3"]
    assert result.tool_calls is None
    assert result.aborted is False


async def test_empty_keepalive_does_not_reset_idle_timer(monkeypatch):
    """Noop provider keepalives after commit must not defer stall detection forever."""
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.08)
    seen: list[str] = []
    provider = _KeepaliveThenStallProvider(
        keepalive_gap=0.01,
        stall=0.2,
        pre_content="worker partial",
    )
    result = await stream_llm_round(provider, _request(), seen.append, lambda _d: None)
    assert result.aborted is True
    assert result.content == "worker partial"
    assert seen == ["worker partial"]
    assert provider.calls == 1


async def test_gate_disabled_never_trips(monkeypatch):
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.0)
    seen: list[str] = []
    provider = _StallProvider([LLMChunk(delta_content="x")], stall=0.1)
    # Gate off (asyncio.timeout(None)) ⇒ the stall is tolerated; bound the test so a
    # regression that re-enables an idle abort here would surface as a raise, not a hang.
    result = await asyncio.wait_for(
        stream_llm_round(provider, _request(), seen.append, lambda _d: None), timeout=2.0
    )
    assert result.content == "xlate"


class _ResetThenContentProvider:
    """Emits ephemeral reasoning, then stream_reset, then fresh content."""

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        yield LLMChunk(delta_reasoning="stale")
        yield LLMChunk(stream_reset=True)
        yield LLMChunk(delta_content="kept")


async def test_stream_reset_clears_ephemeral_and_live_view(monkeypatch):
    monkeypatch.setattr(settings, "engine_llm_stream_idle_timeout_seconds", 0.0)
    content_seen: list[str] = []
    reasoning_seen: list[str] = []
    resets: list[str] = []
    result = await stream_llm_round(
        _ResetThenContentProvider(),
        _request(),
        content_seen.append,
        reasoning_seen.append,
        on_reset=resets.append,
    )
    assert reasoning_seen == ["stale"]
    assert content_seen == ["kept"]
    assert result.content == "kept"
    assert result.reasoning == ""
    assert resets == ["retry"]
