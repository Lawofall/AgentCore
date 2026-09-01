"""Resolve prepare phase: attachment context + worker wire helpers.

CEO toolset assembly ownership: ``agentcore.tools.ceo_toolset`` (re-exported
here for historical import / monkeypatch seams).

Attachment rendering lives beside this facade:

- ``attachment_conversation`` — conversation deep-read
- ``attachment_images`` — native multimodal / eye→text
- ``attachment_context`` — structure preview + capability-aware prompt block
"""

from __future__ import annotations

from agentcore.conversation.mentions import format_agent_mention_prompt
from agentcore.memory import (
    default_memory_store,  # noqa: F401 — monkeypatch seam (ceo_toolset imports it here)
)
from agentcore.runtime.resolve.attachment_context import (
    _build_attachment_context,  # noqa: F401 — public prepare / pipeline seam
    _build_attachment_prompt,  # noqa: F401
)
from agentcore.tools.builtin.read_conversation import ReadConversationTool
from agentcore.tools.builtin.search_conversations import SearchConversationsTool
from agentcore.tools.ceo_toolset import (
    _assemble_ceo_toolset,  # noqa: F401 — seam
)
from agentcore.tools.ceo_toolset import (
    wire_worker_consult as _wire_worker_consult_tools,  # noqa: F401 — historical name
)
from agentcore.tools.registry import ToolRegistry


def _wire_conversation_log_tools(
    tools: ToolRegistry,
    *,
    folder_id: str | None = None,
) -> None:
    """Register cross-session log tools on any registry (CEO or worker).

    ``search_conversations`` / ``read_conversation`` are ``manual_wire`` —
    never auto-registered by ``build_worker_registry`` / ``build_ceo_tool_registry``.
    Product-always-on (跨会话对话日志访问定案 A); still on-demand until consult.
    """
    tools.register(SearchConversationsTool(folder_id=folder_id))
    tools.register(ReadConversationTool())


def _build_agent_mention_context(
    agent_mentions: list[dict] | None,
) -> str | None:
    """Render conversation-page Agent soft mentions into a prompt block.

    Soft hint only — does not force delegate / hard-route. Empty / missing → None
    so the turn stays byte-identical to today's no-mention assembly.
    """
    return format_agent_mention_prompt(agent_mentions)


def merge_attachment_and_mention_context(
    attachment_context: str | None,
    agent_mentions: list[dict] | None,
) -> str | None:
    """Join file attachment block with optional Agent soft-mention block.

    Mentions ride the same ATTACHMENT volatile tail (紧邻 / 并入) so CEO and
    workers that already consume ``attachment_context`` stay in sync.
    """
    mention = _build_agent_mention_context(agent_mentions)
    parts = [p for p in (attachment_context, mention) if p]
    if not parts:
        return None
    return "\n\n".join(parts)
