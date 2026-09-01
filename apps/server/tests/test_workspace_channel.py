"""Tests for the local-workspace op channel (双模式工作区 P2a).

Covers the three pieces that make "one agent loop, two execution platforms" work
for local mode, without an actual desktop:

  * ``InteractionRegistry`` — the in-process bridge: unknown / double / wrong-
    conversation resolves are refused; a matching resolve settles the Future.
  * ``WorkspaceChannel`` — suspends an op on a Future, delivers a
    ``workspace_op_required`` frame via the fulfill hub carrying the *full* args,
    and returns the desktop's value or re-raises the typed ``WorkspaceError``
    (timeout → IO error).
  * ``LocalWorkspace`` — read/list/grep round-trip through the channel and parse
    back into the same typed shapes ``ServerWorkspace`` returns, and a mutating op
    flips ``dirty`` while a read-only op does not.

A fake "desktop" drives each round trip: it reads the delivered op event (captured
from fulfill dispatch) to learn the ``request_id``, then settles the registry.
"""

from __future__ import annotations

import asyncio

import pytest

from agentcore.fulfill import grace
from agentcore.fulfill.dispatch import DeliverResult
from agentcore.fulfill.dispatch import deliver_client_tool as _real_deliver_client_tool
from agentcore.runtime.events import EventType, SSEEvent
from agentcore.runtime.events.client_tool_reattach import rehang_pending_client_tools
from agentcore.runtime.interaction import (
    InteractionKind,
    InteractionRegistry,
    default_interaction_registry,
)
from agentcore.tools.sandbox.protocol import ExecutionRequest
from agentcore.workspace.channel import WorkspaceChannel, WorkspaceOp
from agentcore.workspace.limits import LOCAL_ROOT_NOT_HELD
from agentcore.workspace.local import LocalWorkspace
from agentcore.workspace.protocol import (
    AmbiguousMatch,
    GrepQuery,
    OutsideWorkspace,
    PathNotFound,
    WorkspaceIOError,
)
from tests.client_tool_fulfill_testutil import await_fulfill_event, install_test_hub

pytestmark = pytest.mark.anyio

CONV = "conv-1"
# Own conversation id: the reconnect case drives the process-wide registry that
# ``rehang_pending_client_tools`` reads, so it must not share pending entries.
CONV_RECONNECT = "conv-1-reconnect"
USER = "user-ws-channel"
ROOT_ID = "root-abc"

# Capture list filled by the autouse deliver patch (SSEEvent instances).
_CAPTURE: list[SSEEvent] = []


@pytest.fixture(autouse=True)
def _patch_deliver(monkeypatch: pytest.MonkeyPatch):
    """Default: every CLIENT_TOOL deliver succeeds and is captured for tests."""
    _CAPTURE.clear()

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
        _CAPTURE.append(event)
        return DeliverResult.DELIVERED

    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.deliver_client_tool", fake_deliver
    )


def _make(
    timeout: float = 5.0, *, execute_slack: float = 15.0
) -> tuple[LocalWorkspace, InteractionRegistry]:
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=timeout,
        root_id=ROOT_ID,
    )
    return (
        LocalWorkspace(channel, execute_timeout_slack=execute_slack),
        registry,
    )


async def _await_request() -> SSEEvent:
    """Return the op event the channel just delivered (yielding so the op runs)."""
    for _ in range(2000):
        if _CAPTURE:
            return _CAPTURE.pop(0)
        await asyncio.sleep(0)
    raise AssertionError("no workspace_op_required event delivered")


async def _round_trip(coro, registry: InteractionRegistry, response: dict):
    """Drive one op: start it, answer it as the desktop would, return (result, event)."""
    task = asyncio.create_task(coro)
    event = await _await_request()
    assert registry.resolve(event.payload["request_id"], response, conversation_id=CONV)
    return await task, event


async def _round_trip_execute(
    coro,
    registry: InteractionRegistry,
    response: dict,
):
    """Drive execute: one desktop EXECUTE (no shortest-program preflight)."""
    task = asyncio.create_task(coro)
    event = await _await_request()
    assert event.payload["op"] == WorkspaceOp.EXECUTE
    assert registry.resolve(event.payload["request_id"], response, conversation_id=CONV)
    return await task, event


# --- LocalWorkspace read-only ops (the P2a "打通") --------------------------


