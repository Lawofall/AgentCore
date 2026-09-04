"""Stop 的取消路径：拆卸不被二次取消掐断，且「用户停止」先于取消成立。

Two shapes are locked here, both invisible on the success path:

- a turn's teardown ``finally`` must survive a second ``CancelledError``
  (「Stop 点两下」/「Stop 后立刻发新消息」) — ``suppress(Exception)`` never caught it,
  so journal flush / audit flush / coordination release / ``llm.close()`` were skipped;
- ``user_stopped`` must be true BEFORE the stop endpoint orphans hot pending cards —
  that pass awaits the DB and cancels Futures, so the turn can unwind inside it and
  read ``is_clean_cancel`` (orphan lease + sweeper re-drive instead of「已停止」).

No DB, no HTTP transport — plain async tests.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentcore.runtime.events import EventSink
from agentcore.runtime.pipeline.teardown import teardown_step
from agentcore.runtime.turn.runs import TurnRunRegistry


async def _never() -> None:
    await asyncio.Event().wait()


# --- teardown_step: 二次取消不得跳过后续拆卸 --------------------------------


async def test_teardown_runs_every_step_when_a_second_cancel_lands_mid_flush():
    """A cancel delivered while a teardown step is in flight must not skip the tail."""
    done: list[str] = []
    flushing = asyncio.Event()
    release = asyncio.Event()

    async def _journal_flush() -> None:
        flushing.set()
        await release.wait()
        done.append("journal")

    async def _audit_flush() -> None:
        await asyncio.sleep(0)
        done.append("audit")

    async def _llm_close() -> None:
        await asyncio.sleep(0)
        done.append("llm_close")

    async def _turn() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            await teardown_step(_journal_flush(), step="journal_flush")
            await teardown_step(_audit_flush(), step="audit_flush")
            await teardown_step(_llm_close(), step="llm_close")

    task = asyncio.create_task(_turn())
    await asyncio.sleep(0)
    task.cancel()  # first Stop → unwinds into the teardown block
    await asyncio.wait_for(flushing.wait(), timeout=1.0)

    task.cancel()  # second Stop lands on the shielded flush, not on the work
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert done == ["journal", "audit", "llm_close"]


async def test_teardown_step_failure_does_not_stop_the_next_step():
    done: list[str] = []

    async def _boom() -> None:
        raise RuntimeError("flush failed")

    async def _ok() -> None:
        done.append("ok")

    await teardown_step(_boom(), step="journal_flush")
    await teardown_step(_ok(), step="audit_flush")
    assert done == ["ok"]


# --- stop 幂等：不向正在拆卸的回合再投一次取消 ------------------------------


async def test_second_stop_does_not_redeliver_cancel_into_teardown():
    """`task.done()` is False while the finally runs — a re-click must not pierce it."""
    reg = TurnRunRegistry()
    done: list[str] = []
    unwinding = asyncio.Event()
    release = asyncio.Event()

    async def _turn() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            unwinding.set()
            # Deliberately unshielded: a re-delivered cancel would land here.
            await release.wait()
            done.append("released")

    task = asyncio.create_task(_turn())
    reg.register(conversation_id="c1", task=task, sink=EventSink())
    await asyncio.sleep(0)

    assert reg.stop("c1") is True
    await asyncio.wait_for(unwinding.wait(), timeout=1.0)
    # Still reported as stopped (idempotent endpoint) without a second cancel.
    assert reg.stop("c1") is True
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert done == ["released"]


async def test_overlap_after_stop_does_not_recancel_the_unwinding_run():
    """「Stop 后立刻发新消息」: the new run takes the slot; the old one keeps unwinding."""
    reg = TurnRunRegistry()
    done: list[str] = []
    unwinding = asyncio.Event()
    release = asyncio.Event()

    async def _turn() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            unwinding.set()
            await release.wait()
            done.append("released")

    old = asyncio.create_task(_turn())
    new = asyncio.create_task(_never())
    try:
        reg.register(conversation_id="c1", task=old, sink=EventSink())
        await asyncio.sleep(0)
        assert reg.stop("c1") is True
        await asyncio.wait_for(unwinding.wait(), timeout=1.0)

        reg.register(conversation_id="c1", task=new, sink=EventSink())
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await old
        assert done == ["released"]
        assert reg.get("c1").task is new
    finally:
        new.cancel()


# --- 停止事实先于取消成立（并发热路 pending） -------------------------------


async def _allow_owner(*_args, **_kwargs) -> None:
    return None


async def test_stop_marks_user_stop_before_orphaning_pending_cards(monkeypatch):
    """≥2 hot pending: the awaiter unwinds mid-orphan and must still read clean cancel."""
    from agentcore.api.routes.conversations import messages as messages_route
    from agentcore.runtime.interaction import (
        InteractionKind,
        default_interaction_registry,
    )
    from agentcore.runtime.turn.runs import turn_runs

    cid = "c-stop-order"
    registry = default_interaction_registry()

    async def fake_emit(**_kwargs) -> None:
        # Stands in for the orphan fact's real DB write — the await that lets the
        # first cancelled card's awaiter unwind while the pass is still running.
        await asyncio.sleep(0)

    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.emit_orphan_fact", fake_emit
    )
    monkeypatch.setattr(
        messages_route, "_require_conversation_write", _allow_owner
    )

    first = registry.create(
        "stop-order-1", cid, kind=InteractionKind.APPROVAL, payload={"tool_name": "read"}
    )
    registry.create(
        "stop-order-2", cid, kind=InteractionKind.APPROVAL, payload={"tool_name": "write"}
    )
    saw: dict[str, bool] = {}

    async def _turn() -> None:
        try:
            await first
        except asyncio.CancelledError:
            saw["clean_cancel"] = turn_runs.is_clean_cancel(cid)
            raise

    task = asyncio.create_task(_turn())
    turn_runs.register(
        conversation_id=cid, task=task, sink=EventSink(message_id="m-live")
    )
    await asyncio.sleep(0)

    try:
        await messages_route.stop_message(
            cid, SimpleNamespace(user_id="u1"), SimpleNamespace(_session=None)
        )
        with pytest.raises(asyncio.CancelledError):
            await task
        # Without the pre-mark the salvage would orphan the lease (sweeper re-drive)
        # and the bubble would read「中断」instead of「已停止」.
        assert saw.get("clean_cancel") is True
    finally:
        registry.discard("stop-order-1")
        registry.discard("stop-order-2")
        if not task.done():
            task.cancel()
        await asyncio.sleep(0)
