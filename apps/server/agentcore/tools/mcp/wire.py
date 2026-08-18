"""Discover local MCP tools via desktop backfill and register on the worker registry."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.desktop.channel import DesktopClientChannel, McpOp, McpOpError
from agentcore.tools.mcp.dynamic import McpDynamicTool, sanitize_mcp_tool_name
from agentcore.tools.registry import ToolRegistry

logger = get_logger(__name__)

# Explicit network list budget (non-turn warm) — fail-fast vs desktop HANDSHAKE.
# Prepare/resume use cache_only and never await ClientTool; HANDSHAKE_TIMEOUT_MS
# stays 45s on desktop (spawn+handshake); server intentional shorter for warm fetch.
_MCP_LIST_TIMEOUT_SECONDS = 1.0
_MCP_CACHE_TTL_SECONDS = 300.0
_MCP_NEGATIVE_CACHE_TTL_SECONDS = 30.0
_TOOL_NAME_MAX = 64


@dataclass(frozen=True)
class McpToolSpec:
    """One MCP tool ready to register on the worker toolset."""

    server_id: str
    server_name: str
    mcp_tool_name: str
    description: str
    input_schema: dict[str, Any] | None


@dataclass(frozen=True)
class McpDiscoverResult:
    """Outcome of one prepare/resume MCP discovery pass."""

    ready_servers: int = 0
    failed_servers: int = 0
    tool_count: int = 0
    server_labels: tuple[str, ...] = field(default_factory=tuple)
    specs: tuple[McpToolSpec, ...] = field(default_factory=tuple)
    degraded: bool = False
    detail: str = ""


@dataclass(frozen=True)
class _DiscoverCacheEntry:
    result: McpDiscoverResult
    expires_at: float


_discover_cache: dict[str, _DiscoverCacheEntry] = {}


def clear_mcp_discover_cache() -> None:
    """Drop process-local discover cache (tests / forced refresh)."""
    _discover_cache.clear()


def _conv_cache_key(conversation_id: str) -> str:
    return f"conv:{conversation_id}"


def _scope_cache_key(cache_scope: str) -> str | None:
    """User-scoped shared key; empty/blank → None (never a global bucket)."""
    scope = (cache_scope or "").strip()
    if not scope:
        return None
    return f"scope:{scope}"


def _cache_lookup(
    conversation_id: str,
    cache_scope: str | None,
    *,
    now: float,
) -> McpDiscoverResult | None:
    """Hit conversation key first, then optional user ``cache_scope``."""
    for key in (_conv_cache_key(conversation_id),):
        cached = _discover_cache.get(key)
        if cached is not None and cached.expires_at > now:
            return cached.result
    scope_key = _scope_cache_key(cache_scope or "")
    if scope_key is not None:
        cached = _discover_cache.get(scope_key)
        if cached is not None and cached.expires_at > now:
            return cached.result
    return None


def _cache_put(
    conversation_id: str,
    result: McpDiscoverResult,
    *,
    cache_scope: str | None = None,
) -> None:
    ttl = (
        _MCP_NEGATIVE_CACHE_TTL_SECONDS
        if result.degraded
        else _MCP_CACHE_TTL_SECONDS
    )
    entry = _DiscoverCacheEntry(result=result, expires_at=time.monotonic() + ttl)
    _discover_cache[_conv_cache_key(conversation_id)] = entry
    scope_key = _scope_cache_key(cache_scope or "")
    if scope_key is not None:
        _discover_cache[scope_key] = entry


def seed_mcp_discover_cache(
    conversation_id: str,
    result: McpDiscoverResult,
    *,
    cache_scope: str | None = None,
) -> None:
    """Write an already-known list result into the process cache (non-turn warm).

    Desktop / sidecar call this after a local or explicit network ``list_tools`` so
    prepare/resume ``cache_only`` discovers can hit without awaiting ClientTool.

    Keys: non-blank ``conversation_id`` and/or ``cache_scope`` (typically ``user_id``).
    Open-project warm has no conversation yet → pass ``""`` + ``cache_scope``.
    At least one key required (never a global bucket).
    """
    conv = (conversation_id or "").strip()
    scope_key = _scope_cache_key(cache_scope or "")
    if not conv and scope_key is None:
        raise ValueError(
            "conversation_id or cache_scope is required to seed MCP discover cache"
        )
    ttl = (
        _MCP_NEGATIVE_CACHE_TTL_SECONDS
        if result.degraded
        else _MCP_CACHE_TTL_SECONDS
    )
    entry = _DiscoverCacheEntry(result=result, expires_at=time.monotonic() + ttl)
    if conv:
        _discover_cache[_conv_cache_key(conv)] = entry
    if scope_key is not None:
        _discover_cache[scope_key] = entry
    logger.info(
        "desktop.mcp_list_cache_seed",
        conversation_id=conv or None,
        cache_scope=(cache_scope or "").strip() or None,
        degraded=result.degraded,
        tool_count=result.tool_count,
    )


def mcp_discover_ttl_remaining(
    *,
    conversation_id: str = "",
    cache_scope: str | None = None,
) -> float:
    """Seconds this discover cache still serves prepare (0.0 = absent / lapsed).

    Same renewal-handshake half as ``account_rules_memory_ttl_remaining``: the
    desktop re-warms within this window so cache_only harvest turns still hit.
    """
    now = time.monotonic()
    conv = (conversation_id or "").strip()
    if conv:
        cached = _discover_cache.get(_conv_cache_key(conv))
        if cached is not None and cached.expires_at > now:
            return cached.expires_at - now
    scope_key = _scope_cache_key(cache_scope or "")
    if scope_key is not None:
        cached = _discover_cache.get(scope_key)
        if cached is not None and cached.expires_at > now:
            return cached.expires_at - now
    return 0.0


def parse_mcp_list_payload(value: Any) -> McpDiscoverResult:
    """Parse a desktop ``list_tools`` payload into ``McpDiscoverResult`` (no I/O).

    Same shape as the network discover path; invalid payload → degraded result.
    """
    servers = value.get("servers") if isinstance(value, dict) else None
    if not isinstance(servers, list):
        return McpDiscoverResult(degraded=True, detail="invalid_list_payload")

    ready = 0
    failed = 0
    labels: list[str] = []
    specs: list[McpToolSpec] = []

    for entry in servers:
        if not isinstance(entry, dict):
            continue
        server_id = str(entry.get("id") or "").strip()
        server_name = str(entry.get("name") or server_id or "MCP").strip() or "MCP"
        status = str(entry.get("status") or "").strip().lower()
        if status != "ready":
            failed += 1
            err = str(entry.get("error") or "handshake_failed")
            logger.info(
                "desktop.mcp_server_failed",
                server_id=server_id,
                detail=err,
            )
            continue
        ready += 1
        labels.append(server_name)
        tools = entry.get("tools")
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            mcp_name = str(tool.get("name") or "").strip()
            if not mcp_name:
                continue
            input_schema = tool.get("inputSchema") or tool.get("input_schema")
            if not isinstance(input_schema, dict):
                input_schema = None
            specs.append(
                McpToolSpec(
                    server_id=server_id or server_name,
                    server_name=server_name,
                    mcp_tool_name=mcp_name,
                    description=str(tool.get("description") or ""),
                    input_schema=input_schema,
                )
            )

    return McpDiscoverResult(
        ready_servers=ready,
        failed_servers=failed,
        tool_count=len(specs),
        server_labels=tuple(labels),
        specs=tuple(specs),
        degraded=failed > 0 and len(specs) == 0,
        detail="",
    )


def mcp_capability_label(result: McpDiscoverResult | None, *, desktop_online: bool) -> str:
    """Capability-line token for ``mcp=…``."""
    if not desktop_online:
        return "未装配"
    if result is None:
        return "未装配"
    if result.tool_count > 0:
        return "已装配"
    if result.degraded or result.failed_servers > 0:
        return "降级（无可用工具）"
    return "未装配"


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _cache_miss_origin_fields() -> dict[str, str]:
    """Searchable origin on empty MCP injection (harvest binds ``execution_harvest``)."""
    from agentcore.runtime.delegate.post_close_gate import current_user_message_origin

    origin = current_user_message_origin()
    return {"origin": origin} if origin else {}


async def discover_mcp_tools(
    channel: DesktopClientChannel | None,
    *,
    cache_scope: str | None = None,
    cache_only: bool = False,
) -> McpDiscoverResult:
    """List enabled MCP Servers on the desktop (no registry mutation).

    Cache-first per ``channel.conversation_id``, and optionally shared via
    ``cache_scope`` (typically ``user_id``) so a new conversation for the same
    user can reuse a fresh list. Success TTL 300s (incl. clean empty),
    degraded/timeout negative TTL 30s.

    ``cache_only=True`` (prepare / resume): read process cache only; miss → empty
    result with ``detail=cache_miss`` and **no** ClientTool / channel request.
    ``cache_only=False`` (explicit warm): may ``request_mcp(LIST_TOOLS)``; on
    channel absence / timeout / error → empty result (caller continues). Per-server
    handshake failures are skipped (degrade that batch only). Never caches under a
    global key without user/conversation dimension.
    """
    if channel is None:
        return McpDiscoverResult(detail="no_desktop_channel")

    started = time.monotonic()
    cache_key_conv = channel.conversation_id
    now = time.monotonic()
    scope_logged = (cache_scope or "").strip() or None
    cached = _cache_lookup(cache_key_conv, cache_scope, now=now)
    if cached is not None:
        logger.info(
            "desktop.mcp_list_cache_hit",
            conversation_id=cache_key_conv,
            cache_scope=scope_logged,
            degraded=cached.degraded,
            tool_count=cached.tool_count,
            duration_ms=_duration_ms(started),
        )
        return cached

    if cache_only:
        origin_fields = _cache_miss_origin_fields()
        logger.info(
            "desktop.mcp_list_cache_miss",
            conversation_id=cache_key_conv,
            cache_scope=scope_logged,
            detail="cache_miss",
            duration_ms=_duration_ms(started),
            tool_count=0,
            **origin_fields,
        )
        return McpDiscoverResult(detail="cache_miss")

    try:
        value = await channel.request_mcp(
            McpOp.LIST_TOOLS,
            {},
            timeout=_MCP_LIST_TIMEOUT_SECONDS,
        )
    except McpOpError as e:
        duration_ms = _duration_ms(started)
        logger.info(
            "desktop.mcp_list_degraded",
            detail=str(e),
            duration_ms=duration_ms,
            tool_count=0,
        )
        result = McpDiscoverResult(degraded=True, detail=str(e))
        _cache_put(cache_key_conv, result, cache_scope=cache_scope)
        return result
    except Exception as e:  # noqa: BLE001 — never block a chat turn on MCP
        duration_ms = _duration_ms(started)
        logger.info(
            "desktop.mcp_list_degraded",
            detail=str(e),
            duration_ms=duration_ms,
            tool_count=0,
        )
        result = McpDiscoverResult(degraded=True, detail=str(e))
        _cache_put(cache_key_conv, result, cache_scope=cache_scope)
        return result

    result = parse_mcp_list_payload(value)
    duration_ms = _duration_ms(started)
    if result.detail == "invalid_list_payload":
        logger.info(
            "desktop.mcp_list_degraded",
            detail="invalid_list_payload",
            duration_ms=duration_ms,
            tool_count=0,
        )
        _cache_put(cache_key_conv, result, cache_scope=cache_scope)
        return result
    if result.degraded:
        logger.info(
            "desktop.mcp_list_degraded",
            detail="servers_failed_no_tools",
            duration_ms=duration_ms,
            tool_count=0,
            failed_servers=result.failed_servers,
        )
    else:
        logger.info(
            "desktop.mcp_list_ok",
            duration_ms=duration_ms,
            tool_count=result.tool_count,
            ready_servers=result.ready_servers,
            failed_servers=result.failed_servers,
        )
    _cache_put(cache_key_conv, result, cache_scope=cache_scope)
    return result


def register_mcp_tools(registry: ToolRegistry, result: McpDiscoverResult) -> int:
    """Register discovered MCP tools onto ``registry``. Returns count registered."""
    used_names: set[str] = set(registry.names)
    count = 0
    for spec in result.specs:
        fc_name = sanitize_mcp_tool_name(spec.server_id, spec.mcp_tool_name)
        base = fc_name
        n = 2
        while fc_name in used_names:
            suffix = f"_{n}"
            fc_name = (base[: _TOOL_NAME_MAX - len(suffix)] + suffix)[:_TOOL_NAME_MAX]
            n += 1
        used_names.add(fc_name)
        registry.register(
            McpDynamicTool(
                fc_name=fc_name,
                server_id=spec.server_id,
                server_name=spec.server_name,
                mcp_tool_name=spec.mcp_tool_name,
                description=spec.description,
                input_schema=spec.input_schema,
            )
        )
        count += 1
    return count


async def discover_and_register_mcp_tools(
    registry: ToolRegistry,
    channel: DesktopClientChannel | None,
    *,
    cache_scope: str | None = None,
    cache_only: bool = False,
) -> McpDiscoverResult:
    """Discover then register (convenience for resume / tests)."""
    result = await discover_mcp_tools(
        channel, cache_scope=cache_scope, cache_only=cache_only
    )
    register_mcp_tools(registry, result)
    return result
