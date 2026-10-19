"""Streamable HTTP MCP client: JSON-RPC, SSE, 401, SSRF."""

from __future__ import annotations

import json

import httpx
import pytest

from agentcore.mcp.http import McpHttpError, StreamableHttpMcpClient, origin_of


def _swap_transport(client: StreamableHttpMcpClient, handler) -> None:
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def allow_url(monkeypatch):
    async def _ok(_url: str):
        return None

    monkeypatch.setattr("agentcore.mcp.http.classify_url", _ok)


@pytest.mark.asyncio
async def test_initialize_and_list_tools_json(allow_url):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "serverInfo": {"name": "demo"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "echo", "description": "Echo"}]},
                },
            )
        return httpx.Response(500)

    client = StreamableHttpMcpClient("https://mcp.example.com/mcp")
    await client.aclose()
    _swap_transport(client, handler)
    try:
        info = await client.initialize()
        assert info["serverInfo"]["name"] == "demo"
        tools = await client.list_tools()
        assert tools[0]["name"] == "echo"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_sse_initialize_result(allow_url):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            frame = (
                'event: message\n'
                'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-11-25"}}\n\n'
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=frame,
            )
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(500)

    client = StreamableHttpMcpClient("https://mcp.example.com/mcp")
    await client.aclose()
    _swap_transport(client, handler)
    try:
        info = await client.initialize()
        assert info["protocolVersion"] == "2025-11-25"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_http_401_is_needs_auth(allow_url):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = StreamableHttpMcpClient("https://mcp.example.com/mcp")
    await client.aclose()
    _swap_transport(client, handler)
    try:
        with pytest.raises(McpHttpError, match="needs_auth"):
            await client.initialize()
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_loopback_url_blocked():
    client = StreamableHttpMcpClient("http://127.0.0.1:9/mcp")
    try:
        with pytest.raises(McpHttpError):
            await client.initialize()
    finally:
        await client.aclose()


def test_origin_of():
    assert origin_of("https://mcp.linear.app/mcp") == "https://mcp.linear.app"
    assert origin_of("not-a-url") == ""
