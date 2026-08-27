"""Serial write-behind journal writer: bounded to one connection + turn-end drain.

The prior model fanned out a task-(and-connection-)per-fact; under a wide parallel
delegation that stormed the pool (asyncpg ``connection_lost`` + non-checked-in-connection
GC). These tests pin the new invariants: at most one in-flight write (no fan-out storm),
emit-ordered ``seq``, every Future resolved (the SSE barrier can never hang), and
best-effort degradation that still drains the rest.
"""

from __future__ import annotations

import asyncio

from agentcore.runtime.journal.seq_space import (
    JOURNAL_LIVE_BAND_WARN_HEADROOM,
    JOURNAL_OVERFLOW_SEQ_START,
    next_live_seq,
    next_overflow_seq,
    replace_prefix_map,
)
from agentcore.runtime.journal.writer import TurnJournalWriter


class _SessionTracker:
    """Counts concurrently-open fake sessions so a fan-out regression shows as max_open > 1."""

    def __init__(self) -> None:
        self.open = 0
        self.max_open = 0

    def factory(self) -> _FakeSession:
        return _FakeSession(self)


class _FakeSession:
    def __init__(self, tracker: _SessionTracker) -> None:
        self._t = tracker

    async def __aenter__(self) -> _FakeSession:
        self._t.open += 1
        self._t.max_open = max(self._t.max_open, self._t.open)
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._t.open -= 1
        return False


def _patch(monkeypatch, tracker: _SessionTracker, repo_cls: type) -> None:
    monkeypatch.setattr(
        "agentcore.conversation.store.cloud.telemetry_session_factory", tracker.factory
    )
    monkeypatch.setattr(
        "agentcore.conversation.store.cloud.TurnJournalRepository", repo_cls
    )
    monkeypatch.setattr(
        "agentcore.runtime.audit.hooks.on_journal_fact_appended", lambda entry: None
    )


async def test_appends_serialized_ordered_and_all_futures_resolve(monkeypatch) -> None:
    tracker = _SessionTracker()
    written: list[int] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(
            self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False
        ) -> int | None:
            # Yield twice: an overlapping (fanned-out) drain would surface as max_open > 1.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            written.append(seq)
            return len(written) - 1

    _patch(monkeypatch, tracker, Repo)
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")

    futures = [writer.schedule_append({"kind": f"k{i}"}) for i in range(25)]
    await writer.flush()

    assert tracker.max_open == 1  # bounded to a single connection — no fan-out storm
    # D7 live: seq=None（DB 分配）；仍串行、emit 序、Future 全 resolve
    assert written == [None] * 25
    assert all(f is not None and f.done() for f in futures)
    assert writer.degraded is False


async def test_write_failure_degrades_but_never_hangs(monkeypatch) -> None:
    tracker = _SessionTracker()
    written: list[int] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(
            self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False
        ) -> int | None:
            if entry.get("kind") == "bad":
                raise RuntimeError("boom")
            written.append(seq)
            return len(written) - 1

    _patch(monkeypatch, tracker, Repo)
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")

    f_ok = writer.schedule_append({"kind": "ok"})
    f_bad = writer.schedule_append({"kind": "bad"})
    f_ok2 = writer.schedule_append({"kind": "ok2"})
    await writer.flush()

    assert writer.degraded is True  # the failure is surfaced (turn journal degraded)
    assert written == [None, None]  # bad skipped; live seq=None
    assert all(f is not None and f.done() for f in (f_ok, f_bad, f_ok2))  # none hang


async def test_flush_without_any_appends_is_noop() -> None:
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")
    await writer.flush()  # no drain task ever started → returns immediately
    assert writer.degraded is False


async def test_seal_stops_further_durable_appends(monkeypatch) -> None:
    """After seal, schedule_append must not write more DB rows (pause hard boundary)."""
    tracker = _SessionTracker()
    written: list[int] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(
            self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False
        ) -> int | None:
            written.append(seq)
            return len(written) - 1

    _patch(monkeypatch, tracker, Repo)
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")

    f0 = writer.schedule_append({"kind": "pre"})
    await writer.seal()

    assert writer.sealed is True
    assert writer.next_seq == 1
    assert written == [None]
    assert f0 is not None and f0.done()

    # Post-seal pause-stream appends are durable no-ops: no Future, no seq bump, no DB write.
    assert writer.schedule_append({"kind": "post"}) is None
    assert writer.schedule_append({"kind": "post2"}) is None
    await writer.flush()
    assert written == [None]
    assert writer.next_seq == 1

    # Idempotent seal.
    await writer.seal()
    assert writer.sealed is True
    assert written == [None]


