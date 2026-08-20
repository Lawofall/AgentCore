"""BrowserTakeoverService (M2 · D8/D17): anytime start, mutex, 留档 paths.

Drives the service + a real ``BrowserSessionRegistry`` with fake sessions and a fake store
(no gVisor, no DB), asserting: start preconditions (already_active / no_session), D8 anytime
takeover (no turn_running gate), tool ``user_in_control`` while taken over, and record
finalization on EVERY teardown path.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from agentcore.runtime.browser.registry import BrowserSessionRegistry, TakeoverMark
from agentcore.runtime.browser.takeover import BrowserTakeoverService
from agentcore.tools.builtin.browser import BrowserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.browser.protocol import (
    BrowserCommand,
    BrowserCommandResult,
    BrowserSessionRequest,
)


class FakeBrowserSession:
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.created_at = time.time()
        self.last_used = time.time()
        self._alive = True
        self.closed = False
        self.sends: list[str] = []

    @property
    def alive(self) -> bool:
        return self._alive

    async def send(self, command: BrowserCommand) -> BrowserCommandResult:
        self.sends.append(command.action)
        self.last_used = time.time()
        return BrowserCommandResult(ok=True, data={"injected": 1})

    async def close(self) -> None:
        self.closed = True
        self._alive = False


class FakeStore:
    """In-memory takeover store: records episodes + idempotent finalize (no DB)."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.finalized: list[tuple[str, str]] = []
        self._n = 0

    async def create(
        self,
        *,
        conversation_id: str,
        user_id: str,
        session_id: str | None = None,
    ) -> tuple[str, datetime]:
        self._n += 1
        rid = f"rec{self._n}"
        ts = datetime.now(UTC)
        self.records[rid] = {
            "user_id": user_id,
            "session_id": session_id,
            "started_at": ts,
            "ended_at": None,
        }
        return rid, ts

    async def finalize(self, *, record_id: str, reason: str) -> bool:
        self.finalized.append((record_id, reason))
        rec = self.records.get(record_id)
        if rec is not None and rec["ended_at"] is None:
            rec["ended_at"] = datetime.now(UTC)
            return True
        return False


def _make_registry(**kw):
    kw.setdefault("max_sessions", 8)
    kw.setdefault("idle_ttl_seconds", 1000)
    kw.setdefault("max_lifetime_seconds", 100000)

    async def factory(request: BrowserSessionRequest) -> FakeBrowserSession:
        return FakeBrowserSession(request.conversation_id)

    return BrowserSessionRegistry(factory=factory, **kw)


def _service(
    reg, store, *, running: bool = False, browser_login_pending: bool = False
) -> BrowserTakeoverService:
    return BrowserTakeoverService(
        registry=reg,
        store=store,
        has_running_turn=lambda _cid: running,
        has_browser_login_pending=lambda _cid: browser_login_pending,
    )


def _req(cid: str) -> BrowserSessionRequest:
    return BrowserSessionRequest(conversation_id=cid)


# -- start preconditions -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_succeeds_with_session_and_no_turn():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    await reg.acquire(_req("c1"))
    res = await svc.start("c1", "u1")
    assert res.active and res.reason == "started" and res.record_id == "rec1"
    assert res.session_id
    assert reg.is_taken_over("c1")
    assert len(store.records) == 1
    assert store.records["rec1"]["session_id"] == res.session_id


@pytest.mark.asyncio
async def test_start_persists_explicit_session_id_on_record():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    _, _, sid_a = await reg.create(_req("c1"), activate=True)
    _, _, sid_b = await reg.create(_req("c1"), activate=False)
    res = await svc.start("c1", "u1", session_id=sid_b)
    assert res.active and res.session_id == sid_b
    assert store.records["rec1"]["session_id"] == sid_b
    assert store.records["rec1"]["session_id"] != sid_a


@pytest.mark.asyncio
async def test_start_no_session_when_none_live():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    res = await svc.start("ghost", "u1")
    assert not res.active and res.reason == "no_session"
    assert store.records == {}  # no record created on a precondition failure
    assert not reg.is_taken_over("ghost")


@pytest.mark.asyncio
async def test_start_allowed_while_turn_running_d8():
    """D8: user may take over anytime — turn_running no longer blocks start."""
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store, running=True)
    await reg.acquire(_req("c1"))
    res = await svc.start("c1", "u1")
    assert res.active and res.reason == "started" and res.record_id == "rec1"
    assert reg.is_taken_over("c1")