async def test_read_round_trips_through_channel():
    local, registry = _make()
    result, event = await _round_trip(
        local.read("a.txt"), registry, {"ok": True, "value": "hello"}
    )
    assert result == "hello"
    assert event.type == EventType.WORKSPACE_OP_REQUIRED
    assert event.payload["op"] == WorkspaceOp.READ
    assert event.payload["args"] == {"path": "a.txt"}
    assert event.payload["conversation_id"] == CONV
    assert event.payload["root_id"] == ROOT_ID
    # A read must not mark the workspace dirty (no end-of-turn snapshot for it).
    assert local.dirty is False


async def test_list_parses_dir_entries():
    local, registry = _make()
    response = {
        "ok": True,
        "value": [
            {"path": "src", "is_dir": True, "mtime_ms": 1000},
            {
                "path": "src/main.py",
                "is_dir": False,
                "size_bytes": 42,
                "mtime_ms": 2000,
            },
            {"path": "readme.md", "is_dir": False},  # optional meta absent → None
        ],
    }
    listing, _ = await _round_trip(local.list(".", "*"), registry, response)
    assert [(e.path, e.is_dir, e.size_bytes, e.mtime_ms) for e in listing] == [
        ("src", True, None, 1000),
        ("src/main.py", False, 42, 2000),
        ("readme.md", False, None, None),
    ]
    # Bare-array answer = a desktop from before the cap became honest.
    assert listing.truncated is False


async def test_list_reports_desktop_truncation():
    """A capped desktop listing must arrive as truncated, not as a complete tree."""
    local, registry = _make()
    response = {
        "ok": True,
        "value": {
            "entries": [{"path": "a.txt", "is_dir": False}],
            "truncated": True,
        },
    }
    listing, event = await _round_trip(local.list(".", "*", cap=1), registry, response)
    assert event.payload["args"]["cap"] == 1
    assert [e.path for e in listing] == ["a.txt"]
    assert listing.truncated is True


async def test_index_files_parses_paths_and_truncation():
    local, registry = _make()
    response = {"ok": True, "value": {"paths": ["a.txt", "sub/b.md"], "truncated": True}}
    (paths, truncated), event = await _round_trip(
        local.index_files(order="recent"), registry, response
    )
    assert event.payload["op"] == WorkspaceOp.INDEX_FILES
    assert event.payload["args"]["order"] == "recent"  # sort preference reaches desktop
    assert paths == ["a.txt", "sub/b.md"]
    assert truncated is True
    # Indexing is read-only — it must not schedule an end-of-turn snapshot.
    assert local.dirty is False


async def test_index_files_parses_entries_fingerprints():
    """Desktop contract: entries with mtime_ms/size_bytes (paths optional dual)."""
    local, registry = _make()
    response = {
        "ok": True,
        "value": {
            "entries": [
                {"path": "a.txt", "mtime_ms": 1000, "size_bytes": 12},
                {"path": "sub/b.md", "mtime_ms": 2000, "size_bytes": 34},
            ],
            "paths": ["a.txt", "sub/b.md"],
            "truncated": False,
        },
    }
    result, _ = await _round_trip(local.index_files(), registry, response)
    assert result.paths == ["a.txt", "sub/b.md"]
    assert result.truncated is False
    assert result.fingerprints() == {
        "a.txt": (1000, 12),
        "sub/b.md": (2000, 34),
    }


async def test_index_files_tolerates_empty_envelope():
    local, registry = _make()
    # A not-yet-promoted / empty workspace answers with a bare ok — degrade to ([], False).
    (paths, truncated), _ = await _round_trip(local.index_files(), registry, {"ok": True})
    assert paths == [] and truncated is False


async def test_grep_parses_result():
    local, registry = _make()
    response = {
        "ok": True,
        "value": {
            "hits": [{"path": "a.py", "line_no": 3, "text": "import os"}],
            "file_counts": [["a.py", 1]],
            "total_matches": 1,
            "truncated": False,
        },
    }
    result, event = await _round_trip(
        local.grep(GrepQuery(pattern="import")), registry, response
    )
    assert event.payload["op"] == WorkspaceOp.GREP
    assert result.total_matches == 1
    assert result.hits[0].path == "a.py"
    assert result.hits[0].line_no == 3
    assert result.file_counts == [("a.py", 1)]


# --- mutating ops route too (skeleton complete; dirty + full args) ----------


async def test_write_marks_dirty_and_sends_full_content():
    local, registry = _make()
    big = "x" * 5000  # full payload, NOT a bounded preview like approvals
    result, event = await _round_trip(
        local.write("out.txt", big), registry, {"ok": True, "value": 5000}
    )
    assert result == 5000
    assert event.payload["args"]["content"] == big
    assert local.dirty is True


