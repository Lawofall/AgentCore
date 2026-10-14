"""CEO orchestration surface roster (``ToolSurface.CEO_ORCHESTRATION``).

Append CEO catalog / wire tools here. Order is part of the public surface; keep
relative order when inserting.
"""

from __future__ import annotations


def load_roster() -> tuple[type, ...]:
    from agentcore.tools.builtin.ask_user import AskUserTool
    from agentcore.tools.builtin.consult import ConsultTool
    from agentcore.tools.builtin.debate import DebateTool
    from agentcore.tools.builtin.graph import GraphTool
    from agentcore.tools.builtin.folders import FoldersTool
    from agentcore.tools.builtin.replan import ReplanTool

    return (
        GraphTool,
        ReplanTool,
        DebateTool,
        ConsultTool,
        FoldersTool,
        AskUserTool,
    )
