"""Per-call platform quota gate on the cloud in-process path (成本配额与计费 §一).

A turn-start-only check let two shapes oversell, both reproduced here:

* **并发回合** — turns started together all read the same pre-turn number and are
  all admitted; nothing looked again while they ran.
* **团队扇出** — one admitted Multi-Agent turn fans out workers and rounds without
  ever re-checking.

Both are driven through :class:`ObservingLLMProvider`, the fence every leaf call
passes, against a fake ledger that grows as calls land (standing in for metering
+ drain). ``_pretend_turn_start_gate`` asserts the old check would still have
admitted the turn, so each test pins the regression rather than just the fix.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace

import pytest

from agentcore.billing import call_quota
from agentcore.conversation.quota import QuotaLimits, enforce_quota
from agentcore.core.errors import LLMQuotaExceededError, QuotaExceededError
from agentcore.core.log_context import clear_log_context, log_context
from agentcore.llm.call_fence import ObservingLLMProvider
from agentcore.llm.pricing import NANO_PER_CNY
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    TokenUsage,
)

_CAP = 10 * NANO_PER_CNY  # ¥10 daily cost cap — the only dimension under test
_SESSION = object()
_UNSET = object()


@pytest.fixture(autouse=True)
def _clean_log_context():
    """``user_id`` decides whether the gate runs — never inherit it from a neighbour."""
    clear_log_context()
    yield
    clear_log_context()


def _user(*, daily_cost_nano: int = _CAP, is_unlimited: bool = False) -> SimpleNamespace:
    """A ``User`` stand-in with only the daily-cost dimension capped.

    Leaving the other three at 0 (unlimited) keeps ``enforce_quota`` on a single
    day-window read, so ``_Ledger.reads`` counts gate invocations exactly.
    """
    return SimpleNamespace(
        is_unlimited=is_unlimited,
        quota_daily_tokens=0,
        quota_monthly_cost_cny=0,
        quota_daily_cost_cny=daily_cost_nano / NANO_PER_CNY,
        quota_daily_requests=0,
    )


class _Ledger:
    """Fake ``CostEventRepository`` — daily spend grows as calls land."""

    def __init__(self, *, spent_nano: int = 0) -> None:
        self.spent_nano = spent_nano
        self.reads = 0

    def __call__(self, _session: object) -> _Ledger:
        return self

    async def aggregate_for_window(self, *, user_id: str, since: datetime) -> dict:
        self.reads += 1
        return {
            "usage": {"input": 0, "output": 0, "reasoning": 0, "cache_hit": 0, "cache_miss": 0},
            "cost": {"input": 0, "cached": 0, "output": 0, "total": self.spent_nano},
            "rounds": 0,
            "turns": 0,
        }


class _Users:
    def __init__(self, user: object | None) -> None:
        self._user = user
        self.reads = 0

    def __call__(self, _session: object) -> _Users:
        return self

    async def get_by_id(self, user_id: str) -> object | None:
        self.reads += 1
        return self._user


class _Sessions:
    """Stand-in for ``telemetry_session_factory``."""

    def __call__(self) -> _Sessions:
        return self

    async def __aenter__(self) -> object:
        return _SESSION

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Leaf:
    """Fake upstream leaf; counts the calls that actually reached upstream."""

    def __init__(self, ledger: _Ledger, *, cost_nano: int = 0, name: str = "platform") -> None:
        self._ledger = ledger
        self._cost = cost_nano
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def _bill(self) -> TokenUsage:
        self.calls += 1
        self._ledger.spent_nano += self._cost
        return TokenUsage(input_tokens=10, output_tokens=10)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="ok", model=request.model, usage=self._bill())

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        usage = self._bill()
        yield LLMChunk(delta_content="ok", usage=usage, finish_reason="stop")

    async def close(self) -> None:
        return None


def _req(*, scenario: str = "chat") -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="deepseek-v4-flash",
        stream=False,
        scenario=scenario,
    )


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch):
    """Wire the gate onto fakes and return a factory for fenced providers."""

    def _install(
        *,
        spent_nano: int = 0,
        cost_nano: int = 0,
        user: object | None = _UNSET,
        leaf_name: str = "platform",
    ) -> SimpleNamespace:
        ledger = _Ledger(spent_nano=spent_nano)
        users = _Users(_user() if user is _UNSET else user)
        monkeypatch.setattr(call_quota, "telemetry_session_factory", _Sessions())
        monkeypatch.setattr(call_quota, "CostEventRepository", ledger)
        monkeypatch.setattr(call_quota, "UserRepository", users)
        leaf = _Leaf(ledger, cost_nano=cost_nano, name=leaf_name)
        return SimpleNamespace(
            ledger=ledger,
            users=users,
            leaf=leaf,
            provider=ObservingLLMProvider(leaf),
        )

    return _install


async def _pretend_turn_start_gate(ledger: _Ledger, user: object) -> None:
    """The old defence: one ``enforce_quota`` before the turn opens.

    Tests call this to assert the turn *was* admitted, so what follows is the
    oversell the per-call gate has to stop.
    """
    await enforce_quota(ledger, "u1", limits=QuotaLimits.for_user(user))


# --- 超卖形态一：并发回合 -------------------------------------------------


async def test_concurrent_turns_admitted_together_are_stopped_once_spend_crosses(gate):
    """Two turns pass the same pre-turn reading; the second cannot then run free."""
    env = gate(spent_nano=9 * NANO_PER_CNY, cost_nano=2 * NANO_PER_CNY)
    user = _user()

    # Both turns are admitted at turn start — ¥9 of ¥10, the stale number they share.
    with log_context(user_id="u1"):
        await _pretend_turn_start_gate(env.ledger, user)
        await _pretend_turn_start_gate(env.ledger, user)

        # Turn A's first call lands and pushes the account to ¥11.
        await env.provider.complete(_req())
        assert env.ledger.spent_nano == 11 * NANO_PER_CNY

        # Turn B is mid-flight on the same account: its next call must be refused.
        with pytest.raises(LLMQuotaExceededError):
            await env.provider.complete(_req())

    assert env.leaf.calls == 1


async def test_concurrent_turns_oversell_without_the_per_call_gate(gate):
    """Same setup, gate disabled: the second turn bills straight through.

    Pins *why* the fence check exists — remove it and this is the behaviour.
    """
    env = gate(spent_nano=9 * NANO_PER_CNY, cost_nano=2 * NANO_PER_CNY)

    with log_context(user_id="u1"):
        await env.leaf.complete(_req())
        await env.leaf.complete(_req())

    assert env.leaf.calls == 2
    assert env.ledger.spent_nano == 13 * NANO_PER_CNY  # ¥3 over a ¥10 cap
    assert env.ledger.reads == 0


# --- 超卖形态二：团队扇出 -------------------------------------------------


async def test_team_fan_out_stops_once_the_turn_spends_its_cap(gate):
    """One admitted Multi-Agent turn cannot keep fanning out past the cap."""
    env = gate(spent_nano=0, cost_nano=3 * NANO_PER_CNY)
    user = _user()
    refused = 0

    with log_context(user_id="u1"):
        await _pretend_turn_start_gate(env.ledger, user)  # admitted at ¥0 of ¥10

        for _ in range(6):
            try:
                await env.provider.complete(_req())
            except LLMQuotaExceededError:
                refused += 1

    # ¥3 × 4 = ¥12 lands (the 4th is admitted at ¥9, still under), then it stops.
    assert env.leaf.calls == 4
    assert refused == 2
    assert env.ledger.spent_nano == 12 * NANO_PER_CNY


async def test_fan_out_stops_a_streamed_worker_call_too(gate):
    """Workers stream; the brake must sit on the stream path, not just ``complete``."""
    env = gate(spent_nano=11 * NANO_PER_CNY, cost_nano=NANO_PER_CNY)

    with log_context(user_id="u1"), pytest.raises(LLMQuotaExceededError):
        async for _ in env.provider.stream(_req()):
            pass

    assert env.leaf.calls == 0


async def test_stream_runs_normally_while_under_the_cap(gate):
    env = gate(spent_nano=NANO_PER_CNY, cost_nano=NANO_PER_CNY)

    with log_context(user_id="u1"):
        chunks = [c async for c in env.provider.stream(_req())]

    assert env.leaf.calls == 1
    assert [c.delta_content for c in chunks] == ["ok"]


# --- 拒绝面：与 sidecar 那条路同形 ---------------------------------------


async def test_refusal_is_the_sidecar_leaf_twin_and_never_retried(gate):
    """Same error class the sidecar's 429 ``QUOTA_EXCEEDED`` envelope maps to."""
    env = gate(spent_nano=20 * NANO_PER_CNY)

    with log_context(user_id="u1"), pytest.raises(LLMQuotaExceededError) as ei:
        await env.provider.complete(_req())

    err = ei.value
    assert err.code == "QUOTA_EXCEEDED"
    assert err.status_code == 429
    assert err.retryable is False
    # The gate's own copy reaches the user (reset window + BYOK exit), not a generic one.
    assert "今日额度" in err.message
    assert "接入自己的 key" in err.message
    # …and so does the instant it resets at: the copy no longer names one, so losing
    # it on this hop would leave the leaf face alone unable to say when the wall lifts.
    assert err.details["reset_at"].endswith("Z")


