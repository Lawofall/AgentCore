"""Unit tests for the device-level CLIENT_TOOL fulfillment hub + dispatch.

Covers register / unregister, multi-device selection (origin device first, then
most recent), root / caps matching, queue-full unhealthy close, and
``deliver_client_tool`` outcomes including the origin pin that keeps disk /
command ops on the machine that started the turn, and the three-way naming of a
selection miss (origin offline / root not held / no fulfiller) that a log read
has to be able to tell apart. Also the departure memory + grace window that let
delivery tell a reconnecting desktop from an absent one, and the observer
sessions (browser clients, no caps) that read account state off the same stream
without ever counting as a machine an op could land on — including on connect:
a web tab must not rehang pending ``workspace_op`` / ``host_op`` onto the desktop.
No DB, no HTTP — plain async tests (asyncio_mode=auto).
"""

from __future__ import annotations

import asyncio

from agentcore.api.routes.fulfill import (
    _format_event,
    _fulfill_stream,
    _seed_registered_session,
)
from agentcore.fulfill import dispatch, grace
from agentcore.fulfill.dispatch import DeliverResult, deliver_client_tool
from agentcore.fulfill.hub import (
    _FULFILLER_QUEUE_MAXSIZE,
    FULFILL_CHANNELS,
    ORIGIN_PINNED_CHANNELS,
    RECENT_PRESENCE_SECONDS,
    FulfillerHub,
    FulfillerIdentity,
    FulfillerSession,
    default_fulfiller_hub,
    origin_pinned,
)
from agentcore.fulfill.user_signal import FRAME_QUEUE_ACCOUNT_SNAPSHOT
from agentcore.runtime.events.types import EventType, SSEEvent
from agentcore.runtime.turn.queue import TurnQueue, new_queued_turn


def test_default_fulfiller_hub_is_singleton():
    assert default_fulfiller_hub() is default_fulfiller_hub()


async def test_register_and_unregister():
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    assert hub.connection_count("u1") == 1
    assert hub.get_session("u1", "d1") is session

    hub.unregister(session)
    assert hub.connection_count("u1") == 0
    assert hub.get_session("u1", "d1") is None


async def test_reregister_same_device_replaces_session():
    hub = FulfillerHub()
    old = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    new = hub.register("u1", "d1", caps=["workspace", "host"], roots=["r2"])
    assert old is not new
    assert hub.get_session("u1", "d1") is new
    assert hub.connection_count("u1") == 1
    assert "host" in new.caps
    assert new.roots == {"r2"}
    # Old session was closed (sentinel delivered).
    assert await old.get() is None


async def test_find_prefers_origin_device_over_most_recent():
    """The turn's own device wins; recency only breaks ties without one."""
    hub = FulfillerHub()
    older = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    await asyncio.sleep(0.01)
    newer = hub.register("u1", "d2", caps=["workspace"], roots=["r1"])

    assert hub.find("u1", root_id="r1", channel="workspace") is newer
    assert (
        hub.find("u1", root_id="r1", channel="workspace", origin_device_id="d1")
        is older
    )
    hub.unregister(newer)
    assert hub.find("u1", root_id="r1", channel="workspace") is older


async def test_find_falls_back_to_most_recent_when_origin_is_gone():
    """Preference only — an offline origin must not blank out an unpinned pick."""
    hub = FulfillerHub()
    hub.register("u1", "d1", caps=["board"], roots=[])
    await asyncio.sleep(0.01)
    newer = hub.register("u1", "d2", caps=["board"], roots=[])

    assert (
        hub.find("u1", root_id=None, channel="board", origin_device_id="gone")
        is newer
    )


async def test_find_require_origin_refuses_a_peer():
    hub = FulfillerHub()
    hub.register("u1", "d1", caps=["host"], roots=[])
    origin = hub.register("u1", "d2", caps=["host"], roots=[])

    assert (
        hub.find(
            "u1",
            root_id=None,
            channel="host",
            origin_device_id="d2",
            require_origin=True,
        )
        is origin
    )
    hub.unregister(origin)
    assert (
        hub.find(
            "u1",
            root_id=None,
            channel="host",
            origin_device_id="d2",
            require_origin=True,
        )
        is None
    )
    # Unknown origin cannot be pinned to anything — old behavior stands.
    assert (
        hub.find("u1", root_id=None, channel="host", require_origin=True) is not None
    )


