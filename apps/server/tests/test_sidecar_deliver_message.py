"""Sidecar deliverMessage / queue RPCs share the process-local in-flight kernel."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.turn.queue import new_queued_turn, turn_queue
from agentcore.runtime.turn.runs import turn_runs
from agentcore.sidecar.protocol import NO_LIVE_TURN, PENDING_INTERACTIONS, QUEUED_TURN_NOT_FOUND
from agentcore.sidecar.server import SidecarServer
from agentcore.sidecar.server_pkg.turns import register_current_turn_run


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    lines: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        lines.append(json.loads(line))

    return lines, write_line


def _req(request_id: int, method: str, params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})


async def _never() -> None:
    await asyncio.Future()


async def _init_sidecar(server: SidecarServer, tmp_path) -> None:
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


def _reply(lines: list[dict[str, Any]], request_id: int) -> dict[str, Any]:
    return next(m for m in lines if m.get("id") == request_id)


@pytest.fixture(autouse=True)
def _clean_slots():
    yield
    turn_queue.clear("c-sidecar-deliver")
    turn_queue.clear("c-sidecar-queue")
    turn_queue.clear("c-sidecar-idle")
    turn_queue.clear("c-sidecar-hot")
    turn_queue.clear("c-sidecar-list")
    turn_queue.clear("c-sidecar-started")


async def test_deliver_message_steer_received_no_new_turn(tmp_path, monkeypatch):
    """Live sidecar + steer → user_interjection received; does not open a new turn."""
    from agentcore.runtime.turn import steer as turn_steer_mod

    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    cid = "c-sidecar-deliver"
    turn_id = "turn-live"
    turn_queue.clear(cid)
    turn_steer_mod._reset_for_tests()
    blocker = asyncio.create_task(_never())
    sink = EventSink()
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink, user_id="u")
    server._register_turn(turn_id, blocker, conversation_id=cid)
    turn_steer_mod.begin_accepting(cid, execution_id="exec-sidecar")
    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination_for_conversation",
        lambda _cid: None,
    )
    persist = MagicMock(side_effect=AssertionError("sidecar must not re-persist attachments"))
    monkeypatch.setattr(
        "agentcore.api.routes.conversations.messages._persist_delivered_interjection_attachments",
        persist,
    )
    try:
        await server.handle_line(
            _req(
                2,
                "deliverMessage",
                {
                    "conversationId": cid,
                    "content": "steer live",
                    "delivery": "steer",
                    "attachments": [
                        {
                            "name": "note.md",
                            "path": "note.md",
                            "workspace_path": "attachments/note.md",
                        }
                    ],
                },
            )
        )
        resp = _reply(lines, 2)
        assert "error" not in resp
        assert resp["result"]["status"] == "received"
        assert resp["result"]["delivery"] == "steer"
        assert turn_queue.depth(cid) == 0
        assert turn_runs.get(cid) is not None
        assert turn_runs.get(cid).task is blocker
        assert server.live_turn_task(cid) is blocker
        inj = [e for e in sink._history if e.type is EventType.USER_INTERJECTION]  # noqa: SLF001
        assert len(inj) == 1
        assert inj[0].payload["status"] == "received"
        assert inj[0].payload["content"] == "steer live"
        persist.assert_not_called()
        # No second confirm sink / extra RPC event stream for the interjection.
        turn_events = [m for m in lines if m.get("method") == "turn/event"]
        assert turn_events == []
    finally:
        turn_steer_mod.end_accepting(cid)
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)
        turn_steer_mod._reset_for_tests()


async def test_deliver_message_queue_uses_sidecar_starter(tmp_path, monkeypatch):
    """Live + queue → enqueue; after host ends drain calls sidecar starter, not stream_chat."""
    from agentcore.runtime.turn import queue as queue_mod

    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    cid = "c-sidecar-queue"
    turn_queue.clear(cid)
    assert queue_mod._queue_starter is not None  # noqa: SLF001
    assert queue_mod._queue_starter.__func__.__name__ == "_start_queued_sidecar_turn"  # noqa: SLF001

    started: list[str] = []

    async def fake_starter(conversation_id: str, item: Any) -> None:
        started.append(item.content)

    async def boom_cloud(*_a: Any, **_k: Any) -> None:
        raise AssertionError("FIFO drain must not call cloud stream_chat in sidecar")

    monkeypatch.setattr(queue_mod, "_start_queued_turn", boom_cloud)
    queue_mod.set_queue_starter(fake_starter)

    blocker = asyncio.create_task(_never())
    sink = EventSink()
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink, user_id="u")
    server._register_turn("turn-host", blocker, conversation_id=cid)
    try:
        await server.handle_line(
            _req(
                2,
                "deliverMessage",
                {"conversationId": cid, "content": "queued next", "delivery": "queue"},
            )
        )
        resp = _reply(lines, 2)
        assert resp["result"]["status"] == "queued"
        assert resp["result"]["queueId"]
        assert turn_queue.depth(cid) == 1
        assert started == []

        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        for _ in range(40):
            if started:
                break
            await asyncio.sleep(0.025)
        assert started == ["queued next"]
    finally:
        if not blocker.done():
            blocker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await blocker
        turn_queue.clear(cid)


async def test_sidecar_fifo_starter_asks_desktop_start_turn(tmp_path, monkeypatch):
    """FIFO drain notifies ``queue/needStart`` and does not run the engine itself."""
    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    cid = "c-need-start"
    monkeypatch.setattr(server, "_creds_for", lambda *_a, **_k: None)
    server._outbox_store = None
    item = new_queued_turn(
        content="next",
        user_id="u",
        user_message_id="11111111-1111-4111-8111-111111111111",
        message_id="22222222-2222-4222-8222-222222222222",
        trace_id="a" * 32,
    )
    starter = asyncio.create_task(server._start_queued_sidecar_turn(cid, item))
    try:
        for _ in range(40):
            if any(m.get("method") == "queue/needStart" for m in lines):
                break
            await asyncio.sleep(0.025)
        notes = [m for m in lines if m.get("method") == "queue/needStart"]
        assert notes
        assert notes[0]["params"]["messageId"] == item.message_id
        assert notes[0]["params"]["queueId"] == item.queue_id
        assert notes[0]["params"]["userMessage"] == "next"
        assert not any(m.get("method") == "turn/event" for m in lines)

        await server.handle_line(
            _req(
                9,
                "startTurn",
                {
                    "turnId": "t-fifo",
                    "conversationId": cid,
                    "userMessage": "next",
                    "history": [],
                    "queueId": item.queue_id,
                    "userMessageId": item.user_message_id,
                    "messageId": item.message_id,
                    "traceId": item.trace_id,
                },
            )
        )
        await asyncio.wait_for(starter, timeout=5)
        assert starter.exception() is None
    finally:
        if not starter.done():
            starter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await starter
        live = server.live_turn_task(cid)
        if live is not None and not live.done():
            live.cancel()
            with pytest.raises(asyncio.CancelledError):
                await live
        turn_queue.clear(cid)


async def test_deliver_message_without_live_fails(tmp_path):
    """No occupying run → RPC error; does not succeed or mention the cloud."""
    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    cid = "c-sidecar-idle"
    await server.handle_line(
        _req(
            2,
            "deliverMessage",
            {"conversationId": cid, "content": "hello", "delivery": "steer"},
        )
    )
    resp = _reply(lines, 2)
    assert resp["error"]["code"] == NO_LIVE_TURN
    assert resp["error"]["data"]["code"] == "no_live_turn"
    msg = resp["error"]["message"].lower()
    assert "cloud" not in msg
    assert "http" not in msg
    assert "/messages" not in msg
    assert turn_queue.depth(cid) == 0
    assert turn_runs.get(cid) is None


async def test_deliver_message_hot_pending_blocked(tmp_path):
    from agentcore.runtime.interaction import InteractionKind, default_interaction_registry

    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    cid = "c-sidecar-hot"
    rid = "approval-sidecar-hot"
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=EventSink(), user_id="u")
    server._register_turn("turn-hot", blocker, conversation_id=cid)
    reg = default_interaction_registry()
    reg.create(rid, cid, kind=InteractionKind.APPROVAL, payload={"tool_name": "x"})
    try:
        await server.handle_line(
            _req(
                2,
                "deliverMessage",
                {"conversationId": cid, "content": "hi", "delivery": "steer"},
            )
        )
        resp = _reply(lines, 2)
        assert resp["error"]["code"] == PENDING_INTERACTIONS
        assert resp["error"]["data"]["code"] == "pending_interactions_awaiting"
        assert "approval" in resp["error"]["data"]["pending_kinds"]
        assert turn_queue.depth(cid) == 0
    finally:
        reg.discard(rid)
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker


async def test_list_and_cancel_queued_turn_rpc(tmp_path):
    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    cid = "c-sidecar-list"
    turn_queue.clear(cid)
    blocker = asyncio.create_task(_never())
    sink = EventSink()
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink, user_id="u")
    item = new_queued_turn(content="later", user_id="u")
    turn_queue.enqueue(cid, item)
    try:
        await server.handle_line(_req(2, "listQueuedTurns", {"conversationId": cid}))
        listed = _reply(lines, 2)["result"]["items"]
        assert len(listed) == 1
        assert listed[0]["queueId"] == item.queue_id
        assert listed[0]["content"] == "later"
        assert listed[0]["position"] == 1

        await server.handle_line(
            _req(3, "cancelQueuedTurn", {"conversationId": cid, "queueId": item.queue_id})
        )
        cancelled = _reply(lines, 3)
        assert cancelled["result"]["ok"] is True
        assert turn_queue.depth(cid) == 0
        assert any(e.type is EventType.TURN_QUEUE_CANCELLED for e in sink._history)  # noqa: SLF001

        await server.handle_line(
            _req(4, "cancelQueuedTurn", {"conversationId": cid, "queueId": item.queue_id})
        )
        missing = _reply(lines, 4)
        assert missing["error"]["code"] == QUEUED_TURN_NOT_FOUND
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


async def test_sidecar_queue_id_emits_started_with_content(tmp_path, monkeypatch):
    """Sidecar dequeue path: ``turn_queue_started`` carries item content (入场帧)."""
    lines, write_line = _recorder()
    server = SidecarServer(write_line)
    await _init_sidecar(server, tmp_path)
    cid = "c-sidecar-started"
    turn_queue.clear(cid)
    monkeypatch.setattr(server, "_creds_for", lambda *_a, **_k: None)
    server._outbox_store = None
    att = {
        "name": "note.md",
        "path": "note.md",
        "text": "",
        "truncated": False,
        "kind": "file",
        "conversation_id": None,
        "binary": False,
        "workspace_path": "attachments/note.md",
    }
    task = asyncio.create_task(
        server._run_turn(
            10,
            "turn-q-started",
            {
                "conversationId": cid,
                "userMessage": "next",
                "history": [],
                "queueId": "q-side",
                "attachments": [att],
                "agentMentions": [{"agent_id": "a1", "role": "研究员"}],
                "userMessageId": "11111111-1111-4111-8111-111111111111",
                "messageId": "22222222-2222-4222-8222-222222222222",
                "traceId": "a" * 32,
            },
        )
    )
    try:
        await asyncio.wait_for(task, timeout=5)
        events = [m["params"]["event"] for m in lines if m.get("method") == "turn/event"]
        assert events, "expected turn/event frames"
        first = events[0]
        assert first["type"] == "turn_queue_started"
        assert first["payload"]["queue_id"] == "q-side"
        assert first["payload"]["content"] == "next"
        assert first["payload"]["attachments"] == [att]
        assert first["payload"]["agent_mentions"] == [
            {"agent_id": "a1", "role": "研究员"}
        ]
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        turn_queue.clear(cid)


async def test_register_current_turn_run_occupies_slot():
    """Sidecar _run_turn helper occupies turn_runs with the current task."""
    cid = "c-register-helper"
    sink = EventSink()
    occupied = asyncio.Event()

    async def _turn() -> None:
        register_current_turn_run(conversation_id=cid, sink=sink, user_id="u")
        run = turn_runs.get(cid)
        assert run is not None
        assert run.task is asyncio.current_task()
        occupied.set()
        await asyncio.Future()

    task = asyncio.create_task(_turn())
    try:
        await occupied.wait()
        live = turn_runs.get(cid)
        assert live is not None
        assert live.task is task
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

