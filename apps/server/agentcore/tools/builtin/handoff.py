"""handoff — worker finish signal; the brief is the closing round's prose.

Worker-only, terminal. The call itself only means「写完了」. The 便条 lives in
that same assistant message's ``content`` (harvested by
:func:`~agentcore.runtime.runs.serialize.debrief_from_transcript`), not in
tool arguments — stuffing a wrap-up into JSON is how argument parse used to
fail after a long ``file_write``.

When to call (identity + this description + engine gate say the same sentence):
- Has dependents: **must** handoff; the closing-round prose carries the conclusion.
- No dependents: **skip** by default; call only for incremental briefing the body
  or files do not already carry.

Terminal by design (``ToolEffect.HANDOFF``): content of the handoff round is kept
(Fork-B rolls back prose only before a NON-terminal tool). Write the deliverable
first; the closing round's content is the short brief.

``final_text`` stays empty — a 0-body round with a brief in content already kept
that prose as the deliverable. Historical runs that stuffed the brief into
arguments still harvest via serialize's args fallback.

Wired into the delegated worker toolset (``build_worker_registry``) and NOT into
``build_builtin_registry`` — so it never reaches the CEO's own toolset
(``build_ceo_tool_registry`` derives the CEO subset from the builtins) or the
read-only ``GET /tools`` capability catalog, mirroring how ``escalate`` is wired
in only where it belongs.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory, ToolEffect
from agentcore.runtime.runs.constants import HANDOFF_TOOL_NAME
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_WORKER_ONLY,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

# Protocol ack on the tool result. Frontend HANDOFF_RECEIPT must stay in lockstep.
# Never a user-facing peek or expand body.
HANDOFF_RECEIPT = "已收尾。"


class HandoffTool:
    """The worker's finish primitive (terminal). Brief rides closing-round content."""

    registration = ToolRegistration(
        surface=ToolSurface.WORKER_ONLY,
        audience=AUDIENCE_WORKER_ONLY,
    )

    @property
    def schema(self) -> ToolSchema:
        # Schema layer: when-to-use + channel + writing shape.
        # Identity only says must-vs-may; empty parameters — do not restore JSON fields.
        return ToolSchema(
            name=HANDOFF_TOOL_NAME,
            description=(
                "收尾。便条写在这一轮正文（给主管/下游）。"
                "有下游：必须调用；一句话结论（现在什么已成立）；"
                "下一棒要接的路径/数字/决定。便条 ≠ 文件说明。"
                "无下游：默认不调用；仅盘上没有的假设/风险/未验证才补。"
                "先写完交付再调用。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        _ = arguments
        n = context.round_content_chars
        body_chars = int(n) if isinstance(n, int) and n >= 0 else 0
        logger.info("worker.handoff", run_id=context.run_id, body_chars=body_chars)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=HANDOFF_RECEIPT,
            effect=ToolEffect.HANDOFF,
            final_text="",
        )
