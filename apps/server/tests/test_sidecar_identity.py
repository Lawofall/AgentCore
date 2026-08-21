"""Sidecar local identity resolves the ``local`` alias to a DB-safe UUID."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from agentcore.sidecar.identity import (
    LOCAL_USER_ALIAS,
    LOCAL_USER_ID,
    resolve_sidecar_user_id,
)
from agentcore.sidecar.server import SidecarServer


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def test_local_user_id_is_uuid():
    uuid.UUID(LOCAL_USER_ID)
    assert str(
        uuid.uuid5(uuid.NAMESPACE_URL, "agentcore:sidecar:local-user")
    ) == LOCAL_USER_ID


def test_resolve_maps_local_alias_and_empty():
    assert resolve_sidecar_user_id(LOCAL_USER_ALIAS) == LOCAL_USER_ID
    assert resolve_sidecar_user_id(None) == LOCAL_USER_ID
    assert resolve_sidecar_user_id("") == LOCAL_USER_ID
    assert resolve_sidecar_user_id("  local  ") == LOCAL_USER_ID


def test_resolve_passes_through_real_ids():
    uid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert resolve_sidecar_user_id(uid) == uid
    assert resolve_sidecar_user_id("u") == "u"


def test_initialize_binds_local_alias_to_uuid(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    root = tmp_path / "ws"
    root.mkdir()

    asyncio.run(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": LOCAL_USER_ALIAS,
                        "workspaceRoot": str(root),
                    },
                }
            )
        )
    )

    resp = next(m for m in sent if m.get("id") == 1)
    assert "result" in resp
    assert "error" not in resp
    assert server._user_id == LOCAL_USER_ID  # noqa: SLF001 — binding under test


def test_initialize_missing_user_id_defaults_to_local_uuid(tmp_path):
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    root = tmp_path / "ws"
    root.mkdir()

    asyncio.run(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"workspaceRoot": str(root)},
                }
            )
        )
    )

    resp = next(m for m in sent if m.get("id") == 1)
    assert "result" in resp
    assert server._user_id == LOCAL_USER_ID  # noqa: SLF001


def test_start_turn_refreshes_user_id_per_turn(tmp_path, monkeypatch):
    """Per-turn ``userId`` overrides initialize seed (long-lived sidecar may have
    started as ``local`` / probe); absent key keeps the current process value.
    """
    captured: list[str] = []

    async def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs["user_id"])
        kwargs["sink"].close()
        return {"finish_reason": "end_turn", "content": "ok", "rounds": 1}

    async def _no_folder(_conversation_id: str) -> None:
        return None

    monkeypatch.setattr("agentcore.sidecar.server.run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "agentcore.sidecar.server_pkg.turns.load_conversation_folder_id",
        _no_folder,
    )

    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    account = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    async def start_turn(turn_id: str, extra: dict[str, Any]) -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": turn_id,
                    "method": "startTurn",
                    "params": {
                        "turnId": turn_id,
                        "conversationId": "c1",
                        "userMessage": "hi",
                        "userMessageId": "11111111-1111-4111-8111-111111111111",
                        "messageId": "22222222-2222-4222-8222-222222222222",
                        "traceId": "a" * 32,
                        **extra,
                    },
                }
            )
        )
        await asyncio.gather(*list(server._turns.values()))

    async def drive() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": LOCAL_USER_ALIAS,
                        "workspaceRoot": str(tmp_path),
                        "inference": {
                            "baseUrl": "http://test.local/v1/inference/v1",
                            "apiKey": "test-inference-tok",
                            "model": "test-model",
                        },
                    },
                }
            )
        )
        assert server._user_id == LOCAL_USER_ID  # noqa: SLF001
        await start_turn("t1", {})  # absent → keep initialize local uuid
        await start_turn("t2", {"userId": account})  # per-turn account
        await start_turn("t3", {})  # absent again → keeps refreshed account
        await start_turn("t4", {"userId": LOCAL_USER_ALIAS})  # explicit local

    asyncio.run(drive())
    assert captured == [LOCAL_USER_ID, account, account, LOCAL_USER_ID]
    assert server._user_id == LOCAL_USER_ID  # noqa: SLF001
