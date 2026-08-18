"""Non-retryable consolidation failures advance memory_synced_at (stop sweeper loops).

Mirrors the abnormal-turn skip posture: deterministic failures drop the window;
retryable AgentCoreError or transient ``llm_failure_class`` (a 429 whose leaf HTTP
budget is spent still counts) leaves the watermark so the next sweep re-selects.
A pool checkout timeout counts as retryable even though it is no AgentCoreError —
it is the one failure here that would otherwise drop a window nothing had read.

Retryable failures are layered: shared upstream (rate limit / 5xx / quota) arms a
whole-sweep cooldown and aborts the rest of the batch; conversation-local
retryables (e.g. timeout) only cool down that conversation id. Refusals the billing
gate *returns* instead of raising bypass the classifier entirely and must arm the
same two layers themselves.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import TimeoutError as SATimeoutError

from agentcore.billing.gate import BackgroundLlmSkip, BackgroundSkipReason
from agentcore.core.errors import (
    RETRY_AFTER_FROM_BACKOFF,
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from agentcore.memory import consolidation


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _wire_failing_consolidate(monkeypatch, *, fail: BaseException) -> dict:
    """Point consolidate_conversation at in-memory fakes; LLM path raises ``fail``.

    Returns a recorder with ``synced_at`` (watermark writes) and helpers for the
    sweeper pending predicate used by ``list_pending_memory_consolidation``.
    """
    latest = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    idle_before = latest + timedelta(seconds=1)
    state: dict = {
        "synced_at": None,
        "latest": latest,
        "idle_before": idle_before,
        "conv_id": "c-fail",
    }

    @asynccontextmanager
    async def _lock(_conversation_id: str):
        yield "u-fail"

    monkeypatch.setattr(consolidation, "user_memory_lock_for", _lock)
    monkeypatch.setattr(consolidation, "async_session_factory", lambda: _FakeSession())

    class _FakeMsgRepo:
        def __init__(self, session):
            pass

        async def latest_created_at(self, conversation_id):
            return state["latest"]

    class _FakeConvRepo:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, conversation_id):
            return SimpleNamespace(
                id=conversation_id,
                folder_id=None,
                memory_synced_at=state["synced_at"],
            )

        async def set_memory_synced_at(self, conversation_id, synced_at):
            assert conversation_id == state["conv_id"]
            state["synced_at"] = synced_at

        async def list_pending_memory_consolidation(self, *, idle_before, limit):
            # Same predicate as ConversationRepository.list_pending_memory_consolidation:
            # latest > coalesce(synced_at, epoch) AND latest <= idle_before.
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            synced = state["synced_at"] if state["synced_at"] is not None else epoch
            if state["latest"] > synced and state["latest"] <= idle_before:
                return [state["conv_id"]]
            return []

    async def _turn_open(_session, _cid):
        return False

    async def _assistant_row(_session, _cid):
        return (
            {"status": "complete", "finish_reason": "end_turn"},
            "正文",
            True,
        )

    async def _history(_session, _cid, *, max_messages, after=None):
        return [
            SimpleNamespace(role="user", content="hi"),
            SimpleNamespace(role="assistant", content="正文"),
        ]

    async def _actions(_session, _cid, *, max_turns, after=None):
        return None

    async def _run_bg(user_id, *, purpose="memory", runner):
        raise fail

    monkeypatch.setattr(consolidation, "MessageRepository", _FakeMsgRepo)
    monkeypatch.setattr(consolidation, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(consolidation, "conversation_turn_open", _turn_open)
    monkeypatch.setattr(consolidation, "_latest_assistant_row", _assistant_row)
    monkeypatch.setattr(consolidation, "load_recent_history", _history)
    monkeypatch.setattr(consolidation, "_load_conversation_action_inventory", _actions)
    monkeypatch.setattr(consolidation, "run_background_llm", _run_bg)
    return state


def _wire_skipping_consolidate(monkeypatch, *, skip: BackgroundLlmSkip) -> dict:
    """Same fakes, except the billing gate *returns* a refusal instead of raising.

    Nothing propagates, so ``consolidate_conversation``'s ``except`` arm — the only
    place that classifies a failure and arms a cooldown — never runs. ``gate_calls``
    counts admissions so a test can show the sweeper is no longer re-burning them.
    """
    state = _wire_failing_consolidate(
        monkeypatch, fail=AssertionError("gate returns a skip; the runner must not run")
    )
    gate_calls: list[str] = []

    async def _run_bg(user_id, *, purpose="memory", runner):
        gate_calls.append(user_id)
        return skip

    monkeypatch.setattr(consolidation, "run_background_llm", _run_bg)
    state["gate_calls"] = gate_calls
    return state


def _pending(state: dict) -> list[str]:
    """In-memory sweeper selection matching the repo HAVING clause."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    synced = state["synced_at"] if state["synced_at"] is not None else epoch
    if state["latest"] > synced and state["latest"] <= state["idle_before"]:
        return [state["conv_id"]]
    return []


