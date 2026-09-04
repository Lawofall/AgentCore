"""云对话多端同权 B2 · 验收 5：排队 / 取消排队 / deferred 在所有订阅端可见。

这三类短暂态描述的是**对话**的状态（队列里有什么、槽位归谁），却一直搭在**回合**的
通道上，于是只有发起端看得见：经典 FIFO 的 ``turn_queued`` 由 POST 自己的 generator 直
出、``resume_deferred`` 同样直出、``turn_queue_cancelled`` 只在此刻恰好有 live run 时才
发（队列比宿主回合活得久，空档里取消 = 对端零信号）。

这里锁住新的对话级信号道：**每端一份、不重复、无 live run 也送到**。仍是「变了」信号——
内容权威还是 ``GET …/queued-turns``，队列本身仍在进程内（不做 durable queue）。

No DB, no HTTP — plain async tests (asyncio_mode=auto).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcore.api import sse
from agentcore.api.routes.conversations import messages as messages_mod
from agentcore.api.routes.conversations import turns as turns_mod
from agentcore.runtime.events import EventSink, EventType, content_delta
from agentcore.runtime.events import conversation_hub as hub_mod
from agentcore.runtime.events.conversation_hub import (
    _SIGNAL_QUEUE_MAXSIZE,
    ConversationStreamHub,
    ConversationWatcher,
)
from agentcore.runtime.turn import steer as steer_mod
from agentcore.runtime.turn.queue import new_queued_turn, turn_queue
from agentcore.runtime.turn.runs import turn_runs


async def _never() -> None:
    await asyncio.Future()


@pytest.fixture
def hub(monkeypatch) -> ConversationStreamHub:
    """A private hub in both seams that reach it (producers + the SSE generator)."""
    fresh = ConversationStreamHub()
    monkeypatch.setattr(hub_mod, "conversation_streams", fresh)
    monkeypatch.setattr(sse, "conversation_streams", fresh)
    return fresh


def _signals(watcher: ConversationWatcher) -> list[EventType]:
    return [event.type for event in watcher.drain_signals()]


def _sink_types(sink: EventSink) -> list[EventType]:
    return [event.type for event in sink._history]  # noqa: SLF001


# --- 信号道本身：扇出、去重、有界 ---------------------------------------------


async def test_signal_reaches_every_watcher_of_that_conversation(hub):
    from agentcore.runtime.events import turn_queue_cancelled

    a = hub.watch("c-fan")
    b = hub.watch("c-fan")
    elsewhere = hub.watch("c-other")

    delivered = hub.publish_signal(
        "c-fan", turn_queue_cancelled(queue_id="q1", conversation_id="c-fan")
    )

    assert delivered == 2
    assert _signals(a) == [EventType.TURN_QUEUE_CANCELLED]
    assert _signals(b) == [EventType.TURN_QUEUE_CANCELLED]
    assert _signals(elsewhere) == []


async def test_watcher_tailing_the_emitting_sink_is_skipped(hub):
    """同一条连接不得折两遍：正跟播该 sink 的端从 sink 收帧，信号道就跳过它。"""
    from agentcore.runtime.events import turn_queued

    sink = EventSink()
    tailing = hub.watch("c-dedup")
    tailing.mark_tailing(sink)
    idle = hub.watch("c-dedup")

    delivered = hub.publish_signal(
        "c-dedup",
        turn_queued(queue_id="q1", position=1, queue_depth=1, conversation_id="c-dedup"),
        already_on_sink=sink,
    )

    assert delivered == 1
    assert _signals(tailing) == []
    assert _signals(idle) == [EventType.TURN_QUEUED]

    # 回合收口后同一端回到空闲态，就该重新收信号了。
    tailing.mark_tailing(None)
    hub.publish_signal(
        "c-dedup",
        turn_queued(queue_id="q2", position=1, queue_depth=1, conversation_id="c-dedup"),
        already_on_sink=sink,
    )
    assert _signals(tailing) == [EventType.TURN_QUEUED]


async def test_signal_backlog_is_bounded_and_sheds_the_oldest(hub):
    """慢端只惩罚自己（§6.1）：满则丢最旧，最新的信号一定留着。"""
    from agentcore.runtime.events import turn_queued

    watcher = hub.watch("c-flood")
    overflow = _SIGNAL_QUEUE_MAXSIZE + 3
    for i in range(overflow):
        hub.publish_signal(
            "c-flood",
            turn_queued(
                queue_id=f"q{i}", position=1, queue_depth=1, conversation_id="c-flood"
            ),
        )

    pending = watcher.drain_signals()
    assert len(pending) == _SIGNAL_QUEUE_MAXSIZE
    assert pending[0].payload["queue_id"] == "q3"
    assert pending[-1].payload["queue_id"] == f"q{overflow - 1}"


# --- 入队：经典 FIFO 与升队两条路各自的腿 --------------------------------------


async def test_classic_fifo_enqueue_signals_watchers_without_touching_the_live_sink(hub):
    """经典 FIFO：对端靠信号道看见；live sink 不发——发起端自己的 POST 流已经有这一帧，
    而它多半正开着那条 live 回合流，再发一遍就是同端重复。"""
    cid = "c-classic-queued"
    turn_queue.clear(cid)
    watcher = hub.watch(cid)
    host_sink = EventSink()
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=host_sink)
    try:
        status = turn_queue.enqueue_and_ensure_drain(
            cid, new_queued_turn(content="排到下一轮", user_id="u")
        )

        pending = watcher.drain_signals()
        assert [e.type for e in pending] == [EventType.TURN_QUEUED]
        assert pending[0].payload["queue_id"] == status.queue_id
        assert pending[0].payload["position"] == 1
        assert EventType.TURN_QUEUED not in _sink_types(host_sink)
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


async def test_promoted_enqueue_takes_the_live_sink_leg_without_doubling(hub):
    """协调升队：发起端只剩 live 回合流可看 → 走 sink；跟播该 sink 的端不再收信号道副本。"""
    cid = "c-promoted-queued"
    turn_queue.clear(cid)
    host_sink = EventSink()
    tailing = hub.watch(cid)
    tailing.mark_tailing(host_sink)
    idle = hub.watch(cid)
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=host_sink)
    try:
        turn_queue.enqueue_and_ensure_drain(
            cid,
            new_queued_turn(content="来自插话", user_id="u"),
            on_live_sink=True,
        )

        assert EventType.TURN_QUEUED in _sink_types(host_sink)
        assert _signals(tailing) == []
        assert _signals(idle) == [EventType.TURN_QUEUED]
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


async def test_enqueue_signals_watchers_even_with_no_live_run(hub):
    """宿主刚好收口的空档里入队：没有 sink 可发，对端仍须知道队列变了。"""
    cid = "c-queued-no-run"
    turn_queue.clear(cid)
    watcher = hub.watch(cid)
    try:
        assert turn_runs.get(cid) is None
        turn_queue.enqueue_and_ensure_drain(
            cid, new_queued_turn(content="孤儿排队", user_id="u")
        )
        assert _signals(watcher) == [EventType.TURN_QUEUED]
    finally:
        turn_queue.clear(cid)


async def test_steer_leftover_keeps_dual_emit_order_and_signals_watchers(hub):
    """经典 steer 收口降级：sink 上 ``user_interjection(queued)`` 仍先于 ``turn_queued``
    （契约次序），对端则从信号道拿到同一帧的降级标注。"""
    steer_mod._reset_for_tests()  # noqa: SLF001
    cid = "c-leftover-signal"
    turn_queue.clear(cid)
    host_sink = EventSink()
    watcher = hub.watch(cid)
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=host_sink)
    try:
        steer_mod.begin_accepting(cid, execution_id="exec-leftover")
        assert steer_mod.try_enqueue(conversation_id=cid, content="晚到的纠偏") is not None
        assert steer_mod.promote_leftovers_to_queue(steer_mod.end_accepting(cid)) == 1

        ordered = [
            e.type
            for e in host_sink._history  # noqa: SLF001
            if e.type in (EventType.USER_INTERJECTION, EventType.TURN_QUEUED)
        ]
        assert ordered == [EventType.USER_INTERJECTION, EventType.TURN_QUEUED]

        pending = watcher.drain_signals()
        assert [e.type for e in pending] == [EventType.TURN_QUEUED]
        assert pending[0].payload["degraded_from"] == "steer"
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)
        steer_mod._reset_for_tests()  # noqa: SLF001


# --- 取消排队：这是今天最黑的那个洞（无 live run 即静默） ----------------------


async def _cancel(cid: str, queue_id: str) -> None:
    await messages_mod.cancel_queued_turn(
        conversation_id=cid,
        queue_id=queue_id,
        user=SimpleNamespace(user_id="u1"),
        conv_repo=SimpleNamespace(_session=None),
    )


@pytest.fixture
def owned_conversation(monkeypatch):
    monkeypatch.setattr(messages_mod, "_require_conversation_write", AsyncMock())


async def test_cancel_with_no_live_run_still_reaches_watchers(hub, owned_conversation):
    """队列比宿主回合活得久：drain 未起 / deferred 占位时取消，过去对端什么也收不到。"""
    cid = "c-cancel-no-run"
    turn_queue.clear(cid)
    watcher = hub.watch(cid)
    try:
        item = new_queued_turn(content="撤回它", user_id="u")
        turn_queue.enqueue(cid, item)
        assert turn_runs.get(cid) is None

        await _cancel(cid, item.queue_id)

        pending = watcher.drain_signals()
        assert [e.type for e in pending] == [EventType.TURN_QUEUE_CANCELLED]
        assert pending[0].payload["queue_id"] == item.queue_id
        assert turn_queue.depth(cid) == 0
    finally:
        turn_queue.clear(cid)


async def test_cancel_with_live_run_reaches_each_connection_once(hub, owned_conversation):
    cid = "c-cancel-live"
    turn_queue.clear(cid)
    host_sink = EventSink()
    tailing = hub.watch(cid)
    tailing.mark_tailing(host_sink)
    idle = hub.watch(cid)
    blocker = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=blocker, sink=host_sink)
    try:
        item = new_queued_turn(content="撤回它", user_id="u")
        turn_queue.enqueue(cid, item)

        await _cancel(cid, item.queue_id)

        assert _sink_types(host_sink).count(EventType.TURN_QUEUE_CANCELLED) == 1
        assert _signals(tailing) == []
        assert _signals(idle) == [EventType.TURN_QUEUE_CANCELLED]
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
        turn_queue.clear(cid)


async def test_cancel_of_unknown_queue_id_signals_nobody(hub, owned_conversation):
    cid = "c-cancel-404"
    turn_queue.clear(cid)
    watcher = hub.watch(cid)
    from agentcore.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await _cancel(cid, "q-never-existed")
    assert _signals(watcher) == []


# --- 冷卡「继续」撞上忙槽（deferred） -----------------------------------------


@pytest.fixture
def resume_route(monkeypatch):
    """Route deps stubbed down to the deferred decision: no DB, no billing, no claim."""
    monkeypatch.setattr(turns_mod, "enforce_user_message_rate_limit", AsyncMock())
    monkeypatch.setattr(
        turns_mod,
        "_preflight_owned_chat_turn",
        AsyncMock(return_value=SimpleNamespace(credentials=None, supports_tools=True, warnings=[])),
    )
    monkeypatch.setattr(turns_mod, "release_request_db_before_sse", AsyncMock())
    monkeypatch.setattr(turns_mod, "load_paused_turn", AsyncMock(return_value=object()))
    monkeypatch.setattr(turns_mod, "prewrite_cold_resume_settlement", AsyncMock())


async def _resume(cid: str, message_id: str):
    from agentcore.api.schemas import ResumeTurnRequest
    from agentcore.runtime.checkpoints import CheckpointDecision

    return await turns_mod.resume_message(
        conversation_id=cid,
        message_id=message_id,
        body=ResumeTurnRequest(decision=CheckpointDecision.CONTINUE),
        user=SimpleNamespace(user_id="u1"),
        session=None,
        x_client_platform=None,
    )


async def test_resume_deferred_reaches_watchers(hub, resume_route):
    """A 点「继续」但槽位忙：B 端那张同样的冷卡也必须看到「放行已记下」。"""
    cid = "c-resume-deferred"
    turn_queue.clear(cid)
    turn_runs._resume_deferred.pop(cid, None)  # noqa: SLF001
    watcher = hub.watch(cid)
    host = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=host, sink=EventSink(message_id="live-other"))
    response = None
    try:
        response = await _resume(cid, "paused-1")

        pending = watcher.drain_signals()
        assert [e.type for e in pending] == [EventType.RESUME_DEFERRED]
        assert pending[0].payload == {
            "message_id": "paused-1",
            "conversation_id": cid,
            "busy_reason": "live_turn",
        }
        # 发起端这条 SSE 自带同一帧——两条连接各一份，谁都不重复。
        first = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=1.0)
        assert "resume_deferred" in first
    finally:
        if response is not None:
            await response.body_iterator.aclose()
        host.cancel()
        with pytest.raises(asyncio.CancelledError):
            await host
        turn_runs._resume_deferred.pop(cid, None)  # noqa: SLF001
        turn_queue.clear(cid)


async def test_repeat_submit_of_the_same_card_does_not_resignal(hub, resume_route):
    """同卡重复提交是幂等 join：对端状态没变，别再戳一次。"""
    cid = "c-resume-rejoin"
    turn_queue.clear(cid)
    turn_runs._resume_deferred.pop(cid, None)  # noqa: SLF001
    watcher = hub.watch(cid)
    host = asyncio.create_task(_never())
    turn_runs.register(conversation_id=cid, task=host, sink=EventSink(message_id="live-other"))
    responses = []
    try:
        responses.append(await _resume(cid, "paused-1"))
        assert _signals(watcher) == [EventType.RESUME_DEFERRED]

        responses.append(await _resume(cid, "paused-1"))
        assert _signals(watcher) == []
    finally:
        for response in responses:
            await response.body_iterator.aclose()
        host.cancel()
        with pytest.raises(asyncio.CancelledError):
            await host
        turn_runs._resume_deferred.pop(cid, None)  # noqa: SLF001
        turn_queue.clear(cid)


# --- 端到端：信号真的从对话级 SSE 流出来 --------------------------------------


async def test_conversation_stream_emits_signals_while_idle(hub, monkeypatch):
    """停在空闲对话上的 B 端：另一端排队 → 这条流直接吐出 ``turn_queued``，而不是干等心跳。"""
    from agentcore.runtime.events import turn_queued

    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.01)
    watcher = hub.watch("c-idle-signal")
    gen = sse._conversation_generator(watcher)  # noqa: SLF001
    try:
        assert (await asyncio.wait_for(gen.__anext__(), timeout=1.0)).startswith(":")

        hub.publish_signal(
            "c-idle-signal",
            turn_queued(
                queue_id="q1", position=1, queue_depth=1, conversation_id="c-idle-signal"
            ),
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert "turn_queued" in frame
        assert '"queue_id": "q1"' in frame

        # 信号不吃掉后续回合：新回合照常重放 + 跟播。
        sink = EventSink()
        hub.publish_run("c-idle-signal", sink)
        assert await asyncio.wait_for(gen.__anext__(), timeout=1.0) == sse._ATTACH_CAUGHT_UP  # noqa: SLF001
        sink.close()
    finally:
        await gen.aclose()


async def test_conversation_stream_emits_signals_while_tailing_a_run(hub, monkeypatch):
    """B 端正跟播回合 1 时 A 端取消了排队项：信号与回合帧同流交错，不必等回合收口。"""
    from agentcore.runtime.events import turn_queue_cancelled

    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.01)
    watcher = hub.watch("c-live-signal")
    sink = EventSink()
    gen = sse._conversation_generator(watcher, initial_sink=sink)  # noqa: SLF001
    try:
        assert await asyncio.wait_for(gen.__anext__(), timeout=1.0) == sse._ATTACH_CAUGHT_UP  # noqa: SLF001
        assert watcher.tailed_sink is sink

        hub.publish_signal(
            "c-live-signal",
            turn_queue_cancelled(queue_id="q1", conversation_id="c-live-signal"),
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert "turn_queue_cancelled" in frame

        sink.emit(content_delta("回合还在跑"))
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert "回合还在跑" in frame
    finally:
        await gen.aclose()
        sink.close()
    assert watcher.tailed_sink is None  # 断开即松开去重键，不误噎下一条信号
