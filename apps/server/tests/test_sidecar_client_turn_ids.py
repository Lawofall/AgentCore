"""Desktop-minted local-turn identity: sidecar must not new_id() assistant/trace."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentcore.conversation.store import reset_conversation_store_for_tests
from agentcore.runtime.turn.queue import new_queued_turn, turn_queue
from agentcore.runtime.turn.runs import turn_runs
from agentcore.sidecar import protocol
from agentcore.sidecar.server import SidecarServer
from agentcore.sidecar.server_pkg.delivery import DeliveryMixin
from agentcore.sidecar.server_pkg.turns import parse_client_turn_ids

CLIENT_TURN_IDS = {
    "userMessageId": "11111111-1111-4111-8111-111111111111",
    "messageId": "22222222-2222-4222-8222-222222222222",
    "traceId": "a" * 32,
}

_FAKE_INFERENCE = {
    "baseUrl": "http://test.local/v1/inference/v1",
    "apiKey": "test-inference-tok",
    "model": "test-model",
}


@pytest.fixture(autouse=True)
def _reset_store_and_queue():
    yield
    turn_queue.clear("c-ids")
    reset_conversation_store_for_tests()


@pytest.fixture(autouse=True)
def _stub_conversation_folder_id(monkeypatch: pytest.MonkeyPatch):
    async def _none(_conversation_id: str) -> None:
        return None

    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        _none,
    )


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def _response(sent: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(m for m in sent if m.get("id") == request_id)


def test_parse_client_turn_ids_requires_32_hex_trace():
    assert parse_client_turn_ids(CLIENT_TURN_IDS) == (
        CLIENT_TURN_IDS["userMessageId"],
        CLIENT_TURN_IDS["messageId"],
        CLIENT_TURN_IDS["traceId"],
    )
    missing = {**CLIENT_TURN_IDS, "messageId": ""}
    assert parse_client_turn_ids(missing) is None
    hyphenated = {**CLIENT_TURN_IDS, "traceId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    assert parse_client_turn_ids(hyphenated) is None
    too_short = {**CLIENT_TURN_IDS, "traceId": "abc"}
    assert parse_client_turn_ids(too_short) is None


def test_run_turn_source_does_not_mint_assistant_or_trace():
    from agentcore.sidecar.server_pkg import turns as turns_mod

    src = inspect.getsource(turns_mod.TurnExecutionMixin._run_turn)
    assert "message_id = new_id()" not in src
    assert 'trace_id = str(params.get("traceId") or "")' not in src
    assert "parse_client_turn_ids" in src


def test_fifo_starter_source_does_not_mint_trace():
    src = inspect.getsource(DeliveryMixin._start_queued_sidecar_turn)
    assert '"traceId": new_id()' not in src
    assert '"userMessageId": new_id()' not in src
    assert "parse_client_turn_ids" in src


def test_start_turn_without_message_id_is_invalid_params(tmp_path, monkeypatch):
    pipeline = AsyncMock(side_effect=AssertionError("pipeline must not run"))
    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", pipeline)
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": False,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "hi",
                        "userMessageId": CLIENT_TURN_IDS["userMessageId"],
                        "traceId": CLIENT_TURN_IDS["traceId"],
                    },
                }
            )
        )

    asyncio.run(drive())
    pipeline.assert_not_awaited()
    err = _response(sent, 2)
    assert err["error"]["code"] == protocol.INVALID_PARAMS
    assert not server._turns


def test_start_turn_uses_client_message_id_on_outbox_bind(tmp_path, monkeypatch):
    bound: dict[str, str] = {}

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
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
                        "approvalsEnabled": False,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        orig = server._outbox_store.bind_turn

        def capture(**kwargs: Any) -> None:
            bound["user_message_id"] = kwargs["user_message_id"]
            bound["message_id"] = kwargs["message_id"]
            bound["trace_id"] = kwargs["trace_id"]
            return orig(**kwargs)

        monkeypatch.setattr(server._outbox_store, "bind_turn", capture)
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        "turnId": "t1",
                        "conversationId": "c1",
                        "userMessage": "hi",
                        **CLIENT_TURN_IDS,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    assert bound["message_id"] == CLIENT_TURN_IDS["messageId"]
    assert bound["user_message_id"] == CLIENT_TURN_IDS["userMessageId"]
    assert bound["trace_id"] == CLIENT_TURN_IDS["traceId"]
    assert "result" in _response(sent, 2)


async def test_deliver_message_queue_stores_client_triple(tmp_path, monkeypatch):
    from agentcore.runtime.events import EventSink

    lines, write_line = _recorder()
    server = SidecarServer(write_line)
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
                    "inference": _FAKE_INFERENCE,
                },
            }
        )
    )
    cid = "c-ids"
    turn_queue.clear(cid)

    async def _never() -> None:
        await asyncio.Future()

    blocker = asyncio.create_task(_never())
    sink = EventSink()
    turn_runs.register(conversation_id=cid, task=blocker, sink=sink, user_id="u")
    server._register_turn("turn-live", blocker, conversation_id=cid)
    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination_for_conversation",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "agentcore.conversation.midflight_persist.persist_midflight_user_message",
        AsyncMock(return_value=CLIENT_TURN_IDS["userMessageId"]),
    )
    try:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "deliverMessage",
                    "params": {
                        "conversationId": cid,
                        "content": "next",
                        "delivery": "queue",
                        **CLIENT_TURN_IDS,
                    },
                }
            )
        )
        pending = turn_queue.list_pending(cid)
        assert pending
        queued = pending[0]
        assert queued.user_message_id == CLIENT_TURN_IDS["userMessageId"]
        assert queued.message_id == CLIENT_TURN_IDS["messageId"]
        assert queued.trace_id == CLIENT_TURN_IDS["traceId"]
        turn_queue.clear(cid)
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


def test_fifo_drain_requires_32_hex(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": False,
                        "inference": _FAKE_INFERENCE,
                    },
                }
            )
        )
        item = new_queued_turn(
            content="next",
            user_id="u",
            user_message_id="u1",
            message_id="m1",
            trace_id="not-hex",
        )
        with pytest.raises(RuntimeError, match="32-hex"):
            await server._start_queued_sidecar_turn("c-ids", item)

    asyncio.run(drive())
    assert not sent or all(m.get("method") != "startTurn" for m in sent)


def test_new_id_is_not_32_hex():
    from agentcore.core.types import new_id

    minted = new_id()
    assert len(minted) != 32
    assert parse_client_turn_ids({**CLIENT_TURN_IDS, "traceId": minted}) is None
