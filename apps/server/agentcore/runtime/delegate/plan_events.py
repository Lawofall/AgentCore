"""Delegate batch graph / roster SSE payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.runtime.delegate.seed_notes import is_note_wall_batch
from agentcore.runtime.events import run_context, run_plan

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

DelegateTool = Any


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


def captain_card(captain_run_id: str) -> dict[str, Any]:
    """Roster card for the CEO captain root node."""
    return {
        "id": captain_run_id,
        "role": "CEO",
        "thinking": True,
    }


def captain_run(captain_run_id: str) -> dict[str, Any]:
    """Plan-time captain root descriptor (kind=captain)."""
    return {
        "id": captain_run_id,
        "agent_id": captain_run_id,
        "task": "",
        "depends_on": [],
        "parent_run_id": None,
        "kind": "captain",
    }


def card(tool: DelegateTool, node) -> dict[str, Any]:
    """Roster entry with the node's declared thinking flag."""
    return {
        "id": node.agent_id,
        "role": node.role,
        "thinking": node.thinking,
    }


def plan_event(
    tool: DelegateTool,
    execution_id: str,
    plan: RunPlan,
    *,
    prev_execution_id: str | None = None,
    act_id: str = "act-1",
):
    """Pre-declare this delegate batch's roster + runs so the graph lights up.

    幕声明：本批 A1 一律归宿主既有幕（默认 ``act-1`` / ``multi_agent``）；开新幕是后续批次。

    Captain 注入：根协调者注入本回合 ``tool._captain_run_id``（每回合新图各自 captain）。
    同回合二次合入 / adopt 热图 merge 仍靠同 ``execution_id``，不写 ``prev_execution_id``。
    """
    roles = list(dict.fromkeys(n.role for n in plan.nodes if n.role))
    agents = [card(tool, n) for n in plan.nodes]
    runs = [run_payload(n) for n in plan.nodes]
    if tool._depth == 0 and tool._captain_run_id:
        agents.insert(0, captain_card(tool._captain_run_id))
        runs.insert(0, captain_run(tool._captain_run_id))
    coordination = getattr(tool, "_coordination", None) or "none"
    wall = is_note_wall_batch(len(plan.nodes), coordination)
    return run_plan(
        execution_id=execution_id,
        plan_type="multi_agent",
        task_summary=f"{len(plan.nodes)} 个 worker：{'、'.join(roles)}" if roles else "",
        agents=agents,
        runs=runs,
        prev_execution_id=prev_execution_id,
        act={"act_id": act_id, "kind": "multi_agent"},
        note_wall=True if wall else None,
    )


def emit_captain_readback(tool: DelegateTool, products: list[dict[str, Any]]) -> None:
    """上下文传递可视化 通道⑤: ship team products back to the CEO bubble."""
    if tool._depth != 0 or not tool._captain_run_id:
        return
    blocks = [
        {
            "channel": "team_result",
            "heading": f"{wp['role']}（{wp['status']}）",
            "body": wp["body"],
            "chars": len(wp["body"]),
            "truncated": wp["truncated"],
            "source_role": wp["role"],
            "source_run_id": wp["run_id"],
            "fidelity": wp["fidelity"],
            "files": wp["files"],
        }
        for wp in products
    ]
    if blocks:
        tool._sink.emit(run_context(tool._captain_run_id, tool._captain_run_id, blocks))
