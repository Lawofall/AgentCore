"""Worker usage lands in cost_events with role/run attribution (no double-bill)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.billing import cost_ledger_queue as queue_mod
from agentcore.billing.call_meter import maybe_enqueue_inprocess_call
from agentcore.billing.turn_ledger import (
    drain_cost_ledger_before_reconcile,
    reconcile_turn_cost_ledger,
)
from agentcore.conversation.common import log_cost_recorded
from agentcore.core.log_context import log_context
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.runtime.costing import ROLE_CAPTAIN, ROLE_MEMBER


class _AliveTask:
    def done(self) -> bool:
        return False


def _pending_rows(queue) -> list[dict]:
    return [r for r in queue._backend._rows.values() if r.get("status") == "pending"]


@pytest.fixture
def running_ledger(monkeypatch, tmp_path: Path):
    queue = queue_mod.reset_cost_ledger_queue_for_tests()
    monkeypatch.setattr(queue_mod.settings, "data_dir", str(tmp_path))
    queue._task = _AliveTask()
    return queue, tmp_path


def _usage(*, inp: int = 100, out: int = 20) -> TokenUsage:
    return TokenUsage(input_tokens=inp, output_tokens=out)


@pytest.mark.asyncio
async def test_maybe_enqueue_materializes_runs_and_stamps_member_role(running_ledger):
    queue, _tmp_path = running_ledger
    with log_context(
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        run_id="del_worker_1",
        parent_run_id="cap_1",
        agent_id="del_worker_1",
        cost_role="member",
        persona="调研员",
    ):
        rid = maybe_enqueue_inprocess_call(
            model="deepseek-v4-flash",
            usage=_usage(),
            scenario="agent",
            credential_source="platform",
        )
    assert rid is not None
    await queue._await_pending_enqueues()
    rows = _pending_rows(queue)
    assert len(rows) == 1
    payload = rows[0]
    assert payload["source"] == "inprocess_call"
    assert payload["materialize_runs"] is True
    call = payload["calls"][0]
    assert call["role"] == ROLE_MEMBER
    assert call["run_id"] == "del_worker_1"
    assert call["parent_run_id"] == "cap_1"
    assert call["persona"] == "调研员"


@pytest.mark.asyncio
async def test_maybe_enqueue_skips_vision_scenario(running_ledger):
    """Vision board_read is billed only via cost_runs orphan — never cost_calls."""
    queue, _tmp_path = running_ledger
    with log_context(user_id="u1", conversation_id="c1", run_id="cap_1"):
        assert (
            maybe_enqueue_inprocess_call(
                model="qwen-vl",
                usage=_usage(),
                scenario="vision.board_read",
            )
            is None
        )
    await queue._await_pending_enqueues()
    assert _pending_rows(queue) == []


def test_log_cost_recorded_by_role_shape():
    """Pin the by_role contract log_stats / timeline triage consume."""
    rows = [
        {
            "run_id": "cap",
            "role": ROLE_CAPTAIN,
            "model": "glm-5.2",
            "tokens": {"input": 10, "output": 2},
            "cost_total_nano": 100,
        },
        {
            "run_id": "w1",
            "role": ROLE_MEMBER,
            "model": "glm-5.2",
            "tokens": {"input": 50, "output": 8},
            "cost_total_nano": 400,
        },
        {
            "run_id": "w2",
            "role": ROLE_MEMBER,
            "model": "glm-5.2",
            "tokens": {"input": 30, "output": 4},
            "cost_total_nano": 200,
        },
    ]
    by_role: dict[str, dict[str, int]] = {}
    for row in rows:
        role = str(row.get("role") or "?")
        bucket = by_role.setdefault(role, {"runs": 0, "total_nano": 0, "input": 0, "output": 0})
        bucket["runs"] += 1
        bucket["total_nano"] += int(row.get("cost_total_nano", 0) or 0)
        tokens = row.get("tokens") or {}
        bucket["input"] += int(tokens.get("input", 0) or 0)
        bucket["output"] += int(tokens.get("output", 0) or 0)
    assert by_role[ROLE_CAPTAIN] == {"runs": 1, "total_nano": 100, "input": 10, "output": 2}
    assert by_role[ROLE_MEMBER] == {"runs": 2, "total_nano": 600, "input": 80, "output": 12}
    log_cost_recorded("c1", "m1", rows)


@pytest.mark.asyncio
async def test_reconcile_materializes_worker_from_calls_not_cost_runs():
    """Even when cost_runs is captain-only, calls for a member run upsert into events."""
    session = MagicMock()
    repo = MagicMock()
    repo.materialize_message_runs = AsyncMock(return_value={"cap_1", "w1"})
    repo.record_runs = AsyncMock(return_value=0)
    member_event = MagicMock(
        run_id="w1",
        parent_run_id="cap_1",
        agent_id="w1",
        role=ROLE_MEMBER,
        persona="调研员",
        model="glm-5.2",
        tokens={"input": 200, "output": 40},
        cost={"total": 900},
        cost_total_nano=900,
        cost_estimated_nano=0,
        currency="USD",
        rounds=2,
        duration_ms=10,
    )
    captain_event = MagicMock(
        run_id="cap_1",
        parent_run_id=None,
        agent_id="cap_1",
        role=ROLE_CAPTAIN,
        persona="CEO",
        model="glm-5.2",
        tokens={"input": 50, "output": 5},
        cost={"total": 100},
        cost_total_nano=100,
        cost_estimated_nano=0,
        currency="USD",
        rounds=1,
        duration_ms=5,
    )
    repo.list_for_message = AsyncMock(return_value=[captain_event, member_event])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "agentcore.billing.cost_ledger_queue.get_cost_ledger_queue",
            lambda: MagicMock(drain_once=AsyncMock(return_value=0)),
        )
        mp.setattr(
            "agentcore.billing.turn_ledger.CostEventRepository",
            lambda _s: repo,
        )
        drained = await drain_cost_ledger_before_reconcile(
            conversation_id="c1",
            message_id="m1",
        )
        rows = await reconcile_turn_cost_ledger(
            session,
            drained=drained,
            user_id="u1",
            conversation_id="c1",
            message_id="m1",
            cost_runs=[
                {
                    "run_id": "cap_1",
                    "role": ROLE_CAPTAIN,
                    "cost_total_nano": 100,
                    "tokens": {},
                    "cost": {},
                    "model": "glm-5.2",
                }
            ],
        )

    repo.materialize_message_runs.assert_awaited_once()
    # Worker already in call_run_ids → not re-inserted via record_runs.
    repo.record_runs.assert_not_awaited()
    assert {r["run_id"] for r in rows} == {"cap_1", "w1"}
    member = next(r for r in rows if r["run_id"] == "w1")
    assert member["role"] == ROLE_MEMBER
    assert member["persona"] == "调研员"
    assert member["cost_total_nano"] == 900


@pytest.mark.asyncio
async def test_reconcile_records_vision_orphan_without_calls():
    session = MagicMock()
    repo = MagicMock()
    repo.materialize_message_runs = AsyncMock(return_value={"cap_1"})
    repo.record_runs = AsyncMock(return_value=1)
    repo.list_for_message = AsyncMock(return_value=[])

    vision_row = {
        "run_id": "vis_abc",
        "role": "vision",
        "parent_run_id": "cap_1",
        "cost_total_nano": 50,
        "tokens": {"input": 1, "output": 1},
        "cost": {},
        "model": "qwen-vl",
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "agentcore.billing.cost_ledger_queue.get_cost_ledger_queue",
            lambda: MagicMock(drain_once=AsyncMock(return_value=0)),
        )
        mp.setattr(
            "agentcore.billing.turn_ledger.CostEventRepository",
            lambda _s: repo,
        )
        drained = await drain_cost_ledger_before_reconcile(
            conversation_id="c1",
            message_id="m1",
        )
        await reconcile_turn_cost_ledger(
            session,
            drained=drained,
            user_id="u1",
            conversation_id="c1",
            message_id="m1",
            cost_runs=[
                {"run_id": "cap_1", "role": ROLE_CAPTAIN, "cost_total_nano": 1},
                vision_row,
            ],
        )

    repo.record_runs.assert_awaited_once()
    kwargs = repo.record_runs.await_args.kwargs
    assert [r["run_id"] for r in kwargs["runs"]] == ["vis_abc"]


@pytest.mark.asyncio
async def test_reconcile_interrupted_turn_cost_emits_and_stamps(monkeypatch):
    """Interrupt closer must reconcile empty cost_runs, emit cost.recorded, set messages.cost."""
    from agentcore.runtime.turn import interrupt as interrupt_mod

    reconcile = AsyncMock(
        return_value=[
            {
                "run_id": "cap_1",
                "role": ROLE_CAPTAIN,
                "model": "glm-5.2",
                "tokens": {"input": 10, "output": 2},
                "cost_total_nano": 100,
                "cost": {"total": 100},
            },
            {
                "run_id": "w1",
                "role": ROLE_MEMBER,
                "model": "glm-5.2",
                "tokens": {"input": 50, "output": 8},
                "cost_total_nano": 400,
                "cost": {"total": 400},
            },
        ]
    )
    recorded: list[tuple] = []
    set_cost = AsyncMock()
    merge_usage = AsyncMock()

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(user_id="u1")

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _mid, conversation_id=None):
            return SimpleNamespace(cost=None)

        async def set_cost(self, *a, **k):
            await set_cost(*a, **k)

        async def merge_usage(self, *a, **k):
            await merge_usage(*a, **k)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        _ConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.MessageRepository",
        _MsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        reconcile,
    )
    monkeypatch.setattr(
        "agentcore.conversation.common.log_cost_recorded",
        lambda cid, mid, rows: recorded.append((cid, mid, rows)),
    )

    await interrupt_mod._reconcile_interrupted_turn_cost(
        message_id="m1",
        conversation_id="c1",
        trace_id="tr",
    )

    reconcile.assert_awaited_once()
    assert reconcile.await_args.kwargs["cost_runs"] == []
    assert reconcile.await_args.kwargs["user_id"] == "u1"
    assert len(recorded) == 1
    assert recorded[0][0] == "c1"
    assert recorded[0][1] == "m1"
    assert {r["role"] for r in recorded[0][2]} == {ROLE_CAPTAIN, ROLE_MEMBER}
    merge_usage.assert_awaited_once()
    set_cost.assert_awaited_once()
    assert merge_usage.await_args.args[0] == "m1"
    assert merge_usage.await_args.kwargs["usage"]["input_tokens"] == 60
    assert merge_usage.await_args.kwargs["usage"]["output_tokens"] == 10
    assert merge_usage.await_args.kwargs["usage"]["cache_hit_tokens"] == 0
    # Tokens land before cost — cost is the skip latch for a second closer.
    assert merge_usage.await_args_list[0].args[0] == "m1"


@pytest.mark.asyncio
async def test_reconcile_interrupted_user_stop_stamps_usage_tokens_from_ledger(
    monkeypatch,
):
    """user_stop interrupt usage must carry token fields that match the ledger.

    Production shape: ``messages.usage`` is only incomplete chrome (no token
    keys; API projects ``usage: null`` so the bubble hides spend) while
    ``cost_events`` for the same ``message_id`` already has the full split.
    Incoming ledger totals overwrite any partial in-memory tokens (9676f5bb).
    """
    from agentcore.runtime.turn import interrupt as interrupt_mod

    ledger_rows = [
        {
            "run_id": "cap_1",
            "role": ROLE_CAPTAIN,
            "tokens": {
                "input": 3_676_943,
                "output": 12_000,
                "reasoning": 100,
                "cache_hit": 3_000_000,
                "cache_miss": 676_943,
            },
            "cost_total_nano": 1,
            "cost": {"total": 1},
        },
        {
            "run_id": "w1",
            "role": ROLE_MEMBER,
            "tokens": {
                "input": 16_728_448,
                "output": 343_915,
                "reasoning": 50,
                "cache_hit": 13_728_448,
                "cache_miss": 3_000_000,
            },
            "cost_total_nano": 2,
            "cost": {"total": 2},
        },
    ]
    ops: list[str] = []

    async def _merge(*_a, **_k):
        ops.append("usage")

    async def _cost(*_a, **_k):
        ops.append("cost")

    merge_usage = AsyncMock(side_effect=_merge)
    set_cost = AsyncMock(side_effect=_cost)

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(user_id="u1")

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _mid, conversation_id=None):
            return SimpleNamespace(
                cost=None,
                usage={
                    "status": "incomplete",
                    "incomplete": True,
                    "finish_reason": "cancelled",
                    "interrupt_reason": "user_stop",
                    # Partial captain tokens already on the row (merge kept them).
                    "input_tokens": 400_000,
                    "output_tokens": 1_000,
                },
            )

        async def set_cost(self, *a, **k):
            await set_cost(*a, **k)

        async def merge_usage(self, *a, **k):
            await merge_usage(*a, **k)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        _ConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.MessageRepository",
        _MsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        AsyncMock(return_value=ledger_rows),
    )
    monkeypatch.setattr(
        "agentcore.conversation.common.log_cost_recorded",
        lambda *a, **k: None,
    )

    await interrupt_mod._reconcile_interrupted_turn_cost(
        message_id="0aba6f35-34dc-482d-9baa-dca9d665b3f4",
        conversation_id="3a0f3a6f-c856-4318-95f9-e3d11bf8fd9a",
        trace_id="tr",
    )

    merge_usage.assert_awaited_once()
    usage = merge_usage.await_args.kwargs["usage"]
    assert usage["input_tokens"] == 20_405_391
    assert usage["output_tokens"] == 355_915
    assert usage["reasoning_tokens"] == 150
    assert usage["cache_hit_tokens"] == 16_728_448
    assert usage["cache_miss_tokens"] == 3_676_943
    assert ops == ["usage", "cost"]


@pytest.mark.asyncio
async def test_reconcile_interrupted_turn_cost_skips_when_cost_stamped(monkeypatch):
    """Second closer must not re-emit cost.recorded once messages.cost is set."""
    from agentcore.runtime.turn import interrupt as interrupt_mod

    reconcile = AsyncMock(return_value=[{"run_id": "cap_1", "role": ROLE_CAPTAIN}])
    recorded: list = []

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id_unscoped(self, _cid):
            return SimpleNamespace(user_id="u1")

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _mid, conversation_id=None):
            return SimpleNamespace(cost={"total": 1})

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        _ConvRepo,
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.MessageRepository",
        _MsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.drain_cost_ledger_before_reconcile",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "agentcore.billing.turn_ledger.reconcile_turn_cost_ledger",
        reconcile,
    )
    monkeypatch.setattr(
        "agentcore.conversation.common.log_cost_recorded",
        lambda *a, **k: recorded.append(a),
    )

    await interrupt_mod._reconcile_interrupted_turn_cost(
        message_id="m1",
        conversation_id="c1",
        trace_id="tr",
    )

    reconcile.assert_not_awaited()
    assert recorded == []


@pytest.mark.asyncio
async def test_close_turn_interrupted_invokes_cost_reconcile(monkeypatch):
    """All interrupt closers funnel through close_turn_interrupted → cost reconcile."""
    from agentcore.runtime.turn import interrupt as interrupt_mod
    from agentcore.runtime.turn.interrupt import TurnInterruptReason, close_turn_interrupted

    reconcile = AsyncMock()
    appended: list = []

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, mid, conversation_id=None):
            return SimpleNamespace(
                content="partial",
                reasoning_content=None,
                trace_id="tr",
                usage={"status": "running"},
            )

        async def upsert_assistant(self, **kwargs):
            return None

    class _JournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            return []

        async def append(self, **kwargs):
            appended.append(kwargs)
            return 0

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _Store:
        async def clear_stream_segments(self, *, turn_id):
            pass

    monkeypatch.setattr(interrupt_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(interrupt_mod, "TurnJournalRepository", _JournalRepo)
    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(interrupt_mod, "_reconcile_interrupted_turn_cost", reconcile)
    monkeypatch.setattr(
        "agentcore.conversation.store.get_cloud_store",
        lambda: _Store(),
    )

    ok = await close_turn_interrupted(
        message_id="m1",
        conversation_id="c1",
        reason=TurnInterruptReason.USER_STOP,
        content="partial",
    )
    assert ok is True
    reconcile.assert_awaited_once()
    assert reconcile.await_args.kwargs["message_id"] == "m1"
    assert reconcile.await_args.kwargs["conversation_id"] == "c1"