@pytest.fixture(autouse=True)
def _reset_cooldowns():
    consolidation._reset_failure_cooldowns_for_tests()
    yield
    consolidation._reset_failure_cooldowns_for_tests()


@pytest.mark.asyncio
async def test_nonretryable_failure_advances_watermark_and_exits_pending(monkeypatch):
    state = _wire_failing_consolidate(monkeypatch, fail=LLMAuthError(provider_name="platform"))
    assert _pending(state) == ["c-fail"]

    changed = await consolidation.consolidate_conversation("c-fail")

    assert changed is False
    assert state["synced_at"] == state["latest"]
    assert _pending(state) == []  # next sweep must not re-select
    assert not consolidation._in_shared_failure_cooldown()
    assert "c-fail" not in consolidation._failure_cooldown_until


@pytest.mark.asyncio
async def test_bare_exception_advances_watermark_and_exits_pending(monkeypatch):
    """AttributeError-class bugs have no retryable flag but are deterministic."""
    state = _wire_failing_consolidate(monkeypatch, fail=AttributeError("'NoneType' object"))
    assert _pending(state) == ["c-fail"]

    changed = await consolidation.consolidate_conversation("c-fail")

    assert changed is False
    assert state["synced_at"] == state["latest"]
    assert _pending(state) == []


@pytest.mark.asyncio
async def test_retryable_failure_keeps_watermark_and_stays_pending(monkeypatch):
    state = _wire_failing_consolidate(
        monkeypatch, fail=LLMUpstreamError("502", upstream_status=502)
    )
    assert _pending(state) == ["c-fail"]

    changed = await consolidation.consolidate_conversation("c-fail")

    assert changed is False
    assert state["synced_at"] is None
    assert _pending(state) == ["c-fail"]  # next sweep still selects (after cooldown)


class _SpyLogger:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.kwargs: list[dict] = []

    def warning(self, event, **kwargs):
        self.events.append(event)
        self.kwargs.append(kwargs)

    def info(self, event, **kwargs):
        self.events.append(event)
        self.kwargs.append(kwargs)

    def debug(self, event, **kwargs):
        self.events.append(event)
        self.kwargs.append(kwargs)

    def error(self, event, **kwargs):
        self.events.append(event)
        self.kwargs.append(kwargs)


@pytest.mark.asyncio
async def test_nonretryable_emits_window_dropped_event(monkeypatch):
    """Dropped windows get their own event — not buried in consolidation_failed."""
    state = _wire_failing_consolidate(monkeypatch, fail=LLMAuthError(provider_name="platform"))
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)

    await consolidation.consolidate_conversation("c-fail")

    assert "memory.consolidation_window_dropped" in spy.events
    assert "memory.consolidation_failed" not in spy.events
    assert "memory.consolidation_backoff" not in spy.events
    assert state["synced_at"] == state["latest"]


@pytest.mark.asyncio
async def test_retryable_emits_consolidation_failed_only(monkeypatch):
    state = _wire_failing_consolidate(
        monkeypatch, fail=LLMUpstreamError("502", upstream_status=502)
    )
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)

    await consolidation.consolidate_conversation("c-fail")

    assert "memory.consolidation_failed" in spy.events
    assert "memory.consolidation_window_dropped" not in spy.events
    assert state["synced_at"] is None
    failed = spy.kwargs[spy.events.index("memory.consolidation_failed")]
    assert failed["error_type"] == "LLMUpstreamError"
    # Shared upstream also arms sweep backoff (separate event).
    assert "memory.consolidation_backoff" in spy.events
    backoff = spy.kwargs[spy.events.index("memory.consolidation_backoff")]
    assert backoff["scope"] == "sweep"
    assert backoff["reason"] == "upstream_unstable"