async def test_execute_parses_result_and_marks_dirty():
    local, registry = _make()
    response = {
        "ok": True,
        "value": {
            "success": True,
            "stdout": "hi\n",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 12,
        },
    }
    req = ExecutionRequest(code="print('hi')", language="python")
    result, event = await _round_trip_execute(
        local.execute(req), registry, response
    )
    assert event.payload["op"] == WorkspaceOp.EXECUTE
    assert event.payload["args"]["code"] == "print('hi')"
    assert result.success and result.stdout == "hi\n"
    assert local.dirty is True


async def test_execute_forwards_registry_env():
    local, registry = _make()
    response = {
        "ok": True,
        "value": {
            "success": True,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 1,
        },
    }
    req = ExecutionRequest(
        code="print(1)",
        language="python",
        env={"NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/", "SECRET": "no"},
    )
    _result, event = await _round_trip_execute(
        local.execute(req), registry, response
    )
    assert event.payload["args"]["env"]["NPM_CONFIG_REGISTRY"].startswith("https://")
    assert event.payload["args"]["env"]["SECRET"] == "no"


# --- typed error mapping (the tool layer must see the same exceptions) ------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("PathNotFound", PathNotFound),
        ("OutsideWorkspace", OutsideWorkspace),
        ("WorkspaceIOError", WorkspaceIOError),
        ("SomethingUnknown", WorkspaceIOError),  # degrade unknown → generic IO
    ],
)
async def test_error_kind_maps_to_typed_exception(kind: str, expected: type):
    local, registry = _make()
    response = {"ok": False, "error": {"kind": kind, "detail": "boom"}}
    with pytest.raises(expected):
        await _round_trip(local.read("x"), registry, response)


async def test_ambiguous_match_carries_count():
    local, registry = _make()
    response = {"ok": False, "error": {"kind": "AmbiguousMatch", "count": 4}}
    with pytest.raises(AmbiguousMatch) as ei:
        await _round_trip(local.replace("a.py", "x", "y", all_=False), registry, response)
    assert ei.value.count == 4


async def test_malformed_envelope_raises_io_error():
    local, registry = _make()
    with pytest.raises(WorkspaceIOError):
        await _round_trip(local.read("x"), registry, {"unexpected": True})


# --- timeout (a dropped desktop never hangs the turn) ----------------------


async def test_timeout_raises_io_error():
    local, _registry = _make(timeout=0.05)
    # No desktop answers, so the op times out and surfaces as a WorkspaceIOError.
    with pytest.raises(WorkspaceIOError, match="活性挂起"):
        await local.read("never-answered.txt")


async def test_single_timeout_keeps_channel_alive_for_next_op():
    """One settle timeout fails that op only — next op can still succeed."""
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=0.05,
        root_id=ROOT_ID,
    )
    with pytest.raises(WorkspaceIOError, match=r"timed out（活性挂起）"):
        await channel.request(WorkspaceOp.READ, {"path": "never-answered.txt"})

    while _CAPTURE:
        _CAPTURE.pop(0)

    task = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "a.txt"}))
    event = await _await_request()
    assert event.payload["op"] == "read"
    assert registry.resolve(
        event.payload["request_id"],
        {"ok": True, "value": "alive"},
        conversation_id=CONV,
    )
    assert await task == "alive"


async def test_after_two_timeouts_third_request_still_delivers():
    """Settle timeouts fail those ops only — the next request still goes to the desktop."""
    local, registry = _make(timeout=0.05)
    with pytest.raises(WorkspaceIOError, match=r"timed out（活性挂起）"):
        await local.read("never-answered-1.txt")
    with pytest.raises(WorkspaceIOError, match=r"timed out（活性挂起）"):
        await local.read("never-answered-2.txt")

    while _CAPTURE:
        _CAPTURE.pop(0)

    task = asyncio.create_task(local.read("third.txt"))
    event = await _await_request()
    assert event.payload["op"] == "read"
    assert registry.resolve(
        event.payload["request_id"],
        {"ok": True, "value": "still-alive"},
        conversation_id=CONV,
    )
    assert await task == "still-alive"


async def test_probe_exec_timeout_does_not_sticky_dead_channel():
    """A1: language probe hang fail-closes advertise only — file channel stays alive."""
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=0.05,
        root_id=ROOT_ID,
    )
    with pytest.raises(WorkspaceIOError, match="probe_exec.*活性挂起"):
        await channel.request(WorkspaceOp.PROBE_EXEC, {})

    # Drain the unanswered probe SSE so the next await sees the file op.
    while _CAPTURE:
        _CAPTURE.pop(0)

    # A real file op must still emit SSE (not reject as channel dead).
    task = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "a.txt"}))
    event = await _await_request()
    assert event.payload["op"] == "read"
    assert registry.resolve(
        event.payload["request_id"],
        {"ok": True, "value": "alive"},
        conversation_id=CONV,
    )
    assert await task == "alive"


