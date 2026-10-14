"""Non-blocking coordinated WaveScheduler host (CEO 协调模式 Phase 2).

Starts the same drive machinery as blocking ``drive``, but returns immediately
after the team is armed; progress posts into :class:`CoordinationSession`.

Mid-coordination secondary ``delegate`` merges into the active session (same
collaboration graph / same event queue) — aligned with classic-path dynamic
delegation — rather than overwriting via :func:`set_active_coordination`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolEffect
from agentcore.runtime.graph.session.journal import record_coordination_snapshot
from agentcore.runtime.graph.session.session import (
    MAX_DECISION_BUDGET,
    MAX_PROGRESS_BUDGET,
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    active_coordination,
    bind_host_journal,
    coordination_budget_for_batch,
    finish_detached_coordination,
    set_active_coordination,
    should_enter_coordination,
    split_coordination_budget,
)
from agentcore.runtime.graph.facing.team_synthesis import worker_output_blurb
from agentcore.runtime.interaction_orphan import suppress_drive_cancelled_wake
from agentcore.tools.protocol import TOOL_AUDIENCE_CEO, ToolResult

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState

from agentcore.runtime.graph.host import GraphHost

logger = get_logger(__name__)


def _ceo_result(**kwargs: Any) -> ToolResult:
    """Stamp coordination-host output as CEO-audience (user-bubble salvage refuses)."""
    kwargs["audience"] = TOOL_AUDIENCE_CEO
    return ToolResult(**kwargs)


def should_defer_run_plan_emit_to_merge(
    tool: GraphHost,
    *,
    execution_id: str,
    coordinate: bool = True,
    worker_count: int = 0,
) -> bool:
    """True when durable ``run_plan`` / ``plan_snapshot`` must wait for arm success.

    Secondary same-turn ``delegate`` merges into the live graph; the merge path
    emits the grown plan after admission. A **fresh** coordination arm runs the
    try_start sibling defense before commit — emitting earlier would leave a
    durable skeleton even when admission later rejects.
    """
    if int(getattr(tool, "_depth", 0) or 0) != 0:
        return False
    existing = active_coordination(execution_id)
    if existing is not None and existing.active:
        return True
    return should_enter_coordination(
        coordinate=coordinate,
        worker_count=worker_count,
        depth=int(getattr(tool, "_depth", 0) or 0),
    )


def _seed_terminal_sets(
    seed_completed: dict[str, Any] | None,
) -> tuple[set[str], set[str]]:
    """Journal seed → ``(completed_run_ids, vacated_run_ids)`` for cross-turn admit."""
    from agentcore.runtime.runs.types import RunPhase

    completed: set[str] = set()
    vacated: set[str] = set()
    if not seed_completed:
        return completed, vacated
    hard = {RunPhase.FAILED, RunPhase.SKIPPED, RunPhase.CANCELLED}
    for rid, state in seed_completed.items():
        rid_s = str(rid).strip()
        if not rid_s:
            continue
        completed.add(rid_s)
        phase = getattr(state, "phase", None)
        if phase in hard:
            vacated.add(rid_s)
    return completed, vacated


def _admit_started_run_ids() -> set[str] | None:
    """Journal ``run_started`` ids, or ``None`` when no fact log is bound.

    ``None`` keeps unit tests on the historical「all incomplete occupy」rule.
    An empty set means the log exists and nobody dispatched — empty seats do
    not isomorphic-lock the next batch.
    """
    from agentcore.runtime.graph.session.isomorphic import started_run_ids_from_entries
    from agentcore.runtime.facts import current_fact_log

    log = current_fact_log.get()
    if log is None:
        return None
    return started_run_ids_from_entries(log.entries())


def commit_admitted_run_plan(
    tool: GraphHost,
    plan: RunPlan,
    *,
    execution_id: str,
) -> None:
    """Durable graph commit after sibling / append admit (snapshot + ``run_plan``)."""
    from agentcore.runtime.graph.facing.plan_events import plan_event
    from agentcore.runtime.graph.session.journal import record_plan_snapshot

    record_plan_snapshot(plan)
    sink = getattr(tool, "_sink", None)
    if sink is None:
        return
    prev = getattr(tool, "_prev_graph_execution_id", None)
    prev_s = prev.strip() if isinstance(prev, str) and prev.strip() else None
    sink.emit(plan_event(tool, execution_id, plan, prev_execution_id=prev_s))


def retract_unstarted_batch(
    tool: GraphHost,
    plan: RunPlan,
    *,
    drop_run_ids: set[str] | frozenset[str],
) -> None:
    """Erase never-started new-batch seats from the durable graph after a late reject.

    Mutates ``plan`` (undo leaked host∪new merge). Rewrites journal snapshot only
    when one already exists. Emits ``run_skipped`` only when those ids already
    rode a ``run_plan`` (do not grow ghost skipped nodes that were never shown).
    """
    drop = {str(x).strip() for x in drop_run_ids if str(x).strip()}
    if not drop:
        return
    dropped_nodes = [n for n in plan.nodes if (n.run_id or "") in drop]
    if not dropped_nodes:
        return
    plan.nodes[:] = [n for n in plan.nodes if (n.run_id or "") not in drop]
    from agentcore.runtime.facts import FactKind, current_fact_log

    log = current_fact_log.get()
    had_snapshot = False
    if log is not None:
        had_snapshot = any(
            e.get("kind") == FactKind.PLAN_SNAPSHOT.value for e in log.entries()
        )
    if had_snapshot:
        from agentcore.runtime.graph.session.journal import record_plan_snapshot

        record_plan_snapshot(plan)
    on_board = False
    sink = getattr(tool, "_sink", None)
    history = getattr(sink, "_history", None) if sink is not None else None
    if isinstance(history, (list, tuple)):
        from agentcore.runtime.events import EventType

        for event in history:
            if getattr(event, "type", None) is not EventType.RUN_PLAN:
                continue
            payload = getattr(event, "payload", None) or {}
            for raw in payload.get("runs") or []:
                if not isinstance(raw, dict):
                    continue
                if str(raw.get("kind") or "") == "captain":
                    continue
                rid = str(raw.get("id") or "").strip()
                if rid in drop:
                    on_board = True
                    break
            if on_board:
                break
    if on_board and sink is not None:
        from agentcore.runtime.events import run_skipped

        for node in dropped_nodes:
            rid = (node.run_id or "").strip()
            if not rid:
                continue
            agent_id = (getattr(node, "agent_id", None) or rid).strip() or rid
            sink.emit(run_skipped(rid, agent_id, reason="abort"))
    last = getattr(tool, "_last_graph_plan", None)
    if last is not None and last is not plan:
        last.nodes[:] = [n for n in last.nodes if (n.run_id or "") not in drop]


def admit_before_run_plan_emit(
    tool: GraphHost,
    plan: RunPlan,
    *,
    execution_id: str,
    call_idx: int | None = None,
    host_plan: RunPlan | None = None,
    seed_completed: dict[str, Any] | None = None,
) -> ToolResult | None:
    """Sibling / append / isomorphic gates before durable ``run_plan`` emit.

    ``plan`` must be the **new batch only** (not host∪new). Cross-turn append
    passes ``host_plan`` + journal ``seed_completed`` so completed seats get
    auto-``replaces`` instead of a false sibling reject on the merged graph.

    Returns a contract-failure :class:`ToolResult` when the batch must not become
    a graph member; ``None`` means admitted (caller may emit then execute).
    """
    from agentcore.runtime.graph.session.append_guard import (
        admit_added_nodes,
        append_overlap_reject_message,
        apply_vacated_seat_replaces,
        find_append_overlaps,
        find_sibling_role_crosses,
    )
    from agentcore.runtime.graph.admit.batch_shape import annotate_batch_meta

    existing = active_coordination(execution_id)
    merging = (
        existing is not None
        and existing.active
        and int(getattr(tool, "_depth", 0) or 0) == 0
    )

    if merging and existing is not None:
        apply_vacated_seat_replaces(
            plan,
            existing.live_plan,
            completed_run_ids=existing.completed_run_ids,
            vacated_run_ids=existing.vacated_run_ids,
        )

        from agentcore.runtime.graph.session.isomorphic import (
            is_isomorphic_redelegation,
            isomorphic_reject_message,
        )

        if is_isomorphic_redelegation(
            plan,
            existing.live_plan,
            completed_run_ids=existing.completed_run_ids,
        ):
            logger.info(
                "delegate.isomorphic_rejected",
                execution_id=execution_id,
                nodes=len(plan.nodes),
                completed=len(existing.completed_run_ids),
                total=existing.total_workers,
                call=call_idx,
                via="pre_emit",
            )
            msg = isomorphic_reject_message(
                plan,
                completed=len(existing.completed_run_ids),
                total=existing.total_workers,
            )
            return annotate_batch_meta(
                _ceo_result(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    effect=ToolEffect.CONTINUE,
                    contract_failure=True,
                ),
                node_count=0,
                has_deps=False,
            )

        ownership = existing.ensure_file_ownership()
        birth_desk = getattr(tool, "_folder_id", None)
        overlaps = find_append_overlaps(
            plan,
            existing.live_plan,
            completed_run_ids=existing.completed_run_ids,
            ownership=ownership,
            birth_desk_id=birth_desk,
        )
        if overlaps:
            completed_k = len(existing.completed_run_ids)
            msg = append_overlap_reject_message(
                overlaps,
                completed=completed_k,
                total=existing.total_workers,
            )
            logger.info(
                "coordination.append_overlap_rejected",
                execution_id=execution_id,
                overlaps=len(overlaps),
                reasons=[o.reason for o in overlaps],
                paths=[o.path for o in overlaps if o.path],
                completed=completed_k,
                total=existing.total_workers,
                call=call_idx,
                via="pre_emit",
            )
            return annotate_batch_meta(
                _ceo_result(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    effect=ToolEffect.CONTINUE,
                    contract_failure=True,
                ),
                node_count=len(plan.nodes),
                has_deps=any(n.depends_on for n in plan.nodes),
            )
        return None

    # Cross-turn append: no live session — admit new batch vs host journal state.
    # Do **not** sibling-scan host∪new (completed same-seat + same artifact is
    # legitimate续派; sibling is same-batch only).
    if (
        host_plan is not None
        and int(getattr(tool, "_depth", 0) or 0) == 0
    ):
        from agentcore.runtime.graph.session.isomorphic import (
            is_isomorphic_redelegation,
            isomorphic_reject_message,
        )

        completed_ids, vacated_ids = _seed_terminal_sets(seed_completed)
        started_ids = _admit_started_run_ids()
        if is_isomorphic_redelegation(
            plan,
            host_plan,
            completed_run_ids=completed_ids,
            started_run_ids=started_ids,
        ):
            logger.info(
                "delegate.isomorphic_rejected",
                execution_id=execution_id,
                nodes=len(plan.nodes),
                completed=len(completed_ids),
                total=len(host_plan.nodes),
                call=call_idx,
                via="pre_emit_cross_turn",
            )
            msg = isomorphic_reject_message(
                plan,
                completed=len(completed_ids),
                total=len(host_plan.nodes),
            )
            return annotate_batch_meta(
                _ceo_result(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    effect=ToolEffect.CONTINUE,
                    contract_failure=True,
                ),
                node_count=0,
                has_deps=False,
            )

        reject = admit_added_nodes(
            plan,
            host_plan,
            completed_run_ids=completed_ids,
            vacated_run_ids=vacated_ids,
            started_run_ids=started_ids,
            ownership=None,
            total_workers=len(host_plan.nodes),
            birth_desk_id=getattr(tool, "_folder_id", None),
        )
        if reject is not None:
            logger.info(
                "coordination.append_overlap_rejected",
                execution_id=execution_id,
                overlaps=1,
                completed=len(completed_ids),
                total=len(host_plan.nodes),
                call=call_idx,
                via="pre_emit_cross_turn",
            )
            return annotate_batch_meta(
                _ceo_result(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=reject,
                    effect=ToolEffect.CONTINUE,
                    contract_failure=True,
                ),
                node_count=len(plan.nodes),
                has_deps=any(n.depends_on for n in plan.nodes),
            )
        return None

    sibling_hits = find_sibling_role_crosses(plan)
    if sibling_hits:
        msg = append_overlap_reject_message(
            sibling_hits,
            completed=0,
            total=len(plan.nodes),
        )
        logger.info(
            "coordination.sibling_role_rejected",
            execution_id=execution_id,
            overlaps=len(sibling_hits),
            call=call_idx,
            via="pre_emit",
        )
        return annotate_batch_meta(
            _ceo_result(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                effect=ToolEffect.CONTINUE,
                contract_failure=True,
            ),
            node_count=len(plan.nodes),
            has_deps=any(n.depends_on for n in plan.nodes),
        )
    return None


def _bind_session_host_journal(session: CoordinationSession) -> None:
    """Pin the arming turn's journal writer + fact log onto the session (pillar A)."""
    from agentcore.runtime.journal.writer import current_journal_writer

    writer = current_journal_writer.get()
    bind_host_journal(
        session,
        writer=writer,
        turn_id=getattr(writer, "turn_id", None) if writer is not None else None,
    )


