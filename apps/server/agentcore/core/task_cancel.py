"""Task cancellation as a first-class outbound-I/O signal.

A run's in-flight HTTP (LLM streams, tool fetches) belongs to the same cancel
scope as the worker task. ``asyncio.CancelledError`` is terminal: abort the
request and never enter a retry loop. httpx/httpcore may wrap the cancel in
``HTTPError``; walk ``__cause__`` / ``__context__``. ``Task.cancelling()``
(3.11+) covers the window where the wrapper already replaced the exception.
"""

from __future__ import annotations

import asyncio


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
