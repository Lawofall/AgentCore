"""Sidecar cold resume × live deferred (wrap_up hold → resume_deferred → auto claim)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest

from agentcore.conversation.store import reset_conversation_store_for_tests
from agentcore.runtime.events import EventType
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.sidecar.paused_store import LocalPausedTurnStore
from agentcore.sidecar.server import SidecarServer


@pytest.fixture(autouse=True)
def _reset_conversation_store():
    yield
    reset_conversation_store_for_tests()


@pytest.fixture(autouse=True)
def _stub_conversation_folder_id(monkeypatch: pytest.MonkeyPatch):
    async def _none(_conversation_id: str) -> None:
        return None

    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        _none,
    )


def _suspension(message_id: str, conversation_id: str) -> AskUserSuspension:
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
        question="要继续吗？\n背景",
    )
    susp.journal_entries = [
        {"kind": "checkpoint_required", "payload": {"id": "cp"}, "ts": None}
    ]
    return susp


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def _response(sent: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(m for m in sent if m.get("id") == request_id)


def _resume_deferred_events(sent: list[dict[str, Any]], turn_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in sent:
        if m.get("method") != "turn/event":
            continue
        params = m.get("params") or {}
        if params.get("turnId") != turn_id:
            continue
        ev = params.get("event") or {}
        if ev.get("type") == EventType.RESUME_DEFERRED.value:
            out.append(ev)
    return out


async def _initialize(server: SidecarServer, tmp_path, *, data_dir: str) -> None:
    await server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "userId": "u",
                    "workspaceRoot": str(tmp_path),
                    "approvalsEnabled": True,
                    "dataDir": data_dir,
                    "inference": {
                        "baseUrl": "http://test.local/v1/inference/v1",
                        "apiKey": "test-inference-tok",
                        "model": "test-model",
                    },
                },
            }
        )
    )


async def _never() -> None:
    await asyncio.Future()


def test_resume_deferred_wrap_up_then_auto_claim(tmp_path, monkeypatch):
    """Host wrap_up still holds ``_turns[message_id]`` → resume_deferred → wake → claim+run."""
    resumed = asyncio.Event()

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        resumed.set()
        kwargs["sink"].close()
        return {
            "finish_reason": "end_turn",
            "content": "deferred-resume-ok",
            "rounds": 1,
            "message_id": kwargs["suspension"].message_id,
        }

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data = tmp_path / "data"
    store = LocalPausedTurnStore(data / "paused")
    message_id = "m-wrap"
    conversation_id = "c-wrap"

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(data))
        await store.save(_suspension(message_id, conversation_id))

        # Simulate D1 hold: host task still registered under the paused message_id.
        host = asyncio.create_task(_never())
        server._register_turn(message_id, host, conversation_id=conversation_id)
        assert server.busy_reason_for_resume(conversation_id, message_id) == "wrap_up"

        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resume",
                    "params": {
                        "messageId": message_id,
                        "conversationId": conversation_id,
                        "decision": "continue",
                        "note": "",
                        "userMessageId": "um-wrap",
                    },
                }
            )
        )
        # Give the deferred task a tick to emit + park.
        await asyncio.sleep(0)
        for _ in range(20):
            if _resume_deferred_events(sent, message_id):
                break
            await asyncio.sleep(0.01)

        deferred = _resume_deferred_events(sent, message_id)
        assert len(deferred) == 1
        assert deferred[0]["payload"]["busy_reason"] == "wrap_up"
        deferred_notes = [
            m
            for m in sent
            if m.get("method") == "turn/event"
            and (m.get("params") or {}).get("turnId") == message_id
        ]
        assert all(n["params"]["conversationId"] == conversation_id for n in deferred_notes)
        assert deferred[0]["payload"]["message_id"] == message_id
        assert conversation_id in server._resume_deferred  # noqa: SLF001
        # Frame still on disk — not claimed yet while waiting.
        assert await store.load(message_id, conversation_id=conversation_id) is not None
        # RPC must not have completed (or erred as turn-already-running) yet.
        assert not any(m.get("id") == 7 for m in sent)

        # Host wrap_up ends → slot frees → deferred claims and runs.
        host.cancel()
        with pytest.raises(asyncio.CancelledError):
            await host
        server._unregister_turn(message_id)

        await asyncio.wait_for(resumed.wait(), timeout=2.0)
        # Resume turn task should finish and reply.
        for _ in range(80):
            if any(m.get("id") == 7 for m in sent):
                break
            await asyncio.sleep(0.025)

    asyncio.run(drive())

    done = _response(sent, 7)
    assert "error" not in done
    assert done["result"]["content"] == "deferred-resume-ok"
    assert done["result"]["messageId"] == message_id
    assert "turn already running" not in json.dumps(sent)


def test_resume_deferred_same_id_joins_both_rpcs(tmp_path, monkeypatch):
    """Same conversation_id + message_id re-submit joins; both RPCs get one resume result."""
    resume_calls = 0
    resumed = asyncio.Event()

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        nonlocal resume_calls
        resume_calls += 1
        resumed.set()
        kwargs["sink"].close()
        return {
            "finish_reason": "end_turn",
            "content": "joined-resume-ok",
            "rounds": 1,
            "message_id": kwargs["suspension"].message_id,
        }

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data = tmp_path / "data"
    store = LocalPausedTurnStore(data / "paused")
    message_id = "m-join"
    conversation_id = "c-join"

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(data))
        await store.save(_suspension(message_id, conversation_id))

        host = asyncio.create_task(_never())
        server._register_turn(message_id, host, conversation_id=conversation_id)

        for rid in (11, 12):
            await server.handle_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "method": "resume",
                        "params": {
                            "messageId": message_id,
                            "conversationId": conversation_id,
                            "decision": "continue",
                            "note": "",
                            "userMessageId": "um-join",
                        },
                    }
                )
            )
            await asyncio.sleep(0)

        for _ in range(20):
            if _resume_deferred_events(sent, message_id):
                break
            await asyncio.sleep(0.01)

        assert len(_resume_deferred_events(sent, message_id)) == 1
        waiter = server._resume_deferred[conversation_id]  # noqa: SLF001
        assert waiter.message_id == message_id
        assert waiter.reply_ids == [11, 12]
        assert not any(m.get("id") in (11, 12) for m in sent)

        host.cancel()
        with pytest.raises(asyncio.CancelledError):
            await host
        server._unregister_turn(message_id)

        await asyncio.wait_for(resumed.wait(), timeout=2.0)
        for _ in range(80):
            if all(any(m.get("id") == rid for m in sent) for rid in (11, 12)):
                break
            await asyncio.sleep(0.025)

    asyncio.run(drive())

    assert resume_calls == 1
    for rid in (11, 12):
        done = _response(sent, rid)
        assert "error" not in done
        assert done["result"]["content"] == "joined-resume-ok"
        assert done["result"]["messageId"] == message_id
    assert "resume superseded" not in json.dumps(sent)


def test_resume_deferred_different_id_still_supersedes(tmp_path, monkeypatch):
    """Different message_id keeps last-click-wins: prior RPC gets resume superseded."""
    resume_calls = 0
    resumed = asyncio.Event()

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        nonlocal resume_calls
        resume_calls += 1
        resumed.set()
        kwargs["sink"].close()
        return {
            "finish_reason": "end_turn",
            "content": f"winner-{kwargs['suspension'].message_id}",
            "rounds": 1,
            "message_id": kwargs["suspension"].message_id,
        }

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data = tmp_path / "data"
    store = LocalPausedTurnStore(data / "paused")
    conversation_id = "c-super"
    first_id = "m-first"
    second_id = "m-second"

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(data))
        await store.save(_suspension(first_id, conversation_id))
        await store.save(_suspension(second_id, conversation_id))

        # Live turn holds the conversation slot (not wrap_up of either paused id).
        host = asyncio.create_task(_never())
        server._register_turn("live-other", host, conversation_id=conversation_id)
        assert server.busy_reason_for_resume(conversation_id, first_id) == "live_turn"

        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 21,
                    "method": "resume",
                    "params": {
                        "messageId": first_id,
                        "conversationId": conversation_id,
                        "decision": "continue",
                        "note": "",
                        "userMessageId": "um-first",
                    },
                }
            )
        )
        await asyncio.sleep(0)
        for _ in range(20):
            if conversation_id in server._resume_deferred:  # noqa: SLF001
                break
            await asyncio.sleep(0.01)
        assert server._resume_deferred[conversation_id].message_id == first_id  # noqa: SLF001

        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 22,
                    "method": "resume",
                    "params": {
                        "messageId": second_id,
                        "conversationId": conversation_id,
                        "decision": "continue",
                        "note": "",
                        "userMessageId": "um-second",
                    },
                }
            )
        )
        await asyncio.sleep(0)
        for _ in range(40):
            if any(m.get("id") == 21 for m in sent):
                break
            await asyncio.sleep(0.01)

        superseded = _response(sent, 21)
        assert superseded["error"]["message"] == "resume superseded"
        assert server._resume_deferred[conversation_id].message_id == second_id  # noqa: SLF001

        host.cancel()
        with pytest.raises(asyncio.CancelledError):
            await host
        server._unregister_turn("live-other")

        await asyncio.wait_for(resumed.wait(), timeout=2.0)
        for _ in range(80):
            if any(m.get("id") == 22 for m in sent):
                break
            await asyncio.sleep(0.025)

    asyncio.run(drive())

    assert resume_calls == 1
    winner = _response(sent, 22)
    assert "error" not in winner
    assert winner["result"]["content"] == f"winner-{second_id}"
    assert winner["result"]["messageId"] == second_id


def test_resume_deferred_without_umid_mints_uuid_outbox_key(tmp_path, monkeypatch):
    """Busy-path prewrite must not mint ``resume-{turn_id}`` as the outbox key."""

    async def fake_resume(**kwargs: Any) -> dict[str, Any]:
        kwargs["sink"].close()
        return {
            "finish_reason": "end_turn",
            "content": "deferred-uuid-ok",
            "rounds": 1,
            "message_id": kwargs["suspension"].message_id,
        }

    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", fake_resume)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data = tmp_path / "data"
    store = LocalPausedTurnStore(data / "paused")
    message_id = "11111111-1111-4111-8111-111111111111"
    conversation_id = "c-umid"

    async def drive() -> None:
        await _initialize(server, tmp_path, data_dir=str(data))
        await store.save(_suspension(message_id, conversation_id))
        host = asyncio.create_task(_never())
        server._register_turn(message_id, host, conversation_id=conversation_id)

        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resume",
                    "params": {
                        "messageId": message_id,
                        "conversationId": conversation_id,
                        "decision": "continue",
                        "traceId": "a" * 32,
                    },
                }
            )
        )
        for _ in range(20):
            if _resume_deferred_events(sent, message_id):
                break
            await asyncio.sleep(0.01)

        from agentcore.conversation.store.outbox import list_outbox_records

        records = list_outbox_records(data / "outbox")
        assert records
        umid = str(records[0]["user_message_id"])
        UUID(umid)
        assert not umid.startswith("resume-")
        assert umid != f"resume-{message_id}"

        host.cancel()
        with pytest.raises(asyncio.CancelledError):
            await host
        server._unregister_turn(message_id)
        for _ in range(80):
            if any(m.get("id") == 7 for m in sent):
                break
            await asyncio.sleep(0.025)

    asyncio.run(drive())
    done = _response(sent, 7)
    assert "error" not in done
    assert done["result"]["content"] == "deferred-uuid-ok"
