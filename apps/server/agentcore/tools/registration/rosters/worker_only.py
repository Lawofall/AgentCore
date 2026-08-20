"""Worker-only surface roster (``ToolSurface.WORKER_ONLY``).

Append worker-audience tools here. Order is part of the public surface; keep
relative order when inserting.
"""

from __future__ import annotations


def load_roster() -> tuple[type, ...]:
    from agentcore.tools.builtin.amend_note import AmendNoteTool
    from agentcore.tools.builtin.browser import BrowserScreenshotTool
    from agentcore.tools.builtin.desktop_notify import DesktopNotifyTool
    from agentcore.tools.builtin.escalate import EscalateTool
    from agentcore.tools.builtin.handoff import HandoffTool
    from agentcore.tools.builtin.post_note import PostNoteTool
    from agentcore.tools.builtin.read_conversation import ReadConversationTool
    from agentcore.tools.builtin.read_notes import ReadNotesTool
    from agentcore.tools.builtin.search_conversations import SearchConversationsTool

    return (
        EscalateTool,
        PostNoteTool,
        ReadNotesTool,
        AmendNoteTool,
        HandoffTool,
        DesktopNotifyTool,
        # L3 团队浏览器截图（worker-only · browser_class · GRANTABLE）
        BrowserScreenshotTool,
        # privacy-gated worker log tools (manual_wire; not auto-registered)
        SearchConversationsTool,
        ReadConversationTool,
    )
