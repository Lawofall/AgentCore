"""OutboxStore progressive write + idempotency tests (as-built: 双模式工作区 §10.3)."""

from __future__ import annotations

import asyncio
import json

import pytest

from agentcore.conversation.store import reset_conversation_store_for_tests
from agentcore.conversation.store.outbox import (
    PHASE_OPEN,
    PHASE_READY,
    OutboxStore,
    captain_text_from_stream_segments,
    list_outbox_records,
    to_record_turn_body,
)
from agentcore.runtime.turn.interrupt import INTERRUPTED_EMPTY_USER_VISIBLE


@pytest.fixture(autouse=True)
def _reset_conversation_store():
    yield
    reset_conversation_store_for_tests()


def _drive(coro):
    return asyncio.run(coro)


def test_progressive_begin_journal_finalize(tmp_path):
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hello",
        message_id="m1",
        trace_id="a" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="a" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="a" * 32,
            entry={"kind": "run_started", "payload": {}},
        )
        await store.append_journal(
            turn_id="m1",
            seq=0,  # duplicate seq — ignored
            conversation_id="c1",
            trace_id="a" * 32,
            entry={"kind": "SHOULD_NOT_REPLACE", "payload": {}},
        )
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hello",
            user_message_id="u1",
            assistant_content="Hello world",
            message_id="m1",
            trace_id="a" * 32,
            finish_reason="stop",
            input_tokens=3,
            output_tokens=2,
            runs={"events": []},
        )
        # Second finalize is a no-op seal.
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hello",
            user_message_id="u1",
            assistant_content="SHORTER",
            message_id="m1",
            trace_id="a" * 32,
            finish_reason="stop",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["phase"] == PHASE_READY
    assert record["content"] == "Hello world"
    assert record["journal"] == {
        "0": {"kind": "run_started", "payload": {}, "ord": 0}
    }
    assert record["ops"][0] == "begin_turn"
    assert "finalize" in record["ops"]
    body = to_record_turn_body(record)
    assert body["user_message_id"] == "u1"
    assert body["content"] == "Hello world"
    assert body["input_tokens"] == 3