async def test_op_timeout_log_includes_path(monkeypatch):
    """workspace.op_timeout must carry path (and directory when present) for replay."""
    import agentcore.workspace.channel as channel_mod
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(channel_mod, "logger", spy)

    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=0.05,
        root_id=ROOT_ID,
    )
    path = "logs/reviews/cases/CASE.md"
    with pytest.raises(WorkspaceIOError, match="活性挂起"):
        await channel.request(WorkspaceOp.READ, {"path": path})

    fields = spy.get("workspace.op_timeout")
    assert fields["op"] == "read"
    assert fields["path"] == path
    assert fields["conversation_id"] == CONV
    assert fields["root_id"] == ROOT_ID
    # derive_channel_timeout floors at 1.0s even when channel_default is tiny.
    assert fields["timeout_ms"] == 1000
    assert "directory" not in fields

    spy.events.clear()
    channel2 = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=0.05,
        root_id=ROOT_ID,
    )
    with pytest.raises(WorkspaceIOError, match="活性挂起"):
        await channel2.request(
            WorkspaceOp.GREP, {"pattern": "x", "directory": "src"}
        )
    grep_fields = spy.get("workspace.op_timeout")
    assert grep_fields["op"] == "grep"
    assert grep_fields["directory"] == "src"
    assert grep_fields["conversation_id"] == CONV
    assert grep_fields["root_id"] == ROOT_ID
    assert grep_fields["timeout_ms"] == 1000


async def test_no_fulfiller_fail_fast_without_wall_clock_wait(monkeypatch):
    """No online fulfiller *and* none seen lately: settle now, no timeout wait.

    The hub has never heard of this user's devices, so the reconnect grace below
    does not engage — a genuinely clientless account keeps its immediate answer.
    """
    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.deliver_client_tool",
        lambda *a, **k: DeliverResult.NO_FULFILLER,
    )
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=5.0,
        root_id=ROOT_ID,
    )
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(WorkspaceIOError, match="无履约方"):
        await channel.request(WorkspaceOp.READ, {"path": "after-close.txt"})
    elapsed = asyncio.get_running_loop().time() - t0
    # Must not burn the 5s channel deadline awaiting a desktop that never saw the op.
    assert elapsed < 0.5

async def test_root_not_held_settles_with_the_authorization_copy(monkeypatch):
    """Desktop online without this root: name the missing grant, not 无履约方."""
    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.deliver_client_tool",
        lambda *a, **k: DeliverResult.ROOT_NOT_HELD,
    )
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=5.0,
        root_id=ROOT_ID,
    )
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(WorkspaceIOError) as ei:
        await channel.request(WorkspaceOp.READ, {"path": "revoked.txt"})
    elapsed = asyncio.get_running_loop().time() - t0
    detail = str(ei.value)
    assert LOCAL_ROOT_NOT_HELD in detail
    assert "read" in detail
    assert "无履约方" not in detail
    assert elapsed < 0.5

def _use_real_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the module-wide deliver stub — these cases assert real hub routing."""
    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.deliver_client_tool", _real_deliver_client_tool
    )


async def _await_pending(registry: InteractionRegistry, conversation_id: str, task):
    """The op's still-open registry entry (fails loudly if it settled instead)."""
    for _ in range(2000):
        pending = registry.list_pending(conversation_id)
        if pending:
            return pending[0]
        if task.done():
            raise AssertionError(f"op settled instead of waiting: {task.result()!r}")
        await asyncio.sleep(0)
    raise AssertionError("op never suspended")


