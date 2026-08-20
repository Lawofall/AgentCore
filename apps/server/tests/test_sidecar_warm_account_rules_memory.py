"""Sidecar warmAccountRulesMemory RPC + initialize capability (non-turn warm)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from agentcore.memory.account_prepare_cache import (
    AccountPrepareSnapshot,
    clear_account_rules_memory_cache,
    get_account_rules_memory_snapshot,
    seed_account_rules_memory_cache,
)
from agentcore.sidecar.protocol import INVALID_REQUEST, NOT_INITIALIZED
from agentcore.sidecar.server import SidecarServer


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


async def _flush_pending(server: SidecarServer) -> None:
    pending = [t for t in list(server._pending_sends) if not t.done()]
    if pending:
        await asyncio.gather(*pending)


def test_warm_account_rules_memory_requires_initialize(tmp_path: Path) -> None:
    clear_account_rules_memory_cache()
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {
                        "folderId": None,
                        "accountAuth": {
                            "baseUrl": "https://x/v1/account",
                            "apiKey": "tok",
                        },
                    },
                }
            )
        )

    asyncio.run(run())
    err = next(m for m in sent if m.get("id") == 2 and "error" in m)
    assert err["error"]["code"] == NOT_INITIALIZED


def test_warm_account_rules_memory_requires_account_auth(tmp_path: Path) -> None:
    clear_account_rules_memory_cache()
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def run() -> None:
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
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {"folderId": "f1"},
                }
            )
        )

    asyncio.run(run())
    err = next(m for m in sent if m.get("id") == 2 and "error" in m)
    assert err["error"]["code"] == INVALID_REQUEST


def test_warm_account_rules_memory_seeds_cache(
    tmp_path: Path, monkeypatch
) -> None:
    clear_account_rules_memory_cache()
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    snap = AccountPrepareSnapshot(
        rules_payload={"global_rules": [{"content": "- always"}]},
        memory_bodies={("", "偏好.md"): "- prefer concise"},
        memory_topics=(),
        degraded=False,
    )
    warm = AsyncMock(return_value=snap)
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.warm_account_rules_memory",
        warm,
    )

    async def run() -> None:
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
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {
                        "folderId": "folder-1",
                        "userId": "u1",
                        "accountAuth": {
                            "baseUrl": "https://api.example.com/v1/account",
                            "apiKey": "acct-tok",
                        },
                    },
                }
            )
        )
        await _flush_pending(server)

    asyncio.run(run())

    init = next(m for m in sent if m.get("id") == 1 and "result" in m)
    assert init["result"]["capabilities"]["warmAccountRulesMemory"] is True
    ok = next(m for m in sent if m.get("id") == 2 and "result" in m)
    assert ok["result"]["ok"] is True
    warm.assert_awaited_once()
    call_kw = warm.await_args.kwargs
    assert call_kw["user_id"] == "u1"
    assert call_kw["folder_id"] == "folder-1"


def test_seed_then_lookup_hits() -> None:
    clear_account_rules_memory_cache()
    snap = AccountPrepareSnapshot(
        rules_payload={"global_rules": []},
        memory_bodies={},
        memory_topics=(),
        degraded=False,
    )
    seed_account_rules_memory_cache("u-seed", None, snap)
    hit = get_account_rules_memory_snapshot("u-seed", None)
    assert hit is not None
    assert hit.rules_payload == {"global_rules": []}
    miss = get_account_rules_memory_snapshot("u-seed", "other-folder")
    assert miss is None
