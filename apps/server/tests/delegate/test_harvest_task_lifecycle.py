"""Settle task lifecycle: strong refs + cancelled settle is observable."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import agentcore.runtime.coordination.harvest as harvest_mod
import agentcore.runtime.coordination.session as session_mod
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
)
from tests.conftest import LogSpy


@pytest.fixture(autouse=True)
def _clean_coordination():
    clear_active_coordination()
    yield
    clear_active_coordination()


def _detached_session(eid: str = "exec-settle-life") -> CoordinationSession:
    session = CoordinationSession(
        execution_id=eid,
        total_workers=1,
        conversation_id="conv-settle-life",
    )
    session.turn_attached = False
    return session


@pytest.mark.asyncio
async def test_arm_settle_now_holds_task_ref_until_done():
    session = _detached_session("exec-settle-retain")
    gate = asyncio.Event()

    async def _block(*_a: object, **_k: object) -> None:
        await gate.wait()

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        _block,
    ):
        session_mod._arm_settle_now(session)

    assert len(session._settle_tasks) == 1
    task = next(iter(session._settle_tasks))
    assert not task.done()

    gate.set()
    await task
    await asyncio.sleep(0)
    assert session._settle_tasks == set()


@pytest.mark.asyncio
async def test_run_settle_logs_cancelled(monkeypatch: pytest.MonkeyPatch):
    """3.13: cancel before the task starts never enters the coroutine body."""
    spy = LogSpy()
    monkeypatch.setattr(session_mod, "logger", spy)
    session = _detached_session("exec-settle-cancel")
    hang = asyncio.Event()

    async def _block(*_a: object, **_k: object) -> None:
        await hang.wait()

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        _block,
    ):
        session_mod._arm_settle_now(session)
        task = next(iter(session._settle_tasks))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert spy.get("coordination.settle_cancelled")["execution_id"] == (
        "exec-settle-cancel"
    )
    await asyncio.sleep(0)
    assert session._settle_tasks == set()


@pytest.mark.asyncio
async def test_run_settle_logs_cancelled_after_start(
    monkeypatch: pytest.MonkeyPatch,
):
    spy = LogSpy()
    monkeypatch.setattr(session_mod, "logger", spy)
    session = _detached_session("exec-settle-cancel-started")
    hang = asyncio.Event()

    async def _block(*_a: object, **_k: object) -> None:
        await hang.wait()

    with patch(
        "agentcore.runtime.coordination.harvest.settle_detached_execution",
        _block,
    ):
        session_mod._arm_settle_now(session)
        task = next(iter(session._settle_tasks))
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert spy.get("coordination.settle_cancelled")["execution_id"] == (
        "exec-settle-cancel-started"
    )
    await asyncio.sleep(0)
    assert session._settle_tasks == set()


@pytest.mark.asyncio
async def test_settle_detached_execution_logs_entry(
    monkeypatch: pytest.MonkeyPatch,
):
    spy = LogSpy()
    monkeypatch.setattr(harvest_mod, "logger", spy)
    session = CoordinationSession(
        execution_id="exec-settle-entry",
        total_workers=1,
        conversation_id="conv-settle-entry",
    )
    session.turn_attached = True
    session.harvest_scheduled = True
    session.mark_settled("detached")

    await harvest_mod.settle_detached_execution(session)

    fields = spy.get("coordination.settle_detached_started")
    assert fields["execution_id"] == "exec-settle-entry"
    assert fields["turn_attached"] is True
    assert spy.get("coordination.settle_skipped_reattached")


def test_execution_terminal_kind_from_session_fields():
    from agentcore.runtime.coordination.harvest import execution_terminal_kind
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    ok = CoordinationSession(execution_id="k-ok", total_workers=1)
    ok.completed_run_ids = {"a"}
    assert execution_terminal_kind(ok) == "success"

    fail = CoordinationSession(execution_id="k-fail", total_workers=2)
    fail.completed_run_ids = {"a", "b"}
    fail.failed_run_ids = {"b"}
    assert execution_terminal_kind(fail) == "failure"

    paused = CoordinationSession(execution_id="k-soft", total_workers=1)
    paused.soft_stop = True
    assert execution_terminal_kind(paused) == "cancelled"

    drive_c = CoordinationSession(execution_id="k-dc", total_workers=1)
    drive_c.drive_cancelled = True
    assert execution_terminal_kind(drive_c) == "cancelled"

    pending_c = CoordinationSession(execution_id="k-pend", total_workers=1)
    pending_c._pending.append(
        CoordinationEvent(kind=CoordinationEventKind.DRIVE_CANCELLED, payload={})
    )
    assert execution_terminal_kind(pending_c) == "cancelled"


def test_settle_push_copy_points_at_graph_not_new_turn():
    from agentcore.runtime.coordination.harvest import _SETTLE_PUSH

    for kind in ("success", "failure", "cancelled"):
        title, body = _SETTLE_PUSH[kind]
        assert "协作图" in body
        assert "打开对话查看" not in body
        assert "【系统收口】" not in title
        assert "【系统收口】" not in body
    assert _SETTLE_PUSH["success"][0] == "团队好了"
    assert _SETTLE_PUSH["cancelled"][0] == "团队已停止"