def test_finalize_complete_overrides_longer_partial(tmp_path):
    """Happy-path finalize may replace a longer mid-stream draft body."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="f" * 32,
    )
    path = tmp_path / "outbox" / "u1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "user_message_id": "u1",
                "conversation_id": "c1",
                "message_id": "m1",
                "user_message": "hi",
                "content": "a long mid-stream draft that spilled past the final",
                "phase": PHASE_OPEN,
                "ops": ["begin_turn"],
                "journal": {},
                "stream_segments": {},
            }
        ),
        encoding="utf-8",
    )

    async def run() -> dict:
        await store.finalize(
            conversation_id="c1",
            user_message="hi",
            assistant_content="final",
            user_message_id="u1",
            message_id="m1",
            trace_id="f" * 32,
            finish_reason="end_turn",
        )
        return json.loads(path.read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["content"] == "final"


def test_begin_turn_idempotent(tmp_path):
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="b" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="b" * 32)
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="b" * 32)
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["ops"].count("begin_turn") == 1


def test_salvage_seals_ready_when_settlement_has_resume_frame(tmp_path):
    """User stop after settlement: seal cancelled + READY (no frameless retain-open)."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="f" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="f" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="f" * 32,
            entry={
                "kind": "team_preview_resolved",
                "payload": {
                    "checkpoint_id": "tp1",
                    "decision": "continue",
                    "resume_frame": {"frame": {"kind": "team_preview"}},
                },
                "ts": None,
            },
        )
        await store.salvage(
            journal=[],
            content="partial+",
            conversation_id="c1",
            trace_id="f" * 32,
            message_id="m1",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["phase"] == PHASE_READY
    assert "salvage" in record["ops"]
    assert "salvage_retain_open" not in record.get("ops", [])
    assert record.get("finish_reason") == "cancelled"


def test_salvage_seals_ready_even_when_later_gate_pending(tmp_path):
    """Salvage seals READY even if journal also has a later cold gate."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="h" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="h" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="h" * 32,
            entry={
                "kind": "team_preview_resolved",
                "payload": {
                    "checkpoint_id": "tp1",
                    "decision": "continue",
                    "resume_frame": {"frame": {"kind": "team_preview"}},
                },
                "ts": None,
            },
        )
        await store.append_journal(
            turn_id="m1",
            seq=1,
            conversation_id="c1",
            trace_id="h" * 32,
            entry={
                "kind": "checkpoint_required",
                "payload": {"checkpoint_id": "cp2"},
                "ts": None,
            },
        )
        await store.salvage(
            journal=[],
            content="partial",
            conversation_id="c1",
            trace_id="h" * 32,
            message_id="m1",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["phase"] == PHASE_READY
    assert record.get("finish_reason") == "cancelled"
    assert "salvage" in record["ops"]
    assert "salvage_retain_open" not in record.get("ops", [])


def test_salvage_marks_ready(tmp_path):
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="c" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="c" * 32)
        await store.salvage(
            journal=[{"kind": "x"}],
            content="partial+",
            conversation_id="c1",
            trace_id="c" * 32,
            message_id="m1",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["phase"] == PHASE_READY
    assert record["content"] == "partial+"
    assert record["finish_reason"] == "cancelled"
    assert "salvage" in record["ops"]
    body = to_record_turn_body(record)
    assert body["finish_reason"] == "cancelled"
    assert body["journal"] == [{"kind": "x"}]


def test_to_record_turn_body_includes_sorted_journal(tmp_path):
    """Crash salvage: runs=None but journal map must ride the write-back body.

    Order follows ``ord`` (write/emission order), stripped from the wire list.
    """
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="e" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="e" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="e" * 32,
            entry={"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
        )
        await store.append_journal(
            turn_id="m1",
            seq=2,
            conversation_id="c1",
            trace_id="e" * 32,
            entry={"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
        )
        await store.salvage(
            journal=[],
            content="partial",
            conversation_id="c1",
            trace_id="e" * 32,
            message_id="m1",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["runs"] is None
    body = to_record_turn_body(record)
    assert "runs" in body
    assert body["runs"] is None
    assert body["journal"] == [
        {"kind": "run_started", "payload": {"id": "r1"}, "ts": "t0"},
        {"kind": "run_completed", "payload": {"id": "r1"}, "ts": None},
    ]
    assert body["finish_reason"] == "cancelled"
    # Sidecar emit / outbox journal stay progressive — PG finalize fills turn_end.
    assert all(e.get("kind") != "turn_end" for e in body["journal"])


def test_list_outbox_records(tmp_path):
    base = tmp_path / "outbox"
    store = OutboxStore(base)
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="a",
        message_id="m1",
        trace_id="d" * 32,
    )
    _drive(store.begin_turn(conversation_id="c1", message_id="m1", trace_id="d" * 32))
    (base / "torn.json").write_text("{", encoding="utf-8")
    records = list_outbox_records(base)
    assert len(records) == 1
    assert records[0]["user_message_id"] == "u1"


def test_stream_segments_survive_hard_kill_without_salvage(tmp_path):
    """D6: StreamCheckpointer flush lands on disk; hard-kill skips salvage but snapshots remain."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hello",
        message_id="m1",
        trace_id="g" * 32,
    )

    async def run() -> None:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="g" * 32)
        await store.upsert_stream_segments(
            turn_id="m1",
            segments=[
                ("captain:content", "half-written reply", 0),
                ("captain:reasoning", "thinking…", 0),
            ],
        )
        # Simulate hard-kill: no salvage / finalize / clear_turn — just drop the process.
        # (ctx stays bound here; the durable proof is the file on disk.)

    _drive(run())
    path = tmp_path / "outbox" / "u1.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["phase"] == "open"
    assert (record.get("content") or "") == ""
    assert record["stream_segments"]["captain:content"] == {
        "text": "half-written reply",
        "generation": 0,
    }
    assert record["stream_segments"]["captain:reasoning"] == {
        "text": "thinking…",
        "generation": 0,
    }
    content, reasoning = captain_text_from_stream_segments(record["stream_segments"])
    assert content == "half-written reply"
    assert reasoning == "thinking…"
    assert "stream_segments" in record["ops"]
    # Read-side overlay stays out of scope for local outbox.
    assert _drive(store.list_stream_segments(turn_id="m1")) == []


