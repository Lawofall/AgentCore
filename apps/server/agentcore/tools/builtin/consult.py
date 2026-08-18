"""consult — unified on-demand pull for skills / rules / memory / deferred tools.

One tool + one ``<按需目录>`` for CEO and workers. Backed by a single
:class:`~agentcore.runtime.context.consult_sources.MergedConsultSource` so the
prompt catalog and ``fetch_by_name`` cannot drift.

Soft miss on unknown / empty name (``success=True`` + available names). Playbook-name
special-case and hard skill failures are intentionally gone — playbooks stay visible
via ``delegate``'s own schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.context.consultable import Consultable
from agentcore.runtime.memory_consult_cache import lookup_consult, remember_consult
from agentcore.tools.on_demand import is_on_demand_tool
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

_CONSULT_OUTPUT_LIMIT = 8000


@dataclass
class ConsultTool:
    """Unified name → body recall. ``source`` is shared with the prompt directory."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_BOTH,
        # Wired by hand when the merged catalog is non-empty (单一 has_entries 门控).
        ceo_wire=CeoWire.CONSULT,
    )

    source: Consultable

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="consult",
            description=(
                "按 name 查阅一条按需条目：系统提示词「按需目录」列出 name 与一行说明"
                "（系统能力指引、按需用户规则、记忆主题笔记、本回合未进工具表的低频工具）。"
                "相关时拉全文再执行/遵守。低频工具 consult 后下一轮才进入可调用工具表；"
                "常驻内容已在 ``<rules>``，常驻工具已在工具表，无需查阅。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "要查阅的条目名称，取自系统提示词「按需目录」里列出的 name。"
                        ),
                    },
                },
                "required": ["name"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def _available_names(self, user_id: str) -> list[str]:
        return [e.name for e in await self.source.list_directory(user_id)]

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        raw = str(arguments.get("name") or "").strip()
        if raw and not is_on_demand_tool(raw):
            cached = lookup_consult(raw)
            if cached is not None:
                logger.info("consult.reuse", name=raw)
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output=cached,
                    output_limit=_CONSULT_OUTPUT_LIMIT,
                    display={"name": raw, "reused": True},
                )

        if not raw:
            available = "、".join(await self._available_names(context.user_id))
            msg = "缺少 name 参数。"
            if available:
                msg += f" 可查阅：{available}。"
            else:
                msg += " 当前按需目录为空。"
            logger.info("consult.miss", name=raw)
            return ToolResult(tool_call_id="", success=True, output=msg)

        body = await self.source.fetch_by_name(context.user_id, raw)
        if body is None:
            available = "、".join(await self._available_names(context.user_id))
            head = f"没有名为 '{raw}' 的条目。"
            tail = f" 可查阅：{available}。" if available else " 当前按需目录为空。"
            logger.info("consult.miss", name=raw)
            return ToolResult(tool_call_id="", success=True, output=head + tail)

        if not is_on_demand_tool(raw):
            remember_consult(raw, body)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=body,
            output_limit=_CONSULT_OUTPUT_LIMIT,
            display={"name": raw},
        )