def test_origin_pin_covers_machine_acting_channels_only():
    assert sorted(ORIGIN_PINNED_CHANNELS) == [
        "external_mount",
        "host",
        "mcp",
        "workspace",
    ]
    for channel in ORIGIN_PINNED_CHANNELS:
        assert origin_pinned(channel, root_id=None) is True
        # A root already names one install — that location logic is untouched.
        assert origin_pinned(channel, root_id="r1") is False
    for channel in ("board", "board_read"):
        assert origin_pinned(channel, root_id=None) is False
    assert "notify" not in FULFILL_CHANNELS


async def test_find_requires_cap_and_root():
    hub = FulfillerHub()
    hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    hub.register("u1", "d2", caps=["host"], roots=["r1"])
    hub.register("u1", "d3", caps=["workspace"], roots=["r2"])

    assert hub.find("u1", root_id="r1", channel="workspace").device_id == "d1"
    assert hub.find("u1", root_id="r1", channel="host").device_id == "d2"
    assert hub.find("u1", root_id="r2", channel="workspace").device_id == "d3"
    assert hub.find("u1", root_id="r1", channel="mcp") is None
    assert hub.find("u1", root_id="missing", channel="workspace") is None


async def test_find_root_none_matches_any_capable_unless_pinned():
    hub = FulfillerHub()
    hub.register("u1", "d1", caps=["board", "host"], roots=[])
    assert hub.find("u1", root_id=None, channel="board") is not None
    assert hub.has_fulfiller("u1", root_id=None, channel="board") is True
    assert hub.has_fulfiller("u1", root_id="r1", channel="board") is False
    # Same rootless lookup, but a pinned channel from another device: absent.
    assert (
        hub.has_fulfiller(
            "u1",
            root_id=None,
            channel="host",
            origin_device_id="other",
            require_origin=True,
        )
        is False
    )


async def test_declare_root_widens_without_reconnect():
    """A registration receipt binds one root; the ones already held stay held."""
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    assert hub.declare_root("u1", "d1", "r2") is True
    assert session.roots == {"r1", "r2"}
    assert hub.find("u1", root_id="r1", channel="workspace") is session
    assert hub.find("u1", root_id="r2", channel="workspace") is session
    assert hub.declare_root("u1", "missing", "r9") is False


async def test_queue_full_closes_session():
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])

    for i in range(_FULFILLER_QUEUE_MAXSIZE):
        assert session.offer({"type": "m", "i": i}) is True

    assert hub.deliver(session, {"type": "overflow"}) is False
    assert hub.get_session("u1", "d1") is None
    assert hub.connection_count("u1") == 0
    # Closed session surfaces the sentinel (backlog drained on close).
    assert await session.get() is None


async def test_deliver_client_tool_no_fulfiller():
    hub = FulfillerHub()
    result = deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        {"type": "workspace_op_required", "payload": {}},
        hub=hub,
    )
    assert result is DeliverResult.NO_FULFILLER


async def test_deliver_root_not_held_when_the_desktop_holds_another_root():
    """Desktop online, this root not declared: the gate's case 2, mid-turn."""
    hub = FulfillerHub()
    holder = hub.register("u1", "d1", caps=["workspace"], roots=["other-root"])

    result = deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        {"type": "workspace_op_required", "payload": {"op": "read"}},
        hub=hub,
    )
    assert result is DeliverResult.ROOT_NOT_HELD
    # The root stays an authorization boundary — nothing was handed to d1.
    assert holder._queue.qsize() == 0


async def test_deliver_no_fulfiller_when_the_online_device_lacks_the_channel():
    """A device without the workspace cap is no desktop at all for this op."""
    hub = FulfillerHub()
    hub.register("u1", "d1", caps=["board"], roots=["r1"])

    result = deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        {"type": "workspace_op_required", "payload": {"op": "read"}},
        hub=hub,
    )
    assert result is DeliverResult.NO_FULFILLER


