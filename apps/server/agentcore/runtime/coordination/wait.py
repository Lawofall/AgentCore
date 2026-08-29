"""Between-round coordination wait for the CEO ReAct loop (Phase 2)."""

from __future__ import annotations

import time

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.coordination.inject import events_to_messages
from agentcore.runtime.coordination.journal import record_coordination_snapshot
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    active_coordination,
)

logger = get_logger(__name__)

# Base idle wait for the next team event when the CEO has nothing else to do.
_COORD_WAIT_TIMEOUT_S = 120.0
# Idle backoff cap: each consecutive「无新事件」idle timeout *or* busy-wait yield
# doubles the wait (``base * 2**idle_streak``) up to this ceiling, so a quiet
# team stops burning a full LLM patrol/yield round every ~2 min. Real events
# reset the streak (see wait.py).
_COORD_WAIT_TIMEOUT_MAX_S = 600.0
# Frontend UX heartbeat while blocked in wait_events (≤15s per task constraint).
_WAIT_HEARTBEAT_S = 15.0
# After a failed worker_completed, briefly coalesce cascade skip/cancel so one
# inject carries 「甲失败，乙丙跳过」instead of a failure wake plus N skip wakes.
_CASCADE_COALESCE_S = 0.15
# Legacy merge knobs — progress-only events no longer wake; tests may still patch.
_MERGE_BATCH_MAX = 3
_MERGE_WINDOW_S = 60.0


def _drive_exhausted(session: CoordinationSession) -> bool:
    """True when the background drive cannot produce further team events."""
    if session.total_workers <= 0:
        return False
    if len(session.completed_run_ids) < session.total_workers:
        return False
    task = session.drive_task
    return task is None or task.done()


def _synthetic_all_completed(session: CoordinationSession) -> CoordinationEvent:
    """Race fallback when the bag is full and drive is gone but no terminal posted.

    Keep ``ALL_COMPLETED`` so wait still closes / stashes (``DRIVE_CANCELLED``
    would not close the session). Bag-full only means the terminal count filled
    — cancel into the bag is intentional — so stamp cancel / fail flags. Inject
    must not use the ``all_completed：`` prefix when cancelled (that reads as
    全员成功). A flag-less ``ALL_COMPLETED`` is the path host.py already
    rejected (fake「全员完成」).
    """
    cancelled = (session.cancel_ids & session.completed_run_ids) - session.failed_run_ids
    failed_n = len(session.failed_run_ids)
    payload: dict[str, object] = {
        "completed": len(session.completed_run_ids),
        "total": session.total_workers,
        "reason": "team_done_shortcircuit",
    }
    if cancelled:
        payload["cancelled"] = True
    if failed_n:
        payload["failed"] = failed_n
        if "cancelled" not in payload:
            payload["criteria_met"] = False
    return CoordinationEvent(
        kind=CoordinationEventKind.ALL_COMPLETED,
        payload=payload,
    )


def _emit_coordination_wait(session: CoordinationSession, *, waiting: bool) -> None:
    """Push ``coordination_wait`` to the live SSE sink (best-effort; never raises)."""
    sink = session.event_sink
    if sink is None:
        return
    try:
        from agentcore.runtime.events import coordination_wait

        sink.emit(
            coordination_wait(
                execution_id=session.execution_id,
                waiting=waiting,
                completed=len(session.completed_run_ids),
                total=session.total_workers,
            )
        )
    except Exception:  # noqa: BLE001 — UX signal must never break the CEO loop
        logger.warning(
            "coordination.wait_sse_failed",
            execution_id=session.execution_id,
            waiting=waiting,
            exc_info=True,
        )


async def _wait_events_with_ux(
    session: CoordinationSession,
    *,
    timeout: float,
) -> list[CoordinationEvent]:
    """``wait_events`` with enter/exit ``coordination_wait`` SSE + ≤15s heartbeats."""
    import asyncio

    _emit_coordination_wait(session, waiting=True)
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return []
            chunk = min(_WAIT_HEARTBEAT_S, remaining)
            events = await session.wait_events(timeout=chunk)
            if events:
                return events
            # Still blocked — refresh completed/total for the waiting UI.
            _emit_coordination_wait(session, waiting=True)
    finally:
        _emit_coordination_wait(session, waiting=False)


def _idle_wait_timeout(session: CoordinationSession) -> float:
    """Idle wait with exponential backoff (空转唤醒降频).

    First idle wait uses ``_COORD_WAIT_TIMEOUT_S``; each further「无新事件」idle
    timeout widens it by ``2**idle_streak`` up to ``_COORD_WAIT_TIMEOUT_MAX_S``, so a
    quiet team is patrolled ever less often (still patrolled — 保留卡死巡查语义).
    """
    timeout = _COORD_WAIT_TIMEOUT_S * (2 ** max(0, session.idle_streak))
    return min(timeout, _COORD_WAIT_TIMEOUT_MAX_S)