def _seed_session_completed(
    session: CoordinationSession,
    seed_completed: dict[str, RunState] | None,
) -> None:
    """Pre-fill completed_run_ids from host/resume seeds so completed/total stays honest."""
    if not seed_completed:
        return
    from agentcore.runtime.runs.types import RunPhase

    terminal = {
        RunPhase.COMPLETED,
        RunPhase.FAILED,
        RunPhase.CANCELLED,
        RunPhase.SKIPPED,
    }
    vacated = {
        RunPhase.FAILED,
        RunPhase.CANCELLED,
        RunPhase.SKIPPED,
    }
    for run_id, state in seed_completed.items():
        if state.phase in terminal:
            session.mark_worker_completed(run_id)
        if state.phase in vacated:
            session.vacated_run_ids.add(run_id)
        if state.phase is RunPhase.FAILED:
            session.failed_run_ids.add(run_id)
    # Seeded terminals are prior-wave history — don't name them as「本轮新完成」.
    session.progress_reported_completed |= set(session.completed_run_ids)


def _start_echo_counts(
    plan: RunPlan,
    seed_completed: dict[str, RunState] | None,
) -> tuple[int, int]:
    """Return (added_count, already_completed_count) for the start echo."""
    if not seed_completed:
        return len(plan.nodes), 0
    seeded_ids = set(seed_completed)
    added = sum(1 for n in plan.nodes if n.run_id not in seeded_ids)
    return added, len(seeded_ids)