@pytest.mark.asyncio
async def test_consolidation_failed_names_the_exception_class(monkeypatch):
    """同一句「上游限流」文案落进 reason=other 时，error_type 指出它是哪个异常类。

    生产签名：绝大多数限流文案报 other（裸/被包装的异常），少数报 rate_limit
    （真 LLMRateLimitError）。分类照旧按异常类型判，不看文案——这里只断言
    error_type 把两者区分开来，供下一个日志窗口定位包装层。
    """
    state = _wire_failing_consolidate(monkeypatch, fail=RuntimeError("上游限流，请稍后再试"))

    # Watermark write fails → non-retryable failure reports as a plain failure
    # instead of claiming a dropped window.
    class _WriteFailsRepo(consolidation.ConversationRepository):
        async def set_memory_synced_at(self, conversation_id, synced_at):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(consolidation, "ConversationRepository", _WriteFailsRepo)
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)

    await consolidation.consolidate_conversation("c-fail")

    assert "memory.consolidation_window_dropped" not in spy.events
    failed = spy.kwargs[spy.events.index("memory.consolidation_failed")]
    assert failed["error_type"] == "RuntimeError"
    assert failed["reason"] == "other"  # 文案不参与分类；无「含限流字样就判 rate_limit」兜底
    assert state["synced_at"] is None
    # Non-retryable → no backoff armed (observability-only change, posture unchanged).
    assert "memory.consolidation_backoff" not in spy.events


@pytest.mark.asyncio
async def test_rate_limit_arms_sweep_backoff_and_aborts_remaining_batch(monkeypatch):
    """限流 → 整轮退避且不再逐会话重烧 (production self-reheat signature)."""
    calls: list[str] = []

    async def _consolidate(cid: str, *, store=None):
        calls.append(cid)
        # First pending hit trips shared upstream; subsequent ids must not run.
        if cid == "c1":
            consolidation._mark_shared_failure_cooldown(reason="rate_limit")
        return False

    class _FakeConvRepo:
        def __init__(self, session):
            pass

        async def list_pending_memory_consolidation(self, *, idle_before, limit):
            return ["c1", "c2", "c3"]

    monkeypatch.setattr(consolidation, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(consolidation, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(consolidation, "consolidate_conversation", _consolidate)
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_base_seconds",
        300,
        raising=True,
    )
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_max_seconds",
        1800,
        raising=True,
    )

    attempted = await consolidation.consolidation_sweep_once()

    assert calls == ["c1"]  # c2/c3 aborted — no per-conversation reburn
    assert attempted == 1
    assert consolidation._in_shared_failure_cooldown()

    # Next sweep while cooldown active: zero attempts.
    calls.clear()
    attempted2 = await consolidation.consolidation_sweep_once()
    assert attempted2 == 0
    assert calls == []


@pytest.mark.asyncio
async def test_rate_limit_via_consolidate_arms_shared_cooldown(monkeypatch):
    state = _wire_failing_consolidate(monkeypatch, fail=LLMRateLimitError())
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_base_seconds",
        300,
        raising=True,
    )
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_max_seconds",
        1800,
        raising=True,
    )

    await consolidation.consolidate_conversation("c-fail")

    assert state["synced_at"] is None  # retryable: watermark untouched
    assert consolidation._in_shared_failure_cooldown()
    assert "c-fail" not in consolidation._failure_cooldown_until
    assert "memory.consolidation_backoff" in spy.events
    backoff = spy.kwargs[spy.events.index("memory.consolidation_backoff")]
    assert backoff["scope"] == "sweep"
    assert backoff["reason"] == "rate_limit"
    assert backoff["cooldown_seconds"] == 300.0
    assert backoff["streak"] == 1


@pytest.mark.asyncio
async def test_exhausted_rate_limit_keeps_watermark_and_stays_pending(monkeypatch):
    """退避 2→4→8→16 后下一次冷却 32s 超出 30s 上限：leaf 把 retryable 翻 False。

    那是本次调用的 HTTP 预算耗尽，不是「这窗永远做不成」。按 retryable 推进水位
    会把窗口永久丢掉；failure_class 仍是 transient，留给下次 sweep。
    """
    fail = LLMRateLimitError(
        retry_after=32.0,
        retry_after_source=RETRY_AFTER_FROM_BACKOFF,
    )
    assert fail.retryable is False
    state = _wire_failing_consolidate(monkeypatch, fail=fail)
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_base_seconds",
        300,
        raising=True,
    )
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_max_seconds",
        1800,
        raising=True,
    )
    assert _pending(state) == ["c-fail"]

    changed = await consolidation.consolidate_conversation("c-fail")

    assert changed is False
    assert state["synced_at"] is None
    assert _pending(state) == ["c-fail"]
    assert "memory.consolidation_window_dropped" not in spy.events
    assert "memory.consolidation_failed" in spy.events
    # Same shared-upstream cooldown as a still-retryable 429 — not a busy reburn.
    assert consolidation._in_shared_failure_cooldown()
    assert "c-fail" not in consolidation._failure_cooldown_until