async def test_op_dispatched_into_a_reconnect_blind_window_survives_it(monkeypatch):
    """桌面 SSE 刚断开（1–4s 后就回来）：op 挂住等重连，别当场判无履约方。"""
    _use_real_dispatch(monkeypatch)
    hub, session = install_test_hub(
        monkeypatch,
        user_id=USER,
        device_id="desk-1",
        roots=[ROOT_ID],
        caps={"workspace"},
    )
    hub.unregister(session)  # the SSE dropped; the machine never went anywhere

    registry = default_interaction_registry()
    for leftover in list(registry.list_pending(CONV_RECONNECT)):
        registry.discard(leftover.id)
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV_RECONNECT,
        registry=registry,
        timeout_seconds=30.0,
        root_id=ROOT_ID,
    )
    task = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "notes.md"}))
    try:
        pending = await _await_pending(registry, CONV_RECONNECT, task)
        # Held, not settled — a settled Future is nothing left to re-hang.
        assert grace.is_held(pending.id) is True

        back = hub.register(USER, "desk-1", caps=["workspace"], roots=[ROOT_ID])
        assert rehang_pending_client_tools(USER) == 1
        frame = await await_fulfill_event(back)
        assert frame["type"] == "workspace_op_required"
        assert frame["payload"]["request_id"] == pending.id
        assert grace.is_held(pending.id) is False

        assert registry.resolve(
            pending.id,
            {"ok": True, "value": "notes"},
            conversation_id=CONV_RECONNECT,
        )
        assert await asyncio.wait_for(task, timeout=1.0) == "notes"
    finally:
        task.cancel()
        for leftover in list(registry.list_pending(CONV_RECONNECT)):
            registry.discard(leftover.id)


async def test_desktop_that_left_long_ago_gets_no_grace(monkeypatch):
    """宽限只给「刚刚还在」的设备；早已离线 = 与今天完全一样，立刻失败。"""
    _use_real_dispatch(monkeypatch)
    monkeypatch.setattr("agentcore.fulfill.hub.RECENT_PRESENCE_SECONDS", 0.0)
    hub, session = install_test_hub(
        monkeypatch,
        user_id=USER,
        device_id="desk-1",
        roots=[ROOT_ID],
        caps={"workspace"},
    )
    hub.unregister(session)

    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=30.0,
        root_id=ROOT_ID,
    )
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(WorkspaceIOError, match="无履约方"):
        await channel.request(WorkspaceOp.READ, {"path": "gone.txt"})
    assert asyncio.get_running_loop().time() - t0 < 0.5

async def test_grace_expiry_settles_with_the_same_answer_well_inside_the_deadline(
    monkeypatch,
):
    """设备没回来：宽限到点就按原文案结算，绝不拖到 channel deadline。"""
    _use_real_dispatch(monkeypatch)
    hub, session = install_test_hub(
        monkeypatch,
        user_id=USER,
        device_id="desk-1",
        roots=[ROOT_ID],
        caps={"workspace"},
    )
    hub.unregister(session)

    # 1.2s deadline → 0.2s grace (clamped by the settle slack), so the wait is
    # observable and the failure still lands as a settle, not a timeout.
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=InteractionRegistry(),
        timeout_seconds=1.2,
        root_id=ROOT_ID,
    )
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(WorkspaceIOError, match="无履约方"):
        await channel.request(WorkspaceOp.READ, {"path": "never-back.txt"})
    elapsed = asyncio.get_running_loop().time() - t0
    assert 0.15 <= elapsed < 1.0
    # Settled, not hung: the sticky-dead streak is for liveness timeouts only.

async def test_delivered_op_stays_open_until_resolve():
    """Fulfill delivery keeps the Future open until resolve (rehang / desktop settle)."""
    from agentcore.runtime.events.client_tool_reattach import pending_client_tool_events
    from agentcore.runtime.interaction import default_interaction_registry

    registry = default_interaction_registry()
    for leftover in list(registry.list_pending(CONV)):
        registry.discard(leftover.id)

    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=5.0,
        root_id=ROOT_ID,
    )
    task = asyncio.create_task(
        channel.request(WorkspaceOp.READ, {"path": "after-detach.txt"})
    )
    event = await _await_request()
    assert not task.done()
    assert event.payload["op"] == "read"

    pending = [r for r in registry.list_pending(CONV) if not r.future.done()]
    assert len(pending) == 1
    request_id = pending[0].id

    # Registry can rebuild the EPHEMERAL frame for fulfiller rehang.
    rehung = pending_client_tool_events(CONV)
    assert any(
        e.type == EventType.WORKSPACE_OP_REQUIRED
        and e.payload.get("request_id") == request_id
        and e.payload.get("op") == "read"
        for e in rehung
    )

    assert registry.resolve(
        request_id,
        {"ok": True, "value": "from-reattach"},
        conversation_id=CONV,
    )
    assert await task == "from-reattach"

async def test_index_io_timeout_does_not_block_next_file_op():
    """An index-style read hang fails that op only — the next file op still delivers."""
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=0.05,
        root_id=ROOT_ID,
    )
    with pytest.raises(WorkspaceIOError, match="read.*活性挂起"):
        await channel.request(
            WorkspaceOp.READ,
            {"path": "logs/reviews/cases/CASE.md"},
        )

    while _CAPTURE:
        _CAPTURE.pop(0)

    task = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "a.txt"}))
    event = await _await_request()
    assert event.payload["op"] == "read"
    assert registry.resolve(
        event.payload["request_id"],
        {"ok": True, "value": "alive"},
        conversation_id=CONV,
    )
    assert await task == "alive"


