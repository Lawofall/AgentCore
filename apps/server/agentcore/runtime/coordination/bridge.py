"""Bridge worker escalate signals into the CEO coordination queue.

Phase 3: when a :class:`CoordinationSession` is active, escalations post into
the event queue so the living CEO can arbitrate — they do **not** force a
supervised SCOPE wave-boundary YIELD.

Timeout wrapping (warn → hard TIMEOUT → grace → force cancel) also lives here so
coordination drives and nested blocking drives share one executor surface.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    active_coordination,
)

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunSpec, RunState
    from agentcore.runtime.runs.wave import BoundaryOutcome, BoundaryReason

logger = get_logger(__name__)

OnBoundary = Callable[
    ["BoundaryReason", list["RunSpec"], dict[str, "RunState"]],
    Awaitable["BoundaryOutcome"],
]


def post_escalation_to_coordination(
    *,
    run_id: str,
    role: str = "",
    kind: str = "normal",
    question: str = "",
    assumption: str = "",
    blocking: bool = False,
    source: str = "escalate",
    summary: str = "",
    execution_id: str | None = None,
    escalation_id: str = "",
    ownership_paths: list[str] | None = None,
    lock_owner_run_id: str = "",
    escalator_is_lock_owner_nested_child: bool | None = None,
    ownership_kind: str | None = None,
    owner_status: str | None = None,
) -> bool:
    """Post an escalation into the active coordination queue. Returns True if posted."""
    session = active_coordination(execution_id)
    if session is None or not session.active:
        return False
    payload: dict[str, Any] = {
        "run_id": run_id,
        "role": role or run_id,
        "kind": kind,
        "question": question,
        "assumption": assumption,
        "blocking": blocking,
        "source": source,
        "summary": summary or question,
        "escalation_id": escalation_id,
    }
    if ownership_paths:
        payload["ownership_paths"] = list(ownership_paths)
    if lock_owner_run_id:
        payload["lock_owner_run_id"] = lock_owner_run_id
    if escalator_is_lock_owner_nested_child is not None:
        payload["escalator_is_lock_owner_nested_child"] = bool(
            escalator_is_lock_owner_nested_child
        )
    if ownership_kind:
        payload["ownership_kind"] = ownership_kind
    if owner_status:
        payload["owner_status"] = owner_status
    posted = session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ESCALATION,
            payload=payload,
        )
    )
    if posted:
        logger.info(
            "coordination.escalation_routed",
            run_id=run_id,
            kind=kind,
            source=source,
            blocking=blocking,
            execution_id=session.execution_id,
            ownership_paths=ownership_paths or None,
            escalator_is_nested=escalator_is_lock_owner_nested_child,
        )
    return posted


def post_completed_escalations(
    session: CoordinationSession,
    plan: RunPlan,
    completed: dict[str, RunState],
    *,
    newly: set[str],
) -> None:
    """Surface transcript-harvested escalations on newly terminal workers (safety net)."""
    for run_id in newly:
        state = completed.get(run_id)
        if state is None or not state.escalations:
            continue
        node = plan.by_id(run_id)
        role = (node.role if node else None) or run_id
        for esc in state.escalations:
            if esc.get("consumed"):
                continue
            session.post(
                CoordinationEvent(
                    kind=CoordinationEventKind.ESCALATION,
                    payload={
                        "run_id": run_id,
                        "role": role,
                        "kind": esc.get("kind") or "normal",
                        "question": esc.get("question") or "",
                        "assumption": esc.get("assumption") or "",
                        "blocking": bool(esc.get("blocking")),
                        "source": "run_state",
                        "summary": esc.get("question") or "",
                    },
                )
            )


def coordination_boundary_hook(
    session: CoordinationSession,
    base_hook: OnBoundary | None,
) -> OnBoundary:
    """Wrap the supervised boundary hook: SCOPE → event queue + PROCEED (no YIELD).

    CHECKPOINT under coordination is handled inside ``boundary_hook`` (active session →
    ``_pending_boundary`` + YIELD, no durable plan_review). BIND still delegates to base.
    """

    async def on_boundary(
        reason: BoundaryReason,
        nodes: list[RunSpec],
        completed: dict[str, RunState],
    ) -> BoundaryOutcome:
        from agentcore.runtime.runs import BoundaryOutcome, BoundaryReason

        if reason is BoundaryReason.SCOPE and session.active:
            # Live escalate / completion harvest already queued the signal; here we only
            # suppress YIELD so the wave keeps running while the CEO arbitrates.
            for node in nodes:
                state = completed.get(node.run_id)
                role = node.role or node.run_id
                if state is not None:
                    for e in state.escalations:
                        if e.get("kind") in ("scope", "dep") and not e.get("consumed"):
                            session.post(
                                CoordinationEvent(
                                    kind=CoordinationEventKind.ESCALATION,
                                    payload={
                                        "run_id": node.run_id,
                                        "role": role,
                                        "kind": e.get("kind") or "scope",
                                        "question": e.get("question") or "",
                                        "assumption": e.get("assumption") or "",
                                        "blocking": bool(e.get("blocking")),
                                        "source": "scope_boundary",
                                        "summary": e.get("question") or "",
                                    },
                                )
                            )
            logger.info(
                "coordination.scope_proceed",
                execution_id=session.execution_id,
                nodes=[n.run_id for n in nodes],
            )
            # Wave marks escalations consumed after on_boundary returns; keep scheduling.
            return BoundaryOutcome.PROCEED

        if base_hook is not None:
            return await base_hook(reason, nodes, completed)
        return BoundaryOutcome.PROCEED

    return on_boundary


def _timeout_shrank_delivery(
    session: CoordinationSession | None,
    run_id: str,
    state: RunState,
) -> bool:
    """True when a notified timeout carries real交付缩水 evidence for ``run_id``.

    任一即可判为「真缩水」：(a) 交接简报被引擎降级合成（``debrief.degraded``）；
    (b) 该 worker 真正进入过 timeout wind-down；(c) 硬收尾强制取消；
    (d) 终态为 CANCELLED 且曾收到 TIMEOUT（宽限后强制取消）。
    仅收到超时通知却自然完成 + 合格交接 + 未进 wind-down + 未强制取消 → 非缩水。
    """
    from agentcore.runtime.runs.timeout_hard import get_hard_timeout
    from agentcore.runtime.runs.types import RunPhase

    guard = get_hard_timeout(run_id)
    if guard is not None and guard.shrank_delivery():
        return True
    debrief = state.debrief if isinstance(state.debrief, dict) else None
    if debrief and debrief.get("degraded"):
        return True
    if session is not None:
        if session.entered_timeout_wind_down(run_id):
            return True
        if session.was_timeout_force_cancelled(run_id):
            return True
    return state.phase is RunPhase.CANCELLED and (
        (session is not None and session.was_timeout_notified(run_id))
        or (guard is not None and guard.was_timed_out())
    )


def _stamp_timeout_warning(state: RunState) -> RunState:
    from agentcore.runtime.runs.cutoff import REASON_WORKER_TIMEOUT, WORKER_TIMEOUT_WARNING

    warnings = list(state.warnings or [])
    if WORKER_TIMEOUT_WARNING not in warnings:
        warnings.append(WORKER_TIMEOUT_WARNING)
        state.warnings = warnings
    gaps = list(getattr(state, "delivery_gaps", None) or [])
    if not any(
        isinstance(g, dict) and g.get("reason") == REASON_WORKER_TIMEOUT for g in gaps
    ):
        gaps.append(
            {"description": WORKER_TIMEOUT_WARNING, "reason": REASON_WORKER_TIMEOUT}
        )
        state.delivery_gaps = gaps
    return state


def wrap_executor_with_timeouts(
    executor: Callable[..., Awaitable[RunState]],
    session: CoordinationSession | None = None,
) -> Callable[..., Awaitable[RunState]]:
    """Arm per-worker hard-timeout around the real executor when ``timeout_s`` is set.

    No product-default wall clock: omitted / non-positive ``spec.policy.timeout_s``
    does not start a timer. With a coordination ``session``, the worker is still
    registered for ``cancel_worker`` resolution. With session and positive timeout:
    CEO gets TIMEOUT, force-cancel uses ``cancel_ids``. Without session (nested
    depth>0 blocking drive): same wind-down / grace / force-cancel via the shared
    timeout_hard registry, cancelling the worker task directly.
    """

    async def timed_executor(spec: RunSpec, completed: dict[str, RunState]) -> RunState:
        import asyncio

        from agentcore.runtime.runs.timeout_hard import (
            arm_hard_timeout,
            disarm_hard_timeout,
            get_hard_timeout,
        )

        role = spec.role or spec.agent_name or spec.run_id
        timeout_s = spec.policy.timeout_s
        worker_task = asyncio.current_task()

        if session is not None:
            session.arm_worker_timeout(spec.run_id, role=role, timeout_s=timeout_s)
        else:

            def _on_force_cancel(_guard: Any, _reason: str) -> None:
                if worker_task is not None and not worker_task.done():
                    # The cancel msg becomes run_cancelled.reason — a timeout kill
                    # must not be filed as a user redirect.
                    worker_task.cancel("worker_timeout")

            arm_hard_timeout(
                spec.run_id,
                timeout_s=timeout_s,
                role=role,
                on_force_cancel=_on_force_cancel,
            )
        state: RunState | None = None
        should_stamp = False
        try:
            state = await executor(spec, completed)
            # Decide stamp BEFORE disarm so HardTimeoutGuard flags are still readable.
            notified = (
                session.was_timeout_notified(spec.run_id)
                if session is not None
                else False
            )
            guard = get_hard_timeout(spec.run_id)
            if session is None and guard is not None and guard.was_timed_out():
                notified = True
            should_stamp = notified and _timeout_shrank_delivery(
                session, spec.run_id, state
            )
        finally:
            if session is not None:
                session.disarm_worker_timeout(spec.run_id)
            else:
                disarm_hard_timeout(spec.run_id)
        assert state is not None
        if should_stamp:
            state = _stamp_timeout_warning(state)
        return state

    return timed_executor