@pytest.mark.asyncio
async def test_timeout_arms_conversation_cooldown_not_sweep(monkeypatch):
    state = _wire_failing_consolidate(monkeypatch, fail=LLMTimeoutError("timed out"))
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_failure_cooldown_seconds",
        600,
        raising=True,
    )

    await consolidation.consolidate_conversation("c-fail")

    assert state["synced_at"] is None
    assert not consolidation._in_shared_failure_cooldown()
    assert consolidation._in_conversation_failure_cooldown("c-fail")
    backoff = spy.kwargs[spy.events.index("memory.consolidation_backoff")]
    assert backoff["scope"] == "conversation"
    assert backoff["reason"] == "timeout"
    assert backoff["conversation_id"] == "c-fail"

    # Same conversation skipped; cooldown does not block other ids in a sweep.
    assert await consolidation.consolidate_conversation("c-fail") is False


@pytest.mark.asyncio
async def test_conversation_cooldown_skips_one_id_sweep_continues(monkeypatch):
    calls: list[str] = []

    async def _consolidate(cid: str, *, store=None):
        calls.append(cid)
        return False

    class _FakeConvRepo:
        def __init__(self, session):
            pass

        async def list_pending_memory_consolidation(self, *, idle_before, limit):
            return ["c-hot", "c-ok"]

    consolidation._failure_cooldown_until["c-hot"] = __import__("time").monotonic() + 60
    monkeypatch.setattr(consolidation, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(consolidation, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(consolidation, "consolidate_conversation", _consolidate)

    attempted = await consolidation.consolidation_sweep_once()

    assert calls == ["c-ok"]
    assert attempted == 1


@pytest.mark.asyncio
async def test_shared_cooldown_exponential_capped_and_recovers(monkeypatch):
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_base_seconds",
        100,
        raising=True,
    )
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_max_seconds",
        250,
        raising=True,
    )

    consolidation._mark_shared_failure_cooldown(reason="rate_limit")
    assert consolidation._shared_failure_streak == 1
    first_until = consolidation._shared_failure_cooldown_until

    consolidation._mark_shared_failure_cooldown(reason="rate_limit")
    assert consolidation._shared_failure_streak == 2
    # 100 * 2^1 = 200
    assert consolidation._shared_failure_cooldown_until >= first_until

    consolidation._mark_shared_failure_cooldown(reason="rate_limit")
    assert consolidation._shared_failure_streak == 3
    # 100 * 2^2 = 400 capped to 250
    remaining = consolidation._shared_failure_cooldown_until - __import__("time").monotonic()
    assert remaining <= 250.0 + 0.5

    # Success clears streak — recovery path (not permanent off).
    consolidation._clear_shared_failure_cooldown()
    assert consolidation._shared_failure_streak == 0
    assert not consolidation._in_shared_failure_cooldown()


def test_shared_cooldown_expires_lazily(monkeypatch):
    import time

    consolidation._shared_failure_cooldown_until = time.monotonic() - 1
    consolidation._shared_failure_streak = 3
    assert not consolidation._in_shared_failure_cooldown()
    assert consolidation._shared_failure_cooldown_until == 0.0


@pytest.mark.asyncio
async def test_quota_skip_arms_sweep_cooldown_and_stops_the_reburn(monkeypatch):
    """配额拒绝是「被返回而非被抛出」的——照样要退避，否则 sweeper 每 300s 白烧一次。"""
    state = _wire_skipping_consolidate(
        monkeypatch, skip=BackgroundLlmSkip(reason=BackgroundSkipReason.QUOTA_EXCEEDED)
    )
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_base_seconds",
        300,
        raising=True,
    )
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_max_seconds",
        1800,
        raising=True,
    )

    changed = await consolidation.consolidate_conversation("c-fail")

    assert changed is False
    # Watermark stays put: the window is deferred, never dropped.
    assert state["synced_at"] is None
    assert _pending(state) == ["c-fail"]
    assert consolidation._in_shared_failure_cooldown()
    assert "c-fail" not in consolidation._failure_cooldown_until
    backoff = spy.kwargs[spy.events.index("memory.consolidation_backoff")]
    assert backoff["scope"] == "sweep"
    assert backoff["reason"] == "quota_exceeded"
    assert backoff["cooldown_seconds"] == 300.0

    # Still pending is exactly why a cooldown is needed: without one the next pass
    # spends another admission on an upstream that has already said no.
    assert await consolidation.consolidate_conversation("c-fail") is False
    assert state["gate_calls"] == ["u-fail"]


