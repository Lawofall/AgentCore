"""Model rewind: record the cut, then start one follow segment from the target."""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.graph.shape.rewind import (
    CAP_MSG,
    MAX_MODEL_REWINDS,
    RewindNode,
    RewindRejected,
    memory_text,
    model_rewind_count,
    plan_rewind,
)
from agentcore.runtime.facts import GraphRewindFact, current_fact_log, record_turn_fact
from agentcore.runtime.runs.types import TERMINAL_PHASES, RunKind, RunPhase, RunSpec, RunState
from agentcore.tools.protocol import ToolEffect, ToolResult

logger = get_logger(__name__)


def nodes_from_live(plan: Any, seed: dict[str, RunState] | None) -> list[RewindNode]:
    states = seed or {}
    nodes: list[RewindNode] = []
    for spec in plan.nodes:
        state = states.get(spec.run_id)
        sealed = state is not None and state.phase is RunPhase.COMPLETED
        conclusion = ""
        if sealed and state is not None:
            conclusion = (state.content or "").strip()
            if not conclusion and isinstance(state.debrief, dict):
                conclusion = str(state.debrief.get("summary") or "").strip()
        nodes.append(
            RewindNode(
                id=spec.run_id,
                depends_on=tuple(spec.depends_on or []),
                goal=spec.task or spec.role or spec.run_id,
                conclusion=conclusion,
                sealed=sealed,
            )
        )
    return nodes


def _fail(text: str) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output=text,
        effect=ToolEffect.CONTINUE,
    )


def _follow_id(existing: set[str], index: int) -> str:
    base = f"rw_{index}"
    if base not in existing:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing:
        suffix += 1
    return f"{base}_{suffix}"


async def apply_model_rewind(tool: Any, arguments: dict[str, Any]) -> ToolResult:
    """Cut the graph to a sealed segment and run one new node that depends on it."""
    if int(getattr(tool, "_depth", 0) or 0) != 0:
        return _fail("这一步不能回转整张图。")
    target_id = str(arguments.get("target_id") or "").strip()
    reason = str(arguments.get("reason") or "").strip()
    next_goal = str(arguments.get("next_goal") or "").strip()
    if not target_id or not reason or not next_goal:
        return _fail("写下要回到的段、原因，以及接着要做成的事。")

    plan = getattr(tool, "_last_graph_plan", None)
    if plan is None or not getattr(plan, "nodes", None):
        return _fail("图上还没有封口的段。")

    log = current_fact_log.get()
    entries = log.entries() if log is not None else []
    used = model_rewind_count(entries)
    if used >= MAX_MODEL_REWINDS:
        return _fail(CAP_MSG)

    execution_id = (
        getattr(tool, "_last_graph_execution_id", None)
        or getattr(getattr(tool, "_base_tool_context", None), "execution_id", None)
        or ""
    )
    if not execution_id:
        return _fail("这张图还没开始，不能回转。")

    seed = dict(getattr(tool, "_last_graph_seed", None) or {})
    for run_id, state in (getattr(tool, "_last_drive_results", None) or {}).items():
        seed.setdefault(run_id, state)
    try:
        planned = plan_rewind(
            nodes_from_live(plan, seed),
            target_id,
            reason,
            original_request=getattr(tool, "_user_message", "") or "",
        )
    except RewindRejected as exc:
        return _fail(str(exc))

    window_run_id = getattr(tool, "_captain_run_id", "") or ""
    record_turn_fact(
        GraphRewindFact(
            target_id=planned.target_id,
            scar=planned.scar,
            kept_ids=planned.kept_ids,
            cut_ids=planned.cut_ids,
            conclusions=planned.conclusions,
            original_request=planned.original_request,
            source="model",
            window_run_id=window_run_id,
            reason=planned.reason,
        ).to_fact()
    )
    from agentcore.runtime.events import graph_rewind

    tool._sink.emit(
        graph_rewind(
            target_run_id=planned.target_id,
            cut_run_ids=list(planned.cut_ids),
            scar=planned.scar,
            source="model",
        )
    )

    from agentcore.runtime.runs.plan import RunPlan

    follow_plan = RunPlan(nodes=list(plan.nodes))
    follow_plan.origin = plan.origin
    follow_run_id = _follow_id({node.run_id for node in follow_plan.nodes}, used + 1)
    follow_plan.add(
        RunSpec(
            run_id=follow_run_id,
            agent_id=follow_run_id,
            agent_name="接着做",
            kind=RunKind.AGENT,
            task=f"{next_goal}\n\n{planned.scar}",
            role="接着做",
            depth=1,
            parent_run_id=window_run_id or None,
            depends_on=[planned.target_id],
        )
    )
    for run_id in planned.cut_ids:
        state = seed.get(run_id)
        if state is None or state.phase not in TERMINAL_PHASES:
            seed[run_id] = RunState(phase=RunPhase.CANCELLED)

    from agentcore.runtime.graph.drive.drive import drive
    from agentcore.runtime.graph.drive.plan_events import plan_event

    if execution_id:
        tool._sink.emit(plan_event(tool, execution_id, follow_plan))
    logger.info(
        "rewind.model",
        target_id=planned.target_id,
        cut=len(planned.cut_ids),
        follow=follow_run_id,
    )
    result = await drive(
        tool,
        follow_plan,
        execution_id=execution_id,
        seed_completed=seed,
        coordinate=False,
    )
    prefix = memory_text(planned.conclusions, planned.scar)
    if result.output:
        result.output = f"{prefix}\n\n{result.output}"
    else:
        result.output = prefix
    if result.success and execution_id:
        tool._last_graph_execution_id = execution_id
        tool._last_graph_plan = RunPlan(nodes=list(follow_plan.nodes))
        tool._last_graph_plan.origin = follow_plan.origin
        from agentcore.runtime.graph.session.session import active_coordination
        from agentcore.runtime.graph.drive.drive_finalize import stamp_last_graph_seed

        active = active_coordination(execution_id)
        if active is None or not active.active:
            stamp_last_graph_seed(tool, follow_plan, tool._last_drive_results)
    return result
