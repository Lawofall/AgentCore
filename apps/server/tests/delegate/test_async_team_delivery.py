"""批次 1：异步团队产出投递 — 四支柱回归。"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    active_coordination,
    active_coordination_for_conversation,
    adopt_active_execution,
    bind_host_journal,
    clear_active_coordination,
    current_execution_id,
    emit_execution_detached,
    finish_detached_coordination,
    release_turn_coordination,
    set_active_coordination,
)
from agentcore.runtime.coordination.wait import await_coordination_injection
from agentcore.runtime.events import EventSink, EventType, execution_completed
from agentcore.runtime.events.types import SSEEvent
from agentcore.runtime.facts import Fact, TurnFactLog, current_fact_log
from agentcore.runtime.journal.fold import _splice_synthetic_deltas


@pytest.fixture(autouse=True)
def _clean_coordination():
    clear_active_coordination()
    yield
    clear_active_coordination()


class _RecordingWriter:
    """Minimal journal writer stand-in for closed-sink DURABLE persistence."""

    def __init__(self, turn_id: str = "host-turn") -> None:
        self.turn_id = turn_id
        self.sealed = False
        self.entries: list[dict] = []

    def schedule_append(self, entry: dict):
        self.entries.append(entry)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[int | None] = loop.create_future()
        fut.set_result(len(self.entries))
        return fut

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_pillar_a_closed_sink_persists_run_completed_via_host_writer():
    """支柱 A：sink 关闭后 DURABLE 仍经 execution host writer 落盘。"""
    writer = _RecordingWriter()
    session = CoordinationSession(
        execution_id="exec-a",
        total_workers=1,
        conversation_id="conv-a",
    )
    bind_host_journal(session, writer=writer, turn_id="host-turn")
    set_active_coordination(session)

    sink = EventSink(conversation_id="conv-a", message_id="host-turn")
    sink.close()

    sink.emit(
        SSEEvent(
            type=EventType.RUN_COMPLETED,
            payload={
                "run_id": "r1",
                "agent_id": "w1",
                "output_summary": "队员正文",
                "duration_ms": 10,
                "role": "member",
                "model": "test",
                "usage": {"input": 1, "output": 1, "total": 2},
                "cost": {
                    "input": 0,
                    "cached": 0,
                    "output": 0,
                    "total": 0,
                    "currency": "USD",
                },
                "execution_id": "exec-a",
            },
        )
    )

    kinds = [e.get("kind") for e in writer.entries]
    assert EventType.RUN_COMPLETED.value in kinds


@pytest.mark.asyncio
async def test_pillar_a_closed_sink_persists_without_payload_execution_id():
    """支柱 A：payload 无 execution_id + ContextVar 清空后，经 conversation 注册表落盘。"""
    from agentcore.runtime.coordination.session import current_execution_id
    from agentcore.runtime.journal.writer import current_journal_writer

    writer = _RecordingWriter()
    session = CoordinationSession(
        execution_id="exec-a-fallback",
        total_workers=1,
        conversation_id="conv-a-fallback",
    )
    bind_host_journal(session, writer=writer, turn_id="host-turn")
    set_active_coordination(session)

    # Simulate turn teardown: journal writer + execution ContextVars reset, but
    # conversation→execution registry still holds the live session.
    jw_token = current_journal_writer.set(None)
    eid_token = current_execution_id.set(None)
    try:
        sink = EventSink(conversation_id="conv-a-fallback", message_id="host-turn")
        sink.close()
        sink.emit(
            SSEEvent(
                type=EventType.RUN_COMPLETED,
                payload={
                    "run_id": "r1",
                    "agent_id": "w1",
                    "output_summary": "队员正文",
                    "duration_ms": 10,
                    "role": "member",
                    "model": "test",
                    "usage": {"input": 1, "output": 1, "total": 2},
                    "cost": {
                        "input": 0,
                        "cached": 0,
                        "output": 0,
                        "total": 0,
                        "currency": "USD",
                    },
                    # intentionally no execution_id — production hole before fix
                },
            )
        )
    finally:
        current_journal_writer.reset(jw_token)
        current_execution_id.reset(eid_token)

    kinds = [e.get("kind") for e in writer.entries]
    assert EventType.RUN_COMPLETED.value in kinds


def test_run_completed_factory_carries_execution_id_when_set():
    """生产工厂：非空 execution_id 写入 payload；空串保持旧 fixture 字节兼容。"""
    from agentcore.runtime.events import run_completed

    with_eid = run_completed(
        "r1",
        "w1",
        output_summary="done",
        duration_ms=1,
        execution_id="exec-factory",
    )
    assert with_eid.payload["execution_id"] == "exec-factory"

    without = run_completed("r1", "w1", output_summary="done", duration_ms=1)
    assert "execution_id" not in without.payload


@pytest.mark.asyncio
async def test_pillar_a_fold_rebuilds_member_output_from_journal_facts():
    """支柱 A：message_final + run_completed → fold 拼出队员正文。"""
    events = [
        {
            "type": "run_started",
            "payload": {"run_id": "r1", "agent_id": "w1", "role": "研究员"},
            "timestamp": "2026-01-01T00:00:00.000Z",
        },
        {
            "type": "run_completed",
            "payload": {
                "run_id": "r1",
                "agent_id": "w1",
                "output_summary": "完整调研结论",
                "duration_ms": 100,
                "role": "member",
                "model": "test",
                "usage": {"input": 1, "output": 1, "total": 2},
                "cost": {
                    "input": 0,
                    "cached": 0,
                    "output": 0,
                    "total": 0,
                    "currency": "USD",
                },
            },
            "timestamp": "2026-01-01T00:00:01.000Z",
        },
    ]
    finals = {"r1": {"content": "完整调研结论", "reasoning": "思考过程"}}
    agent_runs = {"r1": "w1"}
    spliced = _splice_synthetic_deltas(events, finals, agent_runs)
    types = [e["type"] for e in spliced]
    assert EventType.RUN_REASONING_DELTA.value in types
    assert EventType.RUN_OUTPUT_DELTA.value in types
    assert EventType.RUN_COMPLETED.value in types
    out = next(e for e in spliced if e["type"] == EventType.RUN_OUTPUT_DELTA.value)
    assert out["payload"]["delta"] == "完整调研结论"


@pytest.mark.asyncio
async def test_pillar_b_registry_is_routing_source_for_adopt():
    """支柱 B：conversation→execution 注册表收养后 ContextVar 指向活跃执行。"""
    session = CoordinationSession(
        execution_id="exec-b",
        total_workers=2,
        conversation_id="conv-b",
    )
    set_active_coordination(session)
    assert active_coordination_for_conversation("conv-b") is session

    adopted = adopt_active_execution("conv-b")
    assert adopted is session
    assert session.turn_attached is True
    assert active_coordination() is session


@pytest.mark.asyncio
async def test_inject_close_then_adopt_does_not_reopen():
    """inject+close 后会话关闭；不得为收口轮 reopen。"""
    product = "【队员成品】调研报告正文……"
    session = CoordinationSession(
        execution_id="exec-p1",
        total_workers=1,
        conversation_id="conv-p1",
    )
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 1, "total": 1, "output": product},
        )
    )
    set_active_coordination(session)
    token = current_execution_id.set("exec-p1")
    try:
        msgs = await await_coordination_injection([])
        assert session.active is False
        assert "勿做最终合成" not in (msgs[0].content or "")
        assert "报告本波结果" not in (msgs[0].content or "")
        assert "团队已全部结束" in (msgs[0].content or "")
        assert product in (msgs[0].content or "")
        assert active_coordination_for_conversation("conv-p1") is None
        assert adopt_active_execution("conv-p1") is None
    finally:
        current_execution_id.reset(token)
        clear_active_coordination()


@pytest.mark.asyncio
async def test_pillar_b_release_emits_detached_and_keeps_registry():
    """支柱 B/D：teardown 发 execution_detached，注册表保留 live drive。"""
    writer = _RecordingWriter()

    async def _slow():
        await asyncio.sleep(0.2)

    session = CoordinationSession(
        execution_id="exec-detach",
        total_workers=2,
        conversation_id="conv-detach",
    )
    bind_host_journal(session, writer=writer)
    session.drive_task = asyncio.create_task(_slow())
    set_active_coordination(session)

    release_turn_coordination("exec-detach")
    assert session.turn_attached is False
    assert active_coordination("exec-detach") is session
    kinds = [e.get("kind") for e in writer.entries]
    assert EventType.EXECUTION_DETACHED.value in kinds

    session.drive_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session.drive_task


@pytest.mark.asyncio
async def test_pillar_c_injected_already_detached_still_harvests():
    """CEO 已离开且注入旗标已立 → finish_detached 仍须立刻结算，禁止清 session。"""
    session = CoordinationSession(
        execution_id="exec-injected-detached",
        total_workers=1,
        conversation_id="conv-injected-detached",
    )
    session.turn_attached = False
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    set_active_coordination(session)

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        new_callable=AsyncMock,
    ) as harvest:
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        await asyncio.sleep(0.05)
        harvest.assert_awaited_once()
        assert session.settled_via == "attached_inject"


@pytest.mark.asyncio
async def test_pillar_c_finish_detached_schedules_harvest():
    """支柱 C：无附着回合时 finish_detached 调度结算（非静默 clear）。"""
    session = CoordinationSession(
        execution_id="exec-c",
        total_workers=1,
        conversation_id="conv-c",
    )
    set_active_coordination(session)
    session.turn_attached = False

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        new_callable=AsyncMock,
    ) as harvest:
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        await asyncio.sleep(0.05)
        harvest.assert_awaited_once()


@pytest.mark.asyncio
async def test_pillar_c_stale_attach_after_terminal_still_harvests():
    """复现：terminal 后 turn_attached 误粘滞 → 必须仍进 harvest（禁静默结束）。

    对应线上 ContextVar 错放：跨图 append 在 gather 子任务写了 host eid，
    父回合 teardown 只 release 了 mint id，宿主 session 的 turn_attached 一直 True，
    旧 finish_detached 直接 return → 只有 terminal_posted、无 harvest_*。
    """
    from structlog.testing import capture_logs

    import agentcore.runtime.coordination.session as session_mod
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    session = CoordinationSession(
        execution_id="exec-stale-attach",
        total_workers=2,
        conversation_id="conv-stale-attach",
    )
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 2, "total": 2, "output": "done"},
        )
    )
    assert session.terminal_posted is True
    # Teardown never detached this session (wrong eid released).
    session.turn_attached = True
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 0.05),
        patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.01),
        patch(
            "agentcore.runtime.coordination.harvest.settle_detached_execution",
            new_callable=AsyncMock,
        ) as harvest,
        capture_logs() as logs,
    ):
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        assert any(
            e.get("event") == "coordination.settle_armed_while_attached" for e in logs
        )
        await asyncio.sleep(0.2)
        harvest.assert_awaited_once()
        assert session.turn_attached is False
        assert any(
            e.get("event") == "coordination.settle_stale_attach_forcing" for e in logs
        )


@pytest.mark.asyncio
async def test_attached_inject_visible_close_skips_harvest():
    """首回合已写出可见正文 → 交还附着后跳过后台结算通知，不再开第二条消息。"""
    from structlog.testing import capture_logs

    import agentcore.runtime.coordination.session as session_mod

    writer = _RecordingWriter()
    session = CoordinationSession(
        execution_id="exec-visible-close-skip",
        total_workers=1,
        conversation_id="conv-visible-close-skip",
    )
    session.turn_attached = True
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    session.note_attached_inject_visible_close("交付正文")
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 2.0),
        patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.02),
        patch(
            "agentcore.runtime.coordination.harvest.settle_detached_execution",
            new_callable=AsyncMock,
        ) as harvest,
        capture_logs() as logs,
    ):
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        harvest.assert_not_awaited()
        session.turn_attached = False
        await asyncio.sleep(0.1)
        harvest.assert_not_awaited()
        assert session.harvest_scheduled is False
        assert session.settled_via == "attached_inject"
        assert any(
            e.get("event") == "coordination.settle_skipped_visible_close"
            for e in logs
        )
        assert any(e.get("kind") == "execution_completed" for e in writer.entries)
        assert active_coordination("exec-visible-close-skip") is None


@pytest.mark.asyncio
async def test_live_occupant_past_grace_then_visible_close_skips_harvest():
    """注入后 grace 到期时槽位仍有活回合 → 不得当粘滞强拆；主回合稍后写出正文则不再另开结算通知轮。"""
    from structlog.testing import capture_logs

    import agentcore.runtime.coordination.session as session_mod
    from agentcore.runtime.turn.runs import turn_runs

    writer = _RecordingWriter()
    session = CoordinationSession(
        execution_id="exec-live-then-close",
        total_workers=1,
        conversation_id="conv-live-then-close",
    )
    session.turn_attached = True
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    async def _hold() -> None:
        await asyncio.Event().wait()

    occupant = asyncio.create_task(_hold())
    turn_runs.register(
        conversation_id="conv-live-then-close",
        task=occupant,
        sink=EventSink(),
    )
    try:
        with (
            patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 0.05),
            patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.02),
            patch(
                "agentcore.runtime.coordination.harvest.settle_detached_execution",
                new_callable=AsyncMock,
            ) as harvest,
            capture_logs() as logs,
        ):
            finish_detached_coordination(session)
            assert session.harvest_scheduled is True
            await asyncio.sleep(0.2)
            harvest.assert_not_awaited()
            assert session.turn_attached is True
            assert session.settled_via == "attached_inject"
            assert not any(
                e.get("event") == "coordination.settle_stale_attach_forcing"
                for e in logs
            )
            assert any(
                e.get("event") == "coordination.settle_attach_waiting_live_occupant"
                for e in logs
            )

            session.note_attached_inject_visible_close("交付终稿")
            assert session.attached_inject_visible_close is True
            session.turn_attached = False
            await asyncio.sleep(0.1)
            harvest.assert_not_awaited()
            assert session.harvest_scheduled is False
            assert session.settled_via == "attached_inject"
            assert any(
                e.get("event") == "coordination.settle_skipped_visible_close"
                for e in logs
            )
            assert any(e.get("kind") == "execution_completed" for e in writer.entries)
            assert active_coordination("exec-live-then-close") is None
    finally:
        occupant.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await occupant


@pytest.mark.asyncio
async def test_live_occupant_past_grace_then_empty_slot_still_harvests():
    """活回合在 grace 后离开且未写出可见正文 → 槽位已空，仍须结算。"""
    from structlog.testing import capture_logs

    import agentcore.runtime.coordination.session as session_mod
    from agentcore.runtime.turn.runs import turn_runs

    session = CoordinationSession(
        execution_id="exec-live-then-empty",
        total_workers=1,
        conversation_id="conv-live-then-empty",
    )
    session.turn_attached = True
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    set_active_coordination(session)

    async def _hold() -> None:
        await asyncio.Event().wait()

    occupant = asyncio.create_task(_hold())
    turn_runs.register(
        conversation_id="conv-live-then-empty",
        task=occupant,
        sink=EventSink(),
    )
    try:
        with (
            patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 0.05),
            patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.02),
            patch(
                "agentcore.runtime.coordination.harvest.settle_detached_execution",
                new_callable=AsyncMock,
            ) as harvest,
            capture_logs() as logs,
        ):
            finish_detached_coordination(session)
            await asyncio.sleep(0.2)
            harvest.assert_not_awaited()
            assert session.settled_via == "attached_inject"
            occupant.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await occupant
            await asyncio.sleep(0.15)
            harvest.assert_awaited_once()
            assert session.turn_attached is False
            assert session.settled_via == "attached_inject"
            assert any(
                e.get("event") == "coordination.settle_stale_attach_forcing"
                for e in logs
            )
    finally:
        if not occupant.done():
            occupant.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await occupant


@pytest.mark.asyncio
async def test_attached_inject_without_visible_close_still_harvests():
    """首回合未产出可见正文 → 结算仍兜住。"""
    import agentcore.runtime.coordination.session as session_mod

    session = CoordinationSession(
        execution_id="exec-no-visible-close",
        total_workers=1,
        conversation_id="conv-no-visible-close",
    )
    session.turn_attached = True
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    session.note_attached_inject_visible_close("   ")
    assert session.attached_inject_visible_close is False
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 2.0),
        patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.02),
        patch(
            "agentcore.runtime.coordination.harvest.settle_detached_execution",
            new_callable=AsyncMock,
        ) as harvest,
    ):
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        await asyncio.sleep(0.05)
        harvest.assert_not_awaited()
        session.turn_attached = False
        await asyncio.sleep(0.1)
        harvest.assert_awaited_once()
        assert session.settled_via == "attached_inject"


@pytest.mark.asyncio
async def test_attached_inject_content_reset_clears_visible_close_still_harvests():
    """打标后 content_reset 清空 → 终态无可见正文，结算仍兜住。"""
    import agentcore.runtime.coordination.session as session_mod

    session = CoordinationSession(
        execution_id="exec-reset-clears-close",
        total_workers=1,
        conversation_id="conv-reset-clears-close",
    )
    session.turn_attached = True
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    session.note_attached_inject_visible_close("交付正文")
    assert session.attached_inject_visible_close is True
    session.clear_attached_inject_visible_close()
    assert session.attached_inject_visible_close is False
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 2.0),
        patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.02),
        patch(
            "agentcore.runtime.coordination.harvest.settle_detached_execution",
            new_callable=AsyncMock,
        ) as harvest,
    ):
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        await asyncio.sleep(0.05)
        harvest.assert_not_awaited()
        session.turn_attached = False
        await asyncio.sleep(0.1)
        harvest.assert_awaited_once()
        assert session.settled_via == "attached_inject"


@pytest.mark.asyncio
async def test_pillar_c_attached_inject_then_detach_still_harvests():
    """同回合 wait 注入 ≠ 可见收口：CEO 交还附着后仍须结算（通知，不开新消息）。"""
    import agentcore.runtime.coordination.session as session_mod

    session = CoordinationSession(
        execution_id="exec-inject-then-harvest",
        total_workers=1,
        conversation_id="conv-inject-then-harvest",
    )
    session.turn_attached = True
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 2.0),
        patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.02),
        patch(
            "agentcore.runtime.coordination.harvest.settle_detached_execution",
            new_callable=AsyncMock,
        ) as harvest,
    ):
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        await asyncio.sleep(0.05)
        session.all_completed_injected = True
        session.mark_settled("attached_inject")
        await asyncio.sleep(0.05)
        harvest.assert_not_awaited()
        session.turn_attached = False
        await asyncio.sleep(0.1)
        harvest.assert_awaited_once()


@pytest.mark.asyncio
async def test_pillar_c_injected_stale_attach_still_harvests():
    """CEO 已离开但 turn_attached 粘滞 + 注入旗标仍在 → grace 后仍 harvest。"""
    from structlog.testing import capture_logs

    import agentcore.runtime.coordination.session as session_mod

    session = CoordinationSession(
        execution_id="exec-inject-stale",
        total_workers=1,
        conversation_id="conv-inject-stale",
    )
    session.turn_attached = True
    session.all_completed_injected = True
    session.mark_settled("attached_inject")
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 0.05),
        patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.01),
        patch(
            "agentcore.runtime.coordination.harvest.settle_detached_execution",
            new_callable=AsyncMock,
        ) as harvest,
        capture_logs() as logs,
    ):
        finish_detached_coordination(session)
        assert session.harvest_scheduled is True
        await asyncio.sleep(0.2)
        harvest.assert_awaited_once()
        assert session.turn_attached is False
        assert not any(
            e.get("event") == "coordination.harvest_cancelled_attached_inject"
            for e in logs
        )
        assert any(
            e.get("event") == "coordination.settle_stale_attach_forcing" for e in logs
        )


@pytest.mark.asyncio
async def test_soft_stop_skips_harvest_arming():
    """ask_user soft_stop 取消 drive 不得武装结算（resume 从 journal 重建）。"""
    session = CoordinationSession(
        execution_id="exec-soft-skip-harvest",
        total_workers=2,
        conversation_id="conv-soft-skip-harvest",
    )
    session.soft_stop = True
    session.turn_attached = True
    session.completed_run_ids.add("r1")
    set_active_coordination(session)

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        new_callable=AsyncMock,
    ) as harvest:
        finish_detached_coordination(session)
        assert session.harvest_scheduled is False
        await asyncio.sleep(0.05)
        harvest.assert_not_awaited()
        # Session stays registered for turn release / resume rebuild.
        assert active_coordination("exec-soft-skip-harvest") is session


@pytest.mark.asyncio
async def test_pillar_c_detach_during_grace_runs_harvest():
    """grace 期内 release 清掉 turn_attached → 立即结算（不必等满 grace）。"""
    import agentcore.runtime.coordination.session as session_mod

    session = CoordinationSession(
        execution_id="exec-grace-detach",
        total_workers=1,
        conversation_id="conv-grace-detach",
    )
    session.turn_attached = True
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_SETTLE_ATTACH_GRACE_S", 2.0),
        patch.object(session_mod, "_SETTLE_ATTACH_POLL_S", 0.02),
        patch(
            "agentcore.runtime.coordination.harvest.settle_detached_execution",
            new_callable=AsyncMock,
        ) as harvest,
    ):
        finish_detached_coordination(session)
        await asyncio.sleep(0.05)
        session.turn_attached = False
        await asyncio.sleep(0.1)
        harvest.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_follows_conversation_host_when_mint_differs():
    """ContextVar mint ≠ conversation host → release mint 也须 detach 宿主 drive。"""

    async def _slow():
        await asyncio.sleep(0.3)

    mint = CoordinationSession(
        execution_id="exec-mint",
        total_workers=1,
        conversation_id="conv-follow",
        active=False,
    )
    host = CoordinationSession(
        execution_id="exec-host",
        total_workers=2,
        conversation_id="conv-follow",
    )
    host.drive_task = asyncio.create_task(_slow())
    host.turn_attached = True
    # Mint still registered but conversation index points at host (append).
    set_active_coordination(mint)
    set_active_coordination(host)
    assert active_coordination_for_conversation("conv-follow") is host

    release_turn_coordination("exec-mint")
    assert host.turn_attached is False

    host.drive_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await host.drive_task


@pytest.mark.asyncio
async def test_release_follows_host_when_mint_never_registered():
    """跨回合 append：mint 从未入表时，须靠 conversation_id 跟到宿主并 detach。

    复现 gather ContextVar miss：父 teardown 仍拿 prepare 的 mint eid，
    `_sessions.get(mint)` 为 None；旧逻辑静默 return，host.turn_attached 粘滞。
    """
    from structlog.testing import capture_logs

    async def _slow():
        await asyncio.sleep(0.3)

    host = CoordinationSession(
        execution_id="exec-host-orphan-mint",
        total_workers=2,
        conversation_id="conv-orphan-mint",
    )
    host.drive_task = asyncio.create_task(_slow())
    host.turn_attached = True
    set_active_coordination(host)
    assert active_coordination_for_conversation("conv-orphan-mint") is host
    assert active_coordination("exec-mint-never") is None

    with capture_logs() as logs:
        # Without conversation_id: still stuck (documents the miss).
        release_turn_coordination("exec-mint-never")
        assert host.turn_attached is True

        release_turn_coordination(
            "exec-mint-never", conversation_id="conv-orphan-mint"
        )
        assert host.turn_attached is False
        assert any(
            e.get("event") == "coordination.turn_detached" for e in logs
        )

    host.drive_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await host.drive_task


@pytest.mark.asyncio
async def test_pillar_c_settle_skips_when_reattached():
    from agentcore.runtime.coordination.harvest import settle_detached_execution

    session = CoordinationSession(
        execution_id="exec-c2",
        total_workers=1,
        conversation_id="conv-c2",
    )
    session.turn_attached = True
    session.harvest_scheduled = True
    session.mark_settled("detached")
    set_active_coordination(session)
    await settle_detached_execution(session)
    assert active_coordination("exec-c2") is session
    assert session.harvest_scheduled is False
    assert session.settled_via is None


@pytest.mark.asyncio
async def test_pillar_d_await_live_detached_drive_delays_until_done():
    """支柱 D1：pipeline 返回后有 live detached drive 时，sink.close 须等 drive 结束。"""
    from agentcore.runtime.coordination.session import await_live_detached_drive

    release = asyncio.Event()

    async def _slow():
        await release.wait()

    session = CoordinationSession(
        execution_id="exec-d1-delay",
        total_workers=2,
        conversation_id="conv-d1-delay",
    )
    session.drive_task = asyncio.create_task(_slow())
    session.turn_attached = False  # already detached (post release_turn)
    set_active_coordination(session)

    sink = EventSink(conversation_id="conv-d1-delay", message_id="host-turn")
    closed = asyncio.Event()

    async def _owner_close_path():
        # Mirrors sidecar/cloud: await drive, then close.
        awaited = await await_live_detached_drive("conv-d1-delay")
        assert awaited is True
        sink.close()
        closed.set()

    owner = asyncio.create_task(_owner_close_path())
    await asyncio.sleep(0.05)
    assert not closed.is_set()
    assert not sink._closed

    # Live events still land on the open sink while we wait.
    sink.emit(
        SSEEvent(
            type=EventType.RUN_COMPLETED,
            payload={
                "run_id": "r1",
                "agent_id": "w1",
                "output_summary": "done",
                "duration_ms": 1,
            },
        )
    )

    release.set()
    await asyncio.wait_for(owner, timeout=2)
    assert sink._closed
    assert closed.is_set()


@pytest.mark.asyncio
async def test_pillar_d_await_terminal_posted_hung_drive_bounded_return():
    """终态已投递 + drive 挂死 → grace 内有界返回，不 cancel drive，仍 settle。"""
    from structlog.testing import capture_logs

    from agentcore.runtime.coordination import session as session_mod
    from agentcore.runtime.coordination.session import await_live_detached_drive

    hang = asyncio.Event()

    async def _hung() -> None:
        await hang.wait()

    session = CoordinationSession(
        execution_id="exec-d1-hung",
        total_workers=1,
        conversation_id="conv-d1-hung",
    )
    session.drive_task = asyncio.create_task(_hung())
    session.turn_attached = False
    session.terminal_posted = True
    set_active_coordination(session)

    with (
        patch.object(session_mod, "_AWAIT_DETACHED_DRIVE_GRACE_S", 0.05),
        capture_logs() as logs,
    ):
        awaited = await asyncio.wait_for(
            await_live_detached_drive("conv-d1-hung"), timeout=1
        )

    assert awaited is True
    assert not session.drive_task.done()
    assert not session.drive_task.cancelled()
    expired = [
        e
        for e in logs
        if e.get("event") == "coordination.await_detached_drive_grace_expired"
    ]
    assert expired
    assert expired[0].get("conversation_id") == "conv-d1-hung"

    session.drive_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await session.drive_task


@pytest.mark.asyncio
async def test_pillar_d_await_skips_when_no_live_detached_drive():
    """无 live detached drive（仍附着 / user_stopped / 无 session）→ 立即返回，不阻塞 close。"""
    from agentcore.runtime.coordination.session import await_live_detached_drive

    assert await await_live_detached_drive("missing") is False

    async def _slow():
        await asyncio.sleep(30)

    session = CoordinationSession(
        execution_id="exec-d1-attached",
        total_workers=1,
        conversation_id="conv-d1-attached",
    )
    session.drive_task = asyncio.create_task(_slow())
    session.turn_attached = True  # still arming turn
    set_active_coordination(session)
    assert await await_live_detached_drive("conv-d1-attached") is False

    session.turn_attached = False
    session.user_stopped = True
    assert await await_live_detached_drive("conv-d1-attached") is False

    session.drive_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await session.drive_task


@pytest.mark.asyncio
async def test_pillar_d_execution_events_factories():
    """支柱 D：协议事件工厂形状。"""
    session = CoordinationSession(
        execution_id="exec-d",
        total_workers=3,
        conversation_id="conv-d",
    )
    writer = _RecordingWriter()
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)
    session.turn_attached = False
    emit_execution_detached(session, reason="early_close")
    assert any(e.get("kind") == "execution_detached" for e in writer.entries)

    done = execution_completed(
        execution_id="exec-d",
        conversation_id="conv-d",
        completed=3,
        total=3,
        status="completed",
    )
    assert done.type is EventType.EXECUTION_COMPLETED
    assert done.payload["completed"] == 3
    assert done.payload["status"] == "completed"


@pytest.mark.asyncio
async def test_cancelled_harvest_emits_execution_status_cancelled():
    """cancelled harvest → execution_completed.payload.status=cancelled。"""
    from agentcore.runtime.coordination.harvest import emit_execution_completed

    writer = _RecordingWriter()
    session = CoordinationSession(
        execution_id="exec-cancel-status",
        total_workers=1,
        conversation_id="conv-cancel-status",
    )
    session.soft_stop = True
    session.turn_attached = False
    session.completed_run_ids = {"r1"}
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    emit_execution_completed(session)

    done = next(e for e in writer.entries if e.get("kind") == "execution_completed")
    assert done["payload"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_unsettled_runs_emit_run_cancelled_before_execution_completed():
    """发 execution 终态前：未 settle 的 plan node 先有 run_cancelled。"""
    from agentcore.runtime.coordination.harvest import emit_execution_completed
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec

    writer = _RecordingWriter()
    session = CoordinationSession(
        execution_id="exec-unsettled",
        total_workers=2,
        conversation_id="conv-unsettled",
    )
    session.turn_attached = False
    session.completed_run_ids = {"r1"}
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="r1", task="done"),
            RunSpec(run_id="r2", task="still running"),
        ]
    )
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    emit_execution_completed(session)

    kinds = [e.get("kind") for e in writer.entries]
    assert EventType.RUN_CANCELLED.value in kinds
    assert EventType.EXECUTION_COMPLETED.value in kinds
    assert kinds.index(EventType.RUN_CANCELLED.value) < kinds.index(
        EventType.EXECUTION_COMPLETED.value
    )
    cancel = next(e for e in writer.entries if e.get("kind") == "run_cancelled")
    assert cancel["payload"]["run_id"] == "r2"
    assert "r2" in session.completed_run_ids
    assert "r2" not in session.ceo_cancel_worker_ids
    done = next(e for e in writer.entries if e.get("kind") == "execution_completed")
    # 残局 cancelled → status cancelled（不变量优先）
    assert done["payload"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_pillar_d_await_soft_stop_still_waits_for_live_drive():
    """soft_stop 时若 drive 仍 live，须继续 await（勿立刻 False 导致 sink 早关）。"""
    from agentcore.runtime.coordination.session import await_live_detached_drive

    release = asyncio.Event()

    async def _slow():
        await release.wait()

    session = CoordinationSession(
        execution_id="exec-soft-await",
        total_workers=1,
        conversation_id="conv-soft-await",
    )
    session.drive_task = asyncio.create_task(_slow())
    session.turn_attached = False
    session.soft_stop = True
    set_active_coordination(session)

    closed = asyncio.Event()

    async def _owner():
        awaited = await await_live_detached_drive("conv-soft-await")
        assert awaited is True
        closed.set()

    owner = asyncio.create_task(_owner())
    await asyncio.sleep(0.05)
    assert not closed.is_set()
    release.set()
    await asyncio.wait_for(owner, timeout=2)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_host_journal_after_contextvar_reset_receives_run_completed():
    """host journal：ContextVar reset 后仍收到 run_completed（detach 续写）。"""
    from agentcore.runtime.coordination.session import current_execution_id
    from agentcore.runtime.journal.writer import current_journal_writer

    writer = _RecordingWriter()
    stale = _RecordingWriter(turn_id="stale-turn")
    session = CoordinationSession(
        execution_id="exec-host-reset",
        total_workers=1,
        conversation_id="conv-host-reset",
    )
    session.turn_attached = False  # already detached
    bind_host_journal(session, writer=writer, turn_id="host-turn")
    set_active_coordination(session)

    # Stale ContextVar still set (child-task inheritance) + execution ContextVar reset.
    jw_token = current_journal_writer.set(stale)  # type: ignore[arg-type]
    eid_token = current_execution_id.set(None)
    try:
        sink = EventSink(conversation_id="conv-host-reset", message_id="host-turn")
        sink.emit(
            SSEEvent(
                type=EventType.RUN_COMPLETED,
                payload={
                    "run_id": "r1",
                    "agent_id": "w1",
                    "output_summary": "队员正文",
                    "duration_ms": 10,
                    "role": "member",
                    "model": "test",
                    "usage": {"input": 1, "output": 1, "total": 2},
                    "cost": {
                        "input": 0,
                        "cached": 0,
                        "output": 0,
                        "total": 0,
                        "currency": "USD",
                    },
                    "execution_id": "exec-host-reset",
                },
            )
        )
    finally:
        current_journal_writer.reset(jw_token)
        current_execution_id.reset(eid_token)

    assert EventType.RUN_COMPLETED.value in [e.get("kind") for e in writer.entries]
    assert not stale.entries  # must not land on the stale ContextVar writer


@pytest.mark.asyncio
async def test_post_detach_run_failed_enters_host_fact_log_and_refresh_snapshot():
    """detach 后终态：host fact log + writer 都落盘，finalize 快照含该帧（非契约变更）。"""
    from agentcore.runtime.events import FinishReason, run_failed
    from agentcore.runtime.journal.writer import current_journal_writer
    from agentcore.runtime.pipeline.finalize import refresh_result_journal_from_host

    writer = _RecordingWriter()
    fact_log = TurnFactLog()
    fact_log.record_fact(Fact(kind="run_started", payload={"run_id": "r2"}, ts="t0"))
    session = CoordinationSession(
        execution_id="exec-post-detach",
        total_workers=2,
        conversation_id="conv-post-detach",
    )
    session.turn_attached = False
    fl_token = current_fact_log.set(fact_log)
    try:
        bind_host_journal(session, writer=writer, turn_id="host-turn")
    finally:
        current_fact_log.reset(fl_token)
    assert session.host_fact_log is fact_log
    set_active_coordination(session)

    jw_token = current_journal_writer.set(None)
    try:
        sink = EventSink(conversation_id="conv-post-detach", message_id="host-turn")
        sink.emit(
            run_failed(
                "r2",
                "w2",
                "余额不足",
                execution_id="exec-post-detach",
            )
        )
        result = {
            "finish_reason": FinishReason.END_TURN,
            "journal_entries": [
                {"kind": "run_started", "payload": {"run_id": "r2"}, "ts": "t0"},
            ],
        }
        refresh_result_journal_from_host(result, sink=sink)
    finally:
        current_journal_writer.reset(jw_token)

    assert EventType.RUN_FAILED.value in [e.get("kind") for e in writer.entries]
    assert "run_failed" in [e.get("kind") for e in fact_log.entries()]
    assert "run_failed" in [e.get("kind") for e in (result.get("journal_entries") or [])]


@pytest.mark.asyncio
async def test_await_live_detached_drive_logs_unsettled_when_host_journal_missing_run():
    """post-detach：harvest 已盖 settled_via，但宿主 journal 缺终态帧 → terminal_unsettled。"""
    from structlog.testing import capture_logs

    from agentcore.runtime.coordination.session import await_live_detached_drive

    async def _done() -> None:
        return None

    fact_log = TurnFactLog()
    fact_log.record_fact(Fact(kind="run_failed", payload={"run_id": "w1"}))
    session = CoordinationSession(
        execution_id="exec-unsettle-post",
        total_workers=2,
        conversation_id="conv-unsettle-post",
    )
    session.turn_attached = False
    session.completed_run_ids.update({"w1", "w2"})
    session.host_fact_log = fact_log
    session.drive_task = asyncio.create_task(_done())
    session.mark_settled("detached")
    set_active_coordination(session)
    with capture_logs() as logs:
        awaited = await await_live_detached_drive("conv-unsettle-post")
    assert awaited is True
    unsettle = [e for e in logs if e.get("event") == "coordination.terminal_unsettled"]
    assert unsettle
    assert "w2" in (unsettle[0].get("missing_run_ids") or [])


@pytest.mark.asyncio
async def test_sealed_host_writer_still_persists_run_completed():
    """pause seal + still-attached ContextVar：worker 终态落到 overflow host，不静默丢。"""
    from agentcore.runtime.events import run_completed as run_completed_ev
    from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer

    written: list[dict] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(
            self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False
        ) -> int | None:
            written.append(dict(entry))
            return len(written) - 1

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    import agentcore.conversation.store.cloud as cloud_mod

    with (
        patch.object(cloud_mod, "telemetry_session_factory", lambda: _Sess()),
        patch.object(cloud_mod, "TurnJournalRepository", Repo),
        patch("agentcore.runtime.audit.hooks.on_journal_fact_appended", lambda entry: None),
    ):
        writer = TurnJournalWriter(turn_id="host-turn", conversation_id="conv-seal", trace_id=None)
        fact_log = TurnFactLog()
        session = CoordinationSession(
            execution_id="exec-seal-host",
            total_workers=1,
            conversation_id="conv-seal",
        )
        fl = current_fact_log.set(fact_log)
        wt = current_journal_writer.set(writer)
        try:
            bind_host_journal(session, writer=writer, turn_id="host-turn")
            set_active_coordination(session)
            await writer.seal()
            assert session.host_journal_writer is not writer
            sink = EventSink(conversation_id="conv-seal", message_id="host-turn")
            sink.emit(
                run_completed_ev(
                    "r1",
                    "w1",
                    output_summary="done",
                    duration_ms=1,
                    execution_id="exec-seal-host",
                )
            )
            await writer.flush()
        finally:
            clear_active_coordination("exec-seal-host")
            current_journal_writer.reset(wt)
            current_fact_log.reset(fl)

    assert EventType.RUN_COMPLETED.value in [e.get("kind") for e in written]
    assert "run_completed" in [e.get("kind") for e in fact_log.entries()]
    session.completed_run_ids.add("r1")
    session.mark_settled("detached")
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        session.check_terminal_settlement(journal_entries=fact_log.entries())
    assert not any(e.get("event") == "coordination.terminal_unsettled" for e in logs)