@pytest.mark.asyncio
async def test_quota_skip_declared_recovery_beats_the_exponential_ladder(monkeypatch):
    """上游自报 12.7h 恢复：直接开到封顶，不再从 5 分钟一级级爬——但也不照单全收。"""
    declared = 12.7 * 3600
    state = _wire_skipping_consolidate(
        monkeypatch,
        skip=BackgroundLlmSkip(
            reason=BackgroundSkipReason.QUOTA_EXCEEDED, declared_recovery_in=declared
        ),
    )
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_base_seconds",
        300,
        raising=True,
    )
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_shared_failure_cooldown_max_seconds",
        1800,
        raising=True,
    )

    await consolidation.consolidate_conversation("c-fail")

    assert state["synced_at"] is None
    backoff = spy.kwargs[spy.events.index("memory.consolidation_backoff")]
    # Outlasts both the 300s first rung and the 1800s ladder ceiling …
    assert backoff["cooldown_seconds"] == consolidation._DECLARED_COOLDOWN_CAP_SECONDS
    # … yet is still clamped: a process-wide gate may not sit out half a day.
    assert backoff["cooldown_seconds"] < declared
    assert backoff["declared_recovery_sec"] == pytest.approx(declared)
    remaining = consolidation._shared_failure_cooldown_until - time.monotonic()
    assert remaining == pytest.approx(consolidation._DECLARED_COOLDOWN_CAP_SECONDS, abs=5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        BackgroundSkipReason.NO_CREDENTIALS,
        BackgroundSkipReason.AUTH_REJECTED,
        BackgroundSkipReason.TURN_AUTH_DEAD,
    ],
)
async def test_account_shaped_skips_stay_conversation_local(monkeypatch, reason):
    """没 key / key 被拒是这个账号自己的墙，进程级 sweep 闸会连带饿死其他所有用户。"""
    state = _wire_skipping_consolidate(monkeypatch, skip=BackgroundLlmSkip(reason=reason))
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)
    monkeypatch.setattr(
        consolidation.settings,
        "memory_consolidation_failure_cooldown_seconds",
        600,
        raising=True,
    )

    await consolidation.consolidate_conversation("c-fail")

    assert state["synced_at"] is None
    assert not consolidation._in_shared_failure_cooldown()
    assert consolidation._in_conversation_failure_cooldown("c-fail")
    backoff = spy.kwargs[spy.events.index("memory.consolidation_backoff")]
    assert backoff["scope"] == "conversation"
    assert backoff["reason"] == reason.value


@pytest.mark.asyncio
async def test_pool_timeout_keeps_watermark_and_stays_pending(monkeypatch):
    """池超时是瞬时基建故障：判成确定性失败就会推进水位、把整窗真丢掉。"""
    state = _wire_failing_consolidate(
        monkeypatch,
        fail=SATimeoutError(
            "QueuePool limit of size 5 overflow 10 reached, connection timed out"
        ),
    )
    spy = _SpyLogger()
    monkeypatch.setattr(consolidation, "logger", spy)
    assert _pending(state) == ["c-fail"]

    changed = await consolidation.consolidate_conversation("c-fail")

    assert changed is False
    assert state["synced_at"] is None
    assert _pending(state) == ["c-fail"]  # window survives for the next sweep
    assert "memory.consolidation_window_dropped" not in spy.events
    assert "memory.consolidation_failed" in spy.events
    # Local saturation, not an upstream wall: cool this id, let the sweep continue.
    assert not consolidation._in_shared_failure_cooldown()
    assert consolidation._in_conversation_failure_cooldown("c-fail")


def test_pool_timeout_is_retryable_through_the_cause_chain():
    """sqlalchemy.exc.TimeoutError 不是 AgentCoreError，被包一层后也必须仍判可重试。"""
    pool_timeout = SATimeoutError("QueuePool limit of size 5 overflow 10 reached")
    assert consolidation._consolidation_failure_retryable(pool_timeout)

    wrapped = RuntimeError("consolidation session failed")
    wrapped.__cause__ = pool_timeout
    assert consolidation._consolidation_failure_retryable(wrapped)

    # Unchanged: a plain bug is still deterministic, so the watermark still advances.
    assert not consolidation._consolidation_failure_retryable(AttributeError("boom"))


def test_persistence_defaults_include_consolidation_cooldowns():
    from agentcore.config.persistence import PersistenceSettings

    defaults = PersistenceSettings()
    assert defaults.memory_consolidation_failure_cooldown_seconds == 600
    assert defaults.memory_consolidation_shared_failure_cooldown_base_seconds == 300
    assert defaults.memory_consolidation_shared_failure_cooldown_max_seconds == 1800
