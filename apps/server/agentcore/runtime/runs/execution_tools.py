"""Whether a run node is treated as holding execution tools."""

from __future__ import annotations

from typing import Any


def node_holds_execution_tools(spec: Any) -> bool:
    """Nodes are treated as holding execution tools. The registry answers what is mounted."""
    del spec
    return True