async def test_index_maintainer_skips_when_channel_inflight(monkeypatch):
    """IndexMaintainer must not hard-charge ensure while Local channel is busy."""
    import contextlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import agentcore.workspace.indexing.maintainer as maint_mod
    from agentcore.workspace.indexing.maintainer import IndexMaintainer

    channel = SimpleNamespace(_inflight={"req-busy"})
    backend = SimpleNamespace(_channel=channel)
    manager = SimpleNamespace(
        set_building=lambda _v: None,
        ensure_index=AsyncMock(return_value=True),
    )
    maintainer = IndexMaintainer(manager, backend)  # type: ignore[arg-type]
    monkeypatch.setattr(maint_mod, "_CHANNEL_QUIET_WAIT_MAX_S", 0.15)
    try:
        maintainer.schedule()
        # Past one quiet-wait cap while inflight stays busy — ensure must not run.
        await asyncio.sleep(0.25)
        manager.ensure_index.assert_not_awaited()
        # Drain inflight so a coalesced follow-up can proceed.
        channel._inflight.clear()
        for _ in range(100):
            if manager.ensure_index.await_count >= 1:
                break
            await asyncio.sleep(0.02)
        manager.ensure_index.assert_awaited()
    finally:
        if maintainer._task is not None and not maintainer._task.done():  # noqa: SLF001
            maintainer._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maintainer._task


async def test_index_maintainer_waits_then_runs_when_channel_quiets():
    """When inflight drains within the quiet window, ensure_index proceeds."""
    import contextlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agentcore.workspace.indexing.maintainer import IndexMaintainer

    channel = SimpleNamespace(_inflight={"req-1"})
    backend = SimpleNamespace(_channel=channel)
    manager = SimpleNamespace(
        set_building=lambda _v: None,
        ensure_index=AsyncMock(return_value=True),
    )
    maintainer = IndexMaintainer(manager, backend)  # type: ignore[arg-type]
    maintainer.schedule()
    await asyncio.sleep(0.08)
    assert manager.ensure_index.await_count == 0
    channel._inflight.clear()
    for _ in range(100):
        if manager.ensure_index.await_count >= 1:
            break
        await asyncio.sleep(0.02)
    manager.ensure_index.assert_awaited()
    if maintainer._task is not None and not maintainer._task.done():  # noqa: SLF001
        maintainer._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintainer._task


async def test_local_mutation_defers_index_schedule_until_flush(tmp_path):
    """Local mutations mark dirty only; flush schedules + drains (no mid-turn create_task)."""
    import contextlib
    from unittest.mock import AsyncMock

    from agentcore.workspace.indexing.registry import (
        clear_index_registry,
        shared_index_maintainer_for_dir,
        shared_index_manager_for_dir,
    )

    clear_index_registry()
    try:
        local, registry = _make()
        idx = local._index_cache_dir()  # noqa: SLF001
        manager = shared_index_manager_for_dir(idx)
        maintainer = shared_index_maintainer_for_dir(idx, local)
        local._index_manager = manager  # noqa: SLF001
        local._index_maintainer = maintainer  # noqa: SLF001

        await _round_trip(
            local.write("out.txt", "hello"),
            registry,
            {"ok": True, "value": 5},
        )
        assert local.dirty is True
        assert manager.content_dirty is True
        assert maintainer.building is False
        assert maintainer._task is None  # noqa: SLF001 — mutation must not schedule

        manager.ensure_index = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await local.flush_code_index_maintenance()
        manager.ensure_index.assert_awaited()
        assert maintainer.building is False
        if maintainer._task is not None and not maintainer._task.done():  # noqa: SLF001
            maintainer._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maintainer._task
    finally:
        clear_index_registry()


async def test_local_start_code_index_maintenance_still_schedules(tmp_path):
    """Turn-start / code_search kick must still create_task immediately."""
    import contextlib
    from unittest.mock import AsyncMock

    from agentcore.workspace.indexing.registry import (
        clear_index_registry,
        shared_index_manager_for_dir,
    )

    clear_index_registry()
    try:
        local, _registry = _make()
        manager = shared_index_manager_for_dir(local._index_cache_dir())  # noqa: SLF001
        manager.ensure_index = AsyncMock(return_value=True)  # type: ignore[method-assign]
        local._index_manager = manager  # noqa: SLF001

        local.start_code_index_maintenance()
        maintainer = local._index_maintainer  # noqa: SLF001
        assert maintainer is not None
        assert maintainer.building or maintainer._task is not None  # noqa: SLF001
        await maintainer.drain()
        manager.ensure_index.assert_awaited()
        if maintainer._task is not None and not maintainer._task.done():  # noqa: SLF001
            maintainer._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maintainer._task
    finally:
        clear_index_registry()


