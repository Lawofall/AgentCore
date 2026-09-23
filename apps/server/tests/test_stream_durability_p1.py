"""P1 stream durability — StreamCheckpointer, overlay, salvage, sweeper (流式回复持久化)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from agentcore.conversation.store.merge import pick_longest, pick_monotonic_content
from agentcore.conversation.store.overlay import (
    overlay_message_fields,
    overlay_runs_with_segments,
    should_overlay_stream_state,
)
from agentcore.runtime.events import (
    EventSink,
    FinishReason,
    content_delta,
    content_reset,
    reasoning_delta,
    tool_use_start,
)
from agentcore.runtime.events.run import run_output_delta, run_reasoning_delta, run_started
from agentcore.runtime.events.stream_checkpointer import (
    CHANNEL_CAPTAIN_CONTENT,
    CHANNEL_CAPTAIN_REASONING,
    FLUSH_BYTES,
    StreamCheckpointer,
    run_output_channel,
    run_reasoning_channel,
)
from agentcore.runtime.events.types import EventType

# --- ① flush triggers + same-gen monotonic ---


def test_flush_interval_is_three_seconds():
    from agentcore.runtime.events.stream_checkpointer import FLUSH_INTERVAL_S

    assert FLUSH_INTERVAL_S == 3.0


async def test_checkpointer_flushes_on_byte_threshold(monkeypatch):
    flushed: list[list[tuple[str, str, int]]] = []

    class _Store:
        async def upsert_stream_segments(self, *, turn_id, segments):
            flushed.append(list(segments))

    monkeypatch.setattr(
        "agentcore.runtime.conversation_store.get_conversation_store",
        lambda: _Store(),
    )
    ck = StreamCheckpointer(turn_id="t1")
    # Stay under timer; cross 4KB via one big delta.
    big = "x" * (FLUSH_BYTES + 10)
    ck.observe(content_delta(big))
    # flush_now schedules a task — drive it
    assert ck._flush_inflight is not None
    await ck._flush_inflight
    assert flushed
    assert flushed[0][0][0] == CHANNEL_CAPTAIN_CONTENT
    assert len(flushed[0][0][1]) == len(big)


async def test_checkpointer_boundary_flush_on_tool_use_start(monkeypatch):
    flushed: list = []

    class _Store:
        async def upsert_stream_segments(self, *, turn_id, segments):
            flushed.append(list(segments))

    monkeypatch.setattr(
        "agentcore.runtime.conversation_store.get_conversation_store",
        lambda: _Store(),
    )
    ck = StreamCheckpointer(turn_id="t1")
    ck.observe(content_delta("hello"))
    ck.observe(tool_use_start(tool_call_id="c1", tool_name="web_search", arguments={}))
    assert ck._flush_inflight is not None
    await ck._flush_inflight
    assert any(ch == CHANNEL_CAPTAIN_CONTENT for ch, _, _ in flushed[0])


async def test_checkpointer_generation_bump_on_content_reset(monkeypatch):
    flushed: list = []

    class _Store:
        async def upsert_stream_segments(self, *, turn_id, segments):
            flushed.append(list(segments))

    monkeypatch.setattr(
        "agentcore.runtime.conversation_store.get_conversation_store",
        lambda: _Store(),
    )
    ck = StreamCheckpointer(turn_id="t1")
    ck.observe(content_delta("old"))
    ck.observe(content_reset("retry"))
    await ck.flush()
    # After reset, generation=1 and text empty (dirty).
    snap = ck.memory_snapshot()
    assert CHANNEL_CAPTAIN_CONTENT not in snap or snap.get(CHANNEL_CAPTAIN_CONTENT) == ""
    assert ck._channels[CHANNEL_CAPTAIN_CONTENT].generation == 1


async def test_checkpointer_worker_per_run_accumulators(monkeypatch):
    class _Store:
        async def upsert_stream_segments(self, *, turn_id, segments):
            pass

    monkeypatch.setattr(
        "agentcore.runtime.conversation_store.get_conversation_store",
        lambda: _Store(),
    )
    ck = StreamCheckpointer(turn_id="t1")
    ck.observe(run_started("w1", "researcher", kind="agent"))
    ck.observe(run_reasoning_delta(run_id="w1", agent_id="researcher", delta="think "))
    ck.observe(run_output_delta(run_id="w1", agent_id="researcher", delta="out"))
    snap = ck.memory_snapshot()
    assert snap[run_reasoning_channel("w1")] == "think "
    assert snap[run_output_channel("w1")] == "out"


# --- ② overlay ---


def test_overlay_running_fills_partial_content_and_reasoning():
    usage = {"status": "running"}
    segments = [
        {"channel": CHANNEL_CAPTAIN_CONTENT, "text": "半截正文更长一些", "generation": 0},
        {"channel": CHANNEL_CAPTAIN_REASONING, "text": "思考片段", "generation": 0},
    ]
    content, reasoning = overlay_message_fields(
        content="半截",
        reasoning_content=None,
        segments=segments,
        usage=usage,
    )
    assert content == "半截正文更长一些"
    assert reasoning == "思考片段"


def test_overlay_paused_prefers_columns():
    usage = {"status": "running", "paused": True}
    assert should_overlay_stream_state(usage) is False
    content, reasoning = overlay_message_fields(
        content="pause snapshot",
        reasoning_content="pause think",
        segments=[
            {"channel": CHANNEL_CAPTAIN_CONTENT, "text": "stale longer segment text", "generation": 0},
        ],
        usage=usage,
    )
    assert content == "pause snapshot"
    assert reasoning == "pause think"


def test_overlay_complete_skips_segments():
    usage = {"status": "complete"}
    content, reasoning = overlay_message_fields(
        content="final",
        reasoning_content="done",
        segments=[
            {"channel": CHANNEL_CAPTAIN_CONTENT, "text": "should not win", "generation": 0},
        ],
        usage=usage,
    )
    assert content == "final"
    assert reasoning == "done"


def test_overlay_runs_synthesizes_partial_worker_output():
    usage = {"status": "running"}
    runs = {
        "events": [
            {
                "type": EventType.RUN_STARTED.value,
                "payload": {
                    "run_id": "w1",
                    "kind": "agent",
                    "agent_id": "researcher",
                },
                "timestamp": "t0",
            }
        ]
    }
    segments = [
        {
            "channel": run_output_channel("w1"),
            "text": "worker partial",
            "generation": 0,
        },
        {
            "channel": run_reasoning_channel("w1"),
            "text": "worker think",
            "generation": 0,
        },
    ]
    out = overlay_runs_with_segments(runs, segments, usage=usage)
    assert out is not None
    types = [e["type"] for e in out["events"]]
    assert EventType.RUN_REASONING_DELTA.value in types
    assert EventType.RUN_OUTPUT_DELTA.value in types
    deltas = {
        e["type"]: e["payload"]["delta"]
        for e in out["events"]
        if e["type"]
        in (EventType.RUN_OUTPUT_DELTA.value, EventType.RUN_REASONING_DELTA.value)
    }
    assert deltas[EventType.RUN_OUTPUT_DELTA.value] == "worker partial"
    assert deltas[EventType.RUN_REASONING_DELTA.value] == "worker think"


# --- ③ sweeper salvage no-DAG ---


async def test_sweeper_salvages_no_dag_from_stream_state(monkeypatch):
    from agentcore.runtime.leases import sweeper as sweeper_mod

    message_id = "m-nodag"
    conversation_id = "c1"
    expired_row = SimpleNamespace(message_id=message_id, conversation_id=conversation_id)
    claimed_row = SimpleNamespace(
        message_id=message_id, conversation_id=conversation_id, user_id="u1"
    )
    salvage_calls: list[dict] = []

    class _FakeLeaseRepo:
        def __init__(self, _session):
            pass

        async def list_expired(self, *, before, limit):
            return [expired_row]

        async def claim_expired(self, mid, *, new_owner_id, before, phase="recovering"):
            return claimed_row

        async def release(self, mid, *, owner_id=None):
            pass

    class _FakePausedRepo:
        def __init__(self, _session):
            pass

        async def get(self, mid):
            return None

    class _FakeJournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            # Pure chat — no DAG facts.
            return []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def _fake_salvage(**kwargs):
        salvage_calls.append(kwargs)
        return True

    monkeypatch.setattr(sweeper_mod, "TurnLeaseRepository", _FakeLeaseRepo)
    monkeypatch.setattr(sweeper_mod, "PausedTurnRepository", _FakePausedRepo)
    monkeypatch.setattr(sweeper_mod, "TurnJournalRepository", _FakeJournalRepo)
    monkeypatch.setattr(sweeper_mod, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(sweeper_mod.settings, "turn_lease_enabled", True)
    monkeypatch.setattr(sweeper_mod, "salvage_no_dag_turn", _fake_salvage)

    started = await sweeper_mod.run_turn_lease_sweep()
    assert started == 0  # salvage, not recover
    assert len(salvage_calls) == 1
    assert salvage_calls[0]["message_id"] == message_id


async def test_salvage_no_dag_writes_incomplete_interrupted(monkeypatch):
    from agentcore.runtime.leases.sweeper import salvage_no_dag_turn

    upserts: list[dict] = []
    appended: list[dict] = []
    cleared: list[str] = []

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, mid, conversation_id=None):
            return SimpleNamespace(
                content="已有前缀",
                reasoning_content=None,
                trace_id="tr",
                usage=None,
            )

        async def upsert_assistant(self, **kwargs):
            upserts.append(kwargs)
            return SimpleNamespace(id=kwargs["message_id"])

    class _JournalRepo:
        def __init__(self, _session):
            pass

        async def load_owned(self, turn_id, conversation_id):
            return []

        async def append(self, **kwargs):
            appended.append(kwargs)
            return 0

    class _Store:
        async def list_stream_segments(self, *, turn_id):
            return [
                {"channel": CHANNEL_CAPTAIN_CONTENT, "text": "已有前缀加尾巴", "generation": 0},
                {"channel": CHANNEL_CAPTAIN_REASONING, "text": "想过", "generation": 0},
            ]

        async def clear_stream_segments(self, *, turn_id):
            cleared.append(turn_id)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.MessageRepository",
        _MsgRepo,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.TurnJournalRepository",
        _JournalRepo,
    )
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.conversation.store.get_cloud_store",
        lambda: _Store(),
    )

    ok = await salvage_no_dag_turn(message_id="m1", conversation_id="c1")
    assert ok
    assert upserts[0]["metadata"]["status"] == "incomplete"
    assert upserts[0]["metadata"]["finish_reason"] == FinishReason.INTERRUPTED.value
    assert "已有前缀加尾巴" in upserts[0]["content"]
    assert upserts[0]["reasoning_content"] == "想过"
    assert appended
    assert appended[0]["seq"] is None
    assert appended[0]["entry"]["kind"] == "turn_end"
    assert cleared == ["m1"]


# --- ④ error/FAILED salvage longest ---


def test_pick_longest_merges_segment_captain_sink():
    assert (
        pick_longest("seg", "captain longer text", "sink")
        == "captain longer text"
    )
    assert pick_longest(None, "", "only sink") == "only sink"


def test_reasoning_monotonic_survives_none_incoming():
    assert pick_monotonic_content("kept thinking", None) == "kept thinking"
    assert pick_monotonic_content(None, "new") == "new"


# --- ⑤ pause/finalize clear + EventSink wiring ---


async def test_event_sink_observes_deltas_into_checkpointer(monkeypatch):
    class _Store:
        async def upsert_stream_segments(self, *, turn_id, segments):
            pass

    monkeypatch.setattr(
        "agentcore.runtime.conversation_store.get_conversation_store",
        lambda: _Store(),
    )
    sink = EventSink(conversation_id="c1", message_id="m1")
    sink.emit(content_delta("hi"))
    sink.emit(reasoning_delta("think"))
    assert sink.streamed_content() == "hi"
    assert sink.streamed_reasoning() == "think"
    snap = sink.stream_memory_snapshot()
    assert snap[CHANNEL_CAPTAIN_CONTENT] == "hi"
    assert snap[CHANNEL_CAPTAIN_REASONING] == "think"
    await sink.flush_stream_state()


async def test_outbox_stream_segments_persist_without_overlay(tmp_path):
    """D6: upsert writes stream_segments; list_* stays empty (no local overlay)."""
    from agentcore.conversation.store.outbox import OutboxStore
    from agentcore.runtime.events.stream_checkpointer import CHANNEL_CAPTAIN_CONTENT

    store = OutboxStore(tmp_path / "outbox")
    store.bind_turn(
        conversation_id="c1",
        user_message_id="u1",
        user_message="hi",
        message_id="t1",
        trace_id="i" * 32,
    )
    await store.begin_turn(conversation_id="c1", message_id="t1", trace_id="i" * 32)
    await store.upsert_stream_segments(
        turn_id="t1", segments=[(CHANNEL_CAPTAIN_CONTENT, "x", 0)]
    )
    record = json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))
    assert record["stream_segments"][CHANNEL_CAPTAIN_CONTENT]["text"] == "x"
    assert await store.list_stream_segments(turn_id="t1") == []
    assert await store.list_stream_segments_map(turn_ids=["t1"]) == {}
    await store.clear_stream_segments(turn_id="t1")
    cleared = json.loads((tmp_path / "outbox" / "u1.json").read_text(encoding="utf-8"))
    assert cleared["stream_segments"] == {}


async def test_cloud_clear_after_pause_snapshot(monkeypatch):
    """Pause path clears segments only after successful upsert (时序不变量)."""
    from agentcore.conversation.store.cloud import CloudStore

    cleared: list[str] = []
    upserted: list[str] = []

    class _MsgRepo:
        def __init__(self, _session):
            pass

        async def upsert_assistant(self, **kwargs):
            upserted.append(kwargs["message_id"])
            return SimpleNamespace(id=kwargs["message_id"])

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "agentcore.conversation.store.cloud.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.conversation.store.cloud.MessageRepository",
        _MsgRepo,
    )

    store = CloudStore()

    async def _clear(*, turn_id):
        cleared.append(turn_id)

    monkeypatch.setattr(store, "clear_stream_segments", _clear)
    monkeypatch.setattr(
        "agentcore.runtime.turn.interrupt.project_settled_message_cost",
        AsyncMock(),
    )

    await store._finalize_cloud(
        result={
            "message_id": "m-pause",
            "content": "paused body",
            "reasoning_content": "paused think",
            "finish_reason": FinishReason.PAUSED,
        },
        conversation_id="c1",
        user_id="u1",
        folder_id=None,
        backend=MagicMock(),
        sink=EventSink(),
        user_message="hi",
        llm_credentials=None,
        trace_id="tr",
        turn_id="m-pause",
        duration_ms=1,
    )
    assert upserted == ["m-pause"]
    assert cleared == ["m-pause"]
