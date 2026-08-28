"""external_mount_readonly — silently mount a local directory read-only (C1).

CEO + worker; approval=never; assembled only when desktop_online. Suspends on the
desktop ClientTool channel; desktop mints a session root (no picker) and POSTs
the grant. This tool then hot-attaches mounts onto the live turn backend so
``file_read external/…`` works in the same turn.
"""

from __future__ import annotations

import json
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.desktop.channel import ExternalMountError
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace import grant_store
from agentcore.workspace.external_mounts import external_ns
from agentcore.workspace.hot_attach import attach_grants_to_backend

logger = get_logger(__name__)

EXTERNAL_MOUNT_READONLY_TOOL_NAME = "external_mount_readonly"

_WELL_KNOWN = frozenset({"desktop", "downloads", "documents"})

# Resolve-path categories from desktop grant (≠ timeout / channel dead).
_RESOLVE_REASONS = frozenset({"not_found", "not_directory", "ambiguous", "invalid"})


# Authored copy keyed on desktop ``reason`` — do not scan the user's original
# request (or the path) to guess "installer vs folder".
_MOUNT_NOT_DIRECTORY = (
    "这是文件不是文件夹；请选它所在的目录，或把内容放进当前工作区。"
)
_MOUNT_NOT_FOUND = (
    "找不到该目录，无法挂载。"
    "挂载只接受文件夹；若给的是安装包/文件，请改选它所在目录或放进工作区。"
)


def format_external_mount_error(exc: ExternalMountError) -> str:
    """Model-facing error: stable reason copy + reason tag when present."""
    reason = (exc.reason or "").strip() or None
    if reason == "not_directory":
        detail = _MOUNT_NOT_DIRECTORY
    elif reason == "not_found":
        detail = _MOUNT_NOT_FOUND
    else:
        detail = str(exc).strip() or "找不到该目录，无法挂载"
    if not reason:
        return detail
    parts = [f"{detail}（reason={reason}）"]
    if reason in _RESOLVE_REASONS:
        parts.append("勿用相同参数盲重试。")
    return " ".join(parts)


class ExternalMountReadonlyTool:
    """Silently mount a user-local directory under ``external/<alias>/`` (readonly)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        desktop_online_class=True,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=EXTERNAL_MOUNT_READONLY_TOOL_NAME,
            description=(
                "静默挂载用户本机某个目录为只读（仅桌面在线）。"
                "用户点到本机目录要看/分析时调用；成功后立刻 `external/<别名>/…`。"
                "可选 path 与/或 well_known+target_name；失败带 reason，勿同参盲重试。"
                "整理/写回勿用本工具。"
                "HOW→consult(external_mount_readonly)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "本机绝对目录路径提示（可选；与 well_known 二选一或并用，"
                            "path 优先）。成功结果不含绝对路径。"
                        ),
                    },
                    "well_known": {
                        "type": "string",
                        "enum": sorted(_WELL_KNOWN),
                        "description": (
                            "常见目录根：desktop / downloads / documents（可选）。"
                        ),
                    },
                    "target_name": {
                        "type": "string",
                        "description": (
                            "well_known 下的子目录/压缩包名（可选；已知时写入）。"
                        ),
                    },
                },
            },
            category=ToolCategory.INTERACTION,
            approval=ToolApproval.NEVER,
            timeout_seconds=60.0,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        channel = context.desktop_channel
        if channel is None:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "external_mount_readonly 需要桌面回填通道：当前无在线桌面客户端，"
                    "无法挂载本机目录。请如实说明须用桌面客户端，勿假装已挂载。"
                ),
            )

        path = str(arguments.get("path") or "").strip() or None
        well_known_raw = str(arguments.get("well_known") or "").strip().lower()
        well_known = well_known_raw if well_known_raw in _WELL_KNOWN else None
        target_name = str(arguments.get("target_name") or "").strip() or None

        if not path and not well_known:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=(
                    "external_mount_readonly 需要 path 和/或 well_known"
                    "（desktop|downloads|documents）；找不到目录时明确失败。"
                ),
            )

        logger.info(
            "desktop.external_mount_request",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            has_path=bool(path),
            well_known=well_known,
            has_target_name=bool(target_name),
        )
        try:
            value = await channel.request_external_mount_readonly(
                path=path,
                well_known=well_known,
                target_name=target_name,
            )
        except ExternalMountError as e:
            reason = (e.reason or "").strip() or None
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=format_external_mount_error(e),
                metadata={"code": reason} if reason else {},
            )

        root_id = str(value.get("root_id") or "").strip()
        label = str(value.get("label") or "").strip()
        alias_hint = str(value.get("alias") or "").strip() or None
        display_label = str(value.get("display_label") or "").strip() or None
        namespace_from_desktop = str(value.get("namespace") or "").strip() or None

        if not root_id:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="桌面挂载回填缺少 root_id，无法登记授权",
            )

        # Desktop may have POSTed the grant already; same root_id refresh is idempotent.
        mount = await grant_store.add_grant(
            context.conversation_id,
            root_id=root_id,
            label=label or alias_hint or "external",
            alias_hint=alias_hint or label or None,
            mode="readonly",
        )
        await attach_grants_to_backend(
            context.backend,
            context.conversation_id,
            desktop_channel=channel,
            workspace_channel=context.workspace_channel,
        )

        namespace = namespace_from_desktop or external_ns(mount.alias)
        payload = {
            "namespace": namespace,
            "alias": mount.alias,
            "label": mount.label,
            "mode": "readonly",
        }
        if display_label:
            payload["display_label"] = display_label
        return ToolResult(
            tool_call_id="",
            success=True,
            output=json.dumps(payload, ensure_ascii=False),
        )