def test_stream_segments_monotonic_and_ready_sealed(tmp_path):
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="h" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="h" * 32)
        await store.upsert_stream_segments(
            turn_id="m1",
            segments=[("captain:content", "hello", 0)],
        )
        # Same-gen shorter must not shrink.
        await store.upsert_stream_segments(
            turn_id="m1",
            segments=[("captain:content", "he", 0)],
        )
        await store.upsert_stream_segments(
            turn_id="m1",
            segments=[("captain:content", "hello world", 0)],
        )
        await store.finalize(
            conversation_id="c1",
            user_message="hi",
            user_message_id="u1",
            assistant_content="final",
            message_id="m1",
            trace_id="h" * 32,
            finish_reason="stop",
        )
        # Sealed ready: further stream upserts ignored.
        await store.upsert_stream_segments(
            turn_id="m1",
            segments=[("captain:content", "should not land", 1)],
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["content"] == "final"
    assert record["stream_segments"]["captain:content"]["text"] == "hello world"
    assert record["phase"] == PHASE_READY


def test_outbox_process_journal_visible_at_semantic_boundary(tmp_path):
    """Local mid-run: process_* lands in outbox before finalize (D6 / attach isomorphic)."""
    from agentcore.conversation.store import set_conversation_store
    from agentcore.conversation.store.outbox import journal_entries_from_map
    from agentcore.runtime.events import EventSink, content_delta, tool_use_start
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="j" * 32,
    )
    set_conversation_store(store)

    async def run() -> None:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="j" * 32)
        log = TurnFactLog()
        fl = current_fact_log.set(log)
        writer = TurnJournalWriter(
            turn_id="m1", conversation_id="c1", trace_id="j" * 32
        )
        wt = current_journal_writer.set(writer)
        sink = EventSink()
        try:
            sink.emit(content_delta("## 旁白\n本地可见。"))
            sink.emit(tool_use_start("tc1", "web_search", {"query": "q"}))
            # Drain SSE barriers so journal writes complete before we read the file.
            while True:
                ev = await sink.get()
                if ev is None:
                    break
                if ev.type.value == "tool_use_start":
                    sink.close()
            await writer.flush()
        finally:
            current_journal_writer.reset(wt)
            current_fact_log.reset(fl)

        record = store.find_record_by_message_id("m1")
        assert record is not None
        assert record["phase"] == "open"
        entries = journal_entries_from_map(record.get("journal")) or []
        kinds = [e.get("kind") for e in entries]
        assert "process_content" in kinds
        assert "tool_use_start" in kinds
        assert kinds.index("process_content") < kinds.index("tool_use_start")

    _drive(run())


def test_write_retries_transient_replace_lock(tmp_path, monkeypatch):
    """Windows WinError 5 / sharing violation: retry replace, then succeed."""
    import agentcore.conversation.store.outbox as outbox_mod

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="r" * 32,
    )
    calls = {"n": 0}
    import agentcore.workspace.fs_replace as fs_replace_mod

    real_replace = fs_replace_mod.os.replace

    def flaky(src, dst):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 3:
            err = OSError(5, "Access is denied")
            err.winerror = 5  # type: ignore[attr-defined]
            raise err
        real_replace(src, dst)

    monkeypatch.setattr(fs_replace_mod.os, "replace", flaky)
    monkeypatch.setattr(outbox_mod, "_REPLACE_RETRY_DELAYS_S", (0.0, 0.0, 0.0, 0.0, 0.0))

    async def run() -> None:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="r" * 32)

    _drive(run())
    assert calls["n"] == 3
    record = json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))
    assert record["phase"] == PHASE_OPEN
    assert "begin_turn" in record["ops"]


def test_write_logs_failure_after_exhausted_replace_retries(tmp_path, monkeypatch):
    """Exhausted transient locks still surface ``sidecar.outbox_write_failed``."""
    import agentcore.conversation.store.outbox as outbox_mod

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="f" * 32,
    )
    errors: list[tuple] = []

    def always_locked(src, dst):  # noqa: ANN001
        del src, dst
        err = OSError(5, "Access is denied")
        err.winerror = 5  # type: ignore[attr-defined]
        raise err

    class _Spy:
        def error(self, event, **kwargs):  # noqa: ANN001
            errors.append((event, kwargs))

        def warning(self, event, **kwargs):  # noqa: ANN001
            pass

    import agentcore.workspace.fs_replace as fs_replace_mod

    monkeypatch.setattr(fs_replace_mod.os, "replace", always_locked)
    monkeypatch.setattr(outbox_mod, "_REPLACE_RETRY_DELAYS_S", (0.0, 0.0))
    monkeypatch.setattr(outbox_mod, "logger", _Spy())

    async def run() -> None:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="f" * 32)

    _drive(run())  # must not raise into the turn
    assert errors and errors[0][0] == "sidecar.outbox_write_failed"
    assert not (tmp_path / "outbox" / "u1.json").exists()


