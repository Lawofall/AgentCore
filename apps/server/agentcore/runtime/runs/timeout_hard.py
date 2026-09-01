"""Worker 超时硬收尾：TIMEOUT 后禁新调用 → 宽限一轮交卷 → 强制取消。

与协调 session 解耦：嵌套子团队（无 CoordinationSession）与根协调共用同一状态机。
引擎在 LLM / 工具入口查询本模块；计时器由 :func:`arm_hard_timeout` 武装。

墙钟语义：阈值只累计 worker **自身编排 / 空转**时间。以下区间暂停倒计时：

- LLM 调用在飞（:func:`mark_llm_inflight`）——避免慢上游单次调用被误判；
- 嵌套子团队阻塞等待（:func:`mark_waiting_children`）—— 凡 captain 在
  nested ``drive`` 墙钟内等子队员时，不得按「自己在干活」耗尽 hard-timeout /
  ``grace_wall`` 强杀父节点。

普通非嵌套 worker 的空转 / 自有工具时间仍累计。wind_down 软着陆与宽限轮机制不变。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

# 宽限轮墙钟上限（秒）：宽限一轮交卷；单轮跨太久仍强制取消，避免无限拖。
DEFAULT_GRACE_WALL_S = 90.0
# Active-time poll chunk: small enough for unit tests, negligible vs 300s+ prod thresholds.
_ACTIVE_SLEEP_CHUNK_S = 0.05


class HardTimeoutPhase(StrEnum):
    ARMED = "armed"
    WARNED = "warned"
    TIMED_OUT = "timed_out"
    GRACE = "grace"
    FORCE_CANCEL = "force_cancel"
    DISARMED = "disarmed"


@dataclass
class HardTimeoutGuard:
    """Per-run hard-timeout state machine (shared by coordination + nested drives)."""

    run_id: str
    threshold_s: float
    role: str = ""
    warn_ratio: float = 0.75
    grace_wall_s: float = DEFAULT_GRACE_WALL_S
    # Optional hooks (coordination posts TIMEOUT / requests cancel via cancel_ids).
    on_warn: Any | None = field(default=None, repr=False)
    on_timeout: Any | None = field(default=None, repr=False)
    on_force_cancel: Any | None = field(default=None, repr=False)

    phase: HardTimeoutPhase = HardTimeoutPhase.ARMED
    wind_down_pending: bool = False
    wind_down_entered: bool = False
    grace_granted: bool = False
    grace_consumed: bool = False
    force_cancel_requested: bool = False
    started_at: float = field(default_factory=time.monotonic)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    # Nested-safe depth: pause active-time countdown while > 0
    # (LLM inflight and/or waiting on nested children).
    _pause_depth: int = field(default=0, repr=False)
    _resume_event: asyncio.Event | None = field(default=None, repr=False)

    def arm(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.phase = HardTimeoutPhase.ARMED
        self.wind_down_pending = False
        self.wind_down_entered = False
        self.grace_granted = False
        self.grace_consumed = False
        self.force_cancel_requested = False
        self._pause_depth = 0
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self.started_at = time.monotonic()
        self._task = asyncio.create_task(
            self._fire(), name=f"hard-timeout-{self.run_id[:12]}"
        )

    def disarm(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        if self.phase is not HardTimeoutPhase.FORCE_CANCEL:
            self.phase = HardTimeoutPhase.DISARMED
        # Unblock any waiter in _sleep_active.
        if self._resume_event is not None and not self._resume_event.is_set():
            self._resume_event.set()

    def mark_timeout_paused(self, paused: bool) -> None:
        """Pause (True) / resume (False) active-time countdown (nested-safe depth)."""
        if self._resume_event is None:
            self._resume_event = asyncio.Event()
            self._resume_event.set()
        if paused:
            was_idle = self._pause_depth == 0
            self._pause_depth += 1
            if was_idle:
                self._resume_event.clear()
            return
        if self._pause_depth <= 0:
            return
        self._pause_depth -= 1
        if self._pause_depth == 0:
            self._resume_event.set()

    def mark_llm_inflight(self, inflight: bool) -> None:
        """Pause / resume active-time around an LLM call."""
        self.mark_timeout_paused(inflight)

    def mark_waiting_children(self, waiting: bool) -> None:
        """Pause / resume while this worker blocks on a nested sub-team drive."""
        self.mark_timeout_paused(waiting)

    async def _sleep_active(self, duration: float) -> bool:
        """Accumulate ``duration`` seconds of non-paused active time.

        Returns False if the guard was disarmed before the budget was spent.
        Paused intervals (LLM inflight / waiting children) do not consume the
        budget (wall clock may exceed ``duration``).
        """
        remaining = max(0.0, float(duration))
        while remaining > 0.0:
            if self.phase is HardTimeoutPhase.DISARMED:
                return False
            if self._pause_depth > 0:
                event = self._resume_event
                if event is None:
                    await asyncio.sleep(_ACTIVE_SLEEP_CHUNK_S)
                    continue
                try:
                    await asyncio.wait_for(event.wait(), timeout=0.25)
                except TimeoutError:
                    continue
                continue
            chunk = min(remaining, _ACTIVE_SLEEP_CHUNK_S)
            await asyncio.sleep(chunk)
            if self.phase is HardTimeoutPhase.DISARMED:
                return False
            # Became paused mid-chunk → refund (safe direction: fewer false timeouts).
            if self._pause_depth > 0:
                continue
            remaining -= chunk
        return True

    async def _fire(self) -> None:
        threshold = max(0.0, float(self.threshold_s))
        warn_ratio = float(self.warn_ratio)
        warn_at = threshold * warn_ratio if 0.0 < warn_ratio < 1.0 else 0.0
        try:
            if warn_at > 0.0:
                if not await self._sleep_active(warn_at):
                    return
                if self.phase is HardTimeoutPhase.DISARMED:
                    return
                self.phase = HardTimeoutPhase.WARNED
                self.wind_down_pending = True
                logger.info(
                    "worker.timeout_warn",
                    run_id=self.run_id,
                    elapsed_s=round(warn_at, 1),
                    threshold_s=threshold,
                    warn_ratio=warn_ratio,
                )
                if self.on_warn is not None:
                    self.on_warn(self)
                remaining = max(0.0, threshold - warn_at)
                if remaining > 0.0 and not await self._sleep_active(remaining):
                    return
            else:
                if not await self._sleep_active(threshold):
                    return
            if self.phase is HardTimeoutPhase.DISARMED:
                return
            self._enter_timed_out()
            # Grace wall: if the worker never finishes the grace round, force cancel.
            # Also pauses during LLM so a slow handoff call is not force-cancelled mid-flight.
            if not await self._sleep_active(max(0.0, float(self.grace_wall_s))):
                return
            if self.phase in (
                HardTimeoutPhase.DISARMED,
                HardTimeoutPhase.FORCE_CANCEL,
            ):
                return
            if not self.grace_consumed:
                # Still inside grace round or never started it — force cancel.
                self.request_force_cancel(reason="grace_wall")
        except asyncio.CancelledError:
            return

    def _enter_timed_out(self) -> None:
        if self.phase in (
            HardTimeoutPhase.TIMED_OUT,
            HardTimeoutPhase.GRACE,
            HardTimeoutPhase.FORCE_CANCEL,
            HardTimeoutPhase.DISARMED,
        ):
            return
        self.phase = HardTimeoutPhase.TIMED_OUT
        # Ensure wind-down is pending even if warn was skipped (ratio off).
        self.wind_down_pending = True
        elapsed = time.monotonic() - self.started_at
        logger.info(
            "worker.timeout_hard",
            run_id=self.run_id,
            elapsed_s=round(elapsed, 1),
            threshold_s=self.threshold_s,
        )
        if self.on_timeout is not None:
            self.on_timeout(self)

    def consume_wind_down(self) -> bool:
        """True once when a warn/timeout wind-down is pending (engine arms tools)."""
        if self.wind_down_pending:
            self.wind_down_pending = False
            self.wind_down_entered = True
            return True
        return False

    def peek_wind_down_pending(self) -> bool:
        return self.wind_down_pending

    def begin_grace_round(self) -> bool:
        """Grant the single post-TIMEOUT delivery round. True when newly granted."""
        if self.phase is not HardTimeoutPhase.TIMED_OUT:
            return False
        if self.grace_granted or self.grace_consumed:
            return False
        self.phase = HardTimeoutPhase.GRACE
        self.grace_granted = True
        self.wind_down_pending = True
        logger.info("worker.timeout_grace_begin", run_id=self.run_id)
        return True

    def end_grace_round(self) -> None:
        """Mark grace round finished; next LLM/tool entry must force-cancel."""
        if self.phase is HardTimeoutPhase.GRACE or self.grace_granted:
            self.grace_consumed = True
            if self.phase is not HardTimeoutPhase.FORCE_CANCEL:
                self.phase = HardTimeoutPhase.TIMED_OUT
            logger.info("worker.timeout_grace_end", run_id=self.run_id)

    def request_force_cancel(self, *, reason: str = "grace_exhausted") -> None:
        if self.force_cancel_requested:
            return
        self.force_cancel_requested = True
        self.phase = HardTimeoutPhase.FORCE_CANCEL
        logger.info(
            "worker.timeout_force_cancel",
            run_id=self.run_id,
            reason=reason,
        )
        if self.on_force_cancel is not None:
            self.on_force_cancel(self, reason)

    def blocks_new_work(self) -> bool:
        """True when a new LLM/tool invocation must not start."""
        if self.phase is HardTimeoutPhase.FORCE_CANCEL:
            return True
        if self.phase is HardTimeoutPhase.TIMED_OUT and self.grace_consumed:
            return True
        # About to grant grace at round boundary — block until grace begins
        # so we don't start a non-wind-down round after TIMEOUT.
        return self.phase is HardTimeoutPhase.TIMED_OUT and not self.grace_granted

    def allows_grace_round(self) -> bool:
        return self.phase is HardTimeoutPhase.TIMED_OUT and not self.grace_granted

    def was_timed_out(self) -> bool:
        return self.phase in (
            HardTimeoutPhase.TIMED_OUT,
            HardTimeoutPhase.GRACE,
            HardTimeoutPhase.FORCE_CANCEL,
        ) or self.force_cancel_requested

    def shrank_delivery(self) -> bool:
        """True when timeout left real shrinkage evidence (wind-down or force cancel)."""
        return self.wind_down_entered or self.force_cancel_requested


# Process-local registry (worker task keys by run_id).
_GUARDS: dict[str, HardTimeoutGuard] = {}


def get_hard_timeout(run_id: str) -> HardTimeoutGuard | None:
    if not run_id:
        return None
    return _GUARDS.get(run_id)


def mark_timeout_paused(run_id: str, paused: bool) -> None:
    """Pause/resume hard-timeout active-time (LLM inflight or waiting children).

    No-op when ``run_id`` has no armed guard (CEO / solo / already disarmed).
    """
    if not run_id:
        return
    guard = _GUARDS.get(run_id)
    if guard is None:
        return
    guard.mark_timeout_paused(paused)


def mark_llm_inflight(run_id: str, inflight: bool) -> None:
    """Pause/resume hard-timeout active-time while an LLM call is in flight."""
    mark_timeout_paused(run_id, inflight)


def mark_waiting_children(run_id: str, waiting: bool) -> None:
    """Pause/resume hard-timeout while a nested lead blocks on its sub-team.

    Armed on nested ``drive`` (``depth > 0``) against the lead's ``captain_run_id``.
    """
    mark_timeout_paused(run_id, waiting)


def arm_hard_timeout(
    run_id: str,
    *,
    timeout_s: float | int | None,
    role: str = "",
    warn_ratio: float | None = None,
    grace_wall_s: float | None = None,
    on_warn: Any | None = None,
    on_timeout: Any | None = None,
    on_force_cancel: Any | None = None,
) -> HardTimeoutGuard | None:
    """Arm (or refresh) a hard-timeout guard.

    ``timeout_s`` None / ≤0 → do not arm. No product-default wall clock;
    only CEO-explicit ``timeout_ms`` (already converted to seconds) arms a timer.
    """
    if not run_id:
        return None
    try:
        threshold = float(timeout_s) if timeout_s is not None else 0.0
    except (TypeError, ValueError):
        return None
    if threshold <= 0:
        return None
    existing = _GUARDS.get(run_id)
    if existing is not None and existing._task is not None and not existing._task.done():
        # Keep registry current for cancel resolution; do not reset mid-flight.
        if role:
            existing.role = role
        return existing
    if warn_ratio is None:
        try:
            from agentcore.config import settings

            warn_ratio = float(settings.engine_worker_timeout_warn_ratio or 0.0)
        except Exception:  # noqa: BLE001
            warn_ratio = 0.75
    guard = HardTimeoutGuard(
        run_id=run_id,
        threshold_s=threshold,
        role=role or run_id,
        warn_ratio=float(warn_ratio or 0.0),
        grace_wall_s=(
            float(grace_wall_s)
            if grace_wall_s is not None
            else DEFAULT_GRACE_WALL_S
        ),
        on_warn=on_warn,
        on_timeout=on_timeout,
        on_force_cancel=on_force_cancel,
    )
    _GUARDS[run_id] = guard
    guard.arm()
    return guard


def disarm_hard_timeout(run_id: str) -> HardTimeoutGuard | None:
    guard = _GUARDS.pop(run_id, None)
    if guard is not None:
        guard.disarm()
    return guard


def clear_all_hard_timeouts() -> None:
    for run_id in list(_GUARDS):
        disarm_hard_timeout(run_id)
