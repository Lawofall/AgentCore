"""Start or merge a coordination drive. Lives in drive so session does not import drive."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolEffect
from agentcore.runtime.graph.session.host import _ceo_result, commit_admitted_run_plan
from agentcore.runtime.graph.session.journal import record_coordination_snapshot
from agentcore.runtime.graph.session.session import (
    MAX_DECISION_BUDGET,
    MAX_PROGRESS_BUDGET,
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    active_coordination,
    coordination_budget_for_batch,
    finish_detached_coordination,
    set_active_coordination,
    should_enter_coordination,
    split_coordination_budget,
)
from agentcore.runtime.interaction_orphan import suppress_drive_cancelled_wake
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunState
from agentcore.tools.protocol import ToolResult

from agentcore.runtime.graph.host import GraphHost

logger = get_logger(__name__)

def _merge_into_active_coordination(
    tool: GraphHost,
    plan: RunPlan,
    session: CoordinationSession,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    resume_hints: dict | None = None,
    complexity_hint: str,
    call_idx: int,
) -> ToolResult:
    """Append ``plan`` workers onto the live session (budget / cancel / arbitration kept)."""
    from agentcore.runtime.graph.session.append_guard import (
        admit_added_nodes,
        declare_plan_artifacts,
    )
    from agentcore.runtime.graph.session.isomorphic import (
        merge_all_skipped_reject_message,
        merge_partial_skip_message,
    )
    from agentcore.runtime.graph.admit.batch_shape import annotate_batch_meta
    from agentcore.runtime.graph.drive.plan_events import plan_event
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
    from agentcore.runtime.graph.drive.steer import record_plan_snapshot

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
                resume_hints=resume_hints,
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
        output = _append_coordination_period_hint(
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
    tool: GraphHost,
    plan: RunPlan,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    resume_hints: dict | None = None,
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
                resume_hints=resume_hints,
                complexity_hint=complexity_hint,
                call_idx=call_idx,
            )

    if session is None and not should_enter_coordination(
        coordinate=coordinate,
        worker_count=len(plan.nodes),
        depth=tool._depth,
    ):
        return None

    # Same-batch seat overlap before session create (defense if caller skipped
    # pre-emit admit). Scan the **new batch only** (host terminals are 续派).
    creating_fresh = session is None
    if creating_fresh:
        from agentcore.runtime.graph.session.append_guard import (
            append_overlap_reject_message,
            find_sibling_role_crosses,
            same_batch_plan,
        )
        from agentcore.runtime.graph.admit.batch_shape import annotate_batch_meta

        exclude = {
            str(rid).strip()
            for rid in (seed_completed or {})
            if str(rid).strip()
        }
        batch = same_batch_plan(plan, exclude_run_ids=exclude)
        sibling_hits = find_sibling_role_crosses(batch)
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
                "coordination.sibling_role_rejected",
                execution_id=execution_id,
                overlaps=len(sibling_hits),
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
        from agentcore.runtime.graph.session.append_guard import declare_plan_artifacts

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
            resume_hints=resume_hints,
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
    # empty seed dict (cold start) still reads as fresh「团队已启动」.
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
    from agentcore.runtime.graph.admit.batch_shape import annotate_batch_meta

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
    tool: GraphHost,
    plan: RunPlan,
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    resume_hints: dict | None = None,
    complexity_hint: str,
    call_idx: int,
    session: CoordinationSession,
) -> None:
    """Run blocking drive semantics, posting coordination events along the way.

    Owns an independent LLM client so chat-turn teardown ``llm.close()`` cannot
    ReadError-kill in-flight workers (async team model).
    """
    from agentcore.llm.factory import spawn_independent_llm
    from agentcore.runtime.graph.drive.drive import drive_coordinated

    own_llm, owns_llm = spawn_independent_llm(tool._llm)
    prior_llm = tool._llm
    tool._llm = own_llm
    try:
        result = await drive_coordinated(
            tool,
            plan,
            execution_id=execution_id,
            seed_completed=seed_completed,
            resume_hints=resume_hints,
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
            from agentcore.runtime.graph.drive.supervised import format_boundary_for_ceo

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
        from agentcore.runtime.graph.session.drive_cancel import (
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