def test_finalize_replaces_journal_with_complete_result_entries(tmp_path):
    """When result.journal_entries is passed, seal with that map (not progressive stub)."""
    from agentcore.conversation.store.outbox import journal_entries_from_map

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="z" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="z" * 32)
        # Incomplete progressive (team only) — would eclipse full process if kept.
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="z" * 32,
            entry={"kind": "run_started", "payload": {"run_id": "w1"}, "ts": None},
        )
        complete = [
            {
                "kind": "process_reasoning",
                "payload": {"kind": "reasoning", "text": "想"},
                "ts": None,
            },
            {"kind": "run_started", "payload": {"run_id": "w1"}, "ts": None},
            {"kind": "turn_end", "payload": {"finish_reason": "stop"}, "ts": None},
        ]
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hi",
            user_message_id="u1",
            assistant_content="done",
            message_id="m1",
            trace_id="z" * 32,
            finish_reason="stop",
            runs={"events": [], "process": [{"kind": "reasoning", "text": "想"}]},
            journal_entries=complete,
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    entries = journal_entries_from_map(record.get("journal")) or []
    kinds = [e.get("kind") for e in entries]
    assert kinds == ["process_reasoning", "run_started", "turn_end"]
    body = to_record_turn_body(record)
    assert body["journal"][0]["kind"] == "process_reasoning"


def test_concurrent_turns_isolate_context_and_salvage_onto_umid(tmp_path):
    """C1: overlapping binds must not steal umid; stop seals umid file with user text."""
    store = OutboxStore(tmp_path / "outbox")
    base = tmp_path / "outbox"

    async def run() -> None:
        # Turn A (pause→continue style open record).
        store.bind_turn(
            conversation_id="c1",
            user_message_id="uA",
            user_message="hello A",
            message_id="mA",
            trace_id="a" * 32,
        )
        await store.begin_turn(conversation_id="c1", message_id="mA", trace_id="a" * 32)
        await store.append_journal(
            turn_id="mA",
            seq=0,
            conversation_id="c1",
            trace_id="a" * 32,
            entry={"kind": "run_started", "payload": {"id": "rA"}, "ts": None},
        )

        # Turn B binds while A is still live (old global _ctx would overwrite A).
        store.bind_turn(
            conversation_id="c1",
            user_message_id="uB",
            user_message="hello B",
            message_id="mB",
            trace_id="b" * 32,
        )
        await store.begin_turn(conversation_id="c1", message_id="mB", trace_id="b" * 32)
        await store.append_journal(
            turn_id="mB",
            seq=0,
            conversation_id="c1",
            trace_id="b" * 32,
            entry={"kind": "run_started", "payload": {"id": "rB"}, "ts": None},
        )

        # A continues after B stole the old single slot — must still hit uA.json.
        await store.append_journal(
            turn_id="mA",
            seq=1,
            conversation_id="c1",
            trace_id="a" * 32,
            entry={"kind": "tool_use_start", "payload": {"id": "t1"}, "ts": None},
        )

        # Stop A while B is still the "latest" bind.
        await store.salvage(
            journal=[{"kind": "turn_cancelled", "payload": {}}],
            content="partial A+",
            conversation_id="c1",
            trace_id="a" * 32,
            message_id="mA",
        )

        # B keeps going then finalizes.
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hello B",
            user_message_id="uB",
            assistant_content="B done",
            message_id="mB",
            trace_id="b" * 32,
            finish_reason="stop",
        )

    _drive(run())

    a = json.loads((base / "uA.json").read_text(encoding="utf-8"))
    assert a["phase"] == PHASE_READY
    assert a["user_message"] == "hello A"
    assert a["user_message_id"] == "uA"
    assert a["finish_reason"] == "cancelled"
    assert "salvage" in a["ops"]
    assert a["journal"]["0"]["kind"] == "run_started"
    assert a["journal"]["1"]["kind"] == "tool_use_start"

    b = json.loads((base / "uB.json").read_text(encoding="utf-8"))
    assert b["phase"] == PHASE_READY
    assert b["user_message"] == "hello B"
    assert b["content"] == "B done"

    # No assistant-id keyed ready dead letters.
    assert not (base / "mA.json").exists()
    assert not (base / "mB.json").exists()