async def test_local_flush_noops_when_clean():
    """flush is a no-op when nothing is dirty and no maintainer is running."""
    local, _registry = _make()
    await local.flush_code_index_maintenance()  # no manager / maintainer


async def test_server_mutation_still_schedules_index(tmp_path):
    """ServerWorkspace mid-mutation schedule goes through the shared maintainer."""
    import contextlib
    from unittest.mock import AsyncMock

    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.indexing.registry import (
        clear_index_registry,
        shared_index_manager_for_dir,
    )
    from agentcore.workspace.server import ServerWorkspace

    clear_index_registry()
    try:
        ws = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
        manager = shared_index_manager_for_dir(ws.index_dir)
        manager.ensure_index = AsyncMock(return_value=True)  # type: ignore[method-assign]

        await ws.write("x.py", "x = 1\n")
        maintainer = ws._index_maintainer  # noqa: SLF001
        assert maintainer is not None
        assert maintainer._task is not None  # noqa: SLF001 — write still schedules
        await maintainer.drain()
        manager.ensure_index.assert_awaited()
        if maintainer._task is not None and not maintainer._task.done():  # noqa: SLF001
            maintainer._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maintainer._task
    finally:
        clear_index_registry()


async def test_parallel_ops_one_timeout_does_not_fail_sibling():
    """Single settle timeout must not cancel same-channel inflight siblings."""
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=0.2,
        root_id=ROOT_ID,
    )
    t_a = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "a.txt"}))
    t_b = asyncio.create_task(
        channel.request(WorkspaceOp.READ, {"path": "b.txt"}, timeout=5.0)
    )
    events: dict[str, SSEEvent] = {}
    for _ in range(2):
        ev = await _await_request()
        events[ev.payload["args"]["path"]] = ev
    with pytest.raises(WorkspaceIOError, match=r"timed out（活性挂起）"):
        await t_a
    assert registry.resolve(
        events["b.txt"].payload["request_id"],
        {"ok": True, "value": "ok-b"},
        conversation_id=CONV,
    )
    assert await t_b == "ok-b"


async def test_parallel_ops_second_timeout_does_not_cancel_sibling():
    """A later hang does not fail-fast a sibling that still has budget."""
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=1.0,
        root_id=ROOT_ID,
    )
    with pytest.raises(WorkspaceIOError, match=r"timed out（活性挂起）"):
        await channel.request(WorkspaceOp.READ, {"path": "seed.txt"})
    while _CAPTURE:
        _CAPTURE.pop(0)

    t_a = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "a.txt"}))
    t_b = asyncio.create_task(
        channel.request(WorkspaceOp.READ, {"path": "b.txt"}, timeout=5.0)
    )
    events: dict[str, SSEEvent] = {}
    for _ in range(2):
        ev = await _await_request()
        events[ev.payload["args"]["path"]] = ev

    with pytest.raises(WorkspaceIOError, match=r"timed out（活性挂起）"):
        await t_a
    assert registry.resolve(
        events["b.txt"].payload["request_id"],
        {"ok": True, "value": "ok-b"},
        conversation_id=CONV,
    )
    assert await t_b == "ok-b"


async def test_channel_caps_concurrent_suspends():
    """At most max_inflight ops may be suspended; extras wait for a slot."""
    registry = InteractionRegistry()
    cap = 2
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=5.0,
        root_id=ROOT_ID,
        max_inflight=cap,
    )
    tasks = [
        asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": f"{i}.txt"}))
        for i in range(cap + 2)
    ]
    # Pump until the first wave emits; the overflow must not emit yet.
    events: list[SSEEvent] = []
    for _ in range(200):
        while _CAPTURE:
            events.append(_CAPTURE.pop(0))
        if len(events) >= cap:
            break
        await asyncio.sleep(0)
    assert len(events) == cap
    assert len(channel._inflight) == cap  # noqa: SLF001
    await asyncio.sleep(0)
    assert not _CAPTURE

    # Release one slot → a parked waiter suspends and emits.
    assert registry.resolve(
        events[0].payload["request_id"], {"ok": True, "value": "a"}, conversation_id=CONV
    )
    third = await _await_request()
    assert third.payload["args"]["path"] in {f"{i}.txt" for i in range(cap + 2)}

    # Settle every remaining suspended op (wave 1 leftover + newly admitted).
    to_settle = [events[1], third]
    for _ in range(100):
        while _CAPTURE:
            to_settle.append(_CAPTURE.pop(0))
        progressed = False
        for ev in list(to_settle):
            if registry.resolve(
                ev.payload["request_id"], {"ok": True, "value": "x"}, conversation_id=CONV
            ):
                to_settle.remove(ev)
                progressed = True
        if all(t.done() for t in tasks):
            break
        if not progressed:
            await asyncio.sleep(0)
    results = await asyncio.gather(*tasks)
    assert len(results) == cap + 2
    assert all(r in ("a", "x") for r in results)


