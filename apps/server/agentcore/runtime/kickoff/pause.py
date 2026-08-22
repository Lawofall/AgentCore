"""Kickoff pause helpers — leftover journal fold only."""

from __future__ import annotations

from agentcore.tools.registration import execution_class_tool_names


def kickoff_tools(*, show_capabilities: bool) -> list[str]:
    """Execution-class tools listed on a kickoff summary (capability half).

    Empty when the capability half is hidden. New cards are not hung; this list
    remains for summaries / leftover journal fold.
    """
    if not show_capabilities:
        return []
    return sorted(execution_class_tool_names())