@pytest.mark.asyncio
async def test_start_already_active_is_distinguished_and_idempotent():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    await reg.acquire(_req("c1"))
    first = await svc.start("c1", "u1")
    second = await svc.start("c1", "u2")
    assert first.reason == "started"
    assert second.reason == "already_active" and second.active
    assert second.record_id == first.record_id  # same episode, not a new one
    assert len(store.records) == 1


@pytest.mark.asyncio
async def test_start_targets_explicit_session_id():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    _, _, sid_a = await reg.create(_req("c1"), activate=True)
    _, _, sid_b = await reg.create(_req("c1"), activate=False)
    res = await svc.start("c1", "u1", session_id=sid_b)
    assert res.active and res.session_id == sid_b
    assert reg.is_taken_over("c1", session_id=sid_b)
    assert not reg.is_taken_over("c1", session_id=sid_a)


# -- end (explicit) ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_finalizes_and_clears_mark():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    res = await svc.end("c1")
    assert not res.active and res.reason == "ended"
    assert not reg.is_taken_over("c1")
    assert store.finalized == [("rec1", "user_end")]


@pytest.mark.asyncio
async def test_end_when_not_active_is_idempotent():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    res = await svc.end("c1")
    assert not res.active and res.reason == "not_active"
    assert store.finalized == []


@pytest.mark.asyncio
async def test_explicit_end_then_drop_does_not_double_finalize():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    await svc.end("c1")
    await reg.close("c1")  # session torn down after an explicit end
    # Only the explicit end finalized it; the drop found no mark.
    assert store.finalized == [("rec1", "user_end")]


# -- 留档 on every teardown path (D17) ---------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_on_conversation_close_delete():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    await reg.close("c1")  # conversation delete cascade
    assert store.finalized == [("rec1", "closed")]
    assert store.records["rec1"]["ended_at"] is not None


@pytest.mark.asyncio
async def test_finalize_on_idle_reap():
    reg = _make_registry(idle_ttl_seconds=100, max_lifetime_seconds=100000)
    store = FakeStore()
    svc = _service(reg, store)
    session, _ = await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    session.last_used = time.time() - 500  # idle past the TTL
    assert await reg.reap() == 1
    assert store.finalized == [("rec1", "reaped")]


@pytest.mark.asyncio
async def test_finalize_on_max_lifetime_recycle():
    reg = _make_registry(idle_ttl_seconds=100000, max_lifetime_seconds=100)
    store = FakeStore()
    svc = _service(reg, store)
    session, _ = await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    session.created_at = time.time() - 500  # aged past max lifetime even while active
    assert await reg.reap() == 1
    assert store.finalized == [("rec1", "reaped")]


@pytest.mark.asyncio
async def test_finalize_on_driver_crash_reap():
    reg = _make_registry()
    store = FakeStore()
    svc = _service(reg, store)
    session, _ = await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    session._alive = False  # driver crashed
    assert await reg.reap() == 1
    assert store.finalized == [("rec1", "reaped")]


@pytest.mark.asyncio
async def test_finalize_on_shutdown_close_all():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    await reg.close_all()  # lifespan shutdown
    assert store.finalized == [("rec1", "shutdown")]


# -- is_active + tool user_in_control (mutex) --------------------------------------------


@pytest.mark.asyncio
async def test_is_active_reflects_registry_mark():
    reg, store = _make_registry(), FakeStore()
    svc = _service(reg, store)
    assert not svc.is_active("c1")
    await reg.acquire(_req("c1"))
    await svc.start("c1", "u1")
    assert svc.is_active("c1")


@pytest.mark.asyncio
async def test_browser_tool_returns_user_in_control_during_takeover():
    reg = _make_registry()
    session, _ = await reg.acquire(_req("c1"))
    reg.begin_takeover(
        "c1", TakeoverMark(record_id="rec1", user_id="u1", started_at=datetime.now(UTC))
    )
    tool = BrowserTool(registry=reg)
    ctx = ToolContext.create(
        execution_id="",
        run_id="r1",
        agent_id="",
        # Navigate 先做接管互斥，再探 host_kind；接管路径不碰 session / backend.location
        backend=object(),
        user_id="u1",
        conversation_id="c1",
    )
    result = await tool.execute({"action": "navigate", "url": "https://example.com/"}, ctx)
    assert result.success is False
    # Failures put the message in ``error`` only (avoid output+error double).
    assert "接管" in (result.error or result.output or "")
    assert (result.metadata or {}).get("code") == "user_in_control"
    assert session.sends == []  # the tool never touched the session (no queue/wait)