def _idle_patrol_nudge(session: CoordinationSession) -> CoordinationEvent:
    """Periodic「无事件」patrol nudge (distinct from a per-worker TIMEOUT).

    Kept so the CEO can still notice a wedged worker and ``cancel_worker`` — the
    frequency is throttled by :func:`_idle_wait_timeout`, not removed. When the
    team truly stalled (no in-flight LLM/tool), attach each worker's progress
    line so the CEO can decide without guessing.
    """
    progress = session.worker_progress_summary()
    reason = (
        f"等待团队事件超时（已完成 {len(session.completed_run_ids)}/"
        f"{session.total_workers}）。可继续静默等待、cancel_worker（队员"
        "疑似卡死时）、或 ask_user；期间无新语义增量勿调 update_synthesis。"
        f"\n{progress}"
    )
    return CoordinationEvent(
        kind=CoordinationEventKind.TIMEOUT,
        payload={
            "run_id": "",
            "role": "team",
            "status": "idle_wait",
            "reason": reason,
        },
    )


def _has_failed_worker(events: list[CoordinationEvent]) -> bool:
    from agentcore.runtime.coordination.session_budget import _worker_completion_failed

    return any(_worker_completion_failed(ev) for ev in events)


async def _coalesce_cascade(
    session: CoordinationSession,
    events: list[CoordinationEvent],
) -> list[CoordinationEvent]:
    """After a failure, scoop skip/cancel posted in the same scheduler tick."""
    extra = await session.wait_events(timeout=_CASCADE_COALESCE_S)
    if extra:
        events = events + extra
    return events


def _drop_drive_cancelled_while_hot_pending(
    session: CoordinationSession, events: list[CoordinationEvent]
) -> list[CoordinationEvent]:
    """Hot user card: DRIVE_CANCELLED must not wake a 调度中断 close."""
    from agentcore.runtime.interaction_orphan import holds_for_hot_user

    if not holds_for_hot_user(session):
        return events
    return [e for e in events if e.kind is not CoordinationEventKind.DRIVE_CANCELLED]


