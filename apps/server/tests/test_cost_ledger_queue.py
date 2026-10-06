"""Shared cost ledger durable queue: enqueue / drain / idempotency / multi-worker."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentcore.billing import cost_ledger_queue as queue_mod
from agentcore.billing.cost_ledger_queue import MemoryOutboxBackend


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture
def ledger_queue(monkeypatch, tmp_path):
    backend = MemoryOutboxBackend()
    queue = queue_mod.reset_cost_ledger_queue_for_tests(backend=backend)
    monkeypatch.setattr(queue_mod.settings, "data_dir", str(tmp_path))
    return queue


def _sample_runs(*, run_id: str = "run-1") -> list[dict]:
    return [
        {
            "run_id": run_id,
            "agent_id": "CEO",
            "role": "captain",
            "model": "m",
            "tokens": {"input": 1, "output": 2},
            "cost": {},
            "cost_total_nano": 0,
            "currency": "USD",
            "rounds": 1,
            "duration_ms": 10,
        }
    ]


def _sample_call(*, call_id: str, run_id: str = "run-assist") -> dict:
    return {
        "call_id": call_id,
        "run_id": run_id,
        "parent_run_id": None,
        "agent_id": None,
        "role": "assist",
        "persona": "AI 改写",
        "model": "m",
        "tokens": {"input": 1, "output": 2},
        "cost": {},
        "cost_total_nano": 7,
        "cost_estimated_nano": 0,
        "currency": "CNY",
        "duration_ms": 10,
    }


async def test_enqueue_runs_then_drain_records(monkeypatch, ledger_queue):
    calls: list = []

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    rid = ledger_queue.enqueue_runs(
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        trace_id="t1",
        runs=_sample_runs(),
        source="turn",
    )
    assert rid is not None
    assert await ledger_queue.drain_once() == 1
    assert calls and calls[0]["message_id"] == "m1"
    assert calls[0]["runs"][0]["run_id"] == "run-1"
    assert await ledger_queue._backend.pending_count() == 0


async def test_drain_retries_after_write_failure(monkeypatch, ledger_queue):
    """Sync write failed → enqueue; first drain fails; second drain succeeds."""
    attempts = {"n": 0}
    calls: list = []

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("db down")
            calls.append(kw)
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    ledger_queue.enqueue_runs(
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        runs=_sample_runs(run_id="run-retry"),
        source="handoff",
    )
    assert await ledger_queue.drain_once() == 0
    assert await ledger_queue._backend.pending_count() == 1
    assert calls == []

    assert await ledger_queue.drain_once() == 1
    assert calls[0]["runs"][0]["run_id"] == "run-retry"
    assert await ledger_queue._backend.pending_count() == 0


async def test_drains_legacy_proxy_spend_dir(monkeypatch, ledger_queue, tmp_path):
    """Files left in the old proxy_spend_queue path are still consumed."""
    calls: list = []

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    legacy = tmp_path / "telemetry" / "proxy_spend_queue"
    legacy.mkdir(parents=True)
    (legacy / "legacy.json").write_text(
        json.dumps(
            {
                "id": "legacy-1",
                "user_id": "u1",
                "conversation_id": "c1",
                "runs": _sample_runs(run_id="legacy-run"),
            }
        ),
        encoding="utf-8",
    )
    assert await ledger_queue.drain_once() == 1
    assert calls[0]["runs"][0]["run_id"] == "legacy-run"


async def test_drains_legacy_cost_ledger_disk_dir(monkeypatch, ledger_queue, tmp_path):
    """Pre-migration cost_ledger_queue/*.json files are drained once."""
    calls: list = []

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    qdir = tmp_path / "telemetry" / "cost_ledger_queue"
    qdir.mkdir(parents=True)
    (qdir / "old.json").write_text(
        json.dumps(
            {
                "id": "disk-1",
                "user_id": "u1",
                "conversation_id": "c1",
                "runs": _sample_runs(run_id="disk-run"),
            }
        ),
        encoding="utf-8",
    )
    assert await ledger_queue.drain_once() == 1
    assert calls[0]["runs"][0]["run_id"] == "disk-run"
    assert list(qdir.glob("*.json")) == []


async def test_oserror_on_read_leaves_file_for_retry(monkeypatch, ledger_queue, tmp_path):
    """Transient OSError must not quarantine to .corrupt (leave for retry)."""
    qdir = tmp_path / "telemetry" / "cost_ledger_queue"
    qdir.mkdir(parents=True)
    path = qdir / "transient.json"
    path.write_text(
        json.dumps(
            {
                "id": "t1",
                "user_id": "u1",
                "conversation_id": "c1",
                "runs": _sample_runs(run_id="run-os"),
            }
        ),
        encoding="utf-8",
    )

    real_read = Path.read_text
    calls = {"n": 0}

    def flaky_read(self, *args, **kwargs):
        if self == path and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("simulated share violation")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read)

    assert await ledger_queue.drain_once() == 0
    assert path.exists()
    assert list(qdir.glob("*.corrupt")) == []

    written: list = []

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **kw):
            written.append(kw)
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    assert await ledger_queue.drain_once() == 1
    assert written[0]["runs"][0]["run_id"] == "run-os"
    assert not path.exists()


async def test_bad_json_still_quarantines(ledger_queue, tmp_path):
    """Malformed JSON remains poison → .corrupt, never retried as valid."""
    qdir = tmp_path / "telemetry" / "cost_ledger_queue"
    qdir.mkdir(parents=True)
    path = qdir / "bad.json"
    path.write_text("{not-json", encoding="utf-8")

    assert await ledger_queue.drain_once() == 0
    assert not path.exists()
    corrupt = list(qdir.glob("*.corrupt"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == "{not-json"


async def test_invalid_payload_still_quarantines(ledger_queue, tmp_path):
    """Valid JSON missing required fields still quarantines."""
    qdir = tmp_path / "telemetry" / "cost_ledger_queue"
    qdir.mkdir(parents=True)
    path = qdir / "invalid.json"
    path.write_text(
        json.dumps({"id": "x", "user_id": "u1", "runs": [], "calls": []}),
        encoding="utf-8",
    )

    assert await ledger_queue.drain_once() == 0
    assert not path.exists()
    assert list(qdir.glob("*.corrupt"))


async def test_enqueue_account_level_row_without_a_conversation(monkeypatch, ledger_queue):
    """AI 改写: no conversation, and the outbox carries it anyway.

    ``conversation_id`` used to be a hard requirement here, so this spend was
    dropped at enqueue. It now rides through to the sink as NULL — the row is
    account-level, not mis-filed onto some unrelated chat.
    """
    calls: list = []

    class Repo:
        def __init__(self, _session):
            pass

        async def record_calls(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    rid = await ledger_queue.enqueue_calls_async(
        user_id="u1",
        conversation_id=None,
        calls=[_sample_call(call_id="call-assist")],
        source="inprocess_call",
    )
    assert rid is not None
    assert await ledger_queue.drain_once() == 1
    assert calls[0]["conversation_id"] is None
    assert calls[0]["message_id"] is None
    assert calls[0]["calls"][0]["call_id"] == "call-assist"


async def test_enqueue_without_an_account_is_refused(ledger_queue):
    """``user_id`` is the one envelope key the ledger cannot do without.

    Every account window and quota SUM keys on it, so a row without an owner is
    unbillable — refuse at enqueue rather than write money nobody owns.
    """
    assert (
        ledger_queue.enqueue_runs(
            user_id="",
            conversation_id="c1",
            runs=_sample_runs(run_id="run-ownerless"),
            source="turn",
        )
        is None
    )
    await ledger_queue._await_pending_enqueues()
    assert await ledger_queue._backend.pending_count() == 0


async def test_ownerless_disk_payload_quarantines(ledger_queue, tmp_path):
    """A legacy record with spend but no account can never be written — poison."""
    qdir = tmp_path / "telemetry" / "cost_ledger_queue"
    qdir.mkdir(parents=True)
    path = qdir / "ownerless.json"
    path.write_text(
        json.dumps({"id": "x", "runs": _sample_runs(run_id="run-no-owner")}),
        encoding="utf-8",
    )

    assert await ledger_queue.drain_once() == 0
    assert not path.exists()
    assert list(qdir.glob("*.corrupt"))


async def test_idempotent_redrain_same_run_id(monkeypatch, ledger_queue):
    """At-least-once: re-processing the same payload reuses run_id (sink dedupes)."""
    seen: list[str] = []

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **kw):
            seen.append(kw["runs"][0]["run_id"])
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    await ledger_queue.enqueue_runs_async(
        user_id="u1",
        conversation_id="c1",
        runs=_sample_runs(run_id="run-idem"),
        source="turn",
    )
    assert await ledger_queue.drain_once() == 1

    # Re-insert same logical payload (simulates ack loss / at-least-once retry).
    await ledger_queue.enqueue_runs_async(
        user_id="u1",
        conversation_id="c1",
        runs=_sample_runs(run_id="run-idem"),
        source="turn",
    )
    assert await ledger_queue.drain_once() == 1
    assert seen == ["run-idem", "run-idem"]


async def test_multi_fake_worker_interleaved_drain(monkeypatch):
    """Two queue instances sharing one MemoryOutboxBackend never double-drain."""
    shared = MemoryOutboxBackend()
    q1 = queue_mod.CostLedgerQueue(backend=shared)
    q2 = queue_mod.CostLedgerQueue(backend=shared)

    sink_calls: list[str] = []
    barrier = asyncio.Event()
    release = asyncio.Event()

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **kw):
            rid = kw["runs"][0]["run_id"]
            sink_calls.append(rid)
            if rid == "run-slow":
                barrier.set()
                await release.wait()
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    await shared.insert(
        {
            "id": "a",
            "user_id": "u1",
            "conversation_id": "c1",
            "runs": _sample_runs(run_id="run-slow"),
            "calls": [],
            "materialize_runs": False,
            "source": "turn",
        }
    )
    await shared.insert(
        {
            "id": "b",
            "user_id": "u1",
            "conversation_id": "c1",
            "runs": _sample_runs(run_id="run-fast"),
            "calls": [],
            "materialize_runs": False,
            "source": "turn",
        }
    )

    t1 = asyncio.create_task(q1.drain_once())
    await barrier.wait()
    # While q1 holds run-slow in-flight, q2 should only take run-fast.
    n2 = await q2.drain_once()
    release.set()
    n1 = await t1

    assert n1 + n2 == 2
    assert sorted(sink_calls) == ["run-fast", "run-slow"]
    assert await shared.pending_count() == 0


async def test_enqueue_db_failure_falls_back_to_disk(monkeypatch, ledger_queue, tmp_path):
    """DB insert failure → disk fallback + observable error log (not silent drop)."""

    async def boom(_payload):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(ledger_queue._backend, "insert", boom)

    rid = ledger_queue.enqueue_runs(
        user_id="u1",
        conversation_id="c1",
        runs=_sample_runs(run_id="run-fb"),
        source="turn",
    )
    assert rid is not None
    await ledger_queue._await_pending_enqueues()
    files = list((tmp_path / "telemetry" / "cost_ledger_queue").glob("*.json"))
    assert len(files) == 1


async def test_stop_cancels_before_final_drain(monkeypatch, ledger_queue, tmp_path):
    """stop: cancel loop first, then single-threaded final drain (no stop∩loop race)."""
    order: list[str] = []
    barrier = asyncio.Event()

    async def slow_drain_loop():
        order.append("loop_enter")
        barrier.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("loop_cancelled")
            raise

    async def tracking_drain_once():
        order.append("final_drain")
        return 0

    ledger_queue._task = asyncio.create_task(slow_drain_loop(), name="cost_ledger_drain")
    await barrier.wait()
    monkeypatch.setattr(ledger_queue, "drain_once", tracking_drain_once)

    await ledger_queue.stop()

    assert order == ["loop_enter", "loop_cancelled", "final_drain"]
    assert ledger_queue._task is None


class _LevelSpy:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, **kwargs) -> None:
        self.events.append((level, event, kwargs))

    def debug(self, event: str, **kwargs) -> None:
        self._record("debug", event, **kwargs)

    def info(self, event: str, **kwargs) -> None:
        self._record("info", event, **kwargs)

    def warning(self, event: str, **kwargs) -> None:
        self._record("warning", event, **kwargs)

    def error(self, event: str, **kwargs) -> None:
        self._record("error", event, **kwargs)


async def test_enqueue_and_drain_happy_path_logs_are_debug(monkeypatch, ledger_queue):
    """Per-call enqueue/drain acks must not flood info-level jsonl (ledger is Postgres)."""
    spy = _LevelSpy()
    monkeypatch.setattr(queue_mod, "logger", spy)

    class Repo:
        def __init__(self, _session):
            pass

        async def record_runs(self, **_kw):
            return 1

    monkeypatch.setattr("agentcore.db.base.telemetry_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("agentcore.db.repositories.CostEventRepository", Repo)

    rid = ledger_queue.enqueue_runs(
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        runs=_sample_runs(),
        source="turn",
    )
    assert rid is not None
    assert await ledger_queue.drain_once() == 1

    names = [(level, event) for level, event, _ in spy.events]
    assert ("debug", "cost.ledger_enqueued") in names
    assert ("debug", "cost.ledger_drained") in names
    assert ("info", "cost.ledger_enqueued") not in names
    assert ("info", "cost.ledger_drained") not in names
