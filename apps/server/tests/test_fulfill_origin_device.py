"""Origin-device pinning end to end: header → turn context → channel → hub.

Two devices online for one account is the whole point of these cases: the
pre-pin rule picked the most recently registered fulfiller, so a shell command
or file write could run on the laptop the user closed rather than the desk they
are sitting at. Reminder-style channels keep the old any-device behavior.
"""

from __future__ import annotations

import pytest

from agentcore.desktop.channel import (
    DesktopClientChannel,
    HostOp,
    HostOpError,
    McpOp,
    McpOpError,
)
from agentcore.fulfill.hub import FULFILL_CHANNELS, FulfillerHub
from agentcore.fulfill.origin import (
    ORIGIN_DEVICE_OFFLINE,
    current_origin_device,
    origin_device,
)
from agentcore.middleware.origin_device import OriginDeviceMiddleware
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
from agentcore.workspace.protocol import WorkspaceIOError

pytestmark = [pytest.mark.anyio, pytest.mark.real_fulfill_dispatch]

USER = "u-origin"
CONV = "conv-origin"


def _two_devices(monkeypatch, *, origin_online: bool) -> FulfillerHub:
    """Hub with peer B always online and origin A present only when asked."""
    hub = FulfillerHub()
    if origin_online:
        hub.register(USER, "desk-A", caps=FULFILL_CHANNELS, roots=[])
    hub.register(USER, "desk-B", caps=FULFILL_CHANNELS, roots=[])
    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.default_fulfiller_hub", lambda: hub
    )
    monkeypatch.setattr("agentcore.fulfill.hub.default_fulfiller_hub", lambda: hub)
    return hub


def _workspace_channel() -> WorkspaceChannel:
    # root_id="" — the rootless local channel (terminal / bootstrap mounts),
    # which is exactly where the old code fell back to "any capable device".
    return WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        root_id="",
    )


def _desktop_channel() -> DesktopClientChannel:
    return DesktopClientChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
    )


# --- middleware ---------------------------------------------------------------


async def test_middleware_binds_the_header_for_the_request_task():
    seen: list[str | None] = []

    async def app(scope, receive, send):
        seen.append(current_origin_device())

    middleware = OriginDeviceMiddleware(app)
    scope = {"type": "http", "headers": [(b"x-client-device", b"desk-A")]}
    await middleware(scope, None, None)
    assert seen == ["desk-A"]
    # Binding is request-scoped: nothing leaks into the next request.
    assert current_origin_device() is None


async def test_middleware_ignores_absent_blank_and_oversized_ids():
    seen: list[str | None] = []

    async def app(scope, receive, send):
        seen.append(current_origin_device())

    middleware = OriginDeviceMiddleware(app)
    for headers in (
        [],
        [(b"x-client-device", b"   ")],
        [(b"x-client-device", b"x" * 200)],
    ):
        await middleware({"type": "http", "headers": headers}, None, None)
    assert seen == [None, None, None]


# --- pinned channels: the op must land on the origin or fail ------------------


async def test_workspace_op_lands_on_the_origin_device(monkeypatch):
    hub = _two_devices(monkeypatch, origin_online=True)
    origin = hub.get_session(USER, "desk-A")
    peer = hub.get_session(USER, "desk-B")
    assert origin is not None and peer is not None
    channel = _workspace_channel()

    with origin_device("desk-A"):
        task = _spawn(channel.request(WorkspaceOp.READ, {"path": "a.txt"}))
        frame = await _next_frame(origin)
    assert frame["type"] == "workspace_op_required"
    assert peer._queue.qsize() == 0
    task.cancel()


async def test_workspace_op_refuses_to_move_to_another_device(monkeypatch):
    hub = _two_devices(monkeypatch, origin_online=False)
    peer = hub.get_session(USER, "desk-B")
    assert peer is not None
    channel = _workspace_channel()

    with origin_device("desk-A"), pytest.raises(WorkspaceIOError) as ei:
        await channel.request(WorkspaceOp.WRITE, {"path": "a.txt", "content": "x"})
    assert ORIGIN_DEVICE_OFFLINE in str(ei.value)
    assert peer._queue.qsize() == 0


async def test_host_op_refuses_to_move_to_another_device(monkeypatch):
    hub = _two_devices(monkeypatch, origin_online=False)
    peer = hub.get_session(USER, "desk-B")
    assert peer is not None

    with origin_device("desk-A"), pytest.raises(HostOpError) as ei:
        await _desktop_channel().request_host(HostOp.SHELL, {"command": "ls"})
    assert ORIGIN_DEVICE_OFFLINE in str(ei.value)
    assert peer._queue.qsize() == 0


async def test_mcp_op_refuses_to_move_to_another_device(monkeypatch):
    hub = _two_devices(monkeypatch, origin_online=False)
    peer = hub.get_session(USER, "desk-B")
    assert peer is not None

    with origin_device("desk-A"), pytest.raises(McpOpError) as ei:
        await _desktop_channel().request_mcp(McpOp.LIST_TOOLS)
    assert ORIGIN_DEVICE_OFFLINE in str(ei.value)
    assert peer._queue.qsize() == 0


async def test_external_mount_refuses_to_move_to_another_device(monkeypatch):
    from agentcore.desktop.channel import ExternalMountError

    hub = _two_devices(monkeypatch, origin_online=False)
    peer = hub.get_session(USER, "desk-B")
    assert peer is not None

    with origin_device("desk-A"), pytest.raises(ExternalMountError) as ei:
        await _desktop_channel().request_external_mount(path="/tmp/x")
    assert ORIGIN_DEVICE_OFFLINE in str(ei.value)
    assert peer._queue.qsize() == 0


# --- single device: nothing changes ------------------------------------------


async def test_single_device_turn_is_unchanged(monkeypatch):
    """The only install is also the origin — same delivery as before pinning."""
    hub = FulfillerHub()
    only = hub.register(USER, "desk-A", caps=FULFILL_CHANNELS, roots=[])
    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.default_fulfiller_hub", lambda: hub
    )
    channel = _desktop_channel()

    with origin_device("desk-A"):
        task = _spawn(channel.request_host(HostOp.INFO))
        frame = await _next_frame(only)
    assert frame["type"] == "host_op_required"
    task.cancel()


async def test_turn_without_an_origin_keeps_most_recent_device(monkeypatch):
    """Mobile / web / scheduled turns declare no device and must still work."""
    hub = _two_devices(monkeypatch, origin_online=True)
    newest = hub.get_session(USER, "desk-B")
    assert newest is not None
    channel = _desktop_channel()

    assert current_origin_device() is None
    task = _spawn(channel.request_host(HostOp.INFO))
    frame = await _next_frame(newest)
    assert frame["type"] == "host_op_required"
    task.cancel()


# --- helpers ------------------------------------------------------------------


def _spawn(coro):
    import asyncio

    return asyncio.ensure_future(coro)


async def _next_frame(session, *, timeout: float = 1.0) -> dict:
    import asyncio

    frame = await asyncio.wait_for(session.get(), timeout=timeout)
    assert frame is not None
    return frame
