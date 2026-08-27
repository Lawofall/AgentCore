"""Stamp why a coordination ``drive_task`` was cancelled.

Sidecar already stamps the turn task (``CANCEL_REASON_ATTR``). The background
drive had a third, unlabelled path: bare ``CancelledError`` / ``task.cancel()``
with no reason — journal then shows worker ``reason=stop`` and inject says
「进程关闭或回合中断」 even when neither happened.

Call ``cancel_drive_task`` instead of ``drive_task.cancel()``. Child overflow
(wave sees a worker cancelled outside the absorb list) calls
``note_child_cancel_overflow`` before propagating.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentcore.core.task_cancel import (
    cancel_reason_from_done_task,
    cancel_reason_from_task,
    cancel_task,
    normalize_cancel_reason,
    stamp_cancel_reason,
)

_DRIVE_CANCEL_ERROR = {
    "user_stop": "用户已停止本回合，协调调度被取消。",
    "worker_bare_cancel": "队员任务被无指纹取消并上溢，协调调度被取消。",
    "worker_timeout": "队员硬超时，协调调度被取消。",
    "soft_stop": "请示用户挂起，后台调度已停。",
    "stop": "调度层停止在飞队员，协调调度被取消。",
    "abort_signal": "本机流中止，协调调度被取消。",
    "attach_abort": "本机流让位中止，协调调度被取消。",
    "shutdown": "进程关闭，协调调度被取消。",
    "cancelled_without_rpc": "协调调度被取消（无取消指纹）。",
}


def stamp_drive_cancel(session: Any, reason: str) -> str:
    """Record *reason* on the session and the live ``drive_task`` (if any)."""
    normalized = normalize_cancel_reason(reason)
    session.drive_cancel_reason = normalized
    stamp_cancel_reason(getattr(session, "drive_task", None), normalized)
    return normalized


def cancel_drive_task(session: Any, reason: str) -> bool:
    """Stamp then cancel ``session.drive_task``. False when missing / already done."""
    stamp_drive_cancel(session, reason)
    return cancel_task(getattr(session, "drive_task", None), session.drive_cancel_reason)


def note_child_cancel_overflow(child: Any) -> str:
    """Worker finished as a bare cancel — stamp the active drive before propagating.

    Returns the reason written on the session (``worker_bare_cancel`` when the
    child itself had no stamp / message).
    """
    raw = cancel_reason_from_done_task(child)
    reason = (
        "worker_bare_cancel"
        if raw in ("cancelled_without_rpc", "unspecified")
        else raw
    )
    from agentcore.runtime.coordination.session import active_coordination

    session = active_coordination()
    if session is not None:
        stamp_drive_cancel(session, reason)
    return reason


def resolve_drive_cancel_reason(
    session: Any,
    exc: BaseException | None = None,
) -> str:
    """Prefer the session stamp, then ``CancelledError`` args, then the task attr."""
    stamped = getattr(session, "drive_cancel_reason", None)
    if stamped:
        return normalize_cancel_reason(stamped)
    if isinstance(exc, asyncio.CancelledError) and exc.args:
        return normalize_cancel_reason(exc.args[0])
    return cancel_reason_from_task(getattr(session, "drive_task", None))


def drive_cancel_error_copy(reason: str) -> str:
    """CEO-facing one-liner for ``DRIVE_CANCELLED.payload.error`` (not user chrome)."""
    key = normalize_cancel_reason(reason)
    return _DRIVE_CANCEL_ERROR.get(key, f"协调调度被取消（{key}）。")
