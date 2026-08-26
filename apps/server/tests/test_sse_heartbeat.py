"""SSE generator: idle heartbeat + event passthrough + optional id: seq.

The live-tail races a persistent subscription ``get`` task against a heartbeat
timeout (``asyncio.wait``, never ``wait_for``) so a turn that is alive but thinking
keeps the connection flowing bytes without cancelling a get that may already hold a
dequeued event behind its emit-side seq backfill. No DB, no HTTP — plain async tests
(asyncio_mode=auto).
"""

import asyncio
import json

import pytest

from agentcore.api import sse
from agentcore.core.log_context import bind_log_context, clear_log_context
from agentcore.llm.credentials import INFERENCE_TRACE_HEADER
from agentcore.runtime.events import EventSink, content_delta


async def test_forwards_events_then_ends_on_close():
    sink = EventSink()
    sink.emit(content_delta("hi"))
    sink.close()

    frames = [frame async for frame in sse._event_generator(sink, None)]

    # The content event is serialized and delivered; nothing idled, so no
    # heartbeat comment is interleaved.
    assert any("content_delta" in f for f in frames)
    assert all(not f.startswith(":") for f in frames)


async def test_emits_heartbeat_while_idle(monkeypatch):
    # Shrink the cadence so the idle branch fires fast and deterministically.
    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.02)
    sink = EventSink()
    gen = sse._event_generator(sink, None)
    try:
        # Nothing queued → the wait times out → an SSE comment heartbeat frame;
        # the underlying get task stays alive across the ping.
        first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert first.startswith(":")

        # Real events still come through after heartbeats.
        sink.emit(content_delta("hi"))
        second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert "content_delta" in second

        # Closing the sink ends the stream.
        sink.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    finally:
        await gen.aclose()


async def test_heartbeat_does_not_cancel_get_waiting_on_persist_barrier(monkeypatch):
    """SS-1: heartbeat timeout must not cancel a ``get`` awaiting the seq backfill.

    If the get has already dequeued the frame and is waiting for its journal write
    to land, cancelling it (as ``wait_for`` would) drops the event. Persistent get +
    ``asyncio.wait`` must ping while still delivering the event — with its ``id:`` —
    once the barrier resolves.
    """
    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.02)
    sink = EventSink()
    loop = asyncio.get_running_loop()
    barrier: asyncio.Future[int | None] = loop.create_future()

    gen = sse._event_generator(sink, None)
    try:
        ping = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert ping.startswith(":")  # subscribed, nothing to send yet

        # Fan out a frame whose journal write has not landed: the consumer dequeues
        # it and blocks on the emit-side backfill instead of shipping it seq-less.
        sink._deliver(content_delta("held"), barrier)  # noqa: SLF001
        held_ping = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert held_ping.startswith(":")

        barrier.set_result(99)
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert "content_delta" in frame
        assert "\nid: 99\n" in frame

        sink.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    finally:
        if not barrier.done():
            barrier.cancel()
        await gen.aclose()


def test_format_sse_optional_id_line():
    event = content_delta("partial")
    plain = sse._format_sse(event)
    assert "id:" not in plain
    assert plain.startswith(f"event: {event.type}")
    assert "\ndata: " in plain

    with_id = sse._format_sse(event, seq=42)
    assert "\nid: 42\n" in with_id
    # Envelope JSON unchanged — id is a transport line, not a payload field.
    data_line = next(line for line in with_id.split("\n") if line.startswith("data: "))
    envelope = json.loads(data_line[len("data: ") :])
    assert set(envelope) == {"type", "timestamp", "payload"}
    assert "id" not in envelope


def test_pump_sse_style_parser_ignores_id_lines():
    """Mirrors desktop/mobile pumpSSE: only ``data:`` lines are parsed."""
    frame = sse._format_sse(content_delta("hi"), seq=7)
    events = []
    for line in frame.split("\n"):
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[len("data: ") :]))
    assert len(events) == 1
    assert events[0]["type"] == "content_delta"
    assert events[0]["payload"] == {"delta": "hi"}


def test_sse_response_stamps_bound_trace_header():
    sink = EventSink()
    clear_log_context()
    try:
        bind_log_context(trace_id="0123456789abcdef0123456789abcdef")
        stream = sse.sse_response(sink)
        attach = sse.sse_attach_response(sink)
        assert stream.headers[INFERENCE_TRACE_HEADER] == "0123456789abcdef0123456789abcdef"
        assert attach.headers[INFERENCE_TRACE_HEADER] == "0123456789abcdef0123456789abcdef"
    finally:
        clear_log_context()


def test_sse_response_omits_trace_header_when_unbound():
    sink = EventSink()
    clear_log_context()
    stream = sse.sse_response(sink)
    attach = sse.sse_attach_response(sink)
    assert INFERENCE_TRACE_HEADER not in stream.headers
    assert INFERENCE_TRACE_HEADER not in attach.headers