async def test_no_fulfiller_log_says_which_of_the_three_states(monkeypatch):
    """A ``no fulfiller`` line must be readable after the fact, not a shrug."""
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(dispatch, "logger", spy)
    hub = FulfillerHub()
    hub.register("u1", "d1", caps=["workspace"], roots=["other-root"])

    deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        {"type": "workspace_op_required", "payload": {"op": "read"}},
        hub=hub,
    )
    fields = spy.get("fulfill.no_fulfiller")
    assert fields["reason"] == "root_not_held"
    assert fields["root_id"] == "r1"
    assert fields["channel"] == "workspace"
    assert fields["devices"] == 1

    spy.events.clear()
    deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        {"type": "workspace_op_required", "payload": {"op": "read"}},
        hub=FulfillerHub(),
    )
    offline = spy.get("fulfill.no_fulfiller")
    assert offline["reason"] == "desktop_offline"
    assert offline["devices"] == 0

    # Third state keeps its own line (origin device gone, peer still online).
    spy.events.clear()
    pinned_hub = FulfillerHub()
    pinned_hub.register("u1", "peer", caps=["host"], roots=[])
    deliver_client_tool(
        "u1",
        "c1",
        "host",
        None,
        {"type": "host_op_required", "payload": {"op": "host_shell"}},
        origin_device_id="gone",
        hub=pinned_hub,
    )
    assert spy.get("fulfill.origin_offline")["reason"] == "not_online"


async def test_deliver_client_tool_delivered():
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    event = SSEEvent(
        type=EventType.WORKSPACE_OP_REQUIRED,
        payload={"request_id": "req1", "root_id": "r1", "op": "exists"},
    )
    result = deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        event,
        hub=hub,
    )
    assert result is DeliverResult.DELIVERED
    got = await session.get()
    assert got["type"] == "workspace_op_required"
    assert got["payload"]["request_id"] == "req1"


async def test_deliver_retries_after_queue_full_close_when_unpinned():
    """No pin in force: a stuck device is closed and a healthy peer takes it."""
    hub = FulfillerHub()
    stuck = hub.register("u1", "stuck", caps=["workspace"], roots=["r1"])
    await asyncio.sleep(0.01)
    healthy = hub.register("u1", "ok", caps=["workspace"], roots=["r1"])
    # Fill the newer (preferred) session so deliver closes it and falls back.
    for i in range(_FULFILLER_QUEUE_MAXSIZE):
        assert healthy.offer({"type": "pad", "i": i}) is True

    result = deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        {"type": "workspace_op_required", "payload": {"op": "ping"}},
        hub=hub,
    )
    assert result is DeliverResult.DELIVERED
    assert hub.get_session("u1", "ok") is None
    got = await stuck.get()
    assert got["type"] == "workspace_op_required"


async def test_deliver_does_not_retry_onto_a_peer_when_pinned():
    """Queue-full on the origin must not hand a rootless disk op to another machine."""
    hub = FulfillerHub()
    peer = hub.register("u1", "peer", caps=["workspace"], roots=[])
    origin = hub.register("u1", "origin", caps=["workspace"], roots=[])
    for i in range(_FULFILLER_QUEUE_MAXSIZE):
        assert origin.offer({"type": "pad", "i": i}) is True

    result = deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        None,
        {"type": "workspace_op_required", "payload": {"op": "ping"}},
        origin_device_id="origin",
        hub=hub,
    )
    assert result is DeliverResult.ORIGIN_OFFLINE
    # Unhealthy origin still got closed; the peer was left untouched.
    assert hub.get_session("u1", "origin") is None
    assert hub.get_session("u1", "peer") is peer
    assert peer._queue.qsize() == 0