async def test_queued_waiter_still_delivers_after_prior_timeout():
    """A queued op still gets a slot after a prior hang — timeouts do not fail-fast."""
    registry = InteractionRegistry()
    channel = WorkspaceChannel(
        user_id=USER,
        conversation_id=CONV,
        registry=registry,
        timeout_seconds=0.2,
        root_id=ROOT_ID,
        max_inflight=1,
    )
    t_hold = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "hold.txt"}))
    await _await_request()
    t_queued = asyncio.create_task(channel.request(WorkspaceOp.READ, {"path": "queued.txt"}))
    for _ in range(50):
        await asyncio.sleep(0)
    assert not _CAPTURE

    with pytest.raises(WorkspaceIOError, match=r"timed out（活性挂起）"):
        await t_hold
    event = await _await_request()
    assert event.payload["args"]["path"] == "queued.txt"
    assert registry.resolve(
        event.payload["request_id"],
        {"ok": True, "value": "queued-ok"},
        conversation_id=CONV,
    )
    assert await t_queued == "queued-ok"


# --- per-op transport deadline (执行门 timeout policy) ----------------------
#
# A code execution must NOT be cut off by the flat file-op deadline: its transport
# deadline is (the code's own timeout + slack), so the desktop's execution limit
# stays authoritative. File ops keep the flat channel deadline. We assert the exact
# deadline handed to asyncio.wait_for (spying on it inside the channel module).


def _spy_wait_for(monkeypatch) -> list[float]:
    """Record every timeout asyncio.wait_for is called with for an op.

    The create→emit→wait→discard suspend dance now lives in the unified
    InteractionRegistry (runtime.interaction), so the channel forwards its per-op
    deadline to ``registry.suspend`` which awaits there — patch that seam."""
    captured: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy(fut, timeout):  # noqa: ANN001 - duck-typed shim
        captured.append(timeout)
        return await real_wait_for(fut, timeout)

    monkeypatch.setattr("agentcore.runtime.interaction.asyncio.wait_for", spy)
    return captured


async def test_file_op_uses_flat_transport_deadline(monkeypatch):
    captured = _spy_wait_for(monkeypatch)
    local, registry = _make(timeout=30.0, execute_slack=15.0)
    await _round_trip(local.read("a.txt"), registry, {"ok": True, "value": "x"})
    # A read rides the channel-wide deadline, untouched by the execute slack.
    assert captured[-1] == 30.0


async def test_execute_extends_transport_deadline_past_code_timeout(monkeypatch):
    captured = _spy_wait_for(monkeypatch)
    local, registry = _make(timeout=30.0, execute_slack=15.0)
    req = ExecutionRequest(code="print(1)", language="python", timeout_seconds=10)
    response = {
        "ok": True,
        "value": {
            "success": True,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 1,
        },
    }
    await _round_trip_execute(local.execute(req), registry, response)
    # Real run 10+15=25 (not flat 30s). No 5s preflight.
    assert captured[-1] == 25.0


# --- registry guards (defense in depth on the resolve endpoint) ------------


async def test_registry_refuses_unknown_and_double_and_wrong_conversation():
    registry = InteractionRegistry()
    fut = registry.create("req-1", CONV, kind=InteractionKind.CLIENT_TOOL)

    assert registry.resolve("nope", {"ok": True}, conversation_id=CONV) is False
    assert registry.resolve("req-1", {"ok": True}, conversation_id="other") is False
    assert fut.done() is False  # wrong conversation must not settle it

    assert registry.resolve("req-1", {"ok": True, "value": 1}, conversation_id=CONV) is True
    assert registry.resolve("req-1", {"ok": True}, conversation_id=CONV) is False  # double
    assert (await fut)["value"] == 1
