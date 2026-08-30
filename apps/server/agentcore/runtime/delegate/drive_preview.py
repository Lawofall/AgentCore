"""Team preview gate that runs before workers / coordination fork."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.tools.protocol import ToolResult

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

type DelegateTool = Any


async def team_preview_before_workers(
    tool: DelegateTool,
    plan: RunPlan,
    *,
    complexity_hint: str,
    seed_completed: dict[str, Any] | None,
    call_idx: int,
) -> ToolResult | None:
    """No longer hangs a new team_preview card.

    Returns None to proceed (or None for nested). ``command=auto`` silent grant
    still marks ``_auto_grant_pending`` for :func:`apply_delegation_grant`.
    Leftover hung cards are not recovered. light / seed /
    adjust no longer decide whether to emit a card — top-level
    also runs. ``stage_card`` keep is marked when any top-level delegate starts.
    """
    _ = (plan, complexity_hint, call_idx)
    if tool._depth != 0:
        return None
    # 续跑 / 同回合追加带 seed：不开新卡。grant 见 seed 即 no-op。
    if seed_completed is not None:
        return None
    from agentcore.core.types import DEFAULT_PERMISSION_AXES
    from agentcore.runtime.sandbox_approval import worker_gate_applies

    axes = getattr(tool, "_permission_axes", None) or DEFAULT_PERMISSION_AXES
    local_gate = worker_gate_applies(tool._base_tool_context.backend)
    # Card skipped: still silent-grant when command=auto.
    if (
        local_gate
        and tool._approval_gate is not None
        and axes.auto_executes
    ):
        tool._auto_grant_pending = True  # type: ignore[attr-defined]
    from agentcore.runtime.kickoff.stage_card import mark_turn_keeps_stage_card

    mark_turn_keeps_stage_card()
    return None
