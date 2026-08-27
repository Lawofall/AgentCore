"""Drive cancel must carry a reason stamp (no bare task.cancel)."""

from __future__ import annotations

import asyncio

import pytest

from agentcore.core.task_cancel import (
    CANCEL_REASON_ATTR,
    cancel_reason_from_done_task,
    cancel_task,
    stamp_cancel_reason,
)
from agentcore.runtime.coordination.drive_cancel import (
    cancel_drive_task,
    drive_cancel_error_copy,
    note_child_cancel_overflow,
    resolve_drive_cancel_reason,
)
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)


async def test_cancel_task_stamps_attr_and_cancelled_error_message():
    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(hang())
    await started.wait()
    assert cancel_task(task, "user_stop") is True
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert getattr(task, CANCEL_REASON_ATTR) == "user_stop"
    assert caught.value.args[0] == "user_stop"
    assert cancel_reason_from_done_task(task) == "user_stop"


async def test_cancel_drive_task_stamps_session_and_task():
    session = CoordinationSession(execution_id="e-stamp", total_workers=1)

    async def hang() -> None:
        await asyncio.Event().wait()

    session.drive_task = asyncio.create_task(hang())
    await asyncio.sleep(0)
    assert cancel_drive_task(session, "user_stop") is True
    assert session.drive_cancel_reason == "user_stop"
    with pytest.raises(asyncio.CancelledError):
        await session.drive_task
    assert resolve_drive_cancel_reason(session) == "user_stop"


async def test_child_overflow_stamps_worker_bare_cancel():
    clear_active_coordination()
    session = CoordinationSession(execution_id="e-overflow", total_workers=1)
    set_active_coordination(session)

    async def hang() -> None:
        await asyncio.Event().wait()

    child = asyncio.create_task(hang())
    session.drive_task = asyncio.create_task(hang())
    await asyncio.sleep(0)
    child.cancel()
    with pytest.raises(asyncio.CancelledError):
        await child
    reason = note_child_cancel_overflow(child)
    assert reason == "worker_bare_cancel"
    assert session.drive_cancel_reason == "worker_bare_cancel"
    clear_active_coordination()
    session.drive_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session.drive_task


def test_bare_cancel_error_copy_does_not_claim_process_kill():
    assert "进程关闭" not in drive_cancel_error_copy("cancelled_without_rpc")
    assert "进程关闭" not in drive_cancel_error_copy("worker_bare_cancel")
    assert "进程关闭" in drive_cancel_error_copy("shutdown")


async def test_stamp_then_done_task_prefers_attr_over_empty_args():
    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(hang())
    await started.wait()
    stamp_cancel_reason(task, "worker_timeout")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_reason_from_done_task(task) == "worker_timeout"
