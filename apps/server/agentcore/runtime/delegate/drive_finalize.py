"""Post-wave finalize: metrics, pause/boundary, partial fail, criteria, CEO result."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolEffect
from agentcore.runtime.delegate.accumulate import (
    accumulate_usage,
    collect_citations,
    collect_ledger,
    register_sessions,
)
from agentcore.runtime.delegate.ceo_format import build_ceo_synthesis
from agentcore.runtime.delegate.delivery_status import maybe_emit_delivery_status
from agentcore.runtime.delegate.drive_terminal import (
    collect_harvest_user_facts,
    post_session_all_completed,
)
from agentcore.runtime.delegate.nesting import absorb_children
from agentcore.runtime.delegate.supervised import (
    SupervisedRun,
    format_boundary_for_ceo,
)
from agentcore.runtime.events import batch_metrics as batch_metrics_event
from agentcore.runtime.runs.constants import DELEGATE_OUTPUT_LIMIT
from agentcore.tools.protocol import ToolResult

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import BatchMetrics, RunState

type DelegateTool = Any

logger = get_logger(__name__)


def stamp_last_graph_seed(
    tool: DelegateTool,
    plan: RunPlan,
    results: dict[str, RunState] | None,
) -> None:
    """Copy terminal phases from a drive result map onto the tool instance.

    Kickoff must not pretend in-flight workers are done. Call this when the
    wave scheduler has a real result map (finalize, or execute tail after the
    coordination session has already closed). Nodes absent from ``results``
    stay unscheduled for a same-turn append.
    """
    from agentcore.runtime.runs.types import RunState as _RunState

    terminal = results or {}
    tool._last_graph_seed = {
        n.run_id: _RunState(phase=state.phase, error=state.error)
        for n in plan.nodes
        if (state := terminal.get(n.run_id)) is not None
    }


def emit_batch_metrics(
    tool: DelegateTool,
    batch_metrics: list[BatchMetrics],
    *,
    execution_id: str,
    call_idx: int,
    complexity_hint: str,
) -> None:
    if not batch_metrics:
        return
    m = batch_metrics[0]
    logger.info(
        "delegate.completed",
        call=call_idx,
        hint=complexity_hint,
        nodes=m.nodes,
        width=m.width,
        peak=m.peak_running,
        wall_ms=m.wall_ms,
        busy_ms=m.busy_ms,
        avg_parallelism=round(m.busy_ms / m.wall_ms, 2) if m.wall_ms else 0.0,
        slot_starved=m.slot_starved,
        completed=m.completed,
        failed=m.failed,
        skipped=m.skipped,
        # 受监督波循环埋点 (执行引擎架构设计.md §受监督的波循环): boundary fires this segment +
        # scope 信号占比 (derived from raw counts, mirroring avg_parallelism).
        bind=m.bind_boundaries,
        scope=m.scope_boundaries,
        checkpoint=m.checkpoint_boundaries,
        escalations=m.escalations,
        scope_ratio=round(m.scope_escalations / m.escalations, 2) if m.escalations else 0.0,
    )
    # 协作质量 tally (学·度量 §2.5): fold this batch's drift + escalation signals into the
    # turn-level roll-up on the accumulator (rolls up to the captain via absorb_children).
    tool._acc.collab["scope_signals"] += m.scope_escalations
    tool._acc.collab["escalations"] += m.escalations
    # 深层诊断指标 (前端UX设计.md §十): surface the scheduler snapshot to the client so
    # 诊断模式 shows it in run detail (journaled → replays on reload). Whole-batch verbatim
    # — the host already logged it; this just also hands it to the UI fold.
    tool._sink.emit(batch_metrics_event(execution_id=execution_id, metrics=dataclasses.asdict(m)))


def handle_pending_pause(
    tool: DelegateTool,
    *,
    session: Any,
    call_idx: int,
    results: dict[str, RunState],
) -> ToolResult | None:
    """Soft pause after checkpoint YIELD. None → continue finalize."""
    # 挂起即收口 (②): the checkpoint boundary persisted a resume frame and YIELDed (soft
    # pause). End the turn here with a SUSPEND ToolResult — the engine maps it to
    # FinishReason.PAUSED, leaves the delegate call pending (no result), and the persist
    # tail parks the turn (the frame is the record). The已完成 workers' usage / ledger /
    # citations are NOT folded here: they ride the durable frame's ``completed`` and bill
    # on the cold resume drive — matching the disconnect→resume path this collapses onto.
    #
    # 协调态例外：host 靠 ``_pending_pause`` / ``_pending_boundary`` 投递 BOUNDARY_YIELD。
    # 若此处清掉标志，host 永远看不到（竞态）。协调路径保留标志、不 SUSPEND、不收口回合。
    if not tool._pending_pause:
        return None
    if session is not None:
        logger.info("delegate.coord_pause_signal", call=call_idx, completed=len(results))
        return ToolResult(tool_call_id="", success=True, output="")
    tool._pending_pause = False
    logger.info("delegate.paused", call=call_idx, completed=len(results))
    return ToolResult(tool_call_id="", success=True, output="", effect=ToolEffect.SUSPEND)


def handle_pending_boundary(
    tool: DelegateTool,
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    execution_id: str,
    session: Any,
    call_idx: int,
) -> ToolResult | None:
    """Supervised boundary yield. None → continue finalize."""
    from agentcore.runtime.runs import BoundaryReason

    if tool._pending_boundary is None:
        return None
    reason, nodes = tool._pending_boundary
    # 单一事实源 (P5 持久化): a SCOPE yield marked the deviating nodes' escalations
    # ``consumed`` IN PLACE (wave.py). Re-journal their terminal RunState so
    # ``completed_from_journal`` rebuilds the resume seed WITH ``consumed`` — else a
    # durable re-drive (a later checkpoint pause + resume of the same plan) would
    # re-fire an already-handled SCOPE boundary. Last-write-wins per run_id makes the
    # refreshed message_final supersede the pre-consumption one.
    if reason is BoundaryReason.SCOPE:
        from agentcore.runtime.facts import record_turn_fact
        from agentcore.runtime.runs.serialize import run_final_fact

        for node in nodes:
            state = results.get(node.run_id)
            if state is not None:
                record_turn_fact(run_final_fact(node.run_id, state))
    tool._supervised = SupervisedRun(
        plan=plan,
        completed=dict(results),
        execution_id=execution_id,
        reason=reason,
        boundary_run_ids=[n.run_id for n in nodes],
    )
    # 协作质量 tally (学·度量 §2.5, 首计划存活): a supervised boundary handed control back
    # to the captain mid-plan — the opening plan did not run start-to-finish untouched.
    tool._acc.collab["boundary_yields"] += 1
    # CHECKPOINT 是把方向盘交回给**用户**（plan_review 拍板）；BIND / SCOPE 才是队伍
    # 内部自己让出的边界。用户面只认后者，所以在这里就把用户那份单独记出来。
    if reason is BoundaryReason.CHECKPOINT:
        tool._acc.collab["boundary_yields_by_user"] += 1
    logger.info(
        "delegate.yielded",
        call=call_idx,
        reason=reason.value,
        boundary=[n.run_id for n in nodes],
        completed=len(results),
    )
    brief = format_boundary_for_ceo(tool, reason, plan, results, nodes)
    if session is not None:
        # Leave ``_pending_boundary`` for host to post BOUNDARY_YIELD + clear.
        return ToolResult(
            tool_call_id="",
            success=True,
            output=brief,
            output_limit=DELEGATE_OUTPUT_LIMIT,
        )
    tool._pending_boundary = None
    return ToolResult(
        tool_call_id="",
        success=True,
        output=brief,
        output_limit=DELEGATE_OUTPUT_LIMIT,
    )


async def handle_partial_failure(
    tool: DelegateTool,
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    session: Any,
    call_idx: int,
) -> ToolResult | None:
    """Stash plan on THIS-segment FAILED/SKIPPED. None → continue finalize."""
    from agentcore.runtime.runs import BoundaryReason, RunPhase

    # Partial failure: all nodes terminal but some FAILED / SKIPPED — stash the plan so the
    # CEO can replan(add=...) replacement nodes on the SAME DAG (not a fresh delegate).
    # Usage / ledger / citations fold on the resume or dispose path (same as boundary yield).
    # Only failures from THIS drive segment count — nodes already FAILED/SKIPPED in
    # ``seed_completed`` (a replan resume) must not re-trigger stash.
    seeded_ids = set(seed_completed or ())
    failed_nodes = [
        n
        for n in plan.nodes
        if (st := results.get(n.run_id)) is not None
        and st.phase in (RunPhase.FAILED, RunPhase.SKIPPED)
        and n.run_id not in seeded_ids
    ]
    if not failed_nodes or tool._supervised is not None:
        return None
    tool._supervised = SupervisedRun(
        plan=plan,
        completed=dict(results),
        execution_id=execution_id,
        reason=BoundaryReason.SCOPE,
        boundary_run_ids=[n.run_id for n in failed_nodes],
    )
    logger.info(
        "delegate.partial_failure_stashed",
        call=call_idx,
        failed=[n.run_id for n in failed_nodes],
        completed=len(results),
    )
    # 交付状态（诚实对账）：部分失败也是一次收尾——把已落盘 / 缺口如实发给用户；
    # CEO 若 replan 补跑，同 execution_id 再发对账：artifacts 与台账并集（同 path 后写）。
    from agentcore.runtime.runs.disk_truth import stamp_results_disk_truth

    backend = tool._base_tool_context.backend
    await stamp_results_disk_truth(results, backend)
    maybe_emit_delivery_status(
        tool._sink,
        plan,
        results,
        execution_id=execution_id,
        backend=backend,
        promotion_ledger=tool._base_tool_context.promotion_ledger,
    )
    from agentcore.runtime.runs.audit_ledger import load_audit_json_by_path

    audit_json = await load_audit_json_by_path(plan, results, backend)
    synthesis = build_ceo_synthesis(
        tool, plan, results, call_idx=call_idx, audit_json_by_path=audit_json
    )
    partial_output = synthesis.text
    # Coordination terminal: workers are all marked done; without ALL_COMPLETED the
    # CEO idle-waits the full coordination timeout (same class of bug as criteria gap).
    if session is not None:
        post_session_all_completed(
            session,
            output=synthesis.prose,
            roster_text=synthesis.roster_text,
            roster_facts=synthesis.roster_facts,
            closing_text=synthesis.closing_text,
            user_facts=collect_harvest_user_facts(plan, results),
        )
    from agentcore.runtime.delegate.delivery_status import build_delivery_status

    delivery_meta = build_delivery_status(
        plan,
        results,
        execution_id=execution_id,
        backend=tool._base_tool_context.backend,
        promotion_ledger=tool._base_tool_context.promotion_ledger,
    )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=partial_output,
        output_limit=DELEGATE_OUTPUT_LIMIT,
        # Keep success=True (avoid CEO retry storms) but attach structured delivery
        # meta so the CEO / presentation layer sees COMPLETED_WITH_GAPS honesty.
        metadata={
            "delivery": delivery_meta
            or {"state": "partial", "gaps": [], "execution_id": execution_id},
            "partial_failure": True,
            "failed_run_ids": [n.run_id for n in failed_nodes],
        },
    )


async def finalize_successful_drive(
    tool: DelegateTool,
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    execution_id: str,
    session: Any,
    call_idx: int,
) -> ToolResult:
    """Accumulate products, soft overlays, emit delivery, fold CEO ToolResult.

    S3: no kind-based completion binding / criteria_unmet hard path.
    """
    from agentcore.runtime.costing import usage_metadata
    from agentcore.runtime.delegate.completion import collect_completion_soft_notes

    # §十一 来源卡接入 (方案①, 远期规划.md §4.5): snapshot the turn-accumulated sources
    # BEFORE folding this call's workers in, so the slice below is exactly THIS delegate call's
    # NEW (deduped) web sources — including any nested sub-team absorbed just after. Carrying
    # them on the ToolResult lets the CEO-path execute_tools number them into the turn's source
    # cards AND fold each [n]=url back into THIS tool message, so the CEO can cite a worker-found
    # 法条 by a card-aligned [n] (Gap A). merge_citations dedups by url, so the turn-close
    # backstop merge (pipeline.run / resume.finish) re-folds the same sources as a no-op — one
    # numbering source, stable card indices across calls, no reconciliation patch.
    citations_before = len(tool._acc.citations)
    call_usage = accumulate_usage(tool, results)
    collect_ledger(tool, plan, results)
    collect_citations(tool, results)
    registered = register_sessions(tool, plan, results)
    if tool._session_saver is not None:
        for run_session in registered:
            await tool._session_saver(run_session)
    absorb_children(tool)
    new_citations = tool._acc.citations[citations_before:]

    # Soft overlays (D2 / import-graph): preload source texts when TS/Vue landed.
    file_map: dict[str, str] = {}
    backend = tool._base_tool_context.backend
    from agentcore.runtime.runs.disk_truth import stamp_results_disk_truth

    await stamp_results_disk_truth(results, backend)
    if backend is not None:
        from agentcore.runtime.delegate.completion import (
            _batch_landed_graph_sources,
            _collect_graph_source_paths,
        )
        from agentcore.runtime.delegate.graph_integrity import load_source_file_map
        from agentcore.runtime.runs import RunPhase as _RunPhase

        completed = [s for s in results.values() if s.phase is _RunPhase.COMPLETED]
        if _batch_landed_graph_sources(completed):
            file_map = await load_source_file_map(backend, _collect_graph_source_paths(completed))
    soft_notes = collect_completion_soft_notes(results, backend=backend, file_map=file_map or None)

    # 交付状态（诚实对账）：正常收尾——有落盘文件或缺口才发，
    # 纯 prose 成功批次保持无声。Soft overlay notes → state=notes（不 blocking）。
    maybe_emit_delivery_status(
        tool._sink,
        plan,
        results,
        execution_id=execution_id,
        backend=tool._base_tool_context.backend,
        criteria_gaps=soft_notes or None,
        promotion_ledger=tool._base_tool_context.promotion_ledger,
    )

    from agentcore.runtime.runs.audit_ledger import load_audit_json_by_path

    audit_json = await load_audit_json_by_path(plan, results, backend)
    synthesis = build_ceo_synthesis(
        tool, plan, results, call_idx=call_idx, audit_json_by_path=audit_json
    )
    output = synthesis.text
    if session is not None:
        post_session_all_completed(
            session,
            output=synthesis.prose,
            roster_text=synthesis.roster_text,
            roster_facts=synthesis.roster_facts,
            closing_text=synthesis.closing_text,
            user_facts=collect_harvest_user_facts(plan, results),
        )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        output_limit=DELEGATE_OUTPUT_LIMIT,
        metadata=usage_metadata(call_usage),
        citations=new_citations or None,
    )


async def finalize_drive(
    tool: DelegateTool,
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    execution_id: str,
    seed_completed: dict[str, RunState] | None,
    session: Any,
    call_idx: int,
    complexity_hint: str,
    batch_metrics: list[BatchMetrics],
) -> ToolResult:
    """Full post-wave finalize pipeline (metrics → early exits → success)."""
    # 本段 drive 的终态映射交回工具实例：同回合二次委派的 seed 以此为相
    # （``DelegateTool._last_drive_results``）。必须在 pause / boundary / partial 早退之前
    # 记——正是那几条路径上有失败节点与让出后未跑的尾节点。
    tool._last_drive_results = dict(results)
    stamp_last_graph_seed(tool, plan, results)
    # Thrash rebrand memory before early exits (pause / partial) so cold re-delegate
    # in the same conversation still sees DEGRADED/ceiling_backstop workers.
    from agentcore.runtime.coordination.thrash import record_thrashing_from_results

    record_thrashing_from_results(
        conversation_id=str(getattr(tool, "_conversation_id", None) or ""),
        plan=plan,
        results=results,
    )
    emit_batch_metrics(
        tool,
        batch_metrics,
        execution_id=execution_id,
        call_idx=call_idx,
        complexity_hint=complexity_hint,
    )
    paused = handle_pending_pause(tool, session=session, call_idx=call_idx, results=results)
    if paused is not None:
        return paused
    boundary = handle_pending_boundary(
        tool,
        plan,
        results,
        execution_id=execution_id,
        session=session,
        call_idx=call_idx,
    )
    if boundary is not None:
        return boundary
    partial = await handle_partial_failure(
        tool,
        plan,
        results,
        execution_id=execution_id,
        seed_completed=seed_completed,
        session=session,
        call_idx=call_idx,
    )
    if partial is not None:
        return partial
    return await finalize_successful_drive(
        tool,
        plan,
        results,
        execution_id=execution_id,
        session=session,
        call_idx=call_idx,
    )
