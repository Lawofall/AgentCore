"""consult — unified on-demand pull for skills / HOW-bearing tools / rules.

One tool + one ``<按需目录>`` for CEO and workers. Backed by a single
:class:`~agentcore.runtime.context.consult_sources.MergedConsultSource` so the
prompt catalog and ``fetch_by_name`` cannot drift.

Assembled tool schemas sit on the opening FC table; this tool does not
promote them. Soft miss on unknown / empty name (``success=True`` + available
names). Hard skill failures are intentionally gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.runtime.context.consultable import Consultable
from agentcore.runtime.memory_consult_cache import (
    lookup_consult,
    lookup_consult_origin,
    remember_consult,
)
from agentcore.tools.on_demand import is_tool_fc_name
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

_CONSULT_OUTPUT_LIMIT = 8000
_VALID_ORIGIN = frozenset({"system", "user"})


def _consult_display(
    name: str, *, reused: bool = False, origin: str | None = None
) -> dict[str, Any]:
    display: dict[str, Any] = {"name": name}
    if reused:
        display["reused"] = True
    if origin in _VALID_ORIGIN:
        display["origin"] = origin
    return display


@dataclass
class ConsultTool:
    """Unified name → body recall. ``source`` is shared with the prompt directory."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_BOTH,
        # Wired when the directory has a row. An empty catalog leaves it off the table.
        ceo_wire=CeoWire.CONSULT,
        catalog_summary="按名查阅按需目录",
        blurb="翻开官方 HOW 或技能",
    )

    source: Consultable

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="consult",
            description=(
                "按目录 name 拉全文（技能 / 设定）。"
                "已装配工具在开场表，不必靠查阅进表。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "按需目录里列出的 name。",
                    },
                },
                "required": ["name"],
            },
            face=ToolFace.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def _available_names(self, user_id: str) -> list[str]:
        return [e.name for e in await self.source.list_directory(user_id)]

    async def _fetch_body_and_origin(
        self, user_id: str, name: str
    ) -> tuple[str | None, str | None]:
        fetch_hit = getattr(self.source, "fetch_hit", None)
        if fetch_hit is not None:
            hit = await fetch_hit(user_id, name)
            if hit is None:
                return None, None
            return hit.body, hit.origin
        body = await self.source.fetch_by_name(user_id, name)
        return body, None

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        raw = str(arguments.get("name") or "").strip()
        if raw and not is_tool_fc_name(raw):
            cached = lookup_consult(raw)
            if cached is not None:
                logger.info("consult.reuse", name=raw)
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output=cached,
                    output_limit=_CONSULT_OUTPUT_LIMIT,
                    display=_consult_display(
                        raw, reused=True, origin=lookup_consult_origin(raw)
                    ),
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

        body, origin = await self._fetch_body_and_origin(context.user_id, raw)
        if body is None:
            available = "、".join(await self._available_names(context.user_id))
            head = f"没有名为 '{raw}' 的条目。"
            tail = f" 可查阅：{available}。" if available else " 当前按需目录为空。"
            logger.info("consult.miss", name=raw)
            return ToolResult(tool_call_id="", success=True, output=head + tail)

        if not is_tool_fc_name(raw):
            remember_consult(raw, body, origin=origin)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=body,
            output_limit=_CONSULT_OUTPUT_LIMIT,
            display=_consult_display(raw, origin=origin),
        )