async def test_two_devices_pinned_op_lands_on_the_origin_device():
    hub = FulfillerHub()
    origin = hub.register("u1", "A", caps=["host"], roots=[])
    await asyncio.sleep(0.01)
    # B registered later — the pre-pin rule would have chosen it.
    other = hub.register("u1", "B", caps=["host"], roots=[])

    result = deliver_client_tool(
        "u1",
        "c1",
        "host",
        None,
        {"type": "host_op_required", "payload": {"op": "host_shell"}},
        origin_device_id="A",
        hub=hub,
    )
    assert result is DeliverResult.DELIVERED
    got = await origin.get()
    assert got["payload"]["op"] == "host_shell"
    assert other._queue.qsize() == 0


async def test_two_devices_pinned_op_errors_when_origin_left():
    """A goes offline: the shell command must not run on B instead."""
    hub = FulfillerHub()
    origin = hub.register("u1", "A", caps=["host"], roots=[])
    other = hub.register("u1", "B", caps=["host"], roots=[])
    hub.unregister(origin)

    result = deliver_client_tool(
        "u1",
        "c1",
        "host",
        None,
        {"type": "host_op_required", "payload": {"op": "host_shell"}},
        origin_device_id="A",
        hub=hub,
    )
    assert result is DeliverResult.ORIGIN_OFFLINE
    assert other._queue.qsize() == 0


async def test_two_devices_reminder_still_reaches_the_remaining_one():
    """Display channels keep the old any-capable-device behavior."""
    hub = FulfillerHub()
    origin = hub.register("u1", "A", caps=["board"], roots=[])
    other = hub.register("u1", "B", caps=["board"], roots=[])
    hub.unregister(origin)

    result = deliver_client_tool(
        "u1",
        "c1",
        "board",
        None,
        {"type": "board_op_required", "payload": {"board_id": "b1", "ops": [], "summary": "x"}},
        origin_device_id="A",
        hub=hub,
    )
    assert result is DeliverResult.DELIVERED
    got = await other.get()
    assert got["type"] == "board_op_required"


async def test_single_device_pinned_op_keeps_the_no_fulfiller_answer():
    """One install, offline: 'no fulfiller', not 'your other device is gone'."""
    hub = FulfillerHub()
    result = deliver_client_tool(
        "u1",
        "c1",
        "host",
        None,
        {"type": "host_op_required", "payload": {"op": "host_info"}},
        origin_device_id="A",
        hub=hub,
    )
    assert result is DeliverResult.NO_FULFILLER


async def test_rooted_op_ignores_the_pin():
    """Root-bound location logic is unchanged: the root still decides."""
    hub = FulfillerHub()
    holder = hub.register("u1", "B", caps=["workspace"], roots=["r1"])

    result = deliver_client_tool(
        "u1",
        "c1",
        "workspace",
        "r1",
        {"type": "workspace_op_required", "payload": {"op": "read"}},
        origin_device_id="A",
        hub=hub,
    )
    assert result is DeliverResult.DELIVERED
    assert (await holder.get())["type"] == "workspace_op_required"


async def test_seen_recently_remembers_a_device_that_just_dropped():
    """The reconnect blind window: hub empty, machine still very much there."""
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    assert hub.seen_recently("u1", device_id="d1") is True

    hub.unregister(session)
    assert hub.find("u1", root_id="r1", channel="workspace") is None
    assert hub.seen_recently("u1", device_id="d1") is True
    assert hub.seen_recently("u1") is True
    # Older than the presence window = plainly gone, not reconnecting.
    assert hub.seen_recently("u1", device_id="d1", within=0.0) is False
    assert hub.seen_recently("u1", within=0.0) is False


async def test_seen_recently_is_false_for_a_client_that_never_connected():
    hub = FulfillerHub()
    hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    assert hub.seen_recently("u1", device_id="never") is False
    assert hub.seen_recently("someone-else") is False
    # A live session answers for its own device only — the pin still decides
    # whether a peer may take the op.
    assert hub.seen_recently("u1", device_id="d1") is True


