"""Roll nested sub-team spend into the parent delegate accumulator.

Tool construction (``make_child`` / ``make_lead_subteam``) stays in
``tools.builtin.graph.nesting`` so runtime never imports the tool class.
"""

from __future__ import annotations

from typing import Any


def absorb_children(tool: Any) -> None:
    """Fold every nested sub-team spawned this call into the turn totals."""
    for child in tool._children:
        tool._acc.merge(child._acc)
    tool._children.clear()