def test_salvage_after_clear_turn_merges_umid_file(tmp_path):
    """C1: clear_turn drops bind; salvage still seals via on-disk message_id→umid."""
    store = OutboxStore(tmp_path / "outbox")
    base = tmp_path / "outbox"

    async def run() -> None:
        store.bind_turn(
            conversation_id="c1",
            user_message_id="u1",
            user_message="keep me",
            message_id="m1",
            trace_id="c" * 32,
        )
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="c" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="c" * 32,
            entry={"kind": "run_started", "payload": {}, "ts": None},
        )
        # Simulate host finally clearing bind before cancel salvage races.
        store.clear_turn("m1")
        await store.salvage(
            journal=[],
            content="partial+",
            conversation_id="c1",
            trace_id="c" * 32,
            message_id="m1",
        )

    _drive(run())
    record = json.loads((base / "u1.json").read_text(encoding="utf-8"))
    assert record["phase"] == PHASE_READY
    assert record["user_message"] == "keep me"
    assert record["user_message_id"] == "u1"
    assert not (base / "m1.json").exists()


def test_salvage_without_umid_does_not_create_assistant_id_ready(tmp_path):
    """C1: unbound salvage must not invent message_id.json with empty user_message."""
    store = OutboxStore(tmp_path / "outbox")
    base = tmp_path / "outbox"

    async def run() -> None:
        await store.salvage(
            journal=[{"kind": "x"}],
            content="orphan",
            conversation_id="c1",
            trace_id="d" * 32,
            message_id="m-orphan",
        )

    _drive(run())
    assert list(base.glob("*.json")) == []


def test_mutate_does_not_wipe_on_corrupt_read(tmp_path, monkeypatch):
    """BUG-3: existing file that fails read/parse must not be replaced by an empty shell."""
    import agentcore.conversation.store.outbox as outbox_mod

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="keep me",
        message_id="m1",
        trace_id="c" * 32,
    )
    path = tmp_path / "outbox" / "u1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    good = {
        "schema_version": 1,
        "user_message_id": "u1",
        "conversation_id": "c1",
        "message_id": "m1",
        "user_message": "keep me",
        "content": "precious draft",
        "phase": PHASE_OPEN,
        "ops": ["begin_turn"],
        "journal": {},
        "stream_segments": {},
    }
    path.write_text(json.dumps(good), encoding="utf-8")

    errors: list[tuple] = []
    warnings: list[tuple] = []

    class _Spy:
        def error(self, event, **kwargs):  # noqa: ANN001
            errors.append((event, kwargs))

        def warning(self, event, **kwargs):  # noqa: ANN001
            warnings.append((event, kwargs))

    monkeypatch.setattr(outbox_mod, "logger", _Spy())
    monkeypatch.setattr(outbox_mod, "_READ_RETRY_DELAYS_S", (0.0, 0.0, 0.0))
    # Corrupt the file after bind so mutate's read fails.
    path.write_text("{not-json", encoding="utf-8")

    async def run() -> None:
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="c" * 32,
            entry={"kind": "run_started", "payload": {}},
        )

    _drive(run())  # must not raise into the turn
    assert any(e[0] == "sidecar.outbox_read_failed" for e in errors)
    assert any(e[0] == "sidecar.outbox_write_failed" for e in errors)
    assert any(w[0] == "sidecar.outbox_read_retry" for w in warnings)
    # On-disk bytes unchanged — no silent wipe / empty-shell overwrite.
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_mutate_recovers_after_transient_read_failure(tmp_path, monkeypatch):
    """BUG-3: short read retry can recover without aborting the write."""
    import agentcore.conversation.store.outbox as outbox_mod

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="t" * 32,
    )
    path = tmp_path / "outbox" / "u1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "user_message_id": "u1",
                "conversation_id": "c1",
                "message_id": "m1",
                "user_message": "hi",
                "content": "old",
                "phase": PHASE_OPEN,
                "ops": ["begin_turn"],
                "journal": {},
                "stream_segments": {},
            }
        ),
        encoding="utf-8",
    )

    calls = {"n": 0}
    real_read = OutboxStore._read_sync

    def flaky(self, user_message_id: str):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 2:
            raise outbox_mod.OutboxReadError("transient")
        return real_read(self, user_message_id)

    monkeypatch.setattr(OutboxStore, "_read_sync", flaky)
    monkeypatch.setattr(outbox_mod, "_READ_RETRY_DELAYS_S", (0.0, 0.0, 0.0))

    async def run() -> None:
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="t" * 32,
            entry={"kind": "run_started", "payload": {}},
        )

    _drive(run())
    assert calls["n"] == 2
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["journal"] == {
        "0": {"kind": "run_started", "payload": {}, "ord": 0}
    }
    assert record["user_message"] == "hi"