async def test_refusal_chains_the_route_level_quota_error(gate):
    env = gate(spent_nano=20 * NANO_PER_CNY)

    with log_context(user_id="u1"), pytest.raises(LLMQuotaExceededError) as ei:
        await env.provider.complete(_req())

    assert isinstance(ei.value.__cause__, QuotaExceededError)
    assert ei.value.__cause__.dimension == "daily_cost"


# --- 不该拦的：BYOK / 代理 / 无账户 / 不限额 ------------------------------


async def test_byok_calls_are_never_gated(gate):
    """BYOK 自担上游账单（拍板 2026-07-20）— no quota read at all."""
    env = gate(spent_nano=99 * NANO_PER_CNY, leaf_name="user")

    with log_context(user_id="u1"):
        await env.provider.complete(_req())

    assert env.leaf.calls == 1
    assert env.ledger.reads == 0
    assert env.users.reads == 0


async def test_proxy_forwarded_calls_are_not_double_checked(gate):
    """``/inference/`` already gated this physical call at its route."""
    env = gate(spent_nano=99 * NANO_PER_CNY)

    with log_context(user_id="u1"):
        await env.provider.complete(_req(scenario="inference.proxy"))

    assert env.leaf.calls == 1
    assert env.ledger.reads == 0


async def test_no_bound_user_skips_the_gate(gate):
    """Settings probes / evals have no account to charge."""
    env = gate(spent_nano=99 * NANO_PER_CNY)

    await env.provider.complete(_req())

    assert env.leaf.calls == 1
    assert env.users.reads == 0


