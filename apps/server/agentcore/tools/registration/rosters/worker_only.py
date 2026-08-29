"""Worker-only surface roster (``ToolSurface.WORKER_ONLY``).

Append worker-audience tools here. Order is part of the public surface; keep
relative order when inserting.
"""

from __future__ import annotations


def load_roster() -> tuple[type, ...]:
    from agentcore.tools.builtin.desktop_notify import DesktopNotifyTool
    from agentcore.tools.builtin.escalate import EscalateTool
    from agentcore.tools.builtin.handoff import HandoffTool
    from agentcore.tools.builtin.read_conversation import ReadConversationTool
    from agentcore.tools.builtin.search_conversations import SearchConversationsTool

    return (
        EscalateTool,
        HandoffTool,
        DesktopNotifyTool,
        # worker log tools (manual_wire; not auto-registered; product-always-on)
        SearchConversationsTool,
        ReadConversationTool,
    )
