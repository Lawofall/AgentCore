"""Nested sub-team delegate tool construction (thin tools-side adapter).

Drive-layer roll-up (``absorb_children``) lives in ``runtime.delegate.nesting``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentcore.tools.builtin.replan import ReplanTool

if TYPE_CHECKING:
    from agentcore.runtime.runs.executor.identities import LeadSubteam
    from agentcore.tools.builtin.delegate.tool import DelegateTool


def make_child(tool: DelegateTool, captain_run_id: str, captain_depth: int) -> DelegateTool:
    """Mint a delegate tool for a worker that leads one nested sub-team (阶段2)."""
    from agentcore.tools.builtin.delegate.tool import DelegateTool as DelegateToolCls

    child = DelegateToolCls(
        llm=tool._llm,
        sink=tool._sink,
        system_prompt=tool._system_prompt,
        user_message=tool._user_message,
        history=tool._history,
        tools=tool._tools,
        base_tool_context=tool._base_tool_context,
        profile_set=tool._profile_set,
        max_parallel=tool._max_parallel,
        captain_run_id=captain_run_id,
        approval_gate=tool._approval_gate,
        session_store=tool._session_store,
        session_saver=tool._session_saver,
        session_loader=tool._session_loader,
        conversation_id=tool._conversation_id,
        registry=tool._registry,
        checkpoint_timeout_seconds=tool._checkpoint_timeout_seconds,
        checkpoint_enabled=tool._checkpoint_enabled,
        depth=captain_depth,
        permission_axes=tool._permission_axes,
        folder_id=tool._folder_id,
    )
    # Nested default desk: prepare_agent_node may overwrite with parent target.
    child._default_target_folder_id = getattr(tool, "_default_target_folder_id", None)
    child._local_root_claims = getattr(tool, "_local_root_claims", None)
    tool._children.append(child)
    return child


def make_lead_subteam(
    tool: DelegateTool, captain_run_id: str, captain_depth: int
) -> LeadSubteam:
    """Mint a worker-captain's full nested-delegation handle (阶段2 嵌套 + 受监督子计划 B).

    Beyond :func:`make_child` (which mints only the lead's own ``delegate``), this wires the
    two pieces that make a lead a *real* captain rather than a dead-end:

    - the companion ``replan`` bound to THAT child delegate, so the lead finalises /
      re-steers and resumes its OWN sub-plan at a 波边界 exactly like the CEO (去特例);
    - a turn-end ``dispose`` that folds a sub-plan the lead yielded but never resumed
      (堵漏账), so the parent's ``absorb_children`` never misses sub-team spend.

    Returned to the ``runs`` layer as the opaque :class:`LeadSubteam` bundle so ``runs``
    stays free of a concrete tools dependency (lazy import, as elsewhere in this package).
    """
    from agentcore.runtime.runs.executor.identities import LeadSubteam

    child = make_child(tool, captain_run_id, captain_depth)
    replan = ReplanTool(delegate=child)

    async def _dispose() -> None:
        # Implicit stop for a sub-plan the lead yielded but never resumed: folds the
        # completed sub-team's usage/ledger/citations onto the child's _acc (which the
        # parent then absorbs) and materialises the un-run tail SKIPPED. No-op when nothing
        # is paused; the returned ToolResult is unused — the lead already moved on.
        await child.dispose_open_supervised()

    return LeadSubteam(
        tools=(child, replan),
        tool_names=(child.schema.name, replan.schema.name),
        dispose=_dispose,
    )