async def test_unlimited_account_pays_only_the_user_lookup(gate):
    env = gate(spent_nano=99 * NANO_PER_CNY, user=_user(is_unlimited=True))

    with log_context(user_id="u1"):
        await env.provider.complete(_req())

    assert env.leaf.calls == 1
    assert env.users.reads == 1
    assert env.ledger.reads == 0  # all-unlimited short-circuits before the aggregate


async def test_unknown_user_does_not_block_the_call(gate):
    """No row to resolve limits from — refusing would break on a stale id."""
    env = gate(spent_nano=99 * NANO_PER_CNY, user=None)

    with log_context(user_id="ghost"):
        await env.provider.complete(_req())

    assert env.leaf.calls == 1
    assert env.ledger.reads == 0


async def test_background_chrome_degrades_instead_of_raising(monkeypatch):
    """标题/记忆等后台 chrome 恒不因配额抛错——中途拒也只降级（gate.py 契约）。

    The pre-call gate already withholds credentials when quota is spent; a refusal
    that lands one step later must not turn best-effort chrome into a raised 429 —
    it comes back as a skip that names the quota as the cause.
    """
    from agentcore.billing import gate as gate_mod
    from agentcore.billing.gate import BackgroundGateResolve
    from agentcore.llm.credentials import LLMCredentials

    platform = LLMCredentials(api_key="k", base_url="https://u", source="platform")

    async def _resolve(*_args, **_kwargs):
        return BackgroundGateResolve(credentials=platform)

    async def _runner(_creds):
        raise LLMQuotaExceededError()

    monkeypatch.setattr(gate_mod, "resolve_and_gate_background", _resolve)

    outcome = await gate_mod.run_background_llm("u1", purpose="title", runner=_runner)
    assert outcome == gate_mod.BackgroundLlmSkip(
        reason=gate_mod.BackgroundSkipReason.QUOTA_EXCEEDED
    )
    # An undated refusal must not invent a recovery moment.
    assert outcome.declared_recovery_in is None
