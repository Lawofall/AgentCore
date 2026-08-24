"""Coordination telemetry budget pools and CEO wake / idle-backoff stamps.

Split from ``session.py`` — pure move. Pools are observational only; they do
not gate wakes. Queue wait lives on ``SessionQueueMixin``.
"""

# mypy: disable-error-code="misc"

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agentcore.runtime.coordination.session_types import (
    CoordinationEvent,
    CoordinationEventKind,
)

if TYPE_CHECKING:
    from agentcore.runtime.coordination.session import CoordinationSession


_FAILED_WORKER_STATUSES = frozenset({"failed"})


def _worker_completion_failed(event: CoordinationEvent) -> bool:
    """True when this event is a worker terminal failure (not skip/cancel/success)."""
    if event.kind is not CoordinationEventKind.WORKER_COMPLETED:
        return False
    status = str(event.payload.get("status") or "").strip().lower()
    return status in _FAILED_WORKER_STATUSES


class SessionBudgetMixin:
    """Progress/decision telemetry counters plus wake-time / idle-streak stamps."""

    last_wake_monotonic: float | None
    _deferred_progress: list[CoordinationEvent]

    @property
    def budget_remaining(self: CoordinationSession) -> int:
        """两池合计（便利读；不参与序列化）。"""
        return self.progress_budget_remaining + self.decision_budget_remaining

    def consume_progress_budget(self: CoordinationSession) -> bool:
        """Decrement 进度池 telemetry counter. Always returns True after counting.

        批次 4：池耗尽不再 HOLD 唤醒——调用方仍须唤醒；返回值仅表示「本次是否从正数扣减」。
        """
        if self.progress_budget_remaining <= 0:
            return False
        self.progress_budget_remaining -= 1
        return True

    def stash_progress_events(
        self: CoordinationSession, events: list[CoordinationEvent]
    ) -> None:
        """Hold non-waking progress until the next necessary inject."""
        if events:
            self._deferred_progress.extend(events)

    def take_deferred_progress(self: CoordinationSession) -> list[CoordinationEvent]:
        """Pop stashed progress so it can ride a necessary (or terminal) wake."""
        held = self._deferred_progress
        self._deferred_progress = []
        return held

    def consume_decision_budget(self: CoordinationSession) -> bool:
        """Decrement 决策池 telemetry counter. Returns False when already at floor 0.

        必要决策永不因预算被跳过——调用方仍须唤醒；floor-0 只喂遥测。
        """
        if self.decision_budget_remaining <= 0:
            return False
        self.decision_budget_remaining -= 1
        return True

    def is_necessary_decision(
        self: CoordinationSession, events: list[CoordinationEvent]
    ) -> bool:
        """Necessary decision points always wake the CEO (even under budget pressure).

        Routine success ``worker_completed`` is not a decision point — DAG and
        the collaboration graph already moved. Failed completions are: the CEO
        can still replace / replan while siblings run. Skip / cancel ride the
        failure (or ``DRIVE_CANCELLED`` / ``ALL_COMPLETED``) instead of waking
        on their own.
        """
        for ev in events:
            if ev.kind is CoordinationEventKind.ALL_COMPLETED:
                return True
            if ev.kind is CoordinationEventKind.DRIVE_CANCELLED:
                return True
            if ev.kind is CoordinationEventKind.ESCALATION:
                return True
            if ev.kind is CoordinationEventKind.USER_INTERJECTION:
                # Boss mid-flight message — always wake; CEO routes in-graph vs queue.
                return True
            if ev.kind is CoordinationEventKind.TIMEOUT and ev.payload.get("run_id"):
                # Per-worker timeout is a decision point; idle-wait nudge (no run_id) is not.
                return True
            if ev.kind is CoordinationEventKind.BOUNDARY_YIELD:
                return True
            if _worker_completion_failed(ev):
                return True
        return False

    def note_decision_points(
        self: CoordinationSession, events: list[CoordinationEvent]
    ) -> None:
        for ev in events:
            if ev.kind is CoordinationEventKind.WORKER_COMPLETED:
                self._saw_first_completion = True

    def note_wake(self: CoordinationSession) -> None:
        """Stamp the last CEO wake time — batching throttles follow-ups from here."""
        self.last_wake_monotonic = time.monotonic()

    def seconds_since_wake(self: CoordinationSession) -> float | None:
        """Seconds since the last CEO wake, or ``None`` when never woken."""
        if self.last_wake_monotonic is None:
            return None
        return time.monotonic() - self.last_wake_monotonic

    def bump_idle_backoff(self: CoordinationSession) -> None:
        """One more consecutive idle timeout or busy-wait yield (widen next wait)."""
        self.idle_streak += 1

    def reset_idle_backoff(self: CoordinationSession) -> None:
        """Real team activity arrived — reset the idle-patrol backoff."""
        self.idle_streak = 0