def test_finalize_explicit_empty_user_message_clears(tmp_path):
    """BUG-2: explicit ``user_message=""`` must clear, not be swallowed by ``or``."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="prior text",
        message_id="m1",
        trace_id="e" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="e" * 32)
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="",
            user_message_id="u1",
            assistant_content="ok",
            message_id="m1",
            trace_id="e" * 32,
            finish_reason="stop",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["user_message"] == ""
    assert record["phase"] == PHASE_READY
    body = to_record_turn_body(record)
    assert body["user_message"] == ""


def test_finalize_omitted_user_message_keeps_prior(tmp_path):
    """BUG-2: key absent from kwargs keeps the on-disk user_message."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="keep prior",
        message_id="m1",
        trace_id="k" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="k" * 32)
        await store.finalize(
            mode="local",
            conversation_id="c1",
            # user_message intentionally omitted
            user_message_id="u1",
            assistant_content="ok",
            message_id="m1",
            trace_id="k" * 32,
            finish_reason="stop",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["user_message"] == "keep prior"


def test_to_record_turn_body_includes_harvest_origin(tmp_path):
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="【系统收口】后台团队任务已全部完成。",
        message_id="m1",
        trace_id="h" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="h" * 32)
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="【系统收口】后台团队任务已全部完成。",
            user_message_id="u1",
            assistant_content="终稿",
            message_id="m1",
            trace_id="h" * 32,
            finish_reason="stop",
            origin="execution_harvest",
            execution_id="exec-1",
            harvest_kind="success",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["origin"] == "execution_harvest"
    assert record["execution_id"] == "exec-1"
    assert record["harvest_kind"] == "success"
    body = to_record_turn_body(record)
    assert body["origin"] == "execution_harvest"
    assert body["execution_id"] == "exec-1"
    assert body["harvest_kind"] == "success"


def test_salvage_empty_non_user_stop_writes_honesty_note(tmp_path):
    """Local salvage used to seal an empty cancelled bubble; user saw nothing."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="s" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="s" * 32)
        await store.salvage(
            journal=[],
            content="",
            conversation_id="c1",
            trace_id="s" * 32,
            message_id="m1",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["content"] == INTERRUPTED_EMPTY_USER_VISIBLE
    assert record["finish_reason"] == "cancelled"


def test_salvage_empty_user_stop_stays_silent(tmp_path):
    """User pressed stop — local salvage must not invent an explanation."""
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="t" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="t" * 32)
        await store.salvage(
            journal=[],
            content="",
            conversation_id="c1",
            trace_id="t" * 32,
            message_id="m1",
            interrupt_reason="user_stop",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["content"] == ""
    assert record["finish_reason"] == "cancelled"


def test_salvage_copies_harvest_origin_from_bind(tmp_path):
    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="【系统收口】后台团队任务已全部完成。",
        message_id="m1",
        trace_id="h" * 32,
        origin="execution_harvest",
        execution_id="exec-1",
        harvest_kind="success",
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="h" * 32)
        await store.salvage(
            journal=[],
            content="半成品",
            conversation_id="c1",
            trace_id="h" * 32,
            message_id="m1",
        )
        return json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))

    record = _drive(run())
    assert record["origin"] == "execution_harvest"
    assert record["execution_id"] == "exec-1"
    assert record["harvest_kind"] == "success"


def test_ready_outbox_still_appends_run_terminal(tmp_path):
    """Pause READY must not drop a later worker terminal (writer overflow's store twin)."""
    from agentcore.conversation.store.outbox import journal_entries_from_map

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="r" * 32,
    )

    async def run() -> dict:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="r" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="r" * 32,
            entry={"kind": "run_started", "payload": {"run_id": "w1"}},
        )
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hi",
            user_message_id="u1",
            assistant_content="paused",
            message_id="m1",
            trace_id="r" * 32,
            finish_reason="paused",
            journal_entries=[
                {"kind": "run_started", "payload": {"run_id": "w1"}},
            ],
        )
        skipped = await store.append_journal(
            turn_id="m1",
            seq=None,
            conversation_id="c1",
            trace_id="r" * 32,
            entry={"kind": "team_preview_required", "payload": {"checkpoint_id": "cp1"}},
        )
        landed = await store.append_journal(
            turn_id="m1",
            seq=None,
            conversation_id="c1",
            trace_id="r" * 32,
            entry={"kind": "run_completed", "payload": {"run_id": "w1"}},
        )
        record = json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))
        return {"skipped": skipped, "landed": landed, "record": record}

    result = _drive(run())
    assert result["skipped"] is None
    assert result["landed"] is not None
    assert result["record"]["phase"] == PHASE_READY
    kinds = [
        e.get("kind")
        for e in (journal_entries_from_map(result["record"].get("journal")) or [])
    ]
    assert "run_started" in kinds
    assert "run_completed" in kinds
    assert "team_preview_required" not in kinds