# 协调期可见面纪律的唯一所有者。不进每批注入、不进工具 schema。
COORDINATION_PERIOD_HINT = (
    "【协调期】图在转、无新结论可静默；对用户开口只谈请示/阻塞/阶段结论/回应中途插话。"
)


def _append_coordination_period_hint(fact: str) -> str:
    return f"{fact}\n{COORDINATION_PERIOD_HINT}"


def _coordination_start_echo(
    *,
    roster: str,
    added: int,
    total: int,
    completed: int,
    seeded: bool,
) -> str:
    """Host fact plus the one coordination-period posture line."""
    if seeded:
        fact = (
            f"【队员已追加·协调模式】已追加 {added} 名队员（{roster}）；"
            f"图共 {total} 名，其中 {completed} 名已完成。"
        )
    else:
        fact = (
            f"【团队已启动·协调模式】已派出 {added} 名队员（{roster}）；"
            f"图共 {total} 名，其中 {completed} 名已完成。"
        )
    return _append_coordination_period_hint(fact)


def _drop_all_completed_events(session: CoordinationSession) -> int:
    """Remove premature ``ALL_COMPLETED`` events after workers are appended mid-flight."""
    dropped = 0
    kept_pending: list[CoordinationEvent] = []
    for ev in session._pending:
        if ev.kind is CoordinationEventKind.ALL_COMPLETED:
            dropped += 1
        else:
            kept_pending.append(ev)
    session._pending = kept_pending
    kept_queued: list[CoordinationEvent] = []
    while True:
        try:
            ev = session._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if ev.kind is CoordinationEventKind.ALL_COMPLETED:
            dropped += 1
        else:
            kept_queued.append(ev)
    for ev in kept_queued:
        session._queue.put_nowait(ev)
    return dropped