async def test_observer_session_receives_account_state_but_fulfils_nothing():
    """The browser client: same stream, no caps — reads state, runs no op."""
    hub = FulfillerHub()
    observer = hub.register("u1", "web-1", caps=[], roots=[], platform="web")

    assert hub.connection_count("u1") == 1
    assert observer.can_fulfil is False
    # Account-owned state reaches every install, this one included.
    assert hub.broadcast("u1", {"type": "turn_queue_snapshot"}) == 1
    assert (await observer.get())["type"] == "turn_queue_snapshot"
    # Nothing routes to it, on any channel, rooted or not.
    for channel in sorted(FULFILL_CHANNELS):
        assert hub.find("u1", root_id=None, channel=channel) is None
        assert hub.has_fulfiller("u1", root_id=None, channel=channel) is False


async def test_observer_presence_never_stands_in_for_a_desktop():
    """A web tab open is not "the machine is reconnecting" — don't park ops on it."""
    hub = FulfillerHub()
    observer = hub.register("u1", "web-1", caps=[], roots=[])

    # Live: the account has a connection, but nothing that could take the op, so
    # a rootless miss must fail honestly instead of waiting out the grace.
    assert hub.seen_recently("u1") is False
    assert hub.seen_recently("u1", device_id="web-1") is False

    # Gone: no departure mark either, or the same wait returns for a full minute.
    hub.unregister(observer)
    assert hub.seen_recently("u1") is False
    assert hub.seen_recently("u1", device_id="web-1") is False


async def test_observer_alongside_a_desktop_leaves_delivery_untouched():
    """Opening the web client must not change what a desktop-owning account sees."""
    hub = FulfillerHub()
    hub.register("u1", "web-1", caps=[], roots=[])
    desktop = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])

    assert hub.find("u1", root_id="r1", channel="workspace") is desktop
    assert hub.seen_recently("u1") is True
    # And the desktop's own reconnect window still opens when it drops.
    hub.unregister(desktop)
    assert hub.seen_recently("u1", device_id="d1") is True


async def test_observer_connect_skips_rehang_but_still_gets_snapshot(monkeypatch):
    """Web tab: zero caps. Account state yes; do not re-push pending local ops."""
    rehangs: list[str] = []
    monkeypatch.setattr(
        "agentcore.runtime.events.client_tool_reattach.rehang_pending_client_tools",
        lambda user_id: rehangs.append(user_id) or 0,
    )
    monkeypatch.setattr("agentcore.api.routes.fulfill.turn_queue", TurnQueue())

    hub = FulfillerHub()
    observer = hub.register("u1", "web-1", caps=[], roots=[], platform="web")
    assert observer.can_fulfil is False
    _seed_registered_session(observer, hub)

    assert rehangs == []
    assert await observer.get() == {
        "type": FRAME_QUEUE_ACCOUNT_SNAPSHOT,
        "payload": {"queues": []},
    }


async def test_capable_connect_rehangs_pending_ops(monkeypatch):
    """Desktop reconnect: rehang so in-flight CLIENT_TOOL is not dropped."""
    rehangs: list[str] = []
    monkeypatch.setattr(
        "agentcore.runtime.events.client_tool_reattach.rehang_pending_client_tools",
        lambda user_id: rehangs.append(user_id) or 0,
    )
    monkeypatch.setattr("agentcore.api.routes.fulfill.turn_queue", TurnQueue())

    hub = FulfillerHub()
    desktop = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    assert desktop.can_fulfil is True
    _seed_registered_session(desktop, hub)

    assert rehangs == ["u1"]
    assert await desktop.get() == {
        "type": FRAME_QUEUE_ACCOUNT_SNAPSHOT,
        "payload": {"queues": []},
    }


async def test_connect_seed_one_account_queue_frame_even_when_empty(monkeypatch):
    """Empty table still lands — client replace, not silence, not per-conv frames."""
    monkeypatch.setattr(
        "agentcore.runtime.events.client_tool_reattach.rehang_pending_client_tools",
        lambda user_id: 0,
    )
    monkeypatch.setattr("agentcore.api.routes.fulfill.turn_queue", TurnQueue())
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    _seed_registered_session(session, hub, running_conversation_ids=[])
    frame = await session.get()
    assert frame == {
        "type": FRAME_QUEUE_ACCOUNT_SNAPSHOT,
        "payload": {"queues": []},
    }
    assert frame["type"] != "turn_queue_snapshot"


