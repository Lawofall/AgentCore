"""Dynamic MCP tool wrappers — GRANTABLE · on-demand · desktop stdio backfill."""

from __future__ import annotations

import json
import re
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.desktop.channel import McpOp, McpOpError
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema

logger = get_logger(__name__)

# OpenAI-style function name: [a-zA-Z0-9_-]{1,64}
_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")
_TOOL_NAME_MAX = 64

# Timeout ladder — 通道 = 实际值 + slack ≤ 引擎墙钟 (same shape as ``host_shell`` /
# ``host_package_install``). Worst case one MCP call costs the desktop 握手 45s +
# tools/list 30s (server must be respawned) + tools/call 30s (mcp-service.ts).
_MCP_OP_TIMEOUT_SECONDS = 105.0
# Channel transport deadline: the layer that MUST fire first when an MCP Server wedges,
# because only it can name the failure（「本机 MCP 操作超时」+ server / tool below）.
_MCP_CHANNEL_TIMEOUT_SECONDS = _MCP_OP_TIMEOUT_SECONDS + 15.0
# Engine wall-clock ceiling. Its clock starts at tool dispatch — strictly earlier than the
# channel's — so an equal budget (both hardcoded 120s before) makes the MCP-specific error
# unreachable: every wedged Server surfaced as the engine's generic 活性挂起, blurring an
# external tool fault into "the model used the tool wrong". Must outlive the channel.
_MCP_ENGINE_TIMEOUT_SECONDS = _MCP_CHANNEL_TIMEOUT_SECONDS + 15.0


def sanitize_mcp_tool_name(server_id: str, tool_name: str) -> str:
    """Build a unique FC name: ``mcp_{server}_{tool}`` (truncated to 64)."""
    sid = _NAME_SAFE.sub("_", (server_id or "srv").strip())[:16] or "srv"
    tname = _NAME_SAFE.sub("_", (tool_name or "tool").strip())[:40] or "tool"
    raw = f"mcp_{sid}_{tname}"
    return raw[:_TOOL_NAME_MAX]


def _parameters_schema(input_schema: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(input_schema, dict) and input_schema.get("type") == "object":
        return input_schema
    if isinstance(input_schema, dict) and "properties" in input_schema:
        out = dict(input_schema)
        out.setdefault("type", "object")
        return out
    return {"type": "object", "properties": {}}


class McpDynamicTool:
    """One MCP Server tool exposed to the CEO / worker ReAct loop."""

    def __init__(
        self,
        *,
        fc_name: str,
        server_id: str,
        server_name: str,
        mcp_tool_name: str,
        description: str,
        input_schema: dict[str, Any] | None,
    ) -> None:
        self._server_id = server_id
        self._server_name = server_name
        self._mcp_tool_name = mcp_tool_name
        desc = (description or "").strip() or f"MCP 工具 {mcp_tool_name}"
        prefix = f"[MCP · {server_name}] "
        self._schema = ToolSchema(
            name=fc_name,
            description=prefix + desc,
            parameters=_parameters_schema(input_schema),
            category=ToolCategory.SEARCH,
            approval=ToolApproval.GRANTABLE,
            timeout_seconds=_MCP_ENGINE_TIMEOUT_SECONDS,
        )

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    @property
    def mcp_server_id(self) -> str:
        """Stable Server key for on-demand family promote (not the FC name)."""
        return self._server_id

    @property
    def mcp_server_name(self) -> str:
        """Human Server label for the compact on-demand directory line."""
        return self._server_name

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        channel = context.desktop_channel
        if channel is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    f"{self._schema.name} 需要桌面 MCP 回填通道：当前无在线桌面客户端，"
                    "无法调用本机 MCP Server。请如实说明限制，勿假装已调用。"
                ),
            )
        logger.info(
            "desktop.mcp_op_request",
            run_id=context.run_id,
            op=McpOp.CALL_TOOL.value,
            server_id=self._server_id,
            tool=self._mcp_tool_name,
        )
        try:
            value = await channel.request_mcp(
                McpOp.CALL_TOOL,
                {
                    "server_id": self._server_id,
                    "tool_name": self._mcp_tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                },
                timeout=_MCP_CHANNEL_TIMEOUT_SECONDS,
            )
        except McpOpError as e:
            # Name the external dependency: without it a wedged / missing MCP Server is
            # indistinguishable (to the model) from its own misuse of the tool.
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    f"MCP 工具 {self._mcp_tool_name} 调用失败"
                    f"（MCP Server：{self._server_name}）：{e}"
                ),
            )
        is_error = bool(value.get("isError")) if isinstance(value, dict) else False
        content = value.get("content") if isinstance(value, dict) else value
        if isinstance(content, str):
            body = content
        else:
            payload = content if content is not None else value
            body = json.dumps(payload, ensure_ascii=False, indent=2)
        if is_error:
            return ToolResult(
                tool_call_id="",
                success=False,
                output=body,
                error=f"MCP 工具 {self._mcp_tool_name} 返回错误",
            )
        return ToolResult(tool_call_id="", success=True, output=body)
