"""Delegate batch graph / roster SSE payloads."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.events import run_plan
from agentcore.runtime.graph.host import GraphHost
from agentcore.runtime.runs.plan import RunPlan


def run_payload(node) -> dict[str, Any]:
    """One worker's plan-time descriptor for the graph."""
    payload: dict[str, Any] = {
        "id": node.run_id,
        "agent_id": node.agent_id,
        "task": node.task,
        "depends_on": node.depends_on,
        "parent_run_id": node.parent_run_id,
    }
    if node.stance:
        payload["stance"] = node.stance
    if node.group:
        payload["group"] = node.group
    if node.round:
        payload["round"] = node.round
    if node.replaces_run_id:
        payload["replaces_run_id"] = node.replaces_run_id
    return payload


def card(tool: GraphHost, node) -> dict[str, Any]:
    """Roster entry with the node's declared thinking flag."""
    return {
        "id": node.agent_id,
        "role": node.role,
        "thinking": node.thinking,
    }


def plan_event(
    tool: GraphHost,
    execution_id: str,
    plan: RunPlan,
    *,
    prev_execution_id: str | None = None,
    act_id: str = "act-1",
):
    """Pre-declare this delegate batch's roster + runs so the graph lights up.

    Thoughts the loop already opened stay on their own frames. Putting them in
    this payload again would replace their upstream with the scheduler spec,
    which cannot carry edges to earlier turns.
    """
    from agentcore.runtime.graph.shape.step_segment import published_segment_ids

    published = published_segment_ids()
    nodes = [node for node in plan.nodes if node.run_id not in published]
    agents = [card(tool, n) for n in nodes]
    runs = [run_payload(n) for n in nodes]
    return run_plan(
        execution_id=execution_id,
        plan_type="multi_agent",
        task_summary="",
        agents=agents,
        runs=runs,
        prev_execution_id=prev_execution_id,
        act={"act_id": act_id, "kind": "multi_agent"},
    )
