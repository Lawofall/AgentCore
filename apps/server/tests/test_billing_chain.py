"""M11 billing-chain regression (zero LLM, per-PR unit suite).

Pins the five seams the architecture audit called out. Prefer thin fakes over
re-testing surfaces already covered by ``test_billing_preference`` /
``test_cost_ledger_queue`` / ``test_turn_cost_ledger`` / ``test_billing_attribution``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.billing import cost_ledger_queue as queue_mod
from agentcore.billing.attribution import (
    attribution_headers_from_context,
    default_role_for_agent,
    parse_attribution_headers,
)
from agentcore.billing.cost_ledger_queue import MemoryOutboxBackend
from agentcore.billing.gate import preflight_llm_credentials
from agentcore.billing.turn_ledger import (
    drain_cost_ledger_before_reconcile,
    reconcile_turn_cost_ledger,
)
from agentcore.config import settings
from agentcore.core.errors import QuotaExceededError
from agentcore.core.log_context import log_context
from agentcore.llm.credentials import (
    INFERENCE_AGENT_HEADER,
    INFERENCE_PARENT_RUN_HEADER,
    INFERENCE_ROLE_HEADER,
    INFERENCE_RUN_HEADER,
)
from agentcore.runtime.costing import ROLE_CAPTAIN, ROLE_MEMBER


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _IdempotentLedgerRepo:
    """Minimal sink mimicking UNIQUE ``call_id`` / ``run_id`` + materialize upsert."""

    def __init__(self, _session=None):
        pass

    # Shared across drain retries within one test (repo constructed per write).
    calls_by_id: dict[str, dict] = {}
    runs_by_id: dict[str, dict] = {}

    @classmethod
    def reset(cls) -> None:
        cls.calls_by_id = {}
        cls.runs_by_id = {}

    async def record_calls(self, **kw) -> int:
        written = 0
        for call in kw.get("calls") or []:
            cid = str(call["call_id"])
            if cid in self.calls_by_id:
                continue
            self.calls_by_id[cid] = dict(call)
            written += 1
        if kw.get("materialize_runs"):
            by_run: dict[str, list[dict]] = {}
            for call in self.calls_by_id.values():
                rid = str(call.get("run_id") or "")
                if rid:
                    by_run.setdefault(rid, []).append(call)
            for rid, rows in by_run.items():
                total = sum(int(r.get("cost_total_nano", 0) or 0) for r in rows)
                # DO UPDATE: always refresh aggregate from calls.
                self.runs_by_id[rid] = {
                    "run_id": rid,
                    "cost_total_nano": total,
                    "role": rows[0].get("role"),
                }
        return written

    async def record_runs(self, **kw) -> int:
        written = 0
        for run in kw.get("runs") or []:
            rid = str(run["run_id"])
            if rid in self.runs_by_id:
                continue  # DO NOTHING on run_id conflict
            self.runs_by_id[rid] = dict(run)
            written += 1
        return written


@pytest.fixture
def ledger_queue(monkeypatch, tmp_path):
    backend = MemoryOutboxBackend()
    queue = queue_mod.reset_cost_ledger_queue_for_tests(backend=backend)
    monkeypatch.setattr(queue_mod.settings, "data_dir", str(tmp_path))
    _IdempotentLedgerRepo.reset()
    return queue


def _user():
    return SimpleNamespace(
        user_id="u1",
        is_unlimited=False,
        quota_daily_tokens=None,
        quota_monthly_cost_cny=None,
        quota_daily_cost_cny=None,
    )


# --- gate / preflight ---------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_platform_quota_exceeded_propagates(monkeypatch):
    monkeypatch.setattr(settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(settings, "billing_mode", "platform")
    with (
        patch(
            "agentcore.billing.gate.enforce_quota",
            AsyncMock(side_effect=QuotaExceededError("exhausted", dimension="monthly_cost")),
        ),
        pytest.raises(QuotaExceededError) as ei,
    ):
        await preflight_llm_credentials(
            session=MagicMock(),
            user=_user(),
            cost_repo=MagicMock(),
            byok_missing_message="missing",
            model_origin="platform",
        )
    assert ei.value.dimension == "monthly_cost"


# --- drain / outbox 不双计 ----------------------------------------------------


@pytest.mark.asyncio
async def test_drain_redrain_same_call_id_does_not_double_nano(monkeypatch, ledger_queue):
    """At-least-once drain + UNIQUE call_id → stored nano stays single-shot."""
    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.CostEventRepository",
        _IdempotentLedgerRepo,
    )

    call = {
        "call_id": "call-fixed",
        "run_id": "run-1",
        "role": ROLE_MEMBER,
        "model": "m",
        "tokens": {},
        "cost": {},
        "cost_total_nano": 500,
        "currency": "USD",
        "duration_ms": 1,
    }
    await ledger_queue.enqueue_calls_async(
        user_id="u1",
        conversation_id="c1",
        calls=[call],
        source="inprocess_call",
        materialize_runs=True,
    )
    assert await ledger_queue.drain_once() == 1
    await ledger_queue.enqueue_calls_async(
        user_id="u1",
        conversation_id="c1",
        calls=[call],
        source="inprocess_call",
        materialize_runs=True,
    )
    assert await ledger_queue.drain_once() == 1

    assert list(_IdempotentLedgerRepo.calls_by_id) == ["call-fixed"]
    assert _IdempotentLedgerRepo.calls_by_id["call-fixed"]["cost_total_nano"] == 500
    assert _IdempotentLedgerRepo.runs_by_id["run-1"]["cost_total_nano"] == 500


@pytest.mark.asyncio
async def test_drain_redrain_same_run_id_record_runs_do_nothing(monkeypatch, ledger_queue):
    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.CostEventRepository",
        _IdempotentLedgerRepo,
    )

    run = {
        "run_id": "run-idem",
        "role": ROLE_CAPTAIN,
        "model": "m",
        "tokens": {},
        "cost": {},
        "cost_total_nano": 200,
        "currency": "USD",
        "rounds": 1,
        "duration_ms": 1,
    }
    await ledger_queue.enqueue_runs_async(
        user_id="u1", conversation_id="c1", runs=[run], source="turn"
    )
    assert await ledger_queue.drain_once() == 1
    await ledger_queue.enqueue_runs_async(
        user_id="u1", conversation_id="c1", runs=[run], source="turn"
    )
    assert await ledger_queue.drain_once() == 1

    assert list(_IdempotentLedgerRepo.runs_by_id) == ["run-idem"]
    assert _IdempotentLedgerRepo.runs_by_id["run-idem"]["cost_total_nano"] == 200


# --- turn 对账 / materialize --------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_drains_outbox_before_materialize(monkeypatch):
    order: list[str] = []
    open_main_sessions = 0

    class _Queue:
        async def drain_once(self) -> int:
            order.append("drain")
            assert open_main_sessions == 0, "drain must not run under a main-pool session"
            return 0

    repo = MagicMock()
    repo.materialize_message_runs = AsyncMock(
        side_effect=lambda **_kw: (order.append("materialize") or set())
    )
    repo.record_runs = AsyncMock(return_value=0)
    repo.list_for_message = AsyncMock(return_value=[])

    monkeypatch.setattr(
        "agentcore.billing.cost_ledger_queue.get_cost_ledger_queue",
        lambda: _Queue(),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.CostEventRepository",
        lambda _s: repo,
    )

    drained = await drain_cost_ledger_before_reconcile(
        conversation_id="c1",
        message_id="m1",
    )
    open_main_sessions += 1
    try:
        await reconcile_turn_cost_ledger(
            MagicMock(),
            drained=drained,
            user_id="u1",
            conversation_id="c1",
            message_id="m1",
            cost_runs=[],
        )
    finally:
        open_main_sessions -= 1
    assert order == ["drain", "materialize"]
    repo.materialize_message_runs.assert_awaited_once()
    repo.record_runs.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_does_not_drain_while_holding_session(monkeypatch):
    """Acceptance: materialize path never calls drain_once (pool inversion guard)."""
    drain_calls = 0

    class _Queue:
        async def drain_once(self) -> int:
            nonlocal drain_calls
            drain_calls += 1
            return 0

    repo = MagicMock()
    repo.materialize_message_runs = AsyncMock(return_value=set())
    repo.record_runs = AsyncMock(return_value=0)
    repo.list_for_message = AsyncMock(return_value=[])

    monkeypatch.setattr(
        "agentcore.billing.cost_ledger_queue.get_cost_ledger_queue",
        lambda: _Queue(),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.CostEventRepository",
        lambda _s: repo,
    )

    drained = await drain_cost_ledger_before_reconcile(conversation_id="c1", message_id="m1")
    assert drain_calls == 1
    await reconcile_turn_cost_ledger(
        MagicMock(),
        drained=drained,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        cost_runs=[],
    )
    assert drain_calls == 1, "reconcile must not re-enter telemetry drain under main session"


def test_cost_ledger_drained_not_constructible_directly():
    from agentcore.billing.turn_ledger import CostLedgerDrained

    with pytest.raises(TypeError, match="drain_cost_ledger_before_reconcile"):
        CostLedgerDrained()


@pytest.mark.asyncio
async def test_reconcile_empty_cost_runs_still_materializes(monkeypatch):
    """Interrupt / empty fold: calls still land via materialize when message_id set."""
    session = MagicMock()
    repo = MagicMock()
    repo.materialize_message_runs = AsyncMock(return_value={"w1"})
    repo.record_runs = AsyncMock(return_value=0)
    event = MagicMock(
        run_id="w1",
        parent_run_id="cap_1",
        agent_id="w1",
        role=ROLE_MEMBER,
        persona="调研员",
        model="m",
        tokens={"input": 1, "output": 1},
        cost={"total": 10},
        cost_total_nano=10,
        cost_estimated_nano=0,
        currency="USD",
        rounds=1,
        duration_ms=1,
    )
    repo.list_for_message = AsyncMock(return_value=[event])

    monkeypatch.setattr(
        "agentcore.billing.cost_ledger_queue.get_cost_ledger_queue",
        lambda: MagicMock(drain_once=AsyncMock(return_value=0)),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.CostEventRepository",
        lambda _s: repo,
    )
    drained = await drain_cost_ledger_before_reconcile(conversation_id="c1", message_id="m1")
    rows = await reconcile_turn_cost_ledger(
        session,
        drained=drained,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        cost_runs=[],
    )
    repo.materialize_message_runs.assert_awaited_once()
    assert [r["run_id"] for r in rows] == ["w1"]
    assert rows[0]["role"] == ROLE_MEMBER


# --- attribution 注入 ---------------------------------------------------------


def test_attribution_headers_roundtrip_run_parent_agent_role():
    with log_context(
        run_id="del_1",
        parent_run_id="cap_1",
        agent_id="del_1",
        cost_role="member",
        persona="前端工程师",
    ):
        headers = attribution_headers_from_context()

    assert headers[INFERENCE_RUN_HEADER] == "del_1"
    assert headers[INFERENCE_PARENT_RUN_HEADER] == "cap_1"
    assert headers[INFERENCE_AGENT_HEADER] == "del_1"
    assert headers[INFERENCE_ROLE_HEADER] == "member"

    parsed = parse_attribution_headers(headers)
    assert parsed["run_id"] == "del_1"
    assert parsed["parent_run_id"] == "cap_1"
    assert parsed["agent_id"] == "del_1"
    assert parsed["role"] == "member"
    assert parsed["persona"] == "前端工程师"
    assert parsed["call_id"]


def test_parse_attribution_rejects_unknown_role():
    headers = {INFERENCE_ROLE_HEADER: "hacker"}
    assert parse_attribution_headers(headers)["role"] is None


def test_default_role_for_agent_shapes():
    assert default_role_for_agent(agent_id="CEO", run_id=None) == ROLE_CAPTAIN
    assert default_role_for_agent(agent_id="captain", run_id=None) == ROLE_CAPTAIN
    assert default_role_for_agent(agent_id="del_1", run_id="del_1") == ROLE_MEMBER
    assert default_role_for_agent(agent_id="del_1", run_id=None) == ROLE_MEMBER
    assert default_role_for_agent(agent_id=None, run_id=None) == ROLE_CAPTAIN


def test_resolve_ledger_role_prefer_explicit():
    from agentcore.billing.attribution import resolve_ledger_role

    assert resolve_ledger_role(role="arena", agent_id="CEO") == "arena"
    assert resolve_ledger_role(role="memory") == "memory"
    assert resolve_ledger_role(role=None, agent_id="del_1") == ROLE_MEMBER


# --- upsert 幂等（materialize DO UPDATE vs record_runs DO NOTHING） ------------


@pytest.mark.asyncio
async def test_materialize_upsert_updates_aggregate_while_record_runs_skips(
    monkeypatch, ledger_queue
):
    """Calls path refreshes cost_events; bare record_runs never overwrites."""
    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.CostEventRepository",
        _IdempotentLedgerRepo,
    )

    await ledger_queue.enqueue_runs_async(
        user_id="u1",
        conversation_id="c1",
        runs=[
            {
                "run_id": "run-u",
                "role": ROLE_CAPTAIN,
                "model": "m",
                "tokens": {},
                "cost": {},
                "cost_total_nano": 1,  # stale undercount
                "currency": "USD",
                "rounds": 1,
                "duration_ms": 1,
            }
        ],
        source="turn",
    )
    assert await ledger_queue.drain_once() == 1
    assert _IdempotentLedgerRepo.runs_by_id["run-u"]["cost_total_nano"] == 1

    # Second record_runs with higher total must NOT overwrite (DO NOTHING).
    await ledger_queue.enqueue_runs_async(
        user_id="u1",
        conversation_id="c1",
        runs=[
            {
                "run_id": "run-u",
                "role": ROLE_CAPTAIN,
                "model": "m",
                "tokens": {},
                "cost": {},
                "cost_total_nano": 999,
                "currency": "USD",
                "rounds": 1,
                "duration_ms": 1,
            }
        ],
        source="turn",
    )
    assert await ledger_queue.drain_once() == 1
    assert _IdempotentLedgerRepo.runs_by_id["run-u"]["cost_total_nano"] == 1

    # Calls + materialize DO UPDATE replaces the undercount from call authority.
    await ledger_queue.enqueue_calls_async(
        user_id="u1",
        conversation_id="c1",
        calls=[
            {
                "call_id": "call-u1",
                "run_id": "run-u",
                "role": ROLE_CAPTAIN,
                "model": "m",
                "tokens": {},
                "cost": {},
                "cost_total_nano": 777,
                "currency": "USD",
                "duration_ms": 1,
            }
        ],
        source="inprocess_call",
        materialize_runs=True,
    )
    assert await ledger_queue.drain_once() == 1
    assert _IdempotentLedgerRepo.runs_by_id["run-u"]["cost_total_nano"] == 777