async def await_coordination_injection(
    messages: list[LLMMessage],
) -> list[LLMMessage]:
    """If a coordination session is active, wait for team events and inject them.

    Called at the top of each ReAct round after the first. Returns messages to
    append (possibly empty when not coordinating).

    唤醒降噪（协调层记账开销治理）：
    - 例行成功 worker_completed / skip / cancel **不叫醒** CEO，暂存后挂在
      下一次必要决策（失败 / 升级 / 插话 / 超时 / 边界 / 全员完成 / 整队取消）。
    - 失败立刻叫醒，并短暂收口级联跳过/取消，合成一条注入。
    - 空转退避（:func:`_idle_wait_timeout`）：无事件的 idle 巡查按 ``2**idle_streak``
      拉长；忙等（队员仍有 in-flight LLM/工具）**不叫醒** CEO，只退避再等。无人
      in-flight 的卡死仍发周期性 patrol nudge。
    - 阻塞 escalate 已登记 ``pending_arbitrations`` 且队列已空：立刻返回空列表，
      让本轮 ReAct 去 ``resolve_escalation``，勿 idle-wait 挂起队员。队列里已有
      ESCALATION 时仍走必要决策注入（禁止用 pending 短路首次升级注入）。
    - 两池预算（进度池 / 决策池，见 session.py）：纯遥测计数，不闸唤醒。
    """
    session = active_coordination()
    if session is None or not session.active:
        return []

    t0 = time.perf_counter()
    logger.info(
        "coordination.wait_start",
        execution_id=session.execution_id,
        completed=len(session.completed_run_ids),
        total=session.total_workers,
        progress_budget=session.progress_budget_remaining,
        decision_budget=session.decision_budget_remaining,
        idle_streak=session.idle_streak,
        drive_done=(
            session.drive_task is None or session.drive_task.done()
        ),
    )

    # 双池降级为纯遥测（批次 4）：不再因进度池耗尽 HOLD；合并窗口仍攒批，预算只计数。
    merged = 0
    wait_reason = "drained"
    while True:
        events = session.drain_nowait()
        wait_reason = "drained"
        if events:
            # Real team activity already queued — clear any idle-patrol backoff.
            session.reset_idle_backoff()
        elif session.pending_arbitrations:
            # 升级事件已注入（或本轮将由 ReAct 兑现）；挂起队员的 channel.request
            # 仍像 tool in-flight。空等它只会让用户面假进度空转十几分钟。
            wait_reason = "pending_arbitration"
            waited_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "coordination.wait_end",
                execution_id=session.execution_id,
                waited_ms=waited_ms,
                wait_reason=wait_reason,
                events=[],
                merged=merged,
                idle_streak=session.idle_streak,
                completed=len(session.completed_run_ids),
                total=session.total_workers,
                progress_budget=session.progress_budget_remaining,
                decision_budget=session.decision_budget_remaining,
                pending=len(session.pending_arbitrations),
            )
            return []
        elif _drive_exhausted(session):
            # 竞态兜底（非主保障）：主保障见终态对账（附着注入 / harvest）。
            wait_reason = "team_done_shortcircuit"
            events = [_synthetic_all_completed(session)]
            short = events[0].payload
            logger.warning(
                "coordination.team_done_shortcircuit",
                execution_id=session.execution_id,
                completed=len(session.completed_run_ids),
                total=session.total_workers,
                cancelled=bool(short.get("cancelled")),
                failed=short.get("failed", 0),
                detail=(
                    "竞态兜底：终态袋已满且 drive 已结束，队列仍无终态事件。"
                    "主保障是终态对账（附着注入/harvest）；请追查 drive/host 漏投竞态。"
                ),
            )
        else:
            events = await _wait_events_with_ux(
                session, timeout=_idle_wait_timeout(session)
            )
            if events:
                session.reset_idle_backoff()
                wait_reason = "waited"
            else:
                # No coordination events for the idle window. If workers still have
                # in-flight LLM/tool calls, progress simply has not posted yet —
                # do NOT fire a TIMEOUT patrol nudge (那会烧冤枉 LLM 轮). One short
                # re-wait for real events; if still busy, hold (CEO would only wait).
                # User interrupt / failure / all_completed still arrive via the queue.
                # True stall (no in-flight) still yields so CEO can cancel_worker.
                if session.has_inflight_work():
                    wait_reason = "idle_active"
                    logger.info(
                        "coordination.idle_patrol_deferred",
                        execution_id=session.execution_id,
                        completed=len(session.completed_run_ids),
                        total=session.total_workers,
                        busy=len(session._busy_workers),
                        running=len(session.running_workers()),
                    )
                    more = await _wait_events_with_ux(
                        session, timeout=min(1.0, _WAIT_HEARTBEAT_S)
                    )
                    if more:
                        events = more
                        session.reset_idle_backoff()
                        wait_reason = "waited"
                    else:
                        session.bump_idle_backoff()
                        logger.info(
                            "coordination.idle_yield_held_inflight",
                            execution_id=session.execution_id,
                            completed=len(session.completed_run_ids),
                            total=session.total_workers,
                            busy=len(session._busy_workers),
                            idle_streak=session.idle_streak,
                        )
                        continue
                else:
                    session.bump_idle_backoff()
                    wait_reason = "idle_timeout"

        if events:
            events = session.take_deferred_progress() + events
            events = _drop_drive_cancelled_while_hot_pending(session, events)
            if not events:
                continue

        # 失败后短暂收口级联 skip/cancel，合成一次注入。
        if (
            events
            and wait_reason in ("drained", "waited")
            and _has_failed_worker(events)
        ):
            before = len(events)
            events = await _coalesce_cascade(session, events)
            merged += len(events) - before

        nudge = False
        if not events:
            # Still coordinating but nothing arrived — patrol nudge (卡死巡查兜底).
            events = [_idle_patrol_nudge(session)]
            nudge = True

        necessary = session.is_necessary_decision(events)
        # 必要决策：记决策池遥测，立即唤醒。
        if necessary:
            session.consume_decision_budget()
            break
        # 空转巡查 nudge：保留卡死巡查语义，直接唤醒，不消耗任一池。
        if nudge:
            break
        # 例行进展（成功完成 / note / skip / cancel）：入账暂存，不唤醒 CEO。
        session.stash_progress_events(events)
        session.note_decision_points(events)
        if not session.consume_progress_budget():
            logger.info(
                "coordination.progress_budget_floor",
                execution_id=session.execution_id,
                decision_budget=session.decision_budget_remaining,
            )
        continue

    # 记账首个完成等决策点（对齐 is_necessary_decision）。
    session.note_decision_points(events)
    from agentcore.runtime.coordination.interjections import note_interjections_injected

    await note_interjections_injected(session, events)

    waited_ms = int((time.perf_counter() - t0) * 1000)
    event_kinds = [e.kind.value for e in events]
    logger.info(
        "coordination.wait_end",
        execution_id=session.execution_id,
        waited_ms=waited_ms,
        wait_reason=wait_reason,
        events=event_kinds,
        merged=merged,
        idle_streak=session.idle_streak,
        completed=len(session.completed_run_ids),
        total=session.total_workers,
        progress_budget=session.progress_budget_remaining,
        decision_budget=session.decision_budget_remaining,
    )

    has_terminal = any(
        e.kind
        in (
            CoordinationEventKind.ALL_COMPLETED,
            CoordinationEventKind.DRIVE_CANCELLED,
        )
        for e in events
    )
    if has_terminal:
        session.mark_settled("attached_inject")
    has_all = any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in events)
    if has_all:
        session.all_completed_injected = True
        session.close()
        logger.info(
            "coordination.all_completed",
            execution_id=session.execution_id,
            completed=len(session.completed_run_ids),
            total=session.total_workers,
        )

    record_coordination_snapshot(session)
    injected = events_to_messages(session, events)
    # dep_advisories (builder.suspect_missing_dep 搭车) rode this injection via
    # ``format_coordination_events`` — consume once so later injections stay clean.
    if session.dep_advisories:
        session.dep_advisories.clear()
    # Stamp this wake so the next batch of progress events throttles against it.
    session.note_wake()
    logger.debug(
        "coordination.injected",
        execution_id=session.execution_id,
        events=event_kinds,
        progress_budget=session.progress_budget_remaining,
        decision_budget=session.decision_budget_remaining,
    )
    return injected