async def test_seal_overflows_run_terminals_without_writing_pause_stream(monkeypatch) -> None:
    """Post-seal run_* terminals land on overflow; pause-stream kinds stay frozen."""
    from structlog.testing import capture_logs

    tracker = _SessionTracker()
    written: list[dict] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(
            self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False
        ) -> int | None:
            written.append(dict(entry))
            return len(written) - 1

    _patch(monkeypatch, tracker, Repo)
    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")

    pre = writer.schedule_append({"kind": "pre"})
    await writer.seal()
    assert writer.sealed is True
    assert writer.writable() is not writer
    assert writer.writable().sealed is False
    assert [e.get("kind") for e in written] == ["pre"]
    assert pre is not None and pre.done()

    with capture_logs() as logs:
        assert writer.schedule_append({"kind": "team_preview_required"}) is None
        fut = writer.schedule_append(
            {"kind": "run_completed", "payload": {"run_id": "w1"}}
        )
        assert fut is not None
        await writer.flush()

    kinds = [e.get("kind") for e in written]
    assert kinds == ["pre", "run_completed"]
    events = [e.get("event") for e in logs]
    assert "journal.sealed_skip" in events
    assert "journal.sealed_overflow" in events


def test_sealed_overflow_without_loop_logs_drop_and_skips_fact_log() -> None:
    """No event loop after seal: overflow kind errors, fact log stays empty so 对账能响."""
    from structlog.testing import capture_logs

    from agentcore.runtime.coordination.session import CoordinationSession
    from agentcore.runtime.facts import Fact, TurnFactLog, current_fact_log, record_turn_fact
    from agentcore.runtime.journal.writer import current_journal_writer

    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id=None)
    writer._sealed = True  # noqa: SLF001 — isolate enqueue without a running loop
    log = TurnFactLog()
    wt = current_journal_writer.set(writer)
    fl = current_fact_log.set(log)
    try:
        with capture_logs() as logs:
            fut = record_turn_fact(Fact(kind="run_completed", payload={"run_id": "w1"}))
        assert fut is None
        assert log.entries() == []
        assert any(e.get("event") == "journal.sealed_drop" for e in logs)
        session = CoordinationSession(execution_id="e1", total_workers=1)
        session.turn_attached = False
        session.completed_run_ids.add("w1")
        session.mark_settled("detached")
        with capture_logs() as settle_logs:
            session.check_terminal_settlement(journal_entries=log.entries())
        assert any(
            e.get("event") == "coordination.terminal_unsettled" for e in settle_logs
        )
    finally:
        current_fact_log.reset(fl)
        current_journal_writer.reset(wt)


async def test_seal_rebinds_live_host_journal_writer(monkeypatch) -> None:
    """seal() points the coordination session at the unsealed overflow writer."""
    from agentcore.runtime.coordination.session import (
        CoordinationSession,
        bind_host_journal,
        clear_active_coordination,
        set_active_coordination,
    )

    tracker = _SessionTracker()

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(
            self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False
        ) -> int | None:
            return 0

    _patch(monkeypatch, tracker, Repo)
    writer = TurnJournalWriter(turn_id="host-turn", conversation_id="c1", trace_id=None)
    session = CoordinationSession(
        execution_id="exec-seal-rebind",
        total_workers=1,
        conversation_id="c1",
    )
    bind_host_journal(session, writer=writer, turn_id="host-turn")
    set_active_coordination(session)
    try:
        assert session.host_journal_writer is writer
        await writer.seal()
        host = session.host_journal_writer
        assert host is not writer
        assert host is writer.writable()
        assert getattr(host, "sealed", True) is False
    finally:
        clear_active_coordination("exec-seal-rebind")


def _sorted_kinds(journal: dict[str, dict]) -> list[str]:
    return [
        str(journal[k].get("kind") or "")
        for k in sorted(journal, key=lambda key: int(key))
    ]


