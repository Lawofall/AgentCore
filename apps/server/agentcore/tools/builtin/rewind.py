"""rewind: go back to a sealed segment and start the next one from there.

The CEO calls this. A person uses the graph control, which does not start a turn.
Files stay as they are. Only the person's checkbox restores the seal-time snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.runtime.graph.shape.rewind import MAX_MODEL_REWINDS
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

if TYPE_CHECKING:
    from agentcore.tools.builtin.graph import GraphTool

_REWIND_DESCRIPTION = (
    "回到图上已经封口的一段，并立刻从那段再开一段。"
    "之后只记得用户当初的那句话、这一段和它前面各段的结论，"
    "以及一行字，写明丢掉了哪些段、为什么。"
    "丢掉的段还在记录里，标成已切出，不再拿来看。"
    f"一场自己回转最多 {MAX_MODEL_REWINDS} 次。文件不动。"
)

_REWIND_PARAMETERS = {
    "type": "object",
    "properties": {
        "target_id": {
            "type": "string",
            "description": "要回到的那段的 id。它必须已经封口。",
        },
        "reason": {
            "type": "string",
            "description": "后面的段为什么丢掉。写进那一行字。",
        },
        "next_goal": {
            "type": "string",
            "description": "从这段结论接着要做成的事。",
        },
    },
    "required": ["target_id", "reason", "next_goal"],
}


class RewindTool:
    """CEO tool. Shares the turn's graph on the GraphTool."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.COORDINATION,
        catalog_summary="回到已封口的一段",
        blurb="从那段再开一段，丢掉的段标成已切出",
    )

    def __init__(self, *, delegate: GraphTool) -> None:
        self._delegate = delegate

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="rewind",
            description=_REWIND_DESCRIPTION,
            parameters=_REWIND_PARAMETERS,
            face=ToolFace.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        del context
        return await self._delegate.rewind(arguments)
