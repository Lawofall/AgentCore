"""CLIENT_TOOL + hot-path ``*_required`` re-hang on SSE attach (refresh while open)."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from agentcore.api import sse
from agentcore.fulfill.origin import origin_device
from agentcore.runtime.events import EventSink, content_delta
from agentcore.runtime.events.client_tool_reattach import (
    CHANNEL_BOARD,
    CHANNEL_BOARD_READ,
    CHANNEL_EXTERNAL_MOUNT,
    CHANNEL_HOST,
    CHANNEL_WORKSPACE,
    build_client_tool_required,
    client_tool_payload,
)
from agentcore.runtime.events.hot_interaction_reattach import (
    build_hot_interaction_required,
    registry_hot_pending,
)
from agentcore.runtime.events.types import EventType
from agentcore.runtime.interaction import (
    InteractionKind,
    InteractionRegistry,
    default_interaction_registry,
)

pytestmark = pytest.mark.anyio

CONV = "conv-reattach"


def _parse_sse_types(frames: list[str]) -> list[str]:
    types: list[str] = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("event: "):
                types.append(line.removeprefix("event: ").strip())
    return types


def _parse_sse_payloads(frames: list[str]) -> list[dict]:
    out: list[dict] = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line.removeprefix("data: ")))
    return out


def _clear_pending(registry: InteractionRegistry, conversation_id: str = CONV) -> None:
    for leftover in list(registry.list_pending(conversation_id)):
        registry.discard(leftover.id)


async def test_channel_discrimination_builds_correct_event_types():
    registry = InteractionRegistry()
    cases = [
        (
            "req-ws",
            CHANNEL_WORKSPACE,
            EventType.WORKSPACE_OP_REQUIRED.value,
            {"root_id": "r1", "op": "read", "args": {"path": "a.txt"}},
            EventType.WORKSPACE_OP_REQUIRED,
        ),
        (
            "req-host",
            CHANNEL_HOST,
            EventType.HOST_OP_REQUIRED.value,
            {"op": "host_ping", "args": {}},
            EventType.HOST_OP_REQUIRED,
        ),
        (
            "req-board",
            CHANNEL_BOARD,
            EventType.BOARD_OP_REQUIRED.value,
            {"board_id": "b1", "ops": [{"op": "add_node"}], "summary": "x"},
            EventType.BOARD_OP_REQUIRED,
        ),
        (
            "req-bread",
            CHANNEL_BOARD_READ,
            EventType.BOARD_READ_REQUIRED.value,
            {"board_id": "b1", "ids": ["e1"]},
            EventType.BOARD_READ_REQUIRED,
        ),
    ]
    for rid, channel, et, params, expected_type in cases:
        fut = registry.create(
            rid,
            CONV,
            kind=InteractionKind.CLIENT_TOOL,
            payload=client_tool_payload(channel, et, params=params),
        )
        req = registry.get(rid)
        assert req is not None
        event = build_client_tool_required(req)
        assert event is not None
        assert event.type == expected_type
        assert event.payload["request_id"] == rid
        assert event.payload["conversation_id"] == CONV
        assert not fut.done()
        registry.discard(rid)


async def test_external_mount_reattach_preserves_root_id():
    registry = InteractionRegistry()
    fut = registry.create(
        "req-ext",
        CONV,
        kind=InteractionKind.CLIENT_TOOL,
        payload=client_tool_payload(
            CHANNEL_EXTERNAL_MOUNT,
            EventType.EXTERNAL_MOUNT_REQUIRED.value,
            params={"mode": "organize", "root_id": "root-1"},
        ),
    )
    req = registry.get("req-ext")
    assert req is not None
    event = build_client_tool_required(req)
    assert event is not None
    assert event.type == EventType.EXTERNAL_MOUNT_REQUIRED
    assert event.payload["root_id"] == "root-1"
    assert event.payload["mode"] == "organize"
    assert not fut.done()
    registry.discard("req-ext")


async def test_attach_does_not_resend_client_tool(monkeypatch):
    """Display-stream attach no longer re-hangs CLIENT_TOOL (moved to fulfill)."""
    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.01)
    registry = default_interaction_registry()
    _clear_pending(registry)

    sink = EventSink(conversation_id=CONV)
    sink.emit(content_delta("Hi"))

    fut = registry.create(
        "req-open",
        CONV,
        kind=InteractionKind.CLIENT_TOOL,
        payload=client_tool_payload(
            CHANNEL_WORKSPACE,
            EventType.WORKSPACE_OP_REQUIRED.value,
            params={"root_id": "root-1", "op": "read", "args": {"path": "x.txt"}},
            user_id="u-reattach",
        ),
    )
    assert not fut.done()

    gen = sse._attach_generator(sink)
    frames: list[str] = []
    try:
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        frames.append(chunk)
        with contextlib.suppress(TimeoutError):
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=0.05))
    finally:
        await gen.aclose()
        registry.discard("req-open")

    types = _parse_sse_types(frames)
    assert EventType.CONTENT_DELTA.value in types
    assert EventType.WORKSPACE_OP_REQUIRED.value not in types


async def test_fulfill_rehang_redelivers_open_client_tool(monkeypatch):
    from agentcore.fulfill.dispatch import DeliverResult
    from agentcore.runtime.events.client_tool_reattach import rehang_pending_client_tools

    registry = default_interaction_registry()
    _clear_pending(registry)
    captured: list = []

    def fake_deliver(
        user_id,
        conversation_id,
        channel,
        root_id,
        event,
        *,
        origin_device_id=None,
        hub=None,
    ):
        captured.append(event)
        return DeliverResult.DELIVERED

    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.deliver_client_tool", fake_deliver
    )

    fut = registry.create(
        "req-open",
        CONV,
        kind=InteractionKind.CLIENT_TOOL,
        payload=client_tool_payload(
            CHANNEL_WORKSPACE,
            EventType.WORKSPACE_OP_REQUIRED.value,
            params={"root_id": "root-1", "op": "read", "args": {"path": "x.txt"}},
            user_id="u-reattach",
        ),
    )
    assert not fut.done()
    assert rehang_pending_client_tools("u-reattach") == 1
    assert len(captured) == 1
    assert captured[0].type == EventType.WORKSPACE_OP_REQUIRED
    assert captured[0].payload["request_id"] == "req-open"
    assert captured[0].payload["root_id"] == "root-1"
    registry.discard("req-open")


async def test_payload_snapshots_origin_device_without_leaking_it_on_the_wire():
    registry = InteractionRegistry()
    with origin_device("desk-A"):
        payload = client_tool_payload(
            CHANNEL_HOST,
            EventType.HOST_OP_REQUIRED.value,
            params={"op": "host_shell", "args": {}},
            user_id="u-origin",
        )
    assert payload["origin_device_id"] == "desk-A"

    registry.create(
        "req-origin", CONV, kind=InteractionKind.CLIENT_TOOL, payload=payload
    )
    req = registry.get("req-origin")
    assert req is not None
    event = build_client_tool_required(req)
    assert event is not None
    # Routing meta stays server-side: the device does not need to be told which
    # device it is.
    assert "origin_device_id" not in event.payload
    registry.discard("req-origin")


async def test_rehang_routes_by_the_payload_origin_not_the_ambient_one(monkeypatch):
    """Reconnect happens outside the turn — the snapshot is the only truth."""
    from agentcore.fulfill.dispatch import DeliverResult
    from agentcore.runtime.events.client_tool_reattach import rehang_pending_client_tools

    registry = default_interaction_registry()
    _clear_pending(registry)
    seen: list[str | None] = []

    def fake_deliver(
        user_id,
        conversation_id,
        channel,
        root_id,
        event,
        *,
        origin_device_id=None,
        hub=None,
    ):
        seen.append(origin_device_id)
        return DeliverResult.DELIVERED

    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.deliver_client_tool", fake_deliver
    )

    with origin_device("desk-A"):
        payload = client_tool_payload(
            CHANNEL_HOST,
            EventType.HOST_OP_REQUIRED.value,
            params={"op": "host_shell", "args": {}},
            user_id="u-origin",
        )
    registry.create(
        "req-pinned", CONV, kind=InteractionKind.CLIENT_TOOL, payload=payload
    )
    try:
        # Device B reconnecting is what triggers the re-hang.
        with origin_device("desk-B"):
            assert rehang_pending_client_tools("u-origin") == 1
        assert seen == ["desk-A"]
    finally:
        registry.discard("req-pinned")


async def test_attach_skips_discarded_client_tool(monkeypatch):
    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.01)
    registry = default_interaction_registry()
    _clear_pending(registry)

    sink = EventSink(conversation_id=CONV)
    sink.emit(content_delta("Hi"))

    registry.create(
        "req-gone",
        CONV,
        kind=InteractionKind.CLIENT_TOOL,
        payload=client_tool_payload(
            CHANNEL_HOST,
            EventType.HOST_OP_REQUIRED.value,
            params={"op": "host_ping", "args": {}},
        ),
    )
    registry.discard("req-gone")
    assert registry.list_pending(CONV) == []

    gen = sse._attach_generator(sink)
    frames: list[str] = []
    try:
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        frames.append(chunk)
        # Next frame would be live tail / ping — give one more pull briefly.
        with contextlib.suppress(TimeoutError):
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=0.05))
    finally:
        await gen.aclose()

    joined = "".join(frames)
    assert "host_op_required" not in joined
    assert "content_delta" in joined


async def test_build_skips_done_future():
    registry = InteractionRegistry()
    fut = registry.create(
        "req-done",
        CONV,
        kind=InteractionKind.CLIENT_TOOL,
        payload=client_tool_payload(
            CHANNEL_HOST,
            EventType.HOST_OP_REQUIRED.value,
            params={"op": "host_ping", "args": {}},
        ),
    )
    fut.set_result({"ok": True, "value": {}})
    req = registry.get("req-done")
    assert req is not None
    assert build_client_tool_required(req) is None
    registry.discard("req-done")


# --- Hot-path approval / escalation re-hang -----------------------------------


async def test_build_hot_approval_fills_wire_ids():
    registry = InteractionRegistry()
    registry.create(
        "call-42",
        CONV,
        kind=InteractionKind.APPROVAL,
        payload={
            "tool_call_id": "call-42",
            "tool_name": "file_write",
            "arguments": {"path": "a.txt"},
        },
    )
    req = registry.get("call-42")
    assert req is not None
    event = build_hot_interaction_required(req)
    assert event is not None
    assert event.type is EventType.APPROVAL_REQUIRED
    assert event.payload == {
        "approval_id": "call-42",
        "conversation_id": CONV,
        "tool_call_id": "call-42",
        "tool_name": "file_write",
        "arguments": {"path": "a.txt"},
    }
    registry.discard("call-42")


async def test_build_hot_skips_done_and_ceo_escalation():
    registry = InteractionRegistry()
    fut = registry.create(
        "appr-done",
        CONV,
        kind=InteractionKind.APPROVAL,
        payload={"tool_call_id": "appr-done", "tool_name": "mkdir", "arguments": {}},
    )
    fut.set_result("approve")
    done_req = registry.get("appr-done")
    assert done_req is not None
    assert build_hot_interaction_required(done_req) is None

    registry.create(
        "esc-ceo",
        CONV,
        kind=InteractionKind.ESCALATION,
        payload={
            "escalation_id": "esc-ceo",
            "run_id": "r1",
            "agent_id": "w1",
            "question": "q",
            "assumption": "a",
            "awaiting": "ceo",
        },
    )
    ceo_req = registry.get("esc-ceo")
    assert ceo_req is not None
    assert build_hot_interaction_required(ceo_req) is None

    registry.create(
        "esc-user",
        CONV,
        kind=InteractionKind.ESCALATION,
        payload={
            "escalation_id": "esc-user",
            "run_id": "r1",
            "agent_id": "w1",
            "question": "pick?",
            "assumption": "default",
            "awaiting": "user",
        },
    )
    user_req = registry.get("esc-user")
    assert user_req is not None
    esc = build_hot_interaction_required(user_req)
    assert esc is not None
    assert esc.type is EventType.ESCALATION_REQUIRED
    assert esc.payload["escalation_id"] == "esc-user"
    assert esc.payload["awaiting"] == "user"

    registry.discard("appr-done")
    registry.discard("esc-ceo")
    registry.discard("esc-user")


async def test_rebuilt_escalation_keeps_the_original_wait_policy():
    """重连重建的升级卡与原帧同一等待口径：运维配了上限才带 ``timeout_seconds``。

    卡面据此二选一（无上限 = 一直等你），所以这里凭空造 / 丢失上限都会让文案说谎。
    """
    registry = InteractionRegistry()
    base = {
        "run_id": "r1",
        "agent_id": "w1",
        "question": "pick?",
        "assumption": "default",
        "awaiting": "user",
    }
    registry.create("esc-wait", CONV, kind=InteractionKind.ESCALATION, payload=dict(base))
    unlimited_req = registry.get("esc-wait")
    assert unlimited_req is not None
    unlimited = build_hot_interaction_required(unlimited_req)
    assert unlimited is not None
    assert "timeout_seconds" not in unlimited.payload

    registry.create(
        "esc-capped",
        CONV,
        kind=InteractionKind.ESCALATION,
        payload={**base, "timeout_seconds": 900.0},
    )
    capped_req = registry.get("esc-capped")
    assert capped_req is not None
    capped = build_hot_interaction_required(capped_req)
    assert capped is not None
    assert capped.payload["timeout_seconds"] == 900.0

    registry.discard("esc-wait")
    registry.discard("esc-capped")


async def test_attach_resends_open_approval(monkeypatch):
    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.01)
    registry = default_interaction_registry()
    _clear_pending(registry)

    sink = EventSink(conversation_id=CONV)
    sink.emit(content_delta("Hi"))

    registry.create(
        "appr-open",
        CONV,
        kind=InteractionKind.APPROVAL,
        payload={
            "tool_call_id": "appr-open",
            "tool_name": "file_write",
            "arguments": {"path": "x.txt"},
        },
    )

    gen = sse._attach_generator(sink)
    frames: list[str] = []
    try:
        for _ in range(8):
            chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            frames.append(chunk)
            if "approval_required" in chunk:
                break
        else:
            pytest.fail("approval_required not re-emitted on attach")
    finally:
        await gen.aclose()
        registry.discard("appr-open")

    types = _parse_sse_types(frames)
    assert EventType.CONTENT_DELTA.value in types
    assert EventType.APPROVAL_REQUIRED.value in types
    assert types.index(EventType.CONTENT_DELTA.value) < types.index(
        EventType.APPROVAL_REQUIRED.value
    )
    payloads = _parse_sse_payloads(frames)
    card = next(p for p in payloads if p["type"] == EventType.APPROVAL_REQUIRED.value)
    assert card["payload"]["approval_id"] == "appr-open"
    assert card["payload"]["conversation_id"] == CONV
    assert card["payload"]["tool_name"] == "file_write"


async def test_attach_skips_discarded_approval(monkeypatch):
    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.01)
    registry = default_interaction_registry()
    _clear_pending(registry)

    sink = EventSink(conversation_id=CONV)
    sink.emit(content_delta("Hi"))

    registry.create(
        "appr-gone",
        CONV,
        kind=InteractionKind.APPROVAL,
        payload={
            "tool_call_id": "appr-gone",
            "tool_name": "code_execute",
            "arguments": {},
        },
    )
    registry.discard("appr-gone")

    gen = sse._attach_generator(sink)
    frames: list[str] = []
    try:
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        frames.append(chunk)
        with contextlib.suppress(TimeoutError):
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=0.05))
    finally:
        await gen.aclose()

    joined = "".join(frames)
    assert "approval_required" not in joined
    assert "content_delta" in joined


async def test_registry_hot_pending_recovery_payload():
    """Registry-only open approval enters recovery summaries with wire fields."""
    registry = default_interaction_registry()
    _clear_pending(registry)
    registry.create(
        "appr-rec",
        CONV,
        kind=InteractionKind.APPROVAL,
        payload={
            "tool_call_id": "appr-rec",
            "tool_name": "file_write",
            "arguments": {"path": "/tmp/x"},
        },
    )
    try:
        pending = registry_hot_pending(CONV, message_id="msg-live")
        assert len(pending) == 1
        assert pending[0].kind == "approval"
        assert pending[0].id == "appr-rec"
        assert pending[0].message_id == "msg-live"
        assert pending[0].payload["approval_id"] == "appr-rec"
        assert pending[0].payload["conversation_id"] == CONV
        assert pending[0].payload["tool_name"] == "file_write"
    finally:
        registry.discard("appr-rec")
