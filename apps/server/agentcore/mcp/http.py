"""Minimal Streamable HTTP MCP client (tools + roots; no sampling / resources / prompts)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from agentcore.core.logging import get_logger
from agentcore.core.net import (
    PinnedAddressError,
    PinnedIPTransport,
    classify_url,
    describe_net_error,
    outbound_async_client,
    web_timeout,
)

logger = get_logger(__name__)

PROTOCOL_VERSION = "2025-11-25"
_RPC_TIMEOUT = 30.0


class McpHttpError(Exception):
    """Handshake / RPC failure against a remote MCP Server."""


def _sse_data_frames(body: str) -> list[str]:
    frames: list[str] = []
    current: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip("\r")
        if line == "":
            if current:
                frames.append("\n".join(current))
                current = []
            continue
        if line.startswith("data:"):
            current.append(line[5:].lstrip())
    if current:
        frames.append("\n".join(current))
    return frames


def _parse_rpc_payload(response: httpx.Response) -> dict[str, Any]:
    ctype = (response.headers.get("content-type") or "").lower()
    text = response.text or ""
    if "text/event-stream" in ctype:
        for frame in _sse_data_frames(text):
            try:
                parsed = json.loads(frame)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and (
                "result" in parsed or "error" in parsed or parsed.get("method")
            ):
                return parsed
        raise McpHttpError("远程 MCP 未返回 JSON-RPC 结果")
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise McpHttpError("远程 MCP 响应不是 JSON") from exc
    if not isinstance(parsed, dict):
        raise McpHttpError("远程 MCP 响应不是对象")
    return parsed


class StreamableHttpMcpClient:
    """One MCP session against a Streamable HTTP endpoint."""

    def __init__(
        self,
        url: str,
        *,
        authorization: str | None = None,
        roots: tuple[dict[str, str], ...] = (),
    ) -> None:
        self._url = url.strip()
        self._authorization = (authorization or "").strip() or None
        self._roots = roots
        self._session_id: str | None = None
        self._next_id = 1
        self._client = outbound_async_client(
            timeout=web_timeout(_RPC_TIMEOUT),
            transport=PinnedIPTransport(),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def initialize(self) -> dict[str, Any]:
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "agentcore", "version": "1.0.0"},
            },
        )
        await self._rpc("notifications/initialized", None, notification=True)
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = await self._rpc("tools/list", params or None)
            batch = result.get("tools") if isinstance(result, dict) else None
            if isinstance(batch, list):
                tools.extend(item for item in batch if isinstance(item, dict))
            nxt = result.get("nextCursor") if isinstance(result, dict) else None
            if not isinstance(nxt, str) or not nxt.strip():
                break
            cursor = nxt.strip()
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments if isinstance(arguments, dict) else {}},
        )
        return result if isinstance(result, dict) else {}

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        notification: bool = False,
    ) -> dict[str, Any]:
        block = await classify_url(self._url)
        if block is not None:
            raise McpHttpError(block.value)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._next_id
            self._next_id += 1
        if params is not None:
            payload["params"] = params
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._authorization:
            headers["Authorization"] = self._authorization
        try:
            response = await self._client.post(self._url, json=payload, headers=headers)
        except PinnedAddressError as exc:
            raise McpHttpError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise McpHttpError(describe_net_error(exc, url=self._url)) from exc
        session = response.headers.get("mcp-session-id") or response.headers.get(
            "Mcp-Session-Id"
        )
        if session:
            self._session_id = session
        if notification:
            if response.status_code in {200, 202, 204}:
                return {}
            raise McpHttpError(f"远程 MCP 通知失败（HTTP {response.status_code}）")
        if response.status_code == 401:
            raise McpHttpError("needs_auth")
        if response.status_code >= 400:
            raise McpHttpError(f"远程 MCP HTTP {response.status_code}")
        body = _parse_rpc_payload(response)
        if body.get("method") == "roots/list":
            return await self._answer_roots(body)
        error = body.get("error")
        if isinstance(error, dict):
            raise McpHttpError(str(error.get("message") or "远程 MCP 调用失败"))
        result = body.get("result")
        return result if isinstance(result, dict) else {}

    async def _answer_roots(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = request.get("id")
        roots = list(self._roots) or [
            {"uri": "file:///workspace", "name": "workspace"},
        ]
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"roots": roots},
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._authorization:
            headers["Authorization"] = self._authorization
        await self._client.post(self._url, json=payload, headers=headers)
        return {}


def origin_of(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"
