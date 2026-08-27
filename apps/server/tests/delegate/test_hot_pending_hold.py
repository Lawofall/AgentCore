"""Hot user card (approval / auth): do not harvest as cancelled until the user acts."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from agentcore.runtime.coordination.harvest import (
    _cancel_unsettled_plan_workers,
    emit_execution_completed,
    settle_detached_execution,
)
from agentcore.runtime.coordination.inject import format_coordination_events
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    active_coordination,
    bind_host_journal,
    clear_active_coordination,
    finish_detached_coordination,
    set_active_coordination,
)
from agentcore.runtime.events import EventType
from agentcore.runtime.interaction import InteractionKind, InteractionRegistry
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.runtime.runs.wave import WaveScheduler


class _RecordingWriter:
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


@pytest.fixture(autouse=True)
def _clean_coordination():
    clear_active_coordination()
    yield
    clear_active_coordination()


def _pending_approval(monkeypatch: pytest.MonkeyPatch, cid: str, *, tool_name: str):
    reg = InteractionRegistry()
    reg.create(
        "hot-1",
        cid,
        kind=InteractionKind.APPROVAL,
        payload={"tool_name": tool_name},
    )
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.default_interaction_registry",
        lambda: reg,
    )
    return reg


def _unsettled_session(eid: str, cid: str) -> CoordinationSession:
    session = CoordinationSession(execution_id=eid, total_workers=2, conversation_id=cid)
    session.turn_attached = False
    session.completed_run_ids = {"r1"}
    session.live_plan = RunPlan(
        nodes=[
            RunSpec(run_id="r1", task="done"),
            RunSpec(run_id="r2", task="awaiting approval"),
        ]
    )
    return session


@pytest.mark.asyncio
async def test_emit_skips_unsettled_cancel_while_hot_pending(monkeypatch):
    """pending APPROVAL + 非 user_stopped → 不得 _cancel_unsettled / cancelled 终态."""
    cid = "conv-hold-emit"
    _pending_approval(monkeypatch, cid, tool_name="file_write")
    writer = _RecordingWriter()
    session = _unsettled_session("exec-hold-emit", cid)
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    emit_execution_completed(session)

    kinds = [e.get("kind") for e in writer.entries]
    assert EventType.RUN_CANCELLED.value not in kinds
    assert EventType.EXECUTION_COMPLETED.value not in kinds
    assert "r2" not in session.completed_run_ids
    cancelled = _cancel_unsettled_plan_workers(session)
    assert cancelled == []
    assert "r2" not in session.completed_run_ids


@pytest.mark.asyncio
async def test_user_stop_still_cancels_unsettled_with_hot_pending(monkeypatch):
    cid = "conv-hold-stop"
    _pending_approval(monkeypatch, cid, tool_name="file_write")
    writer = _RecordingWriter()
    session = _unsettled_session("exec-hold-stop", cid)
    session.user_stopped = True
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    emit_execution_completed(session)

    kinds = [e.get("kind") for e in writer.entries]
    assert EventType.RUN_CANCELLED.value in kinds
    assert EventType.EXECUTION_COMPLETED.value in kinds
    done = next(e for e in writer.entries if e.get("kind") == "execution_completed")
    assert done["payload"]["status"] == "cancelled"
    assert "r2" in session.completed_run_ids


@pytest.mark.asyncio
async def test_finish_detached_holds_harvest_while_hot_pending(monkeypatch):
    cid = "conv-hold-finish"
    _pending_approval(monkeypatch, cid, tool_name="file_write")
    writer = _RecordingWriter()
    session = _unsettled_session("exec-hold-finish", cid)
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        new_callable=AsyncMock,
    ) as harvest:
        finish_detached_coordination(session)
        await asyncio.sleep(0.05)
        harvest.assert_not_awaited()
        kinds = [e.get("kind") for e in writer.entries]
        assert EventType.RUN_CANCELLED.value not in kinds
        assert EventType.EXECUTION_COMPLETED.value not in kinds
        assert active_coordination("exec-hold-finish") is session
        assert "r2" not in session.completed_run_ids
        for task in list(session._settle_tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_finish_detached_resumes_harvest_after_pending_clears(monkeypatch):
    cid = "conv-hold-resume"
    reg = _pending_approval(monkeypatch, cid, tool_name="file_write")
    session = _unsettled_session("exec-hold-resume", cid)
    set_active_coordination(session)

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        new_callable=AsyncMock,
    ) as harvest:
        finish_detached_coordination(session)
        await asyncio.sleep(0.05)
        harvest.assert_not_awaited()
        assert reg.resolve("hot-1", "allow", conversation_id=cid) is True
        deadline = asyncio.get_running_loop().time() + 2.0
        while harvest.await_count == 0 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        harvest.assert_awaited()


@pytest.mark.asyncio
async def test_harvest_detached_skips_close_while_hot_pending(monkeypatch):
    cid = "conv-hold-detached"
    _pending_approval(monkeypatch, cid, tool_name="file_write")
    session = _unsettled_session("exec-hold-detached", cid)
    session.harvest_scheduled = True
    session.mark_settled("detached")
    set_active_coordination(session)

    await settle_detached_execution(session)

    assert active_coordination("exec-hold-detached") is session
    assert session.harvest_scheduled is False
    assert "r2" not in session.completed_run_ids


@pytest.mark.asyncio
async def test_drive_cancelled_not_posted_while_hot_pending(monkeypatch):
    import importlib

    from agentcore.runtime.coordination.host import _background_drive
    from tests.delegate.conftest import Provider, tool

    drive_mod = importlib.import_module("agentcore.runtime.delegate.drive")
    cid = "conv-hold-drive"
    _pending_approval(monkeypatch, cid, tool_name="file_write")
    from agentcore.runtime.runs import build_run_plan

    plan, errors = build_run_plan(
        [{"role": "研究员", "task": "做A"}, {"role": "写手", "task": "做B"}]
    )
    assert not errors
    session = CoordinationSession(
        execution_id="e-hold-drive", total_workers=2, conversation_id=cid
    )
    set_active_coordination(session)
    t = tool(Provider(["x"]))

    async def _hang(*_a, **_k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(drive_mod, "drive_coordinated", _hang)
    task = asyncio.create_task(
        _background_drive(
            t,
            plan,
            execution_id="e-hold-drive",
            seed_completed=None,
            seed_notes=None,
            complexity_hint="standard",
            call_idx=0,
            session=session,
            coordination="wall",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = session.drain_nowait()
    assert not any(e.kind is CoordinationEventKind.DRIVE_CANCELLED for e in events)
    assert not any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in events)
    for held in list(session._settle_tasks):
        held.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await held


@pytest.mark.asyncio
async def test_inject_close_line_is_waiting_not_cancelled(monkeypatch):
    cid = "conv-hold-inject"
    _pending_approval(monkeypatch, cid, tool_name="file_write")
    session = CoordinationSession(
        execution_id="e-hold-inject", total_workers=2, conversation_id=cid
    )
    events = [
        CoordinationEvent(
            kind=CoordinationEventKind.DRIVE_CANCELLED,
            payload={"completed": 1, "total": 2},
        )
    ]
    text = format_coordination_events(session, events)
    assert "等你允许" in text
    assert "file_write" in text
    assert "团队已取消" not in text
    assert "调度已停" not in text
    assert "协调被打断" not in text


@pytest.mark.asyncio
async def test_drive_cancel_keeps_approval_future_and_registry(monkeypatch):
    """drive_task.cancel 时审批 Future 仍 pending、无 harvest run_cancelled、卡仍在."""
    cid = "conv-hold-wave"
    eid = "exec-hold-wave"
    reg = InteractionRegistry()
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.default_interaction_registry",
        lambda: reg,
    )
    writer = _RecordingWriter()
    session = CoordinationSession(
        execution_id=eid, total_workers=1, conversation_id=cid
    )
    session.turn_attached = False
    bind_host_journal(session, writer=writer)
    set_active_coordination(session)

    started = asyncio.Event()

    async def ex(spec: RunSpec, _completed) -> RunState:
        result = await reg.suspend(
            "hot-wave",
            cid,
            kind=InteractionKind.APPROVAL,
            payload={"tool_name": "file_write"},
            timeout=None,
            on_suspended=started.set,
        )
        return RunState(phase=RunPhase.COMPLETED, content=str(result))

    plan = RunPlan()
    plan.add(RunSpec(run_id="w1", task="await approval"))
    wave = asyncio.create_task(WaveScheduler().run(plan, ex))
    session.drive_task = wave
    await started.wait()
    pending = reg.list_pending(cid)
    assert pending
    fut = pending[0].future
    assert not fut.done()

    wave.cancel()
    await asyncio.sleep(0.08)
    assert not fut.done()
    assert reg.list_pending(cid)
    kinds = [e.get("kind") for e in writer.entries]
    assert EventType.RUN_CANCELLED.value not in kinds
    assert not wave.done()

    assert reg.resolve("hot-wave", "allow", conversation_id=cid) is True
    result = await asyncio.wait_for(wave, timeout=2)
    assert result["w1"].phase is RunPhase.COMPLETED
    assert result["w1"].content == "allow"


@pytest.mark.asyncio
async def test_user_stop_wave_cancel_still_kills_approval(monkeypatch):
    cid = "conv-hold-wave-stop"
    eid = "exec-hold-wave-stop"
    reg = InteractionRegistry()
    monkeypatch.setattr(
        "agentcore.runtime.interaction_orphan.default_interaction_registry",
        lambda: reg,
    )
    session = CoordinationSession(
        execution_id=eid, total_workers=1, conversation_id=cid
    )
    set_active_coordination(session)
    started = asyncio.Event()
    cancel_msgs: list[str] = []

    async def ex(spec: RunSpec, _completed) -> RunState:
        try:
            await reg.suspend(
                "hot-stop",
                cid,
                kind=InteractionKind.APPROVAL,
                payload={"tool_name": "file_write"},
                timeout=None,
                on_suspended=started.set,
            )
        except asyncio.CancelledError as e:
            cancel_msgs.append(str(e.args[0]) if e.args else "")
            raise
        return RunState(phase=RunPhase.COMPLETED, content="no")

    plan = RunPlan()
    plan.add(RunSpec(run_id="w1", task="await approval"))
    wave = asyncio.create_task(WaveScheduler().run(plan, ex))
    session.drive_task = wave
    await started.wait()
    session.user_stopped = True
    wave.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wave
    assert "stop" in cancel_msgs
    assert not reg.list_pending(cid)


@pytest.mark.asyncio
async def test_hold_harvest_waits_for_running_workers_after_pending_clears(monkeypatch):
    cid = "conv-hold-running"
    reg = _pending_approval(monkeypatch, cid, tool_name="file_write")
    session = _unsettled_session("exec-hold-running", cid)
    session._running_workers["r2"] = "写手"
    set_active_coordination(session)

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        new_callable=AsyncMock,
    ) as harvest:
        finish_detached_coordination(session)
        await asyncio.sleep(0.05)
        harvest.assert_not_awaited()
        assert reg.resolve("hot-1", "allow", conversation_id=cid) is True
        await asyncio.sleep(0.45)
        harvest.assert_not_awaited()
        session._running_workers.pop("r2", None)
        deadline = asyncio.get_running_loop().time() + 2.0
        while harvest.await_count == 0 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        harvest.assert_awaited()