def _has_all_completed_in_flight(session: CoordinationSession) -> bool:
    """True when ALL_COMPLETED is already queued / pending / injected."""
    if session.all_completed_injected:
        return True
    if any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in session._pending):
        return True
    queued: list[CoordinationEvent] = []
    while True:
        try:
            queued.append(session._queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    has = any(e.kind is CoordinationEventKind.ALL_COMPLETED for e in queued)
    for ev in queued:
        session._queue.put_nowait(ev)
    return has


def _ensure_terminal_all_completed(
    session: CoordinationSession,
    *,
    output: str = "",
) -> bool:
    """Invariant guard: backfill ALL_COMPLETED only when drive leaked a terminal miss.

    Normal paths post inside ``drive`` (success / criteria-gap / partial-fail).
    Triggering this means a *new* early-return forgot the terminal post — keep the
    CEO unblocked, but warn so the leak is visible.
    """
    if _has_all_completed_in_flight(session):
        return False
    payload: dict[str, Any] = {
        "completed": len(session.completed_run_ids),
        "total": session.total_workers,
    }
    from agentcore.runtime.graph.session.cancel_close import classify_cancel_close

    if classify_cancel_close(session) is not None:
        payload["cancelled"] = True
    if output.strip():
        from agentcore.runtime.graph.facing.terminal_output import cap_all_completed_output

        payload["output"] = cap_all_completed_output(output.strip())
    session.post(
        CoordinationEvent(kind=CoordinationEventKind.ALL_COMPLETED, payload=payload)
    )
    logger.warning(
        "coordination.all_completed_backfill",
        execution_id=session.execution_id,
        completed=len(session.completed_run_ids),
        total=session.total_workers,
        has_output=bool(output.strip()),
        detail=(
            "竞态兜底：drive 终态未投递 ALL_COMPLETED，host 已回填。"
            "主保障是终态对账（附着注入/harvest）；请追查 drive 提前返回竞态。"
        ),
    )
    return True



def post_worker_progress(
    session: CoordinationSession,
    plan: RunPlan,
    completed: dict[str, RunState],
    *,
    sink: Any,
    execution_id: str,
    previously: set[str],
) -> set[str]:
    """Post coordination events for newly terminal workers (preview already emitted)."""
    from agentcore.runtime.graph.session.bridge import post_completed_escalations
    from agentcore.runtime.runs.types import RunPhase

    newly = set(completed) - previously
    terminal: set[str] = set()
    for run_id in newly:
        state = completed[run_id]
        if state.phase not in (
            RunPhase.COMPLETED,
            RunPhase.FAILED,
            RunPhase.CANCELLED,
            RunPhase.SKIPPED,
        ):
            continue
        terminal.add(run_id)
        node = plan.by_id(run_id)
        role = (node.role if node else None) or run_id
        session.mark_worker_completed(run_id)
        if state.phase in (
            RunPhase.FAILED,
            RunPhase.CANCELLED,
            RunPhase.SKIPPED,
        ):
            session.vacated_run_ids.add(run_id)
        if state.phase is RunPhase.FAILED:
            session.failed_run_ids.add(run_id)
        session.post(
            CoordinationEvent(
                kind=CoordinationEventKind.WORKER_COMPLETED,
                payload={
                    "run_id": run_id,
                    "role": role,
                    "status": state.phase.value,
                    "summary": worker_output_blurb(state),
                },
            )
        )
        # Mid-graph thrash memory so secondary cold delegate can refuse rebrand
        # before batch finalize. Fail-soft: thrash accounting must never abort drive.
        if node is not None:
            try:
                from agentcore.runtime.graph.session.thrash import (
                    note_thrashing_worker,
                    thrash_record_from_node,
                )

                rec = thrash_record_from_node(node, state)
                if rec is not None:
                    note_thrashing_worker(session.conversation_id or "", rec)
            except Exception:  # noqa: BLE001 — thrash is advisory only
                logger.exception(
                    "delegate.thrash_note_failed",
                    run_id=run_id,
                    execution_id=execution_id,
                )
    # Safety net: transcript-harvested escalations that missed the live on_escalate bridge.
    if terminal:
        post_completed_escalations(session, plan, completed, newly=terminal)
    return set(completed)
