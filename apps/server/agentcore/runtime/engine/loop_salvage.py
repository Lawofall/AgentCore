"""Captain structured-reply salvage at ReAct loop exit.

Wraps ``salvage_captain_delegate_reply`` with the engine log line. Public callers
go through ``engine.loop``; this module is the salvage concern, not the loop.
"""

from __future__ import annotations

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.turn.outcome import salvage_captain_delegate_reply

logger = get_logger(__name__)


def maybe_salvage_captain_reply(
    *,
    final_content: str,
    messages: list[LLMMessage],
    role: str,
) -> str:
    salvaged = salvage_captain_delegate_reply(
        final_content=final_content, messages=messages, role=role
    )
    if not salvaged:
        return final_content
    logger.info(
        "engine.structured_reply_salvaged",
        chars=len(salvaged),
        source="delegate",
    )
    return salvaged
