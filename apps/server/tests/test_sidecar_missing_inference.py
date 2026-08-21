"""Sidecar refuses startTurn / resume when inference credentials are absent."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

from agentcore.core.error_codes import ErrorCode
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.sidecar.server import SidecarServer


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def _response(sent: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(m for m in sent if m.get("id") == request_id)


async def _initialize_without_inference(server: SidecarServer, tmp_path, *, data_dir: str) -> None:
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
                    "dataDir": data_dir,
                },
            }
        )
    )


def _ask_user_frame(message_id: str, conversation_id: str) -> AskUserSuspension:
    susp = AskUserSuspension(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id="u1",
        captain_run_id="r1",
        checkpoint_id=f"cp-{message_id}",
        tool_call_id="tc1",
        base_system_prompt="sys",
        user_message="paused",
        transcript=[],
        history=[],
        question="继续？",
    )
    susp.journal_entries = [
        {"kind": "checkpoint_required", "payload": {"id": "cp"}, "ts": None}
    ]
    return susp


def test_start_turn_rejects_without_inference_credentials(tmp_path, monkeypatch):
    """Missing inference → structured INFERENCE_TOKEN_EXPIRED result; pipeline never runs."""
    pipeline = AsyncMock(side_effect=AssertionError("run_chat_pipeline must not run"))
    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", pipeline)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data_dir = str(tmp_path / "data")

    async def drive() -> None:
        await _initialize_without_inference(server, tmp_path, data_dir=data_dir)
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        "turnId": "t-missing",
                        "conversationId": "c-missing",
                        "userMessage": "hi",
                        "userMessageId": "u-missing",
                        "messageId": "22222222-2222-4222-8222-222222222222",
                        "traceId": "a" * 32,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())

    pipeline.assert_not_awaited()
    done = _response(sent, 2)
    assert "result" in done
    result = done["result"]
    assert result["finishReason"] == "error"
    assert result["error"]["code"] == ErrorCode.INFERENCE_TOKEN_EXPIRED
    assert "推理凭证" in result["error"]["message"]
    assert result["runs"] is not None
    assert result["runs"]["error"]["code"] == ErrorCode.INFERENCE_TOKEN_EXPIRED

    # Live UI gets the same code via turn/event before the deferred result.
    error_notes = [
        m
        for m in sent
        if m.get("method") == "turn/event" and m["params"]["event"]["type"] == "error"
    ]
    assert error_notes
    assert error_notes[0]["params"]["conversationId"] == "c-missing"
    assert error_notes[0]["params"]["turnId"] == "t-missing"
    assert error_notes[0]["params"]["event"]["payload"]["code"] == ErrorCode.INFERENCE_TOKEN_EXPIRED


def test_resume_rejects_without_inference_before_claim(tmp_path, monkeypatch):
    """Missing inference on resume → RPC error with code; pause frame stays claimable."""
    resume_pipeline = AsyncMock(side_effect=AssertionError("resume_chat_pipeline must not run"))
    monkeypatch.setattr("agentcore.sidecar.server.resume_chat_pipeline", resume_pipeline)

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    message_id = "m-paused-1"
    conversation_id = "c-paused-1"

    frame = _ask_user_frame(message_id, conversation_id)

    async def drive() -> None:
        await _initialize_without_inference(server, tmp_path, data_dir=str(data_dir))
        assert server._paused_store is not None
        await server._paused_store.save(frame)
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resume",
                    "params": {
                        "messageId": message_id,
                        "conversationId": conversation_id,
                        "decision": "continue",
                        "traceId": "b" * 32,
                    },
                }
            )
        )

    asyncio.run(drive())

    resume_pipeline.assert_not_awaited()
    err = _response(sent, 2)
    assert "error" in err
    assert err["error"]["data"]["code"] == ErrorCode.INFERENCE_TOKEN_EXPIRED
    assert "推理凭证" in err["error"]["message"]

    # Frame must remain for remint + retry (never claimed).
    async def _still_there() -> AskUserSuspension | None:
        assert server._paused_store is not None
        return await server._paused_store.load(message_id, conversation_id=conversation_id)

    assert asyncio.run(_still_there()) is not None


def test_start_turn_clears_inference_when_explicit_null(tmp_path, monkeypatch):
    """Per-turn ``inference: null`` clears session creds and early-rejects."""
    pipeline = AsyncMock(side_effect=AssertionError("run_chat_pipeline must not run"))
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
                        "dataDir": str(tmp_path / "data"),
                        "inference": {
                            "baseUrl": "http://test.local/v1/inference/v1",
                            "apiKey": "tok",
                            "model": "m",
                        },
                    },
                }
            )
        )
        assert server._creds is not None
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "startTurn",
                    "params": {
                        "turnId": "t-clear",
                        "conversationId": "c-clear",
                        "userMessage": "hi",
                        "inference": None,
                        "userMessageId": "u-clear",
                        "messageId": "22222222-2222-4222-8222-222222222222",
                        "traceId": "a" * 32,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    asyncio.run(drive())
    pipeline.assert_not_awaited()
    done = _response(sent, 2)
    assert done["result"]["error"]["code"] == ErrorCode.INFERENCE_TOKEN_EXPIRED
