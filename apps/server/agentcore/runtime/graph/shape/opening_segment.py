"""The user's opening stretch is the first node on the collaboration graph.

It is on the graph from the start of the turn. Splitting seals it with the
conclusion the model writes. Later nodes that name no upstream wait on this
node, so their windows receive that conclusion and not this stretch's tool
trace.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.events import run_completed, run_plan, run_started
from agentcore.runtime.events.types import SSEEvent
from agentcore.runtime.graph.shape.step_segment import StepChain
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunKind, RunOrigin, RunPhase, RunSpec, RunState

OPENING_CONCLUSION_REQUIRED = (
    "先写下这段的结论。后面的段只看见这句，看不见这段里的查找和修改。"
)


def opening_segment_id(captain_run_id: str) -> str:
    return f"seg_{captain_run_id}"


def opening_label(user_message: str) -> str:
    text = " ".join((user_message or "").split())
    if not text:
        return "这段"
    if len(text) <= 24:
        return text
    return text[:24] + "…"


def spec_for_open_think(
    chain: StepChain,
    *,
    depth: int,
    parent_run_id: str | None,
) -> RunSpec:
    """Scheduler node for the thought the loop already opened.

    The conversation edge is ``opened_from`` on that segment. It does not belong
    on this spec: the plan topology only knows nodes in the plan, and a later
    plan frame must not restate this id.
    """
    label = chain.think_label or "这段"
    return RunSpec(
        run_id=chain.think_id,
        agent_id=chain.think_id,
        agent_name=label,
        kind=RunKind.AGENT,
        task=label,
        role=label,
        depth=depth,
        parent_run_id=parent_run_id or None,
    )


def plan_with_opening(host_plan: RunPlan | None, opening: RunSpec) -> RunPlan:
    """Host nodes plus the opening, so edges onto it resolve.

    ``build_run_plan`` returns only the new batch. The opening has to sit in
    ``existing_plan`` or those edges are unknown runs. The live thought and the
    captain-run fallback both come through here.
    """
    if host_plan is not None and any(node.run_id == opening.run_id for node in host_plan.nodes):
        return host_plan
    origin = host_plan.origin if host_plan is not None else RunOrigin.CAPTAIN
    host_nodes = list(host_plan.nodes) if host_plan is not None else []
    return RunPlan(nodes=[opening, *host_nodes], origin=origin)


def opening_spec(*, captain_run_id: str, user_message: str) -> RunSpec:
    label = opening_label(user_message)
    run_id = opening_segment_id(captain_run_id)
    return RunSpec(
        run_id=run_id,
        agent_id=run_id,
        agent_name=label,
        kind=RunKind.AGENT,
        task=user_message or label,
        role=label,
        depth=1,
        parent_run_id=captain_run_id or None,
    )


def opening_completed(conclusion: str) -> RunState:
    return RunState(phase=RunPhase.COMPLETED, content=conclusion.strip())


def plan_contains_opening(plan: Any, captain_run_id: str) -> bool:
    if plan is None or not captain_run_id:
        return False
    target = opening_segment_id(captain_run_id)
    return any(getattr(node, "run_id", None) == target for node in plan.nodes)


def link_empty_deps_to_opening(
    tasks: list[dict[str, Any]],
    opening_id: str,
) -> list[dict[str, Any]]:
    """Nodes with no upstream wait on the opening segment."""
    linked: list[dict[str, Any]] = []
    for item in tasks:
        deps = item.get("depends_on") or []
        if deps:
            linked.append(item)
            continue
        linked.append({**item, "depends_on": [opening_id]})
    return linked


def opening_plan_events(
    *,
    execution_id: str,
    captain_run_id: str,
    user_message: str,
    depends_on: list[str] | None = None,
) -> list[SSEEvent]:
    """Graph frames for this turn's opening segment.

    ``depends_on`` is the leaves of the conclusions still in this window.
    Empty means this segment starts a root. The event stays on the current turn.
    """
    spec = opening_spec(captain_run_id=captain_run_id, user_message=user_message)
    upstream = [item for item in (depends_on or []) if item]
    return [
        run_plan(
            execution_id=execution_id,
            plan_type="multi_agent",
            task_summary=spec.role,
            agents=[
                {
                    "id": spec.agent_id,
                    "role": spec.role,
                    "thinking": True,
                }
            ],
            runs=[
                {
                    "id": spec.run_id,
                    "agent_id": spec.agent_id,
                    "task": spec.task,
                    "depends_on": upstream,
                    "parent_run_id": spec.parent_run_id,
                    "kind": "agent",
                }
            ],
            act={"act_id": "act-1", "kind": "multi_agent"},
        ),
        run_started(
            spec.run_id,
            spec.agent_id,
            parent_run_id=spec.parent_run_id,
            kind="agent",
        ),
    ]


def opening_sealed_event(*, captain_run_id: str, conclusion: str) -> SSEEvent:
    run_id = opening_segment_id(captain_run_id)
    text = conclusion.strip()
    return run_completed(
        run_id,
        run_id,
        output_summary=text,
        duration_ms=0,
        role="这段",
        debrief={"summary": text},
    )
