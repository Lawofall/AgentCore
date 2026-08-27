"""Sidecar re-export of the shared cancel-reason stamp.

Solo blocking drives never arm a coordination session, so ``coordination.user_stop_*``
is absent on cancel. Stamping the asyncio.Task lets ``CancelledError`` salvage log
``reason`` even when the only prior signal was ``task.cancel()``.
"""

from __future__ import annotations

from agentcore.core.task_cancel import (
    CANCEL_REASON_ATTR,
    KNOWN_CANCEL_REASONS,
    cancel_reason_from_task,
    normalize_cancel_reason,
    stamp_cancel_reason,
)

__all__ = (
    "CANCEL_REASON_ATTR",
    "KNOWN_CANCEL_REASONS",
    "cancel_reason_from_task",
    "normalize_cancel_reason",
    "stamp_cancel_reason",
)
