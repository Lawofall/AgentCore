"""Sidecar warmMcpDiscover RPC seeds MCP discover cache (non-turn)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentcore.desktop.channel import DesktopClientChannel
from agentcore.sidecar.identity import LOCAL_USER_ID
from agentcore.sidecar.protocol import NOT_INITIALIZED
from agentcore.sidecar.server import SidecarServer
from agentcore.tools.mcp.wire import clear_mcp_discover_cache, discover_mcp_tools


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


def test_warm_mcp_discover_requires_initialize(tmp_path: Path) -> None:
    clear_mcp_discover_cache()
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmMcpDiscover",
                    "params": {"servers": []},
                }
            )
        )

    asyncio.run(run())
    err = next(m for m in sent if m.get("id") == 2 and "error" in m)
    assert err["error"]["code"] == NOT_INITIALIZED


def test_warm_mcp_discover_seeds_scope_cache(tmp_path: Path) -> None:
    clear_mcp_discover_cache()
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    servers = [
        {
            "id": "echo",
            "name": "Echo",
            "status": "ready",
            "tools": [{"name": "ping", "description": "Ping"}],
        }
    ]

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "local",
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
                    "method": "warmMcpDiscover",
                    "params": {"servers": servers},
                }
            )
        )

    asyncio.run(run())

    ok = next(m for m in sent if m.get("id") == 2 and "result" in m)
    assert ok["result"]["ok"] is True
    assert ok["result"]["ttlSeconds"] == pytest.approx(300.0, abs=1.0)
    init = next(m for m in sent if m.get("id") == 1 and "result" in m)
    assert init["result"]["capabilities"]["warmMcpDiscover"] is True

    async def hit_cache() -> None:
        channel = DesktopClientChannel(
        user_id="u-test",
            conversation_id="conv-after-warm",
            registry=AsyncMock(),
            timeout_seconds=1,
        )

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("cache_only miss must not request_mcp")

        channel.request_mcp = boom  # type: ignore[method-assign]
        result = await discover_mcp_tools(
            channel, cache_scope=LOCAL_USER_ID, cache_only=True
        )
        assert result.tool_count == 1
        assert result.specs[0].mcp_tool_name == "ping"

    asyncio.run(hit_cache())


def test_initialize_advertises_warm_mcp_without_seeding(tmp_path: Path) -> None:
    """initialize only marks capability; list+seed is desktop warmMcpDiscover."""
    clear_mcp_discover_cache()
    (tmp_path / "y.py").write_text("y = 2\n", encoding="utf-8")
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
                        "userId": "u",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                    },
                }
            )
        )

    asyncio.run(run())
    init = next(m for m in sent if m.get("id") == 1 and "result" in m)
    assert init["result"]["capabilities"]["warmMcpDiscover"] is True

    async def miss() -> None:
        channel = DesktopClientChannel(
        user_id="u-test",
            conversation_id="c-miss",
            registry=AsyncMock(),
            timeout_seconds=1,
        )
        result = await discover_mcp_tools(channel, cache_scope="u", cache_only=True)
        assert result.detail == "cache_miss"
        assert result.tool_count == 0

    asyncio.run(miss())


_ACCOUNT_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def test_warm_mcp_login_uuid_warm_then_prepare_hits(tmp_path: Path) -> None:
    """Logged-in UUID initialize + warm → prepare cache_only hits same scope."""
    clear_mcp_discover_cache()
    (tmp_path / "z.py").write_text("z = 3\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    servers = [
        {
            "id": "echo",
            "name": "Echo",
            "status": "ready",
            "tools": [{"name": "ping", "description": "Ping"}],
        }
    ]

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": _ACCOUNT_UUID,
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
                    "method": "warmMcpDiscover",
                    "params": {"servers": servers, "userId": _ACCOUNT_UUID},
                }
            )
        )

    asyncio.run(run())
    ok = next(m for m in sent if m.get("id") == 2 and "result" in m)
    assert ok["result"]["ok"] is True
    assert ok["result"]["ttlSeconds"] == pytest.approx(300.0, abs=1.0)

    async def hit() -> None:
        channel = DesktopClientChannel(
        user_id="u-test",
            conversation_id="conv-uuid-prepare",
            registry=AsyncMock(),
            timeout_seconds=1,
        )

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("cache_only miss must not request_mcp")

        channel.request_mcp = boom  # type: ignore[method-assign]
        result = await discover_mcp_tools(
            channel, cache_scope=_ACCOUNT_UUID, cache_only=True
        )
        assert result.tool_count == 1
        assert result.specs[0].mcp_tool_name == "ping"

    asyncio.run(hit())


def test_warm_mcp_refresh_user_id_no_local_dual_write(tmp_path: Path) -> None:
    """initialize local + warm with account userId seeds account scope only (no LOCAL dual-write)."""
    clear_mcp_discover_cache()
    (tmp_path / "w.py").write_text("w = 4\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)
    servers = [
        {
            "id": "echo",
            "name": "Echo",
            "status": "ready",
            "tools": [{"name": "ping", "description": "Ping"}],
        }
    ]

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "local",
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
                    "method": "warmMcpDiscover",
                    "params": {"servers": servers, "userId": _ACCOUNT_UUID},
                }
            )
        )

    asyncio.run(run())
    ok = next(m for m in sent if m.get("id") == 2 and "result" in m)
    assert ok["result"]["ok"] is True
    assert ok["result"]["ttlSeconds"] == pytest.approx(300.0, abs=1.0)

    async def assert_scopes() -> None:
        channel = DesktopClientChannel(
        user_id="u-test",
            conversation_id="conv-cross-key",
            registry=AsyncMock(),
            timeout_seconds=1,
        )

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("cache_only miss must not request_mcp")

        channel.request_mcp = boom  # type: ignore[method-assign]
        hit = await discover_mcp_tools(
            channel, cache_scope=_ACCOUNT_UUID, cache_only=True
        )
        assert hit.tool_count == 1

        # No dual-write: LOCAL scope must miss (prepare with account id would otherwise
        # still miss if we had only seeded LOCAL — this asserts the inverse: account
        # hit + local miss).
        miss = await discover_mcp_tools(
            channel, cache_scope=LOCAL_USER_ID, cache_only=True
        )
        assert miss.detail == "cache_miss"
        assert miss.tool_count == 0

    asyncio.run(assert_scopes())
