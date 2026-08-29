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
from agentcore.runtime.coordination.journal import record_coordination_snapshot
from agentcore.runtime.coordination.session import (
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
from agentcore.runtime.delegate.team_synthesis import worker_output_blurb
from agentcore.runtime.interaction_orphan import suppress_drive_cancelled_wake
from agentcore.tools.protocol import TOOL_AUDIENCE_CEO, ToolResult

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState

DelegateTool = Any

logger = get_logger(__name__)


def _ceo_result(**kwargs: Any) -> ToolResult:
    """Stamp coordination-host output as CEO-audience (user-bubble salvage refuses)."""
    kwargs["audience"] = TOOL_AUDIENCE_CEO
    return ToolResult(**kwargs)


def should_defer_run_plan_emit_to_merge(
    tool: DelegateTool,
    *,
    execution_id: str,
    coordinate: bool = True,
    worker_count: int = 0,
    has_checkpoint: bool = False,
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
    checkpoint_enabled = bool(getattr(tool, "_checkpoint_enabled", False))
    return should_enter_coordination(
        coordinate=coordinate,
        worker_count=worker_count,
        depth=int(getattr(tool, "_depth", 0) or 0),
        has_checkpoint=has_checkpoint,
        checkpoint_enabled=checkpoint_enabled,
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
    from agentcore.runtime.coordination.isomorphic import started_run_ids_from_entries
    from agentcore.runtime.facts import current_fact_log

    log = current_fact_log.get()
    if log is None:
        return None
    return started_run_ids_from_entries(log.entries())


def commit_admitted_run_plan(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
) -> None:
    """Durable graph commit after sibling / append admit (snapshot + ``run_plan``)."""
    from agentcore.runtime.delegate.plan_events import plan_event
    from agentcore.runtime.delegate.steer import record_plan_snapshot

    record_plan_snapshot(plan)
    sink = getattr(tool, "_sink", None)
    if sink is None:
        return
    prev = getattr(tool, "_prev_graph_execution_id", None)
    prev_s = prev.strip() if isinstance(prev, str) and prev.strip() else None
    sink.emit(plan_event(tool, execution_id, plan, prev_execution_id=prev_s))


def retract_unstarted_batch(
    tool: DelegateTool,
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
        from agentcore.runtime.delegate.steer import record_plan_snapshot

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
    tool: DelegateTool,
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
    from agentcore.runtime.coordination.append_guard import (
        admit_added_nodes,
        append_overlap_reject_message,
        apply_vacated_seat_replaces,
        find_append_overlaps,
        find_sibling_artifact_crosses,
    )
    from agentcore.runtime.delegate.batch_shape import annotate_batch_meta

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

        from agentcore.runtime.coordination.isomorphic import (
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
        from agentcore.runtime.coordination.isomorphic import (
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

    birth_desk = getattr(tool, "_folder_id", None)
    sibling_hits = find_sibling_artifact_crosses(plan, birth_desk_id=birth_desk)
    if sibling_hits:
        msg = append_overlap_reject_message(
            sibling_hits,
            completed=0,
            total=len(plan.nodes),
        )
        logger.info(
            "coordination.sibling_artifact_rejected",
            execution_id=execution_id,
            overlaps=len(sibling_hits),
            paths=[o.path for o in sibling_hits if o.path],
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


def _coordination_start_echo(
    *,
    roster: str,
    added: int,
    total: int,
    completed: int,
    seeded: bool,
) -> str:
    """One-sentence host fact for the CEO tool result (playbook lives in ceo_core)."""
    if seeded:
        return (
            f"【队员已追加·协调模式】已追加 {added} 名队员（{roster}）；"
            f"图共 {total} 名，其中 {completed} 名已完成。"
        )
    return (
        f"【团队已启动·协调模式】已派出 {added} 名队员（{roster}）；"
        f"图共 {total} 名，其中 {completed} 名已完成。"
    )


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
    from agentcore.runtime.coordination.cancel_close import classify_cancel_close

    if classify_cancel_close(session) is not None:
        payload["cancelled"] = True
    if output.strip():
        from agentcore.runtime.delegate.terminal_output import cap_all_completed_output

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


def _merge_into_active_coordination(
    tool: DelegateTool,
    plan: RunPlan,
    session: CoordinationSession,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    complexity_hint: str,
    call_idx: int,
) -> ToolResult:
    """Append ``plan`` workers onto the live session (budget / cancel / arbitration kept)."""
    from agentcore.runtime.coordination.append_guard import (
        admit_added_nodes,
        declare_plan_artifacts,
    )
    from agentcore.runtime.coordination.isomorphic import (
        merge_all_skipped_reject_message,
        merge_partial_skip_message,
    )
    from agentcore.runtime.delegate.batch_shape import annotate_batch_meta
    from agentcore.runtime.delegate.plan_events import plan_event
    from agentcore.runtime.runs.plan import RunPlan, RunPlanError

    live = session.live_plan
    drive_running = session.drive_task is not None and not session.drive_task.done()

    # Seat reclaim + overlap: same admit as replan.adds (vacated/completed
    # auto-replaces, then reject incomplete seat / still-running file holders).
    ownership = session.ensure_file_ownership()
    _folder = getattr(tool, "_folder_id", None)
    birth_desk = session.birth_desk_id or (
        _folder.strip() if isinstance(_folder, str) and _folder.strip() else None
    )
    if birth_desk and not session.birth_desk_id:
        session.birth_desk_id = birth_desk
    reject = admit_added_nodes(
        plan,
        live,
        completed_run_ids=session.completed_run_ids,
        vacated_run_ids=session.vacated_run_ids,
        ownership=ownership,
        total_workers=session.total_workers,
        birth_desk_id=birth_desk,
    )
    if reject is not None:
        completed_k = len(session.completed_run_ids)
        logger.info(
            "coordination.append_overlap_rejected",
            execution_id=execution_id,
            completed=completed_k,
            total=session.total_workers,
            call=call_idx,
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

    if live is None and drive_running:
        # Infeasible: background drive owns an unknown plan — do not dual-drive.
        logger.error(
            "coordination.merge_infeasible_no_live_plan",
            execution_id=execution_id,
            total_workers=session.total_workers,
        )
        return annotate_batch_meta(
            _ceo_result(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "协调会话缺少活计划指针，无法安全追加队员。"
                    "请等当前团队 all_completed 后再 delegate，或用 replan(add=…) 在波边界追加。"
                ),
            ),
            node_count=len(plan.nodes),
            has_deps=any(n.depends_on for n in plan.nodes),
        )
    skipped: list[tuple[Any, str]] = []
    if live is None:
        # No live drive: adopt this batch as the live graph and re-arm below.
        live = plan
        session.live_plan = live
        added_nodes = list(plan.nodes)
    else:
        added_nodes = []
        for node in plan.nodes:
            try:
                live.add(node)
            except RunPlanError as exc:
                reason = str(exc)
                logger.warning(
                    "coordination.merge_skip_node",
                    execution_id=execution_id,
                    run_id=node.run_id,
                    error=reason,
                )
                skipped.append((node, reason))
                continue
            added_nodes.append(node)

    if not added_nodes and live is plan:
        added_nodes = list(plan.nodes)

    # Entire batch bounced (e.g. every run_id already on the live graph) — do not
    # mutate budget / emit plan / echo success; CEO must see a structured reject.
    if skipped and not added_nodes:
        completed_k = len(session.completed_run_ids)
        msg = merge_all_skipped_reject_message(
            skipped,
            completed=completed_k,
            total=session.total_workers,
        )
        logger.info(
            "coordination.merge_all_skipped",
            execution_id=execution_id,
            skipped=len(skipped),
            total_workers=session.total_workers,
            call=call_idx,
        )
        return annotate_batch_meta(
            _ceo_result(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                effect=ToolEffect.CONTINUE,
                # 全批跳过是可自纠的契约拒绝（换 id / 换任务）——勿进熔断。
                contract_failure=True,
            ),
            node_count=0,
            has_deps=False,
        )

    added_count = len(added_nodes)
    session.total_workers = len(live.nodes)
    # C3: reserve artifacts for newly admitted nodes (replaces/continue transfer).
    if added_nodes:
        from agentcore.runtime.runs.executor.context import _ancestors_by_id

        declare_plan_artifacts(
            live,
            session.ensure_file_ownership(),
            only_run_ids={n.run_id for n in added_nodes},
            ancestor_map=_ancestors_by_id(live),
            completed_run_ids=session.completed_run_ids,
            birth_desk_id=session.birth_desk_id
            or (
                getattr(tool, "_folder_id", None)
                if isinstance(getattr(tool, "_folder_id", None), str)
                else None
            ),
        )
    # Budget merge（两池遥测）：派新批按 batch 规模补充两池计数额度，各自封顶。
    topup_progress, topup_decision = split_coordination_budget(
        coordination_budget_for_batch(max(1, added_count))
    )
    session.progress_budget_remaining = min(
        MAX_PROGRESS_BUDGET, session.progress_budget_remaining + topup_progress
    )
    session.decision_budget_remaining = min(
        MAX_DECISION_BUDGET, session.decision_budget_remaining + topup_decision
    )
    # Secondary delegate's suspect-dep advisories ride the next injection too.
    if plan.advisories:
        session.dep_advisories.extend(plan.advisories)
    # cancel_ids / pending_arbitrations / resolved_arbitrations / draft retained.

    tool._sink.emit(plan_event(tool, execution_id, live))
    from agentcore.runtime.delegate.steer import record_plan_snapshot

    record_plan_snapshot(live)
    record_coordination_snapshot(session)

    if drive_running:
        # WaveScheduler re-scans ``plan.nodes`` each cycle — appended workers join.
        logger.info(
            "delegate.coordinate_merged",
            execution_id=execution_id,
            added=added_count,
            total_workers=session.total_workers,
            budget_remaining=session.budget_remaining,
            drive="live",
            call=call_idx,
        )
    else:
        # First drive already exited (possibly posted ALL_COMPLETED). Drop that
        # premature terminal event and arm a drive for the newly appended nodes only.
        dropped = _drop_all_completed_events(session)
        session.all_completed_injected = False
        if not session.active:
            session.active = True
        added_plan = RunPlan(
            nodes=list(added_nodes),
            origin=getattr(live, "origin", None) or plan.origin,
        )
        task = asyncio.create_task(
            _background_drive(
                tool,
                added_plan,
                execution_id=execution_id,
                seed_completed=seed_completed,
                complexity_hint=complexity_hint,
                call_idx=call_idx,
                session=session,
            ),
            name=f"coord-drive-merge-{execution_id[:8]}",
        )
        session.drive_task = task
        logger.info(
            "delegate.coordinate_merged",
            execution_id=execution_id,
            added=added_count,
            total_workers=session.total_workers,
            budget_remaining=session.budget_remaining,
            drive="rearmed",
            dropped_all_completed=dropped,
            call=call_idx,
        )

    completed_k = len(session.completed_run_ids)
    if skipped:
        output = merge_partial_skip_message(
            added_nodes=added_nodes,
            skipped=skipped,
            total_workers=session.total_workers,
            completed=completed_k,
        )
    else:
        roles = [n.role or n.agent_name or n.run_id for n in added_nodes]
        roster = "、".join(roles) if roles else "（无新队员）"
        output = (
            f"【队员已追加·协调模式】已追加 {added_count} 名队员（{roster}）；"
            f"图共 {session.total_workers} 名，其中 {completed_k} 名已完成。"
            "仍属同一协作图 / 同一协调会话。"
        )
    return annotate_batch_meta(
        _ceo_result(
            tool_call_id="",
            success=True,
            output=output,
            effect=ToolEffect.CONTINUE,
        ),
        node_count=added_count,
        has_deps=any(n.depends_on for n in added_nodes),
    )


def try_start_coordination(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    complexity_hint: str,
    call_idx: int,
    coordinate: bool,
    session: CoordinationSession | None = None,
) -> ToolResult | None:
    """If the coordinate gate passes, arm a background drive and return the start result.

    Returns ``None`` when the caller should fall through to blocking ``drive``.
    Pass an existing ``session`` on ask_user resume to preserve draft / budget.

    When an **active** coordination session already exists for ``execution_id``
    (CEO mid-flight secondary ``delegate``), merges workers into that session
    instead of creating a second background drive / overwriting the registry.
    """
    # Secondary delegate while coordinating: merge before the ≥2 gate so a solo
    # append still joins the live team (classic dynamic-delegation parity).
    # Ignore coordinate=false here — a blocking drive beside a live session would
    # dual-drive; opt-out is only meaningful for the *first* arm.
    if session is None:
        existing = active_coordination(execution_id)
        if existing is not None and existing.active and tool._depth == 0:
            existing.event_sink = getattr(tool, "_sink", None) or existing.event_sink
            return _merge_into_active_coordination(
                tool,
                plan,
                existing,
                execution_id=execution_id,
                seed_completed=seed_completed,
                complexity_hint=complexity_hint,
                call_idx=call_idx,
            )

    has_checkpoint = any(bool(n.checkpoint_after) for n in plan.nodes)
    checkpoint_enabled = bool(getattr(tool, "_checkpoint_enabled", False))
    if session is None and not should_enter_coordination(
        coordinate=coordinate,
        worker_count=len(plan.nodes),
        depth=tool._depth,
        has_checkpoint=has_checkpoint,
        checkpoint_enabled=checkpoint_enabled,
    ):
        if has_checkpoint and checkpoint_enabled:
            logger.info(
                "coordination.skipped",
                reason="checkpoint_after_in_batch",
                execution_id=execution_id,
                nodes=len(plan.nodes),
                checkpoint_nodes=sum(1 for n in plan.nodes if n.checkpoint_after),
            )
        return None

    # C3 sibling gate before session create (defense if caller skipped pre-emit admit).
    # Same rule as pre_emit: scan the **new batch only** (host terminals in a merged
    # plan are 续派, not sibling).
    creating_fresh = session is None
    if creating_fresh:
        from agentcore.runtime.coordination.append_guard import (
            append_overlap_reject_message,
            find_sibling_artifact_crosses,
            same_batch_plan,
        )
        from agentcore.runtime.delegate.batch_shape import annotate_batch_meta

        exclude = {
            str(rid).strip()
            for rid in (seed_completed or {})
            if str(rid).strip()
        }
        batch = same_batch_plan(plan, exclude_run_ids=exclude)
        sibling_hits = find_sibling_artifact_crosses(
            batch, birth_desk_id=getattr(tool, "_folder_id", None)
        )
        if sibling_hits:
            drop_ids = {n.run_id for n in batch.nodes if n.run_id}
            node_count = len(batch.nodes)
            has_deps = any(n.depends_on for n in batch.nodes)
            retract_unstarted_batch(tool, plan, drop_run_ids=drop_ids)
            msg = append_overlap_reject_message(
                sibling_hits,
                completed=0,
                total=node_count or len(plan.nodes),
            )
            logger.info(
                "coordination.sibling_artifact_rejected",
                execution_id=execution_id,
                overlaps=len(sibling_hits),
                paths=[o.path for o in sibling_hits if o.path],
                call=call_idx,
                via="try_start",
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
                node_count=node_count,
                has_deps=has_deps,
            )

    fresh_session = False
    if session is None:
        init_progress, init_decision = split_coordination_budget(
            coordination_budget_for_batch(len(plan.nodes))
        )
        session = CoordinationSession(
            execution_id=execution_id,
            total_workers=len(plan.nodes),
            progress_budget_remaining=init_progress,
            decision_budget_remaining=init_decision,
            conversation_id=str(getattr(tool, "_conversation_id", None) or ""),
        )
        _folder = getattr(tool, "_folder_id", None)
        session.birth_desk_id = (
            _folder.strip() if isinstance(_folder, str) and _folder.strip() else None
        )
        session.live_plan = plan
        session.event_sink = getattr(tool, "_sink", None)
        _seed_session_completed(session, seed_completed)
        _bind_session_host_journal(session)
        set_active_coordination(session)
        record_coordination_snapshot(session)
        fresh_session = True
    else:
        if session.live_plan is None:
            session.live_plan = plan
        if not session.conversation_id:
            session.conversation_id = str(
                getattr(tool, "_conversation_id", None) or ""
            )
        if not session.birth_desk_id:
            _folder = getattr(tool, "_folder_id", None)
            session.birth_desk_id = (
                _folder.strip() if isinstance(_folder, str) and _folder.strip() else None
            )
        # Resume / re-arm: re-attach live sink (not snapshotted).
        session.event_sink = getattr(tool, "_sink", None) or session.event_sink
        _seed_session_completed(session, seed_completed)
        if session.host_journal_writer is None:
            _bind_session_host_journal(session)
        set_active_coordination(session)

    from agentcore.sidecar.server_pkg.core import get_active_sidecar

    sidecar = get_active_sidecar()
    if sidecar is not None:
        sidecar.apply_folder_scope(session)

    # C3: first arm declares ownership after admission; resume keeps snapshot ledger.
    if fresh_session:
        from agentcore.runtime.coordination.append_guard import declare_plan_artifacts

        declare_plan_artifacts(
            plan,
            session.ensure_file_ownership(),
            completed_run_ids=session.completed_run_ids,
            birth_desk_id=getattr(tool, "_folder_id", None),
        )
        record_coordination_snapshot(session)
        commit_admitted_run_plan(tool, plan, execution_id=execution_id)

    # 疑似缺依赖提示搭车协调注入通道：随首个团队事件简报一并呈现给 CEO，不新增独立唤醒。
    if plan.advisories:
        session.dep_advisories.extend(plan.advisories)

    task = asyncio.create_task(
        _background_drive(
            tool,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            complexity_hint=complexity_hint,
            call_idx=call_idx,
            session=session,
        ),
        name=f"coord-drive-{execution_id[:8]}",
    )
    session.drive_task = task

    roles = [n.role or n.agent_name or n.run_id for n in plan.nodes]
    roster = "、".join(roles)
    added_count, completed_count = _start_echo_counts(plan, seed_completed)
    # Wording: only pre-filled terminal seeds (append / partial resume) use「已追加」;
    # empty seed dict (team_preview continue) still reads as fresh「团队已启动」.
    append_echo = completed_count > 0
    logger.info(
        "delegate.coordinate_started",
        execution_id=execution_id,
        nodes=len(plan.nodes),
        added=added_count,
        already_completed=completed_count,
        call=call_idx,
        resumed=seed_completed is not None,
    )
    from agentcore.runtime.delegate.batch_shape import annotate_batch_meta

    return annotate_batch_meta(
        _ceo_result(
            tool_call_id="",
            success=True,
            output=_coordination_start_echo(
                roster=roster,
                added=added_count,
                total=len(plan.nodes),
                completed=completed_count,
                seeded=append_echo,
            ),
            effect=ToolEffect.CONTINUE,
        ),
        node_count=len(plan.nodes),
        has_deps=any(n.depends_on for n in plan.nodes),
    )


async def _background_drive(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    complexity_hint: str,
    call_idx: int,
    session: CoordinationSession,
) -> None:
    """Run blocking drive semantics, posting coordination events along the way.

    Owns an independent LLM client so chat-turn teardown ``llm.close()`` cannot
    ReadError-kill in-flight workers (async team model).
    """
    from agentcore.llm.factory import spawn_independent_llm
    from agentcore.runtime.delegate.drive import drive_coordinated

    own_llm, owns_llm = spawn_independent_llm(tool._llm)
    prior_llm = tool._llm
    tool._llm = own_llm
    try:
        result = await drive_coordinated(
            tool,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            complexity_hint=complexity_hint,
            call_idx=call_idx,
            session=session,
        )
        # Boundary / pause results surface as coordination events (CEO still alive).
        # drive 在 session 路径上故意保留 ``_pending_*``（见 drive.py），此处消费后清掉。
        if tool._pending_pause:
            tool._pending_pause = False
            session.post(
                CoordinationEvent(
                    kind=CoordinationEventKind.BOUNDARY_YIELD,
                    payload={"reason": "checkpoint", "brief": "计划在 checkpoint 暂停"},
                )
            )
        elif tool._pending_boundary is not None:
            reason, nodes = tool._pending_boundary
            tool._pending_boundary = None
            from agentcore.runtime.delegate.supervised import format_boundary_for_ceo

            # Prefer the brief drive already formatted (includes completed worker output);
            # fall back to a fresh format when drive returned empty.
            brief = (result.output if result is not None and result.output else "") or (
                format_boundary_for_ceo(tool, reason, plan, {}, nodes)
            )
            session.post(
                CoordinationEvent(
                    kind=CoordinationEventKind.BOUNDARY_YIELD,
                    payload={
                        "reason": reason.value,
                        "brief": brief[:2000],
                        "boundary_run_ids": [n.run_id for n in nodes],
                    },
                )
            )
        elif result is not None and result.success:
            # Invariant: drive should already have posted; backfill only on leak.
            _ensure_terminal_all_completed(
                session,
                output=(result.output if result is not None else "") or "",
            )
        elif result is not None:
            # 契约失败（success=False）此前被静默丢弃。只落日志，不推 CEO / 不自愈。
            logger.info(
                "delegate.coordinate_failed",
                execution_id=execution_id,
                error=result.error or "",
                contract_failure=bool(result.contract_failure),
                nodes=len(plan.nodes),
                call=call_idx,
            )
    except asyncio.CancelledError as exc:
        from agentcore.runtime.coordination.drive_cancel import (
            drive_cancel_error_copy,
            resolve_drive_cancel_reason,
        )

        reason = resolve_drive_cancel_reason(session, exc)
        logger.info(
            "delegate.coordinate_cancelled",
            execution_id=execution_id,
            reason=reason,
        )
        # Soft-stop (ask_user hang-frame): do NOT wake with ALL_COMPLETED — that
        # would seal a fake「全员完成」into the pause snapshot. Resume re-drives
        # unfinished workers from the journal seed.
        # Process kill / turn interrupt while host is still waiting: wake with
        # DRIVE_CANCELLED (not ALL_COMPLETED) so inject/wait never imply success.
        # Hot user card (not user_stopped): same as soft_stop — do not wake as
        # 「调度中断」close.
        if not suppress_drive_cancelled_wake(session):
            with contextlib.suppress(Exception):
                session.post(
                    CoordinationEvent(
                        kind=CoordinationEventKind.DRIVE_CANCELLED,
                        payload={
                            "completed": len(session.completed_run_ids),
                            "total": session.total_workers,
                            "reason": reason,
                            "error": drive_cancel_error_copy(reason),
                        },
                    )
                )
        raise
    except Exception:  # noqa: BLE001 — never kill the CEO loop via background task
        logger.exception("delegate.coordinate_failed", execution_id=execution_id)
        # Align with CancelledError non-soft_stop: scheduling crash is not success.
        # ALL_COMPLETED would seal a fake「全员完成」into inject/wait.
        if not suppress_drive_cancelled_wake(session):
            with contextlib.suppress(Exception):
                session.post(
                    CoordinationEvent(
                        kind=CoordinationEventKind.DRIVE_CANCELLED,
                        payload={
                            "completed": len(session.completed_run_ids),
                            "total": session.total_workers,
                            "error": "协调后台调度异常结束，请基于已有结果收口。",
                        },
                    )
                )
    finally:
        tool._llm = prior_llm
        if owns_llm:
            with contextlib.suppress(Exception):
                close = getattr(own_llm, "close", None)
                if close is not None:
                    await close()
        record_coordination_snapshot(session)
        finish_detached_coordination(session)


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
    from agentcore.runtime.coordination.bridge import post_completed_escalations
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
                from agentcore.runtime.coordination.thrash import (
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