def test_replace_prefix_keeps_overflow_band_omitted_by_snapshot() -> None:
    """Prefix rewrite occupies 0..n-1; overflow-band facts are outside that range."""
    snapshot = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-2"}},
    ]
    existing = {
        "0": {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        "1": {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-1"}},
        str(JOURNAL_OVERFLOW_SEQ_START): {
            "kind": "run_completed",
            "payload": {"run_id": "w1"},
        },
        str(JOURNAL_OVERFLOW_SEQ_START + 1): {
            "kind": "execution_completed",
            "payload": {"execution_id": "e1"},
        },
    }
    replaced = replace_prefix_map(snapshot, existing)
    kinds = _sorted_kinds(replaced)
    assert kinds[:2] == ["run_plan", "team_preview_required"]
    assert replaced["1"]["payload"]["checkpoint_id"] == "ck-2"
    assert "run_completed" in kinds
    assert "execution_completed" in kinds
    assert replaced[str(JOURNAL_OVERFLOW_SEQ_START)]["payload"]["run_id"] == "w1"


def test_replace_prefix_keeps_late_fact_of_unlisted_kind() -> None:
    """A late higher-seq fact survives rewrite even when it is not an overflow terminal."""
    snapshot = [
        {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-2"}},
    ]
    existing = {
        "0": {"kind": "run_plan", "payload": {"execution_id": "e1"}},
        "1": {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-1"}},
        "2": {"kind": "note", "payload": {"content": "late-unrelated"}},
    }
    replaced = replace_prefix_map(snapshot, existing)
    kinds = _sorted_kinds(replaced)
    assert kinds[:2] == ["run_plan", "team_preview_required"]
    assert replaced["1"]["payload"]["checkpoint_id"] == "ck-2"
    assert "note" in kinds
    assert replaced["2"]["payload"]["content"] == "late-unrelated"


def test_replace_prefix_growing_snapshot_does_not_eat_overflow_band() -> None:
    """A longer later prefix still cannot collide with overflow-band seqs."""
    snapshot = [
        {"kind": "run_started", "payload": {"run_id": "w1"}},
        {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-2"}},
    ]
    existing = {
        "0": {"kind": "run_started", "payload": {"run_id": "w1"}},
        str(JOURNAL_OVERFLOW_SEQ_START): {
            "kind": "run_completed",
            "payload": {"run_id": "w1"},
        },
    }
    replaced = replace_prefix_map(snapshot, existing)
    kinds = _sorted_kinds(replaced)
    assert kinds[:2] == ["run_started", "team_preview_required"]
    assert "run_completed" in kinds
    assert str(JOURNAL_OVERFLOW_SEQ_START) in replaced


def test_live_seq_allocation_ignores_overflow_band() -> None:
    seqs = [0, 1, JOURNAL_OVERFLOW_SEQ_START, JOURNAL_OVERFLOW_SEQ_START + 3]
    assert next_live_seq(seqs) == 2
    assert next_overflow_seq(seqs) == JOURNAL_OVERFLOW_SEQ_START + 4
    assert next_overflow_seq([0, 1]) == JOURNAL_OVERFLOW_SEQ_START


def test_live_seq_near_overflow_band_warns() -> None:
    """Live-band allocation must not silently collide with the overflow split."""
    from structlog.testing import capture_logs

    with capture_logs() as quiet:
        assert next_live_seq([0, 1]) == 2
    assert "journal.live_seq_near_overflow" not in [e.get("event") for e in quiet]

    approaching = JOURNAL_OVERFLOW_SEQ_START - JOURNAL_LIVE_BAND_WARN_HEADROOM
    with capture_logs() as logs:
        nxt = next_live_seq([approaching - 1])
    assert nxt == approaching  # still in live band; allocation unchanged
    hit = next(e for e in logs if e.get("event") == "journal.live_seq_near_overflow")
    assert hit["seq"] == approaching
    assert hit["overflow_start"] == JOURNAL_OVERFLOW_SEQ_START
    assert hit["remaining"] == JOURNAL_LIVE_BAND_WARN_HEADROOM
    assert hit["op"] == "next_live_seq"

    with capture_logs() as logs:
        nxt = next_live_seq([JOURNAL_OVERFLOW_SEQ_START - 1])
    assert nxt == JOURNAL_OVERFLOW_SEQ_START  # allocation unchanged
    hit = next(e for e in logs if e.get("event") == "journal.live_seq_near_overflow")
    assert hit["seq"] == JOURNAL_OVERFLOW_SEQ_START
    assert hit["remaining"] == 0
