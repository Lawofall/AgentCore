"""Sidecar cancel ≡ cloud /stop: cascade coordination + emit message_end(cancelled)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentcore.conversation.store import reset_conversation_store_for_tests
from agentcore.memory.account_prepare_cache import (
    AccountPrepareSnapshot,
    clear_account_rules_memory_cache,
)
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    active_coordination,
    clear_active_coordination,
    release_turn_coordination,
    set_active_coordination,
)
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.runtime.journal import KIND_TURN_END
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.sidecar.protocol import TURN_CANCELLED
from agentcore.sidecar.server import SidecarServer
from agentcore.sidecar.server_pkg.cancel_tombstone import (
    cancel_tombstone_blocks,
    mark_cancel_tombstone,
)
from agentcore.sidecar.server_pkg.turns import (
    _emit_cancel_end_if_cancelling,
    _emit_user_stop_message_end,
    _ensure_cancelled_turn_end,
)


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    lines: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        lines.append(json.loads(line))

    return lines, write_line


def _req(request_id: int, method: str, params: dict[str, Any]) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )


@pytest.fixture(autouse=True)
def _clear_coord():
    clear_active_coordination()
    yield
    clear_active_coordination()
    reset_conversation_store_for_tests()


def _pause_frame(message_id: str = "m1", conversation_id: str = "c1") -> AskUserSuspension:
    susp = AskUserSuspension(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        captain_run_id="r1",
        checkpoint_id=f"cp-{message_id}",
        tool_call_id="tc1",
        base_system_prompt="sys",
        user_message="原始问题",
        transcript=[],
        history=[],
        question="要继续吗？",
        context="背景",
    )
    susp.journal_entries = [
        {"kind": "checkpoint_required", "payload": {"id": "cp"}, "ts": None},
    ]
    return susp


async def _init_sidecar(server: SidecarServer, tmp_path: Path) -> None:
    await server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "userId": "u",
                    "workspaceRoot": str(tmp_path),
                    "dataDir": str(tmp_path / "data"),
                    "approvalsEnabled": True,
                    "inference": {
                        "baseUrl": "http://test.local/v1/inference/v1",
                        "apiKey": "test-inference-tok",
                        "model": "test-model",
                    },
                },
            }
        )
    )


async def _await_pending_sends(server: SidecarServer) -> None:
    pending = [t for t in list(server._pending_sends) if not t.done()]
    if pending:
        await asyncio.gather(*pending)


async def test_sidecar_cancel_cascades_user_stop_not_detach():
    """cancel marks user_stopped + cancels drive — release clears (no detach continue)."""
    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    server._initialized = True

    conversation_id = "conv-sidecar-stop"
    turn_id = "turn-1"
    session = CoordinationSession(
        execution_id="e-sidecar-stop",
        total_workers=1,
        conversation_id=conversation_id,
    )
    session._running_workers["w1"] = "研究员"

    async def _hang() -> None:
        await asyncio.Event().wait()

    session.drive_task = asyncio.create_task(_hang())
    set_active_coordination(session)

    async def _turn_hang() -> None:
        await asyncio.Event().wait()

    turn_task = asyncio.create_task(_turn_hang())
    server._register_turn(turn_id, turn_task, conversation_id=conversation_id)

    await server.handle_line(
        _req(
            99,
            "cancel",
            {
                "turnId": turn_id,
                "conversationId": conversation_id,
                "reason": "user_stop",
            },
        )
    )

    assert session.user_stopped is True
    assert "w1" in session.cancel_ids
    await asyncio.sleep(0)
    assert turn_task.cancelled() or turn_task.done()
    assert session.drive_task.cancelled() or session.drive_task.done()
    from agentcore.sidecar.server_pkg.cancel_mark import (
        CANCEL_REASON_ATTR,
        cancel_reason_from_task,
    )

    assert getattr(turn_task, CANCEL_REASON_ATTR, None) == "user_stop"
    assert cancel_reason_from_task(turn_task) == "user_stop"

    # Same as cloud /stop: release clears instead of detach-and-continue.
    release_turn_coordination("e-sidecar-stop")
    assert active_coordination("e-sidecar-stop") is None

    replies = [m for m in lines if m.get("id") == 99]
    assert replies and replies[0].get("result", {}).get("cancelled") is True

    if not turn_task.done():
        turn_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn_task


async def test_sidecar_cancel_logs_fingerprint_without_coordination():
    """Solo / no-session cancel still emits turn_cancel_requested (investigation fingerprint)."""
    from structlog.testing import capture_logs

    _lines, write_line = _recorder()
    server = SidecarServer(write_line)
    server._initialized = True

    turn_id = "turn-solo"
    conversation_id = "conv-solo"

    async def _hang() -> None:
        await asyncio.Event().wait()

    turn_task = asyncio.create_task(_hang())
    server._register_turn(turn_id, turn_task, conversation_id=conversation_id)

    with capture_logs() as caps:
        await server.handle_line(
            _req(
                3,
                "cancel",
                {
                    "turnId": turn_id,
                    "conversationId": conversation_id,
                    "reason": "abort_signal",
                },
            )
        )

    events = [c for c in caps if c.get("event") == "sidecar.turn_cancel_requested"]
    assert len(events) == 1
    assert events[0]["reason"] == "abort_signal"
    assert events[0]["cascaded"] is False
    assert events[0]["mode"] == "cancel"
    assert events[0]["task_cancelled"] is True
    assert events[0]["conversation_id"] == conversation_id

    if not turn_task.done():
        turn_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn_task


async def test_cancel_reason_from_task_defaults_without_rpc_stamp():
    from agentcore.sidecar.server_pkg.cancel_mark import cancel_reason_from_task

    assert cancel_reason_from_task(None) == "cancelled_without_rpc"

    async def _noop() -> None:
        return None

    task = asyncio.create_task(_noop())
    await task
    assert cancel_reason_from_task(task) == "cancelled_without_rpc"


async def test_sidecar_cancel_resolves_conversation_from_turn_map():
    """conversationId may be omitted when startTurn registered the mapping."""
    _lines, write_line = _recorder()
    server = SidecarServer(write_line)
    server._initialized = True

    conversation_id = "conv-mapped"
    turn_id = "turn-map"
    session = CoordinationSession(
        execution_id="e-map", total_workers=1, conversation_id=conversation_id
    )

    async def _hang() -> None:
        await asyncio.Event().wait()

    session.drive_task = asyncio.create_task(_hang())
    set_active_coordination(session)

    turn_task = asyncio.create_task(_hang())
    server._register_turn(turn_id, turn_task, conversation_id=conversation_id)

    await server.handle_line(
        _req(7, "cancel", {"turnId": turn_id, "reason": "user_stop"})
    )

    assert session.user_stopped is True

    if not turn_task.done():
        turn_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn_task
    release_turn_coordination("e-map")
    assert active_coordination("e-map") is None


def test_cancel_tombstone_expires_and_evicts_oldest(monkeypatch: pytest.MonkeyPatch):
    """Tombstones are bounded: expired entries drop; overflow evicts soonest-expiring."""
    import time

    from agentcore.sidecar.server_pkg import cancel_tombstone as ct

    stones: dict[str, float] = {}
    mark_cancel_tombstone(stones, "live")
    assert cancel_tombstone_blocks(stones, "live") is True

    stones["live"] = time.monotonic() - 1
    assert cancel_tombstone_blocks(stones, "live") is False

    monkeypatch.setattr(ct, "CANCEL_TOMBSTONE_MAX", 2)
    mark_cancel_tombstone(stones, "a")
    mark_cancel_tombstone(stones, "b")
    mark_cancel_tombstone(stones, "c")
    assert "a" not in stones
    assert set(stones) == {"b", "c"}


async def test_sidecar_cancel_unknown_turn_tombstone_refuses_start(tmp_path: Path):
    """cancel before startTurn registers: tombstone → startTurn is TURN_CANCELLED."""
    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    server._initialized = True
    server._root = tmp_path

    await server.handle_line(
        _req(
            1,
            "cancel",
            {
                "turnId": "turn-early",
                "conversationId": "conv-early",
                "reason": "user_stop",
            },
        )
    )
    replies = [m for m in lines if m.get("id") == 1]
    assert replies and replies[0].get("result", {}).get("cancelled") is True
    assert cancel_tombstone_blocks(server._cancel_tombstones, "turn-early")

    await server.handle_line(
        _req(
            2,
            "startTurn",
            {
                "turnId": "turn-early",
                "conversationId": "conv-early",
                "userMessage": "should not run",
            },
        )
    )
    err = next(m for m in lines if m.get("id") == 2 and "error" in m)
    assert err["error"]["code"] == TURN_CANCELLED
    assert err["error"]["message"] == "turn cancelled"
    assert "turn-early" not in server._turns


async def test_warm_account_rules_memory_does_not_block_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """warmAccountRulesMemory must not hold the read loop while cloud HTTP hangs."""
    clear_account_rules_memory_cache()
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    hang = asyncio.Event()

    async def _hang_warm(*_args: Any, **_kwargs: Any) -> AccountPrepareSnapshot:
        await hang.wait()
        return AccountPrepareSnapshot()

    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.warm_account_rules_memory",
        _hang_warm,
    )

    await server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "userId": "u1",
                    "workspaceRoot": str(tmp_path),
                    "approvalsEnabled": True,
                },
            }
        )
    )

    turn_id = "turn-live"
    conversation_id = "conv-live"

    async def _hang() -> None:
        await asyncio.Event().wait()

    turn_task = asyncio.create_task(_hang())
    server._register_turn(turn_id, turn_task, conversation_id=conversation_id)

    warm_task = asyncio.create_task(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {
                        "folderId": "f1",
                        "accountAuth": {
                            "baseUrl": "https://example.test/v1/account",
                            "apiKey": "tok",
                        },
                    },
                }
            )
        )
    )
    await asyncio.wait_for(warm_task, timeout=1.0)
    # Scheduled fetch is still hanging; cancel must be serviced anyway.
    assert not any(m.get("id") == 2 for m in lines)

    await server.handle_line(
        _req(
            3,
            "cancel",
            {
                "turnId": turn_id,
                "conversationId": conversation_id,
                "reason": "user_stop",
            },
        )
    )
    cancel_replies = [m for m in lines if m.get("id") == 3]
    assert cancel_replies and cancel_replies[0].get("result", {}).get("cancelled") is True
    await asyncio.sleep(0)
    assert turn_task.cancelled() or turn_task.done()

    hang.set()
    pending = [t for t in list(server._pending_sends) if not t.done()]
    if pending:
        await asyncio.gather(*pending)
    if not turn_task.done():
        turn_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn_task


async def _drain_until_message_end(sink: EventSink) -> dict:
    """Pull from the live queue until message_end (MESSAGE_END is history-skipped)."""
    while True:
        ev = await asyncio.wait_for(sink.get(), timeout=1.0)
        assert ev is not None
        if ev.type == EventType.MESSAGE_END:
            return dict(ev.payload)


def test_emit_user_stop_message_end_sets_cancelled_finish_reason():
    sink = EventSink()
    _emit_user_stop_message_end(sink)
    assert not sink._closed
    assert sink._stream_finish_reason == FinishReason.CANCELLED.value


async def test_emit_cancel_end_if_cancelling_only_when_task_cancelling():
    sink = EventSink()
    # Not cancelling → no-op
    _emit_cancel_end_if_cancelling(sink)
    assert sink._stream_finish_reason is None

    async def _body() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            _emit_cancel_end_if_cancelling(sink)

    task = asyncio.create_task(_body())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sink._stream_finish_reason == FinishReason.CANCELLED.value
    payload = await _drain_until_message_end(sink)
    assert payload["finish_reason"] == FinishReason.CANCELLED


async def test_close_user_stop_turn_emits_message_end_even_when_persist_skipped(monkeypatch):
    """Cloud isomorphic C: live confirmation even when durable salvage gates skip."""
    from agentcore.conversation import turn_persistence

    monkeypatch.setattr(
        turn_persistence.settings, "incomplete_turn_persist_enabled", False
    )
    sink = EventSink()
    closed = await turn_persistence.close_user_stop_turn(
        sink=sink,
        conversation_id="c1",
        trace_id="t1",
        message_id="m1",
    )
    assert closed is False
    assert sink._stream_finish_reason == FinishReason.CANCELLED.value
    payload = await _drain_until_message_end(sink)
    assert payload["finish_reason"] == FinishReason.CANCELLED


async def test_close_user_stop_turn_overlap_uses_visible_reason(monkeypatch):
    """Overlap closer must not stamp USER_STOP (that path is silent when empty)."""
    from agentcore.conversation import turn_persistence
    from agentcore.runtime.turn.interrupt import TurnInterruptReason
    from agentcore.runtime.turn.runs import turn_runs

    called: list[dict] = []

    async def _fake_close(**kwargs):
        called.append(kwargs)
        return True

    monkeypatch.setattr(turn_persistence.settings, "incomplete_turn_persist_enabled", True)
    monkeypatch.setattr(turn_persistence, "close_turn_interrupted", _fake_close)
    monkeypatch.setattr(turn_runs, "is_superseded", lambda _cid: True)
    monkeypatch.setattr(turn_runs, "is_user_stop", lambda _cid: False)

    sink = EventSink()
    ok = await turn_persistence.close_user_stop_turn(
        sink=sink,
        conversation_id="c-overlap",
        trace_id="t1",
        message_id="m-overlap",
    )
    assert ok is True
    assert called[0]["reason"] == TurnInterruptReason.OVERLAP
    assert sink._stream_finish_reason == FinishReason.INTERRUPTED.value


async def test_close_user_stop_turn_empty_body_still_durable(monkeypatch):
    """Empty journal + empty content must still durable-close (not skip)."""
    from agentcore.conversation import turn_persistence
    from agentcore.runtime.turn.interrupt import TurnInterruptReason

    called: list[dict] = []

    async def _fake_close(**kwargs):
        called.append(kwargs)
        return True

    monkeypatch.setattr(turn_persistence.settings, "incomplete_turn_persist_enabled", True)
    monkeypatch.setattr(turn_persistence, "close_turn_interrupted", _fake_close)

    sink = EventSink()
    ok = await turn_persistence.close_user_stop_turn(
        sink=sink,
        conversation_id="c1",
        trace_id="t1",
        message_id="m-empty",
    )
    assert ok is True
    assert called and called[0]["load_stream_state"] is True
    assert called[0]["reason"] == TurnInterruptReason.USER_STOP
    assert called[0]["content"] == ""
    assert sink._stream_finish_reason == FinishReason.CANCELLED.value
    payload = await _drain_until_message_end(sink)
    assert payload["finish_reason"] == FinishReason.CANCELLED


async def test_close_user_stop_after_content_reset_salvages_stash(monkeypatch):
    """finish_guard reset then stop: keep pre-reset prose (SSE + durable content)."""
    from agentcore.conversation import turn_persistence
    from agentcore.runtime.events import content_delta, content_reset

    called: list[dict] = []

    async def _fake_close(**kwargs):
        called.append(kwargs)
        return True

    monkeypatch.setattr(turn_persistence.settings, "incomplete_turn_persist_enabled", True)
    monkeypatch.setattr(turn_persistence, "close_turn_interrupted", _fake_close)

    sink = EventSink()
    sink.emit(content_delta("重置前已流式正文"))
    sink.emit(content_reset("finish_guard"))
    assert sink.streamed_content() == ""
    assert sink.interrupt_salvage_content() == "重置前已流式正文"

    # Drop setup frames so stop-path SSE is isolated.
    while True:
        try:
            sink._queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    ok = await turn_persistence.close_user_stop_turn(
        sink=sink,
        conversation_id="c1",
        trace_id="t1",
        message_id="m-reset-stop",
    )
    assert ok is True
    assert called and "重置前已流式正文" in (called[0].get("content") or "")

    # Live SSE: salvage delta before message_end(cancelled).
    first = await asyncio.wait_for(sink.get(), timeout=1.0)
    assert first is not None
    assert first.type == EventType.CONTENT_DELTA
    assert first.payload["delta"] == "重置前已流式正文"
    end = await asyncio.wait_for(sink.get(), timeout=1.0)
    assert end is not None
    assert end.type == EventType.MESSAGE_END
    assert end.payload["finish_reason"] == FinishReason.CANCELLED


async def test_interrupt_stash_cleared_when_live_delta_arrives():
    """New CONTENT_DELTA after reset clears stash — live rewrite owns the body."""
    from agentcore.runtime.events import content_delta, content_reset

    sink = EventSink()
    sink.emit(content_delta("旧稿"))
    sink.emit(content_reset("finish_guard"))
    assert sink.interrupt_salvage_content() == "旧稿"
    sink.emit(content_delta("新稿"))
    assert sink.streamed_content() == "新稿"
    assert sink.interrupt_salvage_content() == "新稿"
    assert sink._interrupt_content_stash is None


def test_ensure_cancelled_turn_end_appends_when_missing():
    out = _ensure_cancelled_turn_end(
        [{"kind": "run_started", "payload": {"run_id": "w1"}, "seq": 0}]
    )
    assert [e.get("kind") for e in out] == ["run_started", KIND_TURN_END]
    assert out[-1]["payload"]["finish_reason"] == FinishReason.CANCELLED.value
    assert out[-1]["seq"] == 1


def test_ensure_cancelled_turn_end_skips_when_present():
    existing = [
        {"kind": KIND_TURN_END, "payload": {"finish_reason": "end_turn"}, "seq": 0}
    ]
    out = _ensure_cancelled_turn_end(existing)
    assert len(out) == 1
    assert out[0]["payload"]["finish_reason"] == "end_turn"


def _message_end_events(sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for msg in sent:
        if msg.get("method") != "turn/event":
            continue
        event = (msg.get("params") or {}).get("event") or {}
        if event.get("type") == "message_end":
            events.append(event)
    return events


async def test_resume_user_stop_emits_message_end_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Resume cancel unwind must emit message_end(cancelled) before sink close."""
    started = asyncio.Event()

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("resume pipeline must be cancelled")

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    assert server._paused_store is not None
    await server._paused_store.save(_pause_frame())

    await server.handle_line(
        _req(
            7,
            "resume",
            {
                "messageId": "m1",
                "conversationId": "c1",
                "decision": "continue",
                "userMessageId": "u1",
                "traceId": "a" * 32,
            },
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2.0)
    turn_task = server._turns["m1"]
    await server.handle_line(
        _req(
            8,
            "cancel",
            {"turnId": "m1", "conversationId": "c1", "reason": "user_stop"},
        )
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(turn_task, timeout=2.0)
        await _await_pending_sends(server)

        ends = _message_end_events(lines)
        assert ends, "resume cancel must pump message_end(cancelled) to the renderer"
        assert ends[-1]["payload"]["finish_reason"] == FinishReason.CANCELLED.value
        err = next(m for m in lines if m.get("id") == 7 and "error" in m)
        assert err["error"]["code"] == TURN_CANCELLED
    finally:
        if not turn_task.done():
            turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(turn_task, timeout=2.0)


async def test_resume_cancel_rpc_does_not_wait_for_hanging_pump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """TURN_CANCELLED must be scheduled even if the event pump never finishes."""
    started = asyncio.Event()
    pump_release = asyncio.Event()

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("resume pipeline must be cancelled")

    async def hanging_pump(*_args: Any, **_kwargs: Any) -> None:
        await pump_release.wait()

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    monkeypatch.setattr(server, "_pump", hanging_pump)
    await _init_sidecar(server, tmp_path)
    assert server._paused_store is not None
    await server._paused_store.save(_pause_frame())

    await server.handle_line(
        _req(
            7,
            "resume",
            {
                "messageId": "m1",
                "conversationId": "c1",
                "decision": "continue",
                "userMessageId": "u1",
                "traceId": "a" * 32,
            },
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2.0)
    turn_task = server._turns["m1"]
    await server.handle_line(
        _req(
            8,
            "cancel",
            {"turnId": "m1", "conversationId": "c1", "reason": "user_stop"},
        )
    )

    async def _cancelled_rpc() -> dict[str, Any]:
        while True:
            err = next((m for m in lines if m.get("id") == 7 and "error" in m), None)
            if err is not None:
                return err
            await _await_pending_sends(server)
            await asyncio.sleep(0)

    try:
        err = await asyncio.wait_for(_cancelled_rpc(), timeout=2.0)
        assert err["error"]["code"] == TURN_CANCELLED
        assert err["error"]["message"] == "turn cancelled"
        assert not turn_task.done()
    finally:
        pump_release.set()
        if not turn_task.done():
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(turn_task, timeout=2.0)
