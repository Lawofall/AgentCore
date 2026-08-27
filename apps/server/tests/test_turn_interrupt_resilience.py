"""P0 ratchets for turn-interrupt resilience semantics.

Pins: user stop ≠ orphan; soft-stop snapshot clean; salvage failure keeps lease;
repeated salvage does not rewrite body.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.runtime.coordination.inject import format_coordination_events
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
)
from agentcore.runtime.events import FinishReason
from agentcore.runtime.turn.interrupt import (
    HARVEST_YIELD_EMPTY_USER_VISIBLE,
    INTERRUPTED_EMPTY_USER_VISIBLE,
    MAX_ROUNDS_EMPTY_USER_VISIBLE,
    OVERLAP_EMPTY_USER_VISIBLE,
    TurnInterruptReason,
    close_turn_interrupted,
    compose_interrupt_body,
    empty_close_user_visible,
    finish_reason_for,
    normalize_interrupt_reason,
)
from agentcore.runtime.turn.runs import TurnRun, turn_runs


@pytest.mark.asyncio
async def test_user_stop_releases_lease_not_orphan(monkeypatch):
    """User /stop closes terminal and releases; never marks orphaned."""
    from agentcore.conversation import turn_runner as runner_mod

    conversation_id = "conv-user-stop"
    released: list[str] = []
    orphaned: list[str] = []
    closed: list[dict] = []

    async def _fake_close(**kwargs):
        closed.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    async def _fake_orphan(mid, *, owner_id=None):
        orphaned.append(mid)

    async def _pipeline(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(runner_mod, "close_user_stop_turn", _fake_close)
    monkeypatch.setattr(runner_mod, "release_turn_lease", _fake_release)
    monkeypatch.setattr(runner_mod, "orphan_turn_lease", _fake_orphan)
    monkeypatch.setattr(runner_mod, "run_chat_pipeline", _pipeline)
    monkeypatch.setattr(runner_mod, "create_assistant_placeholder", AsyncMock())
    monkeypatch.setattr(runner_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(runner_mod, "acquire_turn_lease", AsyncMock(return_value="owner-1"))

    async def _hb(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner_mod, "lease_heartbeat_loop", _hb)
    monkeypatch.setattr(
        "agentcore.demo_tape.hooks.run_tape_turn_if_bound",
        AsyncMock(return_value=None),
    )

    sink = MagicMock()
    sink.bind_content_checkpoint = MagicMock()
    sink.execution_journal = MagicMock(return_value=[])
    sink.streamed_content = MagicMock(return_value="partial")

    async def _noop() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_noop())
    turn_runs._runs[conversation_id] = TurnRun(
        run_id="r1",
        conversation_id=conversation_id,
        task=task,
        sink=sink,
        user_stopped=True,
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await runner_mod.run_and_persist(
                conversation_id=conversation_id,
                user_message="hi",
                user_id="u1",
                folder_id=None,
                sink=sink,
                history=[],
                attachments=None,
                backend=MagicMock(),
                llm_credentials=None,
            )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        turn_runs._runs.pop(conversation_id, None)

    assert closed, "user stop must sync-close the turn"
    assert released, "user stop must release the lease"
    assert not orphaned, "user stop must not orphan for sweeper"


@pytest.mark.asyncio
async def test_user_stop_close_failure_orphans_lease(monkeypatch):
    """Close failure must orphan (not release) — never leave lease-less RUNNING."""
    from agentcore.conversation import turn_runner as runner_mod

    conversation_id = "conv-close-fail"
    released: list[str] = []
    orphaned: list[str] = []

    async def _fake_close(**_kwargs):
        return False

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    async def _fake_orphan(mid, *, owner_id=None):
        orphaned.append(mid)

    async def _pipeline(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(runner_mod, "close_user_stop_turn", _fake_close)
    monkeypatch.setattr(runner_mod, "release_turn_lease", _fake_release)
    monkeypatch.setattr(runner_mod, "orphan_turn_lease", _fake_orphan)
    monkeypatch.setattr(runner_mod, "run_chat_pipeline", _pipeline)
    monkeypatch.setattr(runner_mod, "create_assistant_placeholder", AsyncMock())
    monkeypatch.setattr(runner_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(runner_mod, "acquire_turn_lease", AsyncMock(return_value="owner-1"))

    async def _hb(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner_mod, "lease_heartbeat_loop", _hb)
    monkeypatch.setattr(
        "agentcore.demo_tape.hooks.run_tape_turn_if_bound",
        AsyncMock(return_value=None),
    )

    sink = MagicMock()
    sink.bind_content_checkpoint = MagicMock()

    async def _noop() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_noop())
    turn_runs._runs[conversation_id] = TurnRun(
        run_id="r-fail",
        conversation_id=conversation_id,
        task=task,
        sink=sink,
        user_stopped=True,
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await runner_mod.run_and_persist(
                conversation_id=conversation_id,
                user_message="hi",
                user_id="u1",
                folder_id=None,
                sink=sink,
                history=[],
                attachments=None,
                backend=MagicMock(),
                llm_credentials=None,
            )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        turn_runs._runs.pop(conversation_id, None)

    assert orphaned, "close failure must orphan the lease"
    assert not released, "close failure must not release the lease"


@pytest.mark.asyncio
async def test_close_user_stop_empty_journal_still_durable(monkeypatch):
    """Tool-only / pre-stream cancel: empty journal + empty body still close_turn_interrupted."""
    from agentcore.conversation import turn_persistence
    from agentcore.runtime.events import EventSink

    called: list[dict] = []

    async def _fake_close(**kwargs):
        called.append(kwargs)
        return True

    monkeypatch.setattr(turn_persistence.settings, "incomplete_turn_persist_enabled", True)
    monkeypatch.setattr(turn_persistence, "close_turn_interrupted", _fake_close)

    sink = EventSink()
    # No surface journal, no streamed captain text (e672 shape).
    assert sink.execution_journal() is None
    assert not (sink.streamed_content() or "").strip()

    ok = await turn_persistence.close_user_stop_turn(
        sink=sink,
        conversation_id="c-empty",
        trace_id="tr",
        message_id="m-empty",
    )
    assert ok is True
    assert len(called) == 1
    assert called[0]["message_id"] == "m-empty"
    assert called[0]["load_stream_state"] is True
    assert called[0]["reason"] == TurnInterruptReason.USER_STOP
    assert called[0]["journal"] == []


@pytest.mark.asyncio
async def test_settle_prior_running_assistants_closes_zombies(monkeypatch):
    """begin_turn settle: earlier non-paused RUNNING → close_turn_interrupted."""
    from agentcore.runtime.turn import interrupt as interrupt_mod

    closed: list[dict] = []

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def list_non_paused_running_assistants(
            self, conversation_id, *, exclude_message_id=None
        ):
            assert conversation_id == "c-prior"
            assert exclude_message_id == "m-new"
            return [
                SimpleNamespace(id="m-zombie", trace_id="tr-z", usage={"status": "running"}),
                SimpleNamespace(
                    id="m-has-frame",
                    trace_id="tr-f",
                    usage={"status": "running"},
                ),
            ]

    class _PausedRepo:
        def __init__(self, _session):
            pass

        async def get(self, mid):
            if mid == "m-has-frame":
                return SimpleNamespace(message_id=mid)
            return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def _fake_close(**kwargs):
        closed.append(kwargs)
        return True

    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.db.repositories.MessageRepository",
        _MsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.PausedTurnRepository",
        _PausedRepo,
    )
    monkeypatch.setattr(interrupt_mod, "close_turn_interrupted", _fake_close)

    n = await interrupt_mod.settle_prior_running_assistants(
        conversation_id="c-prior",
        keep_message_id="m-new",
    )
    assert n == 1
    assert len(closed) == 1
    assert closed[0]["message_id"] == "m-zombie"
    # Finding a stale RUNNING row says the lease died, not that we saw a kill.
    assert closed[0]["reason"] == TurnInterruptReason.LEASE_EXPIRED
    assert closed[0]["load_stream_state"] is True


@pytest.mark.asyncio
async def test_begin_turn_settles_prior_before_placeholder(monkeypatch):
    """CloudStore.begin_turn settles prior RUNNING before creating the new row."""
    from agentcore.conversation.store import cloud as cloud_mod
    from agentcore.conversation.store.cloud import CloudStore

    order: list[str] = []

    async def _settle(**_kw):
        order.append("settle")
        return 1

    class Repo:
        def __init__(self, _s):
            pass

        async def create_assistant_placeholder(self, **kw):
            order.append("placeholder")
            return SimpleNamespace(id=kw["message_id"])

    class CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cloud_mod, "async_session_factory", lambda: CM())
    monkeypatch.setattr(cloud_mod, "MessageRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.settle_prior_running_assistants",
        _settle,
    )

    await CloudStore().begin_turn(
        conversation_id="c1", message_id="m-new", trace_id="t" * 32
    )
    assert order == ["settle", "placeholder"]


@pytest.mark.asyncio
async def test_process_kill_orphans_without_user_stop_close(monkeypatch):
    """Non-user CancelledError orphans the lease and skips user-stop closer."""
    from agentcore.conversation import turn_runner as runner_mod

    conversation_id = "conv-kill"
    released: list[str] = []
    orphaned: list[str] = []
    closed: list[dict] = []

    async def _fake_close(**kwargs):
        closed.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    async def _fake_orphan(mid, *, owner_id=None):
        orphaned.append(mid)

    async def _pipeline(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(runner_mod, "close_user_stop_turn", _fake_close)
    monkeypatch.setattr(runner_mod, "release_turn_lease", _fake_release)
    monkeypatch.setattr(runner_mod, "orphan_turn_lease", _fake_orphan)
    monkeypatch.setattr(runner_mod, "run_chat_pipeline", _pipeline)
    monkeypatch.setattr(runner_mod, "create_assistant_placeholder", AsyncMock())
    monkeypatch.setattr(runner_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(runner_mod, "acquire_turn_lease", AsyncMock(return_value="owner-1"))

    async def _hb(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner_mod, "lease_heartbeat_loop", _hb)
    monkeypatch.setattr(
        "agentcore.demo_tape.hooks.run_tape_turn_if_bound",
        AsyncMock(return_value=None),
    )

    sink = MagicMock()
    sink.bind_content_checkpoint = MagicMock()

    # No user_stopped flag on the registry.
    turn_runs._runs.pop(conversation_id, None)
    with pytest.raises(asyncio.CancelledError):
        await runner_mod.run_and_persist(
            conversation_id=conversation_id,
            user_message="hi",
            user_id="u1",
            folder_id=None,
            sink=sink,
            history=[],
            attachments=None,
            backend=MagicMock(),
            llm_credentials=None,
        )

    assert not closed
    assert orphaned
    assert not released


@pytest.mark.asyncio
async def test_shutdown_salvage_releases_lease_not_orphan(monkeypatch):
    """Lifespan shutdown flag makes CancelledError close + release (not orphan)."""
    from agentcore.conversation import turn_runner as runner_mod

    conversation_id = "conv-shutdown"
    released: list[str] = []
    orphaned: list[str] = []
    closed: list[dict] = []

    async def _fake_close(**kwargs):
        closed.append(kwargs)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    async def _fake_orphan(mid, *, owner_id=None):
        orphaned.append(mid)

    async def _pipeline(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(runner_mod, "close_user_stop_turn", _fake_close)
    monkeypatch.setattr(runner_mod, "release_turn_lease", _fake_release)
    monkeypatch.setattr(runner_mod, "orphan_turn_lease", _fake_orphan)
    monkeypatch.setattr(runner_mod, "run_chat_pipeline", _pipeline)
    monkeypatch.setattr(runner_mod, "create_assistant_placeholder", AsyncMock())
    monkeypatch.setattr(runner_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(runner_mod, "acquire_turn_lease", AsyncMock(return_value="owner-1"))

    async def _hb(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr(runner_mod, "lease_heartbeat_loop", _hb)
    monkeypatch.setattr(
        "agentcore.demo_tape.hooks.run_tape_turn_if_bound",
        AsyncMock(return_value=None),
    )

    sink = MagicMock()
    sink.bind_content_checkpoint = MagicMock()

    turn_runs._runs.pop(conversation_id, None)
    turn_runs.begin_shutdown_salvage()
    try:
        with pytest.raises(asyncio.CancelledError):
            await runner_mod.run_and_persist(
                conversation_id=conversation_id,
                user_message="hi",
                user_id="u1",
                folder_id=None,
                sink=sink,
                history=[],
                attachments=None,
                backend=MagicMock(),
                llm_credentials=None,
            )
    finally:
        turn_runs.end_shutdown_salvage()

    assert closed, "shutdown salvage must sync-close the turn"
    assert released, "shutdown salvage must release the lease"
    assert not orphaned, "shutdown salvage must not orphan for sweeper"


@pytest.mark.asyncio
async def test_salvage_turns_on_shutdown_force_releases_timeout(monkeypatch):
    """After grace timeout, leftovers are force-closed and lease-released (no orphan)."""
    from agentcore.runtime.turn import runs as turn_runs_mod

    released: list[str] = []
    orphaned: list[str] = []
    closed: list[str] = []

    async def _fake_close(**kwargs):
        mid = kwargs.get("message_id")
        if mid:
            closed.append(mid)
        return True

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    async def _fake_orphan(mid, *, owner_id=None):
        orphaned.append(mid)

    sink = MagicMock()
    sink.message_id = "m-stuck"
    sink.execution_journal = MagicMock(return_value=[{"type": "content_delta"}])
    sink.streamed_content = MagicMock(return_value="partial output")

    leftover = TurnRun(
        run_id="r-stuck",
        conversation_id="conv-stuck",
        task=asyncio.create_task(asyncio.sleep(3600)),
        sink=sink,
    )

    async def _fake_stop_all(*, timeout: float = 20.0):
        return [leftover]

    monkeypatch.setattr(
        "agentcore.conversation.turn_persistence.close_user_stop_turn",
        _fake_close,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.release_turn_lease",
        _fake_release,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.orphan_turn_lease",
        _fake_orphan,
    )
    monkeypatch.setattr(turn_runs, "stop_all_and_drain", _fake_stop_all)

    turn_runs.end_shutdown_salvage()
    try:
        await turn_runs_mod.salvage_turns_on_shutdown(timeout=0.05)
        assert turn_runs.is_shutdown_salvage()
        assert closed == ["m-stuck"]
        assert released == ["m-stuck"]
        assert not orphaned
    finally:
        leftover.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await leftover.task
        turn_runs.end_shutdown_salvage()


@pytest.mark.asyncio
async def test_salvage_turns_on_shutdown_close_failure_orphans(monkeypatch):
    """Shutdown force-close failure must orphan lease, not release."""
    from agentcore.runtime.turn import runs as turn_runs_mod

    released: list[str] = []
    orphaned: list[str] = []

    async def _fake_close(**_kwargs):
        return False

    async def _fake_interrupt(**_kwargs):
        return False

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    async def _fake_orphan(mid, *, owner_id=None):
        orphaned.append(mid)

    sink = MagicMock()
    sink.message_id = "m-fail-close"
    leftover = TurnRun(
        run_id="r-fail-close",
        conversation_id="conv-fail-close",
        task=asyncio.create_task(asyncio.sleep(3600)),
        sink=sink,
    )

    async def _fake_stop_all(*, timeout: float = 20.0):
        return [leftover]

    monkeypatch.setattr(
        "agentcore.conversation.turn_persistence.close_user_stop_turn",
        _fake_close,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.close_turn_interrupted",
        _fake_interrupt,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.release_turn_lease",
        _fake_release,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.orphan_turn_lease",
        _fake_orphan,
    )
    monkeypatch.setattr(turn_runs, "stop_all_and_drain", _fake_stop_all)

    turn_runs.end_shutdown_salvage()
    try:
        await turn_runs_mod.salvage_turns_on_shutdown(timeout=0.05)
        assert orphaned == ["m-fail-close"]
        assert not released
    finally:
        leftover.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await leftover.task
        turn_runs.end_shutdown_salvage()


@pytest.mark.asyncio
async def test_salvage_failure_keeps_orphaned_lease(monkeypatch):
    """Sweeper must not release when salvage fails — row stays reclaimable."""
    from agentcore.runtime.leases import sweeper as sweeper_mod

    message_id = "m-fail"
    conversation_id = "c-fail"
    expired_row = SimpleNamespace(message_id=message_id, conversation_id=conversation_id)
    claimed_row = SimpleNamespace(
        message_id=message_id, conversation_id=conversation_id, user_id="u1"
    )
    released: list[str] = []
    orphaned: list[str] = []

    class _FakeLeaseRepo:
        def __init__(self, _session):
            pass

        async def list_expired(self, *, before, limit):
            return [expired_row]

        async def claim_expired(self, mid, *, new_owner_id, before, phase="recovering"):
            return claimed_row

        async def release(self, mid, *, owner_id=None):
            released.append(mid)

    class _FakePausedRepo:
        def __init__(self, _session):
            pass

        async def get(self, mid):
            return None

    class _FakeJournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            return []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def _boom(**kwargs):
        raise RuntimeError("db write failed")

    async def _fake_orphan(mid, *, owner_id=None):
        orphaned.append(mid)

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    monkeypatch.setattr(sweeper_mod, "TurnLeaseRepository", _FakeLeaseRepo)
    monkeypatch.setattr(sweeper_mod, "PausedTurnRepository", _FakePausedRepo)
    monkeypatch.setattr(sweeper_mod, "TurnJournalRepository", _FakeJournalRepo)
    monkeypatch.setattr(sweeper_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(sweeper_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(sweeper_mod, "salvage_no_dag_turn", _boom)
    monkeypatch.setattr(sweeper_mod, "orphan_turn_lease", _fake_orphan)
    monkeypatch.setattr(sweeper_mod, "release_turn_lease", _fake_release)

    started = await sweeper_mod.run_turn_lease_sweep()
    assert started == 0
    assert not released
    assert orphaned == [message_id]


@pytest.mark.asyncio
async def test_recover_salvage_failure_does_not_release(monkeypatch):
    from agentcore.runtime.recover import recover_expired_lease
    from agentcore.runtime.recover_hooks import set_crash_delegate_factory
    from agentcore.runtime.turn.state import TurnState

    message_id = "m-recover-fail"
    lease = SimpleNamespace(
        message_id=message_id,
        conversation_id="c1",
        user_id="u1",
        meta={"trace_id": "tr"},
        trace_id=None,
    )
    state = TurnState.from_journal([])
    released: list[str] = []
    orphaned: list[str] = []

    async def _fake_orphan_hot(**kwargs):
        return None

    async def _fake_salvage(**kwargs):
        return False

    async def _fake_release(mid, *, owner_id=None):
        released.append(mid)

    async def _fake_orphan_lease(mid, *, owner_id=None):
        orphaned.append(mid)

    set_crash_delegate_factory(None)
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.orphan_turn_before_recover",
        _fake_orphan_hot,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.sweeper.salvage_interrupted_turn",
        _fake_salvage,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.service.release_turn_lease",
        _fake_release,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.service.orphan_turn_lease",
        _fake_orphan_lease,
    )

    async def _fake_hb(message_id, *, owner_id=None, phase=None):
        return True

    async def _fake_hb_loop(message_id, *, owner_id, interval_seconds, stop, phase="running"):
        await stop.wait()

    monkeypatch.setattr(
        "agentcore.runtime.leases.service.heartbeat_turn_lease",
        _fake_hb,
    )
    monkeypatch.setattr(
        "agentcore.runtime.leases.service.lease_heartbeat_loop",
        _fake_hb_loop,
    )
    lease.meta = {"trace_id": "tr", "recover_attempts": 1}

    await recover_expired_lease(lease, state)
    assert not released
    assert orphaned == [message_id]


@pytest.mark.asyncio
async def test_repeated_salvage_skips_body_upsert(monkeypatch):
    """Already incomplete+terminal → skip content rewrite (no stacked suffix)."""
    upserts: list[dict] = []
    appended: list[dict] = []
    original = compose_interrupt_body("hello", reason=TurnInterruptReason.PROCESS_KILL)

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, mid, conversation_id=None):
            return SimpleNamespace(
                content=original,
                reasoning_content=None,
                trace_id="tr",
                usage={
                    "status": "incomplete",
                    "incomplete": True,
                    "finish_reason": FinishReason.INTERRUPTED.value,
                },
            )

        async def upsert_assistant(self, **kwargs):
            upserts.append(kwargs)

    class _JournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            return [
                {
                    "kind": "turn_end",
                    "payload": {"finish_reason": FinishReason.INTERRUPTED.value},
                }
            ]

        async def append(self, **kwargs):
            appended.append(kwargs)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _Store:
        async def list_stream_segments(self, *, turn_id):
            return []

        async def clear_stream_segments(self, *, turn_id):
            pass

    from agentcore.runtime.turn import interrupt as interrupt_mod

    monkeypatch.setattr(interrupt_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(interrupt_mod, "TurnJournalRepository", _JournalRepo)
    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.conversation.store.get_cloud_store",
        lambda: _Store(),
    )

    ok = await close_turn_interrupted(
        message_id="m1",
        conversation_id="c1",
        reason=TurnInterruptReason.PROCESS_KILL,
        load_stream_state=True,
    )
    assert ok is True
    assert upserts == []
    assert appended == []


@pytest.mark.asyncio
async def test_close_turn_interrupted_load_stream_state_merges_body_content(monkeypatch):
    """load_stream_state must pick_monotonic with passed salvage (reset-stash path)."""
    upserts: list[dict] = []

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, mid, conversation_id=None):
            return SimpleNamespace(
                content="",
                reasoning_content=None,
                trace_id="tr",
                usage={"status": "running"},
            )

        async def upsert_assistant(self, **kwargs):
            upserts.append(kwargs)

    class _JournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            return []

        async def append(self, **kwargs):
            return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _Store:
        async def list_stream_segments(self, *, turn_id):
            # Segment empty after content_reset cleared the checkpointer generation.
            return []

        async def clear_stream_segments(self, *, turn_id):
            pass

    from agentcore.runtime.turn import interrupt as interrupt_mod

    monkeypatch.setattr(interrupt_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(interrupt_mod, "TurnJournalRepository", _JournalRepo)
    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(
        "agentcore.conversation.store.get_cloud_store",
        lambda: _Store(),
    )
    monkeypatch.setattr(
        interrupt_mod,
        "_reconcile_interrupted_turn_cost",
        AsyncMock(return_value=None),
    )

    ok = await close_turn_interrupted(
        message_id="m-stash",
        conversation_id="c1",
        reason=TurnInterruptReason.USER_STOP,
        content="重置前已流式正文",
        load_stream_state=True,
    )
    assert ok is True
    assert upserts
    assert "重置前已流式正文" in (upserts[0].get("content") or "")


@pytest.mark.asyncio
async def test_close_turn_interrupted_ensures_turn_end_when_merge_persist_drops_it(
    monkeypatch,
):
    """Display-journal merge persist can no-op against denser progressive seqs — turn_end
    must still be live-appended so fold finish_reason is not empty."""
    appended: list[dict] = []
    load_calls = {"n": 0}

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
            load_calls["n"] += 1
            # Progressive facts already occupy low seqs; no turn_end yet.
            return [
                {"seq": 0, "kind": "run_plan", "payload": {}},
                {"seq": 1, "kind": "run_started", "payload": {"run_id": "r1"}},
                {"seq": 2, "kind": "run_completed", "payload": {"run_id": "r1"}},
                {"seq": 3, "kind": "process_content", "payload": {"text": "x"}},
            ]

        async def append(self, **kwargs):
            appended.append(kwargs)
            return 4

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _Store:
        async def clear_stream_segments(self, *, turn_id):
            pass

    from agentcore.runtime.turn import interrupt as interrupt_mod

    async def _persist_noop(*_a, **_k):
        # Simulate merge-mode persist that silently drops turn_end (seq conflict).
        return None

    monkeypatch.setattr(interrupt_mod, "MessageRepository", _MsgRepo)
    monkeypatch.setattr(interrupt_mod, "TurnJournalRepository", _JournalRepo)
    monkeypatch.setattr(interrupt_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(interrupt_mod, "persist_turn_journal", _persist_noop)
    monkeypatch.setattr(
        "agentcore.conversation.store.get_cloud_store",
        lambda: _Store(),
    )

    ok = await close_turn_interrupted(
        message_id="m1",
        conversation_id="c1",
        reason=TurnInterruptReason.USER_STOP,
        content="partial",
        journal=[{"type": "run_plan", "payload": {}}],
    )
    assert ok is True
    assert load_calls["n"] >= 1
    assert len(appended) == 1
    assert appended[0]["seq"] is None
    assert appended[0]["entry"]["kind"] == "turn_end"
    assert appended[0]["entry"]["payload"]["finish_reason"] == "cancelled"


def test_inject_cancelled_all_completed_copy():
    session = CoordinationSession(execution_id="e1", total_workers=2)
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.ALL_COMPLETED,
                payload={"completed": 1, "total": 2, "cancelled": True, "error": "x"},
            )
        ],
    )
    assert "任务已取消，基于已完成部分收口" in text
    assert "调度中断" not in text
    assert "团队已全部结束" not in text


def test_inject_drive_cancelled_copy():
    session = CoordinationSession(execution_id="e1", total_workers=2)
    text = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.DRIVE_CANCELLED,
                payload={"completed": 0, "total": 2},
            )
        ],
    )
    assert "drive_cancelled" in text
    assert "协调被打断，基于已完成部分收口" in text
    assert "调度中断" not in text
    assert "已开工队员禁止说成「没启动 / 没跑起来 / 一直未被启动」" in text


@pytest.mark.parametrize(
    "reason",
    [TurnInterruptReason.LEASE_EXPIRED, TurnInterruptReason.PROCESS_KILL],
)
def test_crash_close_with_nothing_streamed_says_so(reason):
    """案 519270db: a 19-minute team turn closed with 0 chars — the bubble said nothing.

    StatusStrip chrome does not survive into history; an empty assistant message is
    indistinguishable from「模型没回答」. Give it words.
    """
    assert compose_interrupt_body("", reason=reason) == INTERRUPTED_EMPTY_USER_VISIBLE


def test_crash_close_with_streamed_text_is_left_alone():
    """Guard against over-reach: partial content keeps the no-chrome-in-body rule."""
    body = compose_interrupt_body("已经写了一半的终稿", reason=TurnInterruptReason.LEASE_EXPIRED)
    assert body == "已经写了一半的终稿"
    assert "中断说明" not in body


def test_user_stop_with_nothing_streamed_stays_silent():
    """The user pressed stop — they know why it ended; do not lecture them."""
    assert compose_interrupt_body("", reason=TurnInterruptReason.USER_STOP) == ""
    assert empty_close_user_visible(TurnInterruptReason.USER_STOP) == ""
    assert empty_close_user_visible("user_stop") == ""


def test_overlap_empty_close_is_user_visible():
    """New message squeezed the old turn — not a Stop; the bubble must say why."""
    assert compose_interrupt_body("", reason=TurnInterruptReason.OVERLAP) == (
        OVERLAP_EMPTY_USER_VISIBLE
    )
    assert "新消息" in OVERLAP_EMPTY_USER_VISIBLE
    assert "run" not in OVERLAP_EMPTY_USER_VISIBLE.lower()
    assert empty_close_user_visible(TurnInterruptReason.OVERLAP) == OVERLAP_EMPTY_USER_VISIBLE
    assert normalize_interrupt_reason("overlap") is TurnInterruptReason.OVERLAP
    assert finish_reason_for(TurnInterruptReason.OVERLAP) is FinishReason.INTERRUPTED


def test_empty_close_paths_have_user_visible_copy():
    """Non-USER_STOP empty closes each have a product-facing sentence."""
    assert empty_close_user_visible("harvest_yield") == HARVEST_YIELD_EMPTY_USER_VISIBLE
    assert empty_close_user_visible("max_rounds") == MAX_ROUNDS_EMPTY_USER_VISIBLE
    assert "未产出回复" in HARVEST_YIELD_EMPTY_USER_VISIBLE
    assert "未产出回复" in MAX_ROUNDS_EMPTY_USER_VISIBLE
    for note in (
        OVERLAP_EMPTY_USER_VISIBLE,
        HARVEST_YIELD_EMPTY_USER_VISIBLE,
        MAX_ROUNDS_EMPTY_USER_VISIBLE,
    ):
        assert "【中断说明】" in note
        assert "harvest" not in note.lower()
        assert "max_rounds" not in note
        assert "salvage" not in note.lower()
        assert "已完成" not in note


def test_sweeper_reasons_do_not_claim_a_kill_nobody_saw():
    """案 519270db 附带项：`process_kill` 曾是清扫兜底的默认值，两轮排查都被它带偏。

    A lease that stopped beating is all the sweeper knows. The literal stays mapped
    for historical rows and for callers that genuinely observed a termination.
    """
    assert normalize_interrupt_reason("no_dag") is TurnInterruptReason.LEASE_EXPIRED
    assert normalize_interrupt_reason("") is TurnInterruptReason.LEASE_EXPIRED
    assert normalize_interrupt_reason("whatever") is TurnInterruptReason.LEASE_EXPIRED
    assert normalize_interrupt_reason("process_kill") is TurnInterruptReason.PROCESS_KILL
    # Both are non-user terminations, so both still land on `interrupted`.
    assert finish_reason_for(TurnInterruptReason.LEASE_EXPIRED) is FinishReason.INTERRUPTED