async def test_connect_seed_packs_all_queues_in_one_account_frame(monkeypatch):
    """Two conversations → one account snapshot, not two incremental frames."""
    q = TurnQueue()
    q.enqueue("c1", new_queued_turn(content="a", user_id="u1"))
    q.enqueue("c2", new_queued_turn(content="b", user_id="u1"))
    monkeypatch.setattr(
        "agentcore.runtime.events.client_tool_reattach.rehang_pending_client_tools",
        lambda user_id: 0,
    )
    monkeypatch.setattr("agentcore.api.routes.fulfill.turn_queue", q)
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    _seed_registered_session(session, hub, running_conversation_ids=[])
    frame = await session.get()
    assert frame["type"] == FRAME_QUEUE_ACCOUNT_SNAPSHOT
    assert {row["conversation_id"] for row in frame["payload"]["queues"]} == {
        "c1",
        "c2",
    }
    activity = await session.get()
    assert activity["type"] == "ai_turn_activity_snapshot"


def test_presence_window_outlasts_the_grace_it_authorizes():
    """Holding an op longer than a device counts as "just here" is incoherent."""
    assert RECENT_PRESENCE_SECONDS >= grace.RECONNECT_GRACE_SECONDS


def test_grace_window_never_outlives_the_op_deadline():
    """Waiting must never convert an honest failure into a timeout."""
    assert grace.window_for(None) == grace.RECONNECT_GRACE_SECONDS
    # Roomy channel deadline: the full grace still fits under it.
    assert grace.window_for(60.0) == grace.RECONNECT_GRACE_SECONDS
    assert grace.window_for(60.0) < 60.0
    # Tight deadline: clamped, and a 1s op keeps today's immediate failure.
    assert grace.window_for(5.0) == 4.0
    assert grace.window_for(1.0) <= 0.0


async def test_hold_expires_once_and_release_cancels_it():
    fired: list[str] = []
    assert grace.hold("req-1", seconds=0.01, on_expire=lambda: fired.append("req-1"))
    assert grace.is_held("req-1") is True
    await asyncio.sleep(0.05)
    assert fired == ["req-1"]
    assert grace.is_held("req-1") is False

    assert grace.hold("req-2", seconds=0.01, on_expire=lambda: fired.append("req-2"))
    assert grace.release("req-2") is True
    await asyncio.sleep(0.05)
    assert fired == ["req-1"]
    assert grace.release("req-2") is False
    # A non-positive window is not a hold at all — the caller settles now.
    assert grace.hold("req-3", seconds=0.0, on_expire=lambda: fired.append("req-3")) is False


async def test_unknown_caps_filtered_on_register():
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace", "not_a_channel"], roots=[])
    assert session.caps == frozenset({"workspace"})


def test_format_event_is_named_sse_frame():
    frame = _format_event({"type": "workspace_op_required", "payload": {"op": "x"}})
    assert frame.startswith("event: workspace_op_required\n")
    assert '"op": "x"' in frame
    assert frame.endswith("\n\n")


async def test_fulfill_stream_ready_then_event_and_unregisters():
    hub = FulfillerHub()
    session = hub.register("u1", "d1", caps=["workspace"], roots=["r1"])
    gen = _fulfill_stream(session, hub)

    first = await gen.__anext__()
    assert first.startswith("event: ready\n")

    assert hub.deliver(session, {"type": "client_tool_cancelled", "payload": {"request_id": "r"}})
    second = await gen.__anext__()
    assert second.startswith("event: client_tool_cancelled\n")

    await gen.aclose()
    assert hub.connection_count("u1") == 0


async def test_session_aiter_ends_on_close():
    session = FulfillerSession(
        FulfillerIdentity(
            user_id="u1",
            device_id="d1",
            platform=None,
            caps=frozenset({"workspace"}),
        ),
        registered_at=0.0,
    )
    await session._queue.put({"type": "a"})
    session.close()
    seen = [event async for event in session]
    assert seen == []
