"""Per-worker hard-timeout arm / warn / notify / force-cancel.

Split from ``session.py`` — pure move. Worker registry lives on
``SessionWorkersMixin``; this mixin only wraps ``timeout_hard``.
"""

# mypy: disable-error-code="misc"

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session_types import (
    DEFAULT_WORKER_TIMEOUT_S,
    CoordinationEvent,
    CoordinationEventKind,
)

if TYPE_CHECKING:
    from agentcore.runtime.coordination.session import CoordinationSession

logger = get_logger("agentcore.runtime.coordination.session")


class SessionTimeoutMixin:
    """Arm / consume / disarm per-worker wall-clock timeout."""

    def arm_worker_timeout(
        self: CoordinationSession,
        run_id: str,
        *,
        role: str = "",
        timeout_s: float | int | None = None,
    ) -> None:
        """Arm hard-timeout for ``run_id`` (warn → TIMEOUT → grace → force cancel).

        Two-phase warn + hard TIMEOUT notification to the CEO; after TIMEOUT the
        engine bans new LLM/tool calls, grants one wind-down grace round, then
        force-cancels via :meth:`request_cancel` (same cancel_ids channel as
        ``cancel_worker``). Nested drives without a session use the same
        :mod:`timeout_hard` registry.
        """
        if not self.active or run_id in self.completed_run_ids:
            return
        # Register the in-flight worker for cancel_worker short→full resolution
        # (refreshed each dispatch; before the idempotent-arm short-circuit so a
        # re-arm still keeps the registry current). Cleared on disarm / completion.
        self._running_workers[run_id] = role or run_id
        from agentcore.runtime.runs.timeout_hard import (
            HardTimeoutGuard,
            arm_hard_timeout,
            get_hard_timeout,
        )

        existing = get_hard_timeout(run_id)
        if existing is not None and existing._task is not None and not existing._task.done():
            if role:
                existing.role = role or existing.role
            return

        self._worker_started_at[run_id] = time.monotonic()
        self._timeout_notified.discard(run_id)
        self._timeout_warned.discard(run_id)
        self._timeout_wind_down_pending.discard(run_id)
        self._timeout_wind_down_entered.discard(run_id)
        self._timeout_force_cancelled.discard(run_id)

        def _on_warn(guard: HardTimeoutGuard) -> None:
            if not self.active or run_id in self.completed_run_ids:
                return
            self._timeout_warned.add(run_id)
            self._timeout_wind_down_pending.add(run_id)
            logger.info(
                "coordination.worker_timeout_warn",
                run_id=run_id,
                elapsed_s=round(guard.threshold_s * guard.warn_ratio, 1),
                threshold_s=guard.threshold_s,
                warn_ratio=guard.warn_ratio,
                execution_id=self.execution_id,
            )

        def _on_timeout(guard: HardTimeoutGuard) -> None:
            if not self.active or run_id in self.completed_run_ids:
                return
            if run_id in self._timeout_notified:
                return
            self._timeout_notified.add(run_id)
            self._timeout_wind_down_pending.add(run_id)
            started = self._worker_started_at.get(run_id)
            elapsed = (time.monotonic() - started) if started is not None else guard.threshold_s
            status = "cancel_requested" if run_id in self.cancel_ids else "running"
            self.post(
                CoordinationEvent(
                    kind=CoordinationEventKind.TIMEOUT,
                    payload={
                        "run_id": run_id,
                        "role": role or run_id,
                        "elapsed_s": round(elapsed, 1),
                        "threshold_s": guard.threshold_s,
                        "status": status,
                        "hard": True,
                        "reason": (
                            f"队员已运行约 {round(elapsed)}s（阈值 {int(guard.threshold_s)}s），"
                            "仍未交付。执行面已进入硬收尾：禁新调查调用、宽限一轮交卷，"
                            "超宽限将强制取消。可 update_synthesis 先出中间合成，"
                            "或 cancel_worker 立即终止。"
                        ),
                    },
                )
            )
            logger.info(
                "coordination.worker_timeout",
                run_id=run_id,
                elapsed_s=round(elapsed, 1),
                threshold_s=guard.threshold_s,
                execution_id=self.execution_id,
                hard=True,
            )

        def _on_force_cancel(guard: HardTimeoutGuard, reason: str) -> None:
            if run_id in self.completed_run_ids:
                return
            self._timeout_force_cancelled.add(run_id)
            self.request_cancel(run_id)
            logger.info(
                "coordination.worker_timeout_force_cancel",
                run_id=run_id,
                reason=reason,
                execution_id=self.execution_id,
            )

        guard = arm_hard_timeout(
            run_id,
            timeout_s=timeout_s,
            role=role or run_id,
            warn_ratio=None,  # resolved inside arm_hard_timeout from settings
            on_warn=_on_warn,
            on_timeout=_on_timeout,
            on_force_cancel=_on_force_cancel,
            default_timeout_s=DEFAULT_WORKER_TIMEOUT_S,
        )
        # Mirror timer task into legacy map so cancel_all_timeouts / disarm still work.
        if guard is not None and guard._task is not None:
            self._timeout_tasks[run_id] = guard._task

    def consume_timeout_wind_down(self: CoordinationSession, run_id: str) -> bool:
        """True once when a timeout warn is pending for ``run_id`` (worker loop arms wind-down).

        消费即记入 ``_timeout_wind_down_entered``——这是「该 worker 真正进入过 timeout
        wind-down（工具面据此被收窄）」的唯一权威痕迹，供收尾对账区分真缩水与自然完成。
        """
        from agentcore.runtime.runs.timeout_hard import get_hard_timeout

        guard = get_hard_timeout(run_id)
        if guard is not None and guard.consume_wind_down():
            self._timeout_wind_down_pending.discard(run_id)
            self._timeout_wind_down_entered.add(run_id)
            return True
        if run_id in self._timeout_wind_down_pending:
            self._timeout_wind_down_pending.discard(run_id)
            self._timeout_wind_down_entered.add(run_id)
            return True
        return False

    def was_timeout_notified(self: CoordinationSession, run_id: str) -> bool:
        """Whether the CEO-facing TIMEOUT notification already fired for ``run_id``."""
        return run_id in self._timeout_notified

    def entered_timeout_wind_down(self: CoordinationSession, run_id: str) -> bool:
        """Whether ``run_id`` actually consumed a timeout wind-down (tools narrowed).

        仅 :meth:`consume_timeout_wind_down` 被引擎消费后为真；「仅 pending 未消费」
        （worker 在预警窗内自然完成、引擎从未收窄工具面）不算——故超时通知后自然完成不留此痕。
        """
        from agentcore.runtime.runs.timeout_hard import get_hard_timeout

        guard = get_hard_timeout(run_id)
        if guard is not None and guard.wind_down_entered:
            return True
        return run_id in self._timeout_wind_down_entered

    def was_timeout_force_cancelled(self: CoordinationSession, run_id: str) -> bool:
        """Whether hard-timeout force-cancelled ``run_id`` via cancel_ids."""
        from agentcore.runtime.runs.timeout_hard import get_hard_timeout

        guard = get_hard_timeout(run_id)
        if guard is not None and guard.force_cancel_requested:
            return True
        return run_id in self._timeout_force_cancelled

    def disarm_worker_timeout(self: CoordinationSession, run_id: str) -> None:
        from agentcore.runtime.runs.timeout_hard import disarm_hard_timeout

        disarm_hard_timeout(run_id)
        self._timeout_tasks.pop(run_id, None)
        self._worker_started_at.pop(run_id, None)
        # Drop from the cancel-resolution registry so a finished worker is no
        # longer resolvable (mark_worker_completed routes through here too).
        self._running_workers.pop(run_id, None)
        self._busy_workers.pop(run_id, None)
        self._worker_spend.pop(run_id, None)

    def cancel_all_timeouts(self: CoordinationSession) -> None:
        for run_id in list(self._timeout_tasks):
            self.disarm_worker_timeout(run_id)
        # Also disarm any registry entries still keyed for this session's workers.
        for run_id in list(self._running_workers):
            self.disarm_worker_timeout(run_id)