def test_resume_finalize_keeps_ready_overflow_terminal(tmp_path):
    """Second pause / resume rewrite must not drop overflow run_completed already on READY."""
    from agentcore.conversation.store.outbox import journal_entries_from_map

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="r" * 32,
    )

    async def run() -> list[str]:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="r" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="r" * 32,
            entry={"kind": "run_started", "payload": {"run_id": "w1"}},
        )
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hi",
            user_message_id="u1",
            assistant_content="paused",
            message_id="m1",
            trace_id="r" * 32,
            finish_reason="paused",
            journal_entries=[
                {"kind": "run_started", "payload": {"run_id": "w1"}},
            ],
        )
        await store.append_journal(
            turn_id="m1",
            seq=None,
            conversation_id="c1",
            trace_id="r" * 32,
            entry={"kind": "run_completed", "payload": {"run_id": "w1"}},
        )
        await store.reopen_for_resume(
            turn_id="m1",
            user_message_id="u1",
            conversation_id="c1",
            trace_id="r" * 32,
        )
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hi",
            user_message_id="u1",
            assistant_content="paused again",
            message_id="m1",
            trace_id="r" * 32,
            finish_reason="paused",
            journal_entries=[
                {"kind": "run_started", "payload": {"run_id": "w1"}},
                {"kind": "team_preview_required", "payload": {"checkpoint_id": "ck-2"}},
            ],
        )
        record = json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))
        return [
            str(e.get("kind") or "")
            for e in (journal_entries_from_map(record.get("journal")) or [])
        ]

    kinds = _drive(run())
    assert kinds[0] == "run_started"
    assert "run_completed" in kinds
    assert "team_preview_required" in kinds
    # Emission order: seal prefix, overflow terminal, then the grown resume prefix.
    assert kinds.index("run_completed") < kinds.index("team_preview_required")


def test_finalize_keeps_late_unlisted_kind(tmp_path):
    """Prefix rewrite must keep a late higher-seq fact that is not an overflow terminal."""
    from agentcore.conversation.store.outbox import journal_entries_from_map

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="m1",
        trace_id="r" * 32,
    )

    async def run() -> list[str]:
        await store.begin_turn(conversation_id="c1", message_id="m1", trace_id="r" * 32)
        await store.append_journal(
            turn_id="m1",
            seq=0,
            conversation_id="c1",
            trace_id="r" * 32,
            entry={"kind": "run_started", "payload": {"run_id": "w1"}},
        )
        await store.append_journal(
            turn_id="m1",
            seq=None,
            conversation_id="c1",
            trace_id="r" * 32,
            entry={"kind": "note", "payload": {"content": "late-unrelated"}},
        )
        await store.finalize(
            mode="local",
            conversation_id="c1",
            user_message="hi",
            user_message_id="u1",
            assistant_content="paused",
            message_id="m1",
            trace_id="r" * 32,
            finish_reason="paused",
            journal_entries=[
                {"kind": "run_started", "payload": {"run_id": "w1"}},
            ],
        )
        record = json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))
        return [
            str(e.get("kind") or "")
            for e in (journal_entries_from_map(record.get("journal")) or [])
        ]

    kinds = _drive(run())
    assert kinds[0] == "run_started"
    assert "note" in kinds

