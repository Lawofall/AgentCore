"""Unit tests for local MCP Client channel + dynamic worker tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agentcore.core.types import ToolApproval
from agentcore.desktop.channel import DesktopClientChannel, McpOp, McpOpError
from agentcore.runtime.events.client_tool_reattach import (
    CHANNEL_MCP,
    build_client_tool_required,
    client_tool_payload,
)
from agentcore.runtime.events.types import EventType
from agentcore.runtime.interaction import InteractionKind, InteractionRequest
from agentcore.tools.builtin import build_ceo_tool_registry, build_worker_registry
from agentcore.tools.mcp.dynamic import McpDynamicTool, sanitize_mcp_tool_name
from agentcore.tools.mcp.wire import (
    McpDiscoverResult,
    McpToolSpec,
    clear_mcp_discover_cache,
    discover_mcp_tools,
    mcp_capability_label,
    parse_mcp_list_payload,
    register_mcp_tools,
    seed_mcp_discover_cache,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _isolate_mcp_discover_cache():
    clear_mcp_discover_cache()
    yield
    clear_mcp_discover_cache()


def test_sanitize_mcp_tool_name_stable_and_bounded():
    name = sanitize_mcp_tool_name("my-server!", "list/files")
    assert name.startswith("mcp_")
    assert len(name) <= 64
    assert "/" not in name
    assert "!" not in name


def test_mcp_capability_label_matrix():
    assert mcp_capability_label(None, desktop_online=False) == "未装配"
    assert mcp_capability_label(None, desktop_online=True) == "未装配"
    ready = McpDiscoverResult(tool_count=2, ready_servers=1)
    assert mcp_capability_label(ready, desktop_online=True) == "已装配"
    degraded = McpDiscoverResult(degraded=True, failed_servers=1)
    assert mcp_capability_label(degraded, desktop_online=True) == "降级（无可用工具）"


def test_register_mcp_tools_worker_only_grantable():
    registry = ToolRegistry()
    result = McpDiscoverResult(
        ready_servers=1,
        tool_count=1,
        specs=(
            McpToolSpec(
                server_id="echo",
                server_name="Echo",
                mcp_tool_name="ping",
                description="Ping",
                input_schema={"type": "object", "properties": {}},
            ),
        ),
    )
    assert register_mcp_tools(registry, result) == 1
    tool = registry.get("mcp_echo_ping")
    assert tool.schema.approval is ToolApproval.GRANTABLE
    assert "MCP" in tool.schema.description
    assert "mcp_echo_ping" in registry.names
    assert "mcp_echo_ping" in registry.deferred_names
    offered = {
        str((d.get("function") or {}).get("name") or d.get("name") or "")
        for d in registry.get_openai_definitions()
    }
    assert "mcp_echo_ping" not in offered


def test_desktop_touch_tool_names_cover_mcp_and_host():
    from agentcore.runtime.sandbox_approval import is_desktop_touch_tool

    assert is_desktop_touch_tool("mcp_echo_ping")
    assert is_desktop_touch_tool("host")
    assert not is_desktop_touch_tool("file_write")
    assert not is_desktop_touch_tool("web_search")


def test_resolve_worker_gate_hands_down_gate_for_mcp_on_cloud():
    """MCP 在云端 worker 上必须有卡可弹；空名册也照传门（该免的卡由收口点免）。"""
    from types import SimpleNamespace

    from agentcore.runtime.delegate.drive_setup import resolve_worker_gate
    from agentcore.tools.mcp.dynamic import McpDynamicTool

    registry = ToolRegistry()
    registry.register(
        McpDynamicTool(
            fc_name="mcp_echo_ping",
            server_id="echo",
            server_name="Echo",
            mcp_tool_name="ping",
            description="Ping",
            input_schema=None,
        )
    )
    gate = object()
    backend = SimpleNamespace(location="server")
    tool = SimpleNamespace(
        _approval_gate=gate,
        _tools=registry,
        _base_tool_context=SimpleNamespace(backend=backend),
    )
    assert resolve_worker_gate(tool) is gate

    empty = ToolRegistry()
    tool_no_mcp = SimpleNamespace(
        _approval_gate=gate,
        _tools=empty,
        _base_tool_context=SimpleNamespace(backend=backend),
    )
    assert resolve_worker_gate(tool_no_mcp) is gate


def test_ceo_registry_has_no_mcp_tools_by_default():
    ceo = {s.name for s in build_ceo_tool_registry(desktop_online=True).list_all()}
    worker = build_worker_registry(desktop_online=True)
    register_mcp_tools(
        worker,
        McpDiscoverResult(
            tool_count=1,
            specs=(
                McpToolSpec(
                    server_id="s",
                    server_name="S",
                    mcp_tool_name="t",
                    description="d",
                    input_schema=None,
                ),
            ),
        ),
    )
    worker_names = {s.name for s in worker.list_all()}
    assert "mcp_s_t" in worker_names
    assert "mcp_s_t" in worker.deferred_names
    offered = {
        str((d.get("function") or {}).get("name") or d.get("name") or "")
        for d in worker.get_openai_definitions()
    }
    assert "mcp_s_t" not in offered
    assert not any(n.startswith("mcp_") for n in ceo)


@pytest.mark.asyncio
async def test_discover_mcp_tools_degrades_without_channel():
    result = await discover_mcp_tools(None)
    assert result.tool_count == 0
    assert result.detail == "no_desktop_channel"


@pytest.mark.asyncio
async def test_discover_mcp_tools_parses_ready_and_failed():
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c1",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "servers": [
                {
                    "id": "ok",
                    "name": "OK",
                    "status": "ready",
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                            },
                        }
                    ],
                },
                {
                    "id": "bad",
                    "name": "Bad",
                    "status": "failed",
                    "error": "spawn failed",
                    "tools": [],
                },
            ]
        }
    )
    result = await discover_mcp_tools(channel)
    assert result.ready_servers == 1
    assert result.failed_servers == 1
    assert result.tool_count == 1
    assert result.specs[0].mcp_tool_name == "echo"
    channel.request_mcp.assert_awaited_once()
    assert channel.request_mcp.await_args.kwargs.get("timeout") == 1.0


@pytest.mark.asyncio
async def test_discover_mcp_tools_cache_hit_skips_request():
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c-cache",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "servers": [
                {
                    "id": "ok",
                    "name": "OK",
                    "status": "ready",
                    "tools": [{"name": "echo", "description": "Echo"}],
                }
            ]
        }
    )
    first = await discover_mcp_tools(channel)
    second = await discover_mcp_tools(channel)
    assert first.tool_count == 1
    assert second.tool_count == 1
    assert second.specs == first.specs
    channel.request_mcp.assert_awaited_once()
    assert channel.request_mcp.await_args.kwargs.get("timeout") == 1.0


@pytest.mark.asyncio
async def test_discover_mcp_tools_cache_only_miss_skips_channel(monkeypatch):
    """prepare-path: cache miss must not await ClientTool / request_mcp."""
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        events.append((event, kwargs))

    monkeypatch.setattr("agentcore.tools.mcp.wire.logger.info", _capture)
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c-cache-only-miss",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        return_value={"servers": []}
    )
    result = await discover_mcp_tools(channel, cache_scope="user-1", cache_only=True)
    assert result.tool_count == 0
    assert result.detail == "cache_miss"
    assert not result.degraded
    channel.request_mcp.assert_not_awaited()
    miss = [e for e in events if e[0] == "desktop.mcp_list_cache_miss"]
    assert len(miss) == 1
    assert miss[0][1]["detail"] == "cache_miss"
    assert miss[0][1]["conversation_id"] == "c-cache-only-miss"
    assert miss[0][1]["cache_scope"] == "user-1"
    assert mcp_capability_label(result, desktop_online=True) == "未装配"


@pytest.mark.asyncio
async def test_mcp_cache_miss_logs_harvest_origin(monkeypatch):
    """Harvest cache_only miss is grep-able via origin=execution_harvest."""
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        events.append((event, kwargs))

    monkeypatch.setattr("agentcore.tools.mcp.wire.logger.info", _capture)
    from agentcore.runtime.delegate.post_close_gate import (
        bind_user_message_origin,
        reset_user_message_origin,
    )

    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c-harvest-miss",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    token = bind_user_message_origin("execution_harvest")
    try:
        result = await discover_mcp_tools(
            channel, cache_scope="user-1", cache_only=True
        )
    finally:
        reset_user_message_origin(token)
    assert result.detail == "cache_miss"
    miss = [e for e in events if e[0] == "desktop.mcp_list_cache_miss"]
    assert len(miss) == 1
    assert miss[0][1]["origin"] == "execution_harvest"


@pytest.mark.asyncio
async def test_mcp_keepalive_rewarm_keeps_cache_only_hit_past_ttl(monkeypatch):
    """Re-seed before TTL so a later harvest-style cache_only discover still hits."""
    import agentcore.tools.mcp.wire as mcp_wire

    class _Clock:
        def __init__(self) -> None:
            self._now = 10_000.0

        def monotonic(self) -> float:
            return self._now

        def advance(self, seconds: float) -> None:
            self._now += seconds

    clock = _Clock()
    monkeypatch.setattr(mcp_wire, "time", clock)
    payload = {
        "servers": [
            {
                "id": "ok",
                "name": "OK",
                "status": "ready",
                "tools": [{"name": "echo", "description": "Echo"}],
            }
        ]
    }
    seeded = parse_mcp_list_payload(payload)
    seed_mcp_discover_cache("", seeded, cache_scope="user-keep")
    assert mcp_wire.mcp_discover_ttl_remaining(cache_scope="user-keep") == pytest.approx(
        300.0
    )

    clock.advance(280.0)
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c-harvest-keep",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("cache_only must not call request_mcp")
    )

    result = await discover_mcp_tools(
        channel, cache_scope="user-keep", cache_only=True
    )
    assert result.tool_count == 1
    seed_mcp_discover_cache("", seeded, cache_scope="user-keep")
    clock.advance(280.0)
    result = await discover_mcp_tools(
        channel, cache_scope="user-keep", cache_only=True
    )
    assert result.tool_count == 1
    assert mcp_wire.mcp_discover_ttl_remaining(cache_scope="user-keep") == pytest.approx(
        20.0
    )


@pytest.mark.asyncio
async def test_seed_then_cache_only_prepare_path_hits():
    """Non-turn seed → prepare-style cache_only discover hits without network."""
    payload = {
        "servers": [
            {
                "id": "ok",
                "name": "OK",
                "status": "ready",
                "tools": [{"name": "echo", "description": "Echo"}],
            }
        ]
    }
    seeded = parse_mcp_list_payload(payload)
    assert seeded.tool_count == 1
    seed_mcp_discover_cache("conv-seed", seeded, cache_scope="user-seed")

    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="conv-seed",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("cache_only must not call request_mcp")
    )
    result = await discover_mcp_tools(
        channel, cache_scope="user-seed", cache_only=True
    )
    assert result.tool_count == 1
    assert result.specs == seeded.specs
    channel.request_mcp.assert_not_awaited()

    # Same user, new conversation → scope hit still works under cache_only.
    other = DesktopClientChannel(
        user_id="u-test",
        conversation_id="conv-other",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    other.request_mcp = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("cache_only must not call request_mcp")
    )
    scoped = await discover_mcp_tools(
        other, cache_scope="user-seed", cache_only=True
    )
    assert scoped.tool_count == 1
    other.request_mcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_discover_mcp_tools_cache_scope_hits_across_conversations():
    """Same user (cache_scope) + new conversation_id → shared cache hit."""
    payload = {
        "servers": [
            {
                "id": "ok",
                "name": "OK",
                "status": "ready",
                "tools": [{"name": "echo", "description": "Echo"}],
            }
        ]
    }
    c1 = DesktopClientChannel(
        user_id="u-test",
        conversation_id="conv-a",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    c1.request_mcp = AsyncMock(return_value=payload)  # type: ignore[method-assign]
    c2 = DesktopClientChannel(
        user_id="u-test",
        conversation_id="conv-b",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    c2.request_mcp = AsyncMock(return_value=payload)  # type: ignore[method-assign]

    first = await discover_mcp_tools(c1, cache_scope="user-1")
    second = await discover_mcp_tools(c2, cache_scope="user-1")
    assert first.tool_count == 1
    assert second.tool_count == 1
    assert second.specs == first.specs
    c1.request_mcp.assert_awaited_once()
    c2.request_mcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_discover_mcp_tools_cache_scope_isolated_per_user():
    """Different cache_scope must not share — no cross-tenant hit."""
    payload = {
        "servers": [
            {
                "id": "ok",
                "name": "OK",
                "status": "ready",
                "tools": [{"name": "echo", "description": "Echo"}],
            }
        ]
    }
    c1 = DesktopClientChannel(
        user_id="u-test",
        conversation_id="conv-a",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    c1.request_mcp = AsyncMock(return_value=payload)  # type: ignore[method-assign]
    c2 = DesktopClientChannel(
        user_id="u-test",
        conversation_id="conv-b",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    c2.request_mcp = AsyncMock(return_value=payload)  # type: ignore[method-assign]

    await discover_mcp_tools(c1, cache_scope="user-1")
    await discover_mcp_tools(c2, cache_scope="user-2")
    c1.request_mcp.assert_awaited_once()
    c2.request_mcp.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_mcp_tools_degrades_on_timeout():
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c1",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        side_effect=McpOpError("timeout")
    )
    result = await discover_mcp_tools(channel)
    assert result.degraded
    assert result.tool_count == 0
    channel.request_mcp.assert_awaited_once()
    assert channel.request_mcp.await_args.kwargs.get("timeout") == 1.0


@pytest.mark.asyncio
async def test_discover_mcp_tools_negative_cache_skips_request():
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c-neg",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        side_effect=McpOpError("timeout")
    )
    first = await discover_mcp_tools(channel)
    second = await discover_mcp_tools(channel)
    assert first.degraded
    assert second.degraded
    assert second.detail == first.detail
    channel.request_mcp.assert_awaited_once()
    assert channel.request_mcp.await_args.kwargs.get("timeout") == 1.0


@pytest.mark.asyncio
async def test_discover_mcp_tools_ok_logs_duration_and_tool_count(monkeypatch):
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        events.append((event, kwargs))

    monkeypatch.setattr(
        "agentcore.tools.mcp.wire.logger.info",
        _capture,
    )
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c-ok-log",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "servers": [
                {
                    "id": "ok",
                    "name": "OK",
                    "status": "ready",
                    "tools": [{"name": "echo", "description": "Echo"}],
                }
            ]
        }
    )
    result = await discover_mcp_tools(channel)
    assert result.tool_count == 1
    ok_events = [e for e in events if e[0] == "desktop.mcp_list_ok"]
    assert len(ok_events) == 1
    assert ok_events[0][1]["tool_count"] == 1
    assert isinstance(ok_events[0][1]["duration_ms"], int)
    assert ok_events[0][1]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_discover_mcp_tools_degraded_logs_duration(monkeypatch):
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        events.append((event, kwargs))

    monkeypatch.setattr(
        "agentcore.tools.mcp.wire.logger.info",
        _capture,
    )
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c-deg-log",
        registry=AsyncMock(),
        timeout_seconds=5,
    )
    channel.request_mcp = AsyncMock(  # type: ignore[method-assign]
        side_effect=McpOpError("timeout")
    )
    result = await discover_mcp_tools(channel)
    assert result.degraded
    deg = [e for e in events if e[0] == "desktop.mcp_list_degraded"]
    assert len(deg) == 1
    assert isinstance(deg[0][1]["duration_ms"], int)
    assert deg[0][1]["duration_ms"] >= 0
    assert deg[0][1]["tool_count"] == 0


@pytest.mark.asyncio
async def test_request_mcp_emits_mcp_op_required():
    from tests.client_tool_fulfill_testutil import DELIVERED_EVENTS

    DELIVERED_EVENTS.clear()

    async def _suspend(*_a, **kwargs):
        on_suspended = kwargs.get("on_suspended")
        if callable(on_suspended):
            on_suspended()
        return {"ok": True, "value": {"servers": []}}

    registry = AsyncMock()
    registry.suspend = AsyncMock(side_effect=_suspend)
    channel = DesktopClientChannel(
        user_id="u-test",
        conversation_id="c1",
        registry=registry,
        timeout_seconds=5,
    )
    value = await channel.request_mcp(McpOp.LIST_TOOLS, {})
    assert value == {"servers": []}
    assert len(DELIVERED_EVENTS) == 1
    assert DELIVERED_EVENTS[0].type.value == "mcp_op_required"
    assert DELIVERED_EVENTS[0].payload["op"] == "list_tools"


@pytest.mark.asyncio
async def test_mcp_dynamic_tool_call_and_no_channel():
    from unittest.mock import MagicMock

    tool = McpDynamicTool(
        fc_name="mcp_s_echo",
        server_id="s",
        server_name="S",
        mcp_tool_name="echo",
        description="Echo",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=MagicMock(location="server"),
        user_id="u1",
        desktop_channel=None,
    )
    miss = await tool.execute({"text": "hi"}, ctx)
    assert not miss.success
    assert "桌面" in (miss.error or "")

    channel = AsyncMock()
    channel.request_mcp = AsyncMock(return_value={"content": "hi", "isError": False})
    ctx2 = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=MagicMock(location="server"),
        user_id="u1",
        desktop_channel=channel,
    )
    ok = await tool.execute({"text": "hi"}, ctx2)
    assert ok.success
    assert ok.output == "hi"
    channel.request_mcp.assert_awaited_once()


def _dynamic_tool() -> McpDynamicTool:
    return McpDynamicTool(
        fc_name="mcp_s_echo",
        server_id="s",
        server_name="Echo Server",
        mcp_tool_name="echo",
        description="Echo",
        input_schema=None,
    )


def _dynamic_ctx(channel) -> ToolContext:
    from unittest.mock import MagicMock

    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="a",
        backend=MagicMock(location="server"),
        user_id="u1",
        desktop_channel=channel,
    )


@pytest.mark.asyncio
async def test_mcp_channel_deadline_sits_below_engine_ceiling():
    """通道 = 实际值 + slack ≤ 引擎墙钟；两层等值时内层 MCP 超时永不可达（TOOL-A3）。"""
    from agentcore.runtime.engine import resolve_tool_timeout
    from agentcore.tools.mcp import dynamic as dynamic_mod

    tool = _dynamic_tool()
    channel = AsyncMock()
    channel.request_mcp = AsyncMock(return_value={"content": "hi"})
    await tool.execute({}, _dynamic_ctx(channel))

    passed = channel.request_mcp.await_args.kwargs.get("timeout")
    assert passed == dynamic_mod._MCP_CHANNEL_TIMEOUT_SECONDS
    assert passed > dynamic_mod._MCP_OP_TIMEOUT_SECONDS  # desktop budget + 往返 slack
    ceiling = resolve_tool_timeout(tool.schema)
    assert ceiling is not None and ceiling > passed


@pytest.mark.asyncio
async def test_wedged_mcp_server_surfaces_mcp_timeout_not_engine_liveness(monkeypatch):
    """MCP Server 卡死：内层通道先响，模型看到指向 MCP 的原因而非通用「活性挂起」。"""
    import asyncio

    from agentcore.runtime.engine import resolve_tool_timeout
    from agentcore.tools.mcp import dynamic as dynamic_mod

    # Same ladder, compressed clock — the ordering is what is under test, not the values.
    monkeypatch.setattr(dynamic_mod, "_MCP_CHANNEL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(dynamic_mod, "_MCP_ENGINE_TIMEOUT_SECONDS", 0.50)

    class _WedgedChannel:
        """Mirrors ``DesktopClientChannel.request_mcp``'s deadline contract."""

        async def request_mcp(self, _op, _args=None, *, timeout=None):
            try:
                await asyncio.wait_for(asyncio.sleep(30), timeout)
            except TimeoutError as e:
                raise McpOpError("本机 MCP 操作超时（call_tool：客户端未响应）") from e
            raise AssertionError("wedged channel must never resolve")

    tool = _dynamic_tool()  # schema built after the patch → picks up the ladder above
    ceiling = resolve_tool_timeout(tool.schema)
    assert ceiling == 0.50

    # Engine backstop applied exactly as ``tool_exec`` does: it must NOT be the one to fire.
    result = await asyncio.wait_for(
        tool.execute({}, _dynamic_ctx(_WedgedChannel())), ceiling
    )
    assert result.success is False
    assert "MCP" in (result.error or "")
    assert "超时" in (result.error or "")
    assert "echo" in (result.error or "")  # names the failing MCP tool …
    assert "Echo Server" in (result.error or "")  # … and its Server


@pytest.mark.asyncio
async def test_mcp_op_error_names_server_and_tool():
    """外部 MCP 故障必须可与「模型用错工具」区分：错误里点名 Server + 工具。"""
    tool = _dynamic_tool()
    channel = AsyncMock()
    channel.request_mcp = AsyncMock(
        side_effect=McpOpError("MCP Server 未启用或不存在（s）")
    )
    result = await tool.execute({}, _dynamic_ctx(channel))
    assert result.success is False
    assert "MCP Server 未启用或不存在（s）" in result.error
    assert "Echo Server" in result.error
    assert "echo" in result.error


def test_mcp_reattach_rebuilds_required_event():
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        req = InteractionRequest(
            id="rid",
            kind=InteractionKind.CLIENT_TOOL,
            conversation_id="c1",
            future=loop.create_future(),
            payload=client_tool_payload(
                CHANNEL_MCP,
                EventType.MCP_OP_REQUIRED.value,
                params={"op": "call_tool", "args": {"server_id": "s", "tool_name": "t"}},
            ),
        )
        event = build_client_tool_required(req)
        assert event is not None
        assert event.type.value == "mcp_op_required"
        assert event.payload["op"] == "call_tool"
    finally:
        loop.close()
