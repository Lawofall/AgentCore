"""Task cancellation as a first-class outbound-I/O signal.

A run's in-flight HTTP (LLM streams, tool fetches) belongs to the same cancel
scope as the worker task. ``asyncio.CancelledError`` is terminal: abort the
request and never enter a retry loop. httpx/httpcore may wrap the cancel in
``HTTPError``; walk ``__cause__`` / ``__context__``. ``Task.cancelling()``
(3.11+) covers the window where the wrapper already replaced the exception.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Set on an ``asyncio.Task`` before ``task.cancel()`` so salvage logs can read
# a reason after the exception has lost its message.
CANCEL_REASON_ATTR = "_agentcore_cancel_reason"

# Sidecar RPC tags + coordination drive overflow. Unknown → kept as trimmed str.
KNOWN_CANCEL_REASONS = frozenset(
    {
        "user_stop",
        "abort_signal",
        "attach_abort",
        "unspecified",
        "cancelled_without_rpc",
        "worker_bare_cancel",
        "worker_timeout",
        "soft_stop",
        "stop",
        "shutdown",
    }
)


def normalize_cancel_reason(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return "unspecified"
    if text in KNOWN_CANCEL_REASONS:
        return text
    return text[:64]


def stamp_cancel_reason(task: Any | None, reason: Any) -> str:
    """Write ``CANCEL_REASON_ATTR`` on *task* (no-op when task is missing)."""
    normalized = normalize_cancel_reason(reason)
    if task is not None:
        setattr(task, CANCEL_REASON_ATTR, normalized)
    return normalized


def cancel_task(task: Any | None, reason: Any) -> bool:
    """Stamp then ``task.cancel(reason)``. False when missing or already done."""
    if task is None or task.done():
        return False
    msg = stamp_cancel_reason(task, reason)
    task.cancel(msg)
    return True


def cancel_reason_from_task(task: Any | None) -> str:
    """Read the stamp; absent ⇒ cancel did not go through ``stamp_cancel_reason``."""
    if task is None:
        return "cancelled_without_rpc"
    stamped = getattr(task, CANCEL_REASON_ATTR, None)
    if stamped is None:
        return "cancelled_without_rpc"
    return normalize_cancel_reason(stamped)


def cancel_reason_from_done_task(task: Any | None) -> str:
    """Stamp, then ``CancelledError`` args, then ``cancelled_without_rpc``."""
    stamped = cancel_reason_from_task(task)
    if stamped != "cancelled_without_rpc":
        return stamped
    if task is None or not task.done() or not task.cancelled():
        return "cancelled_without_rpc"
    try:
        task.exception()
    except asyncio.CancelledError as exc:
        if exc.args:
            return normalize_cancel_reason(exc.args[0])
        return "cancelled_without_rpc"
    except Exception:
        return "cancelled_without_rpc"
    return "cancelled_without_rpc"


def task_is_cancelling() -> bool:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return False
    if task is None:
        return False
    cancelling = getattr(task, "cancelling", None)
    if cancelling is None:
        return False
    return cancelling() > 0


def is_task_cancelled(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, asyncio.CancelledError):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def raise_if_task_cancelled(exc: BaseException | None = None) -> None:
    """Re-raise ``CancelledError`` when *exc* or the current task is a cancel.

    Call at retry-loop heads and in ``except HTTPError`` so a wrapped cancel
    cannot start the next attempt.
    """
    if exc is not None and is_task_cancelled(exc):
        raise asyncio.CancelledError from exc
    if task_is_cancelling():
        raise asyncio.CancelledError
