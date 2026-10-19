"""Discover / call account-level remote MCP tools from the engine (no desktop)."""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.mcp.http import McpHttpError
from agentcore.mcp.servers import call_remote_tool, list_remote_tools
from agentcore.tools.mcp.wire import McpDiscoverResult, McpToolSpec

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 300.0
_remote_cache: dict[str, tuple[McpDiscoverResult, float]] = {}


async def discover_remote_mcp_tools(user_id: str | None) -> McpDiscoverResult:
    uid = (user_id or "").strip()
    if not uid:
        return McpDiscoverResult()
    now = time.monotonic()
    cached = _remote_cache.get(uid)
    if cached is not None and cached[1] > now:
        return cached[0]
    try:
        async with async_session_factory() as session:
            listed = await list_remote_tools(session, uid)
    except Exception as exc:  # noqa: BLE001 — unit tests / no DB must not block chat
        logger.info(
            "mcp.remote_list_degraded",
            detail=str(exc)[:200],
            duration_ms=0,
            tool_count=0,
            failed_servers=0,
        )
        return McpDiscoverResult()

    specs: list[McpToolSpec] = []
    labels: list[str] = []
    for row, tools in listed:
        labels.append(row.name or "MCP")
        for tool in tools:
            mcp_name = str(tool.get("name") or "").strip()
            if not mcp_name:
                continue
            input_schema = tool.get("inputSchema") or tool.get("input_schema")
            specs.append(
                McpToolSpec(
                    server_id=row.id,
                    server_name=row.name or "MCP",
                    mcp_tool_name=mcp_name,
                    description=str(tool.get("description") or ""),
                    input_schema=input_schema if isinstance(input_schema, dict) else None,
                    transport="http",
                )
            )
    result = McpDiscoverResult(
        ready_servers=len(listed),
        failed_servers=0,
        tool_count=len(specs),
        server_labels=tuple(labels),
        specs=tuple(specs),
    )
    _remote_cache[uid] = (result, time.monotonic() + _CACHE_TTL_SECONDS)
    return result


def clear_remote_mcp_cache(user_id: str | None = None) -> None:
    if user_id:
        _remote_cache.pop(user_id, None)
        return
    _remote_cache.clear()


async def execute_remote_mcp_tool(
    user_id: str,
    *,
    server_id: str,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    root_label: str = "workspace",
) -> dict[str, Any]:
    roots = ({"uri": "file:///workspace", "name": root_label or "workspace"},)
    try:
        return await call_remote_tool(
            user_id,
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            roots=roots,
        )
    except McpHttpError as exc:
        raise McpHttpError(
            f"MCP 工具 {tool_name} 调用失败（MCP Server：{server_name}）：{exc}"
        ) from exc
