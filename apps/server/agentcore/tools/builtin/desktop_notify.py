"""desktop_notify — OS notification on the user's desktop (CEO + worker).

Available only when the conversation runs against a local workspace binding (the
desktop Electron app is the client). The tool suspends on the unified client_tool
bridge; the renderer shows the notification via the main-process ``Notification`` API.
GRANTABLE by default; ``command=auto`` silently auto-passes (see sandbox_approval).
Execute fails honestly when no desktop channel is bound.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.desktop.channel import DesktopNotifyError
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

DESKTOP_NOTIFY_TOOL_NAME = "desktop_notify"
_TITLE_MAX = 120
_BODY_MAX = 500


class DesktopNotifyTool:
    """Show a native OS notification via the bound desktop client."""

    registration = ToolRegistration(
        surface=ToolSurface.WORKER_ONLY,
        audience=AUDIENCE_BOTH,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=DESKTOP_NOTIFY_TOOL_NAME,
            description=(
                "在用户的桌面系统通知栏弹出一条原生通知（仅本地绑定工作区 / 桌面客户端可用）。"
                "用于任务完成提醒、需要用户回到电脑前查看、或里程碑提示。"
                "谨慎档需用户审批；少打断/托管（command=auto）下静默放行。不要滥发。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "通知标题（简短，≤120 字）。",
                    },
                    "body": {
                        "type": "string",
                        "description": "通知正文（可选，≤500 字）。",
                    },
                },
                "required": ["title"],
            },
            category=ToolCategory.INTERACTION,
            approval=ToolApproval.GRANTABLE,
            timeout_seconds=30.0,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        channel = context.desktop_channel
        if channel is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "desktop_notify 仅在桌面本地工作区可用：当前无绑定的桌面客户端，"
                    "无法弹出系统通知。请改用对话内说明或 ask_user。"
                ),
            )

        title = str(arguments.get("title") or "").strip()
        if not title:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="desktop_notify 需要非空 title。",
            )
        body = str(arguments.get("body") or "").strip()
        if len(title) > _TITLE_MAX:
            title = title[:_TITLE_MAX]
        if len(body) > _BODY_MAX:
            body = body[:_BODY_MAX]

        logger.info(
            "desktop.notify_request",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            title_len=len(title),
        )
        try:
            await channel.notify(title=title, body=body)
        except DesktopNotifyError as e:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=str(e),
            )

        summary = title if not body else f"{title} — {body}"
        return ToolResult(
            tool_call_id="",
            success=True,
            output=f"已向用户桌面发送系统通知：{summary}",
        )
