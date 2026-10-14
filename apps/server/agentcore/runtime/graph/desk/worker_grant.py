"""Silent ``command=auto`` grant before workers start."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.graph.host import GraphHost


async def maybe_auto_grant_before_workers(
    tool: GraphHost,
    *,
    seed_completed: dict[str, Any] | None,
) -> None:
    """Mark ``_auto_grant_pending`` when ``command=auto`` still silent-grants.

    Nested / seeded resumes are no-ops. Leftover hung cards are not recovered.
    """
    if tool._depth != 0:
        return
    if seed_completed is not None:
        return
    from agentcore.core.types import DEFAULT_PERMISSION_AXES
    from agentcore.runtime.sandbox_approval import worker_gate_applies

    axes = getattr(tool, "_permission_axes", None) or DEFAULT_PERMISSION_AXES
    local_gate = worker_gate_applies(tool._base_tool_context.backend)
    if (
        local_gate
        and tool._approval_gate is not None
        and axes.allows_execution
    ):
        tool._auto_grant_pending = True  # type: ignore[attr-defined]
