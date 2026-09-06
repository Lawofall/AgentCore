"""DesktopClientChannel — route desktop Client Tools to the bound Electron app.

Counterpart of :class:`agentcore.board.channel.BoardChannel` for OS-level desktop
affordances that only exist in the Electron shell (Host ops, MCP stdio, and
external directory mounts).

Wired whenever the desktop client is online (local workspace **or** cloud +
``desktop_online``) — never by pinging ``127.0.0.1`` from the cloud API process.
Delivery goes through the device-level fulfill hub (not the turn display sink).

Host ops, MCP stdio servers and external mounts all act on one specific
machine, so they are pinned to the device that started the turn
(``fulfill.hub.ORIGIN_PINNED_CHANNELS``) and fail honestly when it goes away.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.fulfill.origin import ORIGIN_DEVICE_OFFLINE
from agentcore.runtime.events.client_tool_reattach import (
    CHANNEL_EXTERNAL_MOUNT,
    CHANNEL_HOST,
    CHANNEL_MCP,
    client_tool_payload,
    push_client_tool_required,
)
from agentcore.runtime.events.desktop import (
    external_mount_required,
    host_op_required,
    mcp_op_required,
)
from agentcore.runtime.events.types import EventType
from agentcore.runtime.interaction import InteractionKind
from agentcore.runtime.ports import ClientRequestBridge

logger = get_logger(__name__)


class ExternalMountError(Exception):
    """An external mount request failed (not found, desktop error, drop, or timeout).

    ``reason`` is an optional stable category from the desktop resolve path
    (``not_found`` / ``not_directory`` / ``ambiguous`` / …) so tools can surface
    both a human detail and a machine-stable code to the model.
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


class HostOpError(Exception):
    """A Host op failed (desktop error, drop, timeout, or unsupported op)."""


class McpOpError(Exception):
    """An MCP Client op failed (desktop error, drop, timeout, or server crash)."""


class HostOp(StrEnum):
    """Closed Host op set exchanged over the desktop backfill channel (P0–P3)."""

    PING = "host_ping"
    INFO = "host_info"
    AUDIO_DEVICES = "host_audio_devices"
    STORAGE = "host_storage"
    POWER = "host_power"
    NETWORK_SUMMARY = "host_network_summary"
    APPS = "host_apps"
    # L1 bounded OS event-log summary (CEO+worker · NEVER · 禁整机倾倒)
    OS_LOG_SUMMARY = "host_os_log_summary"
    # P3 general host shell (CEO+worker · GRANTABLE · host_class · 禁 kickoff)
    SHELL = "host_shell"
    OPEN_SETTINGS = "host_open_settings"
    # L3 controlled whitelist (worker · GRANTABLE · host_class only)
    AUDIO_SET_DEFAULT = "host_audio_set_default"
    SERVICE_RESTART = "host_service_restart"
    PACKAGE_INSTALL = "host_package_install"


class McpOp(StrEnum):
    """Closed MCP Client op set over the desktop backfill channel (stdio only)."""

    LIST_TOOLS = "list_tools"
    CALL_TOOL = "call_tool"


@dataclass
class DesktopClientChannel:
    """Suspends until the bound desktop fulfils a Client Tool request."""

    user_id: str
    conversation_id: str
    registry: ClientRequestBridge
    timeout_seconds: float

    async def request_external_mount(
        self,
        *,
        path: str | None = None,
        well_known: str | None = None,
        target_name: str | None = None,
        mode: str | None = None,
        root_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Emit a mount request; return desktop ``value`` (no abs).

        ``mode`` / ``root_id`` are transport-only (readonly silent; write
        confirm upgrades the same session root). Event name is
        ``external_mount_required``.
        """
        request_id = new_id()
        params: dict[str, Any] = {}
        if path:
            params["path"] = path
        if well_known:
            params["well_known"] = well_known
        if target_name:
            params["target_name"] = target_name
        if mode and mode != "readonly":
            params["mode"] = mode
        if root_id:
            params["root_id"] = root_id
        deadline = self.timeout_seconds if timeout is None else timeout
        try:
            result = await self.registry.suspend(
                request_id,
                self.conversation_id,
                kind=InteractionKind.CLIENT_TOOL,
                payload=client_tool_payload(
                    CHANNEL_EXTERNAL_MOUNT,
                    EventType.EXTERNAL_MOUNT_REQUIRED.value,
                    params=params,
                    user_id=self.user_id,
                ),
                timeout=deadline,
                on_suspended=lambda: push_client_tool_required(
                    user_id=self.user_id,
                    conversation_id=self.conversation_id,
                    channel=CHANNEL_EXTERNAL_MOUNT,
                    root_id=None,
                    event=external_mount_required(
                        request_id=request_id,
                        conversation_id=self.conversation_id,
                        path=path,
                        well_known=well_known,
                        target_name=target_name,
                        mode=mode if mode and mode != "readonly" else None,
                        root_id=root_id,
                    ),
                    registry=self.registry,
                    request_id=request_id,
                    error_kind="ExternalMountError",
                    error_detail="挂载本机目录失败: no fulfiller（无履约方）",
                    origin_offline_detail=f"挂载本机目录失败: {ORIGIN_DEVICE_OFFLINE}",
                    deadline_seconds=deadline,
                ),
            )
        except TimeoutError as e:
            logger.info(
                "desktop.external_mount_timeout",
                conversation_id=self.conversation_id,
                request_id=request_id,
            )
            raise ExternalMountError("挂载本机目录超时（客户端未响应）") from e

        if not isinstance(result, dict) or not result.get("ok"):
            detail = ""
            reason: str | None = None
            if isinstance(result, dict):
                err = result.get("error")
                if isinstance(err, dict):
                    detail = str(err.get("detail", "") or "")
                    raw_reason = err.get("reason")
                    if isinstance(raw_reason, str) and raw_reason.strip():
                        reason = raw_reason.strip()
                elif err:
                    detail = str(err)
            raise ExternalMountError(
                detail or "找不到该目录，无法挂载",
                reason=reason,
            )
        value = result.get("value")
        return value if isinstance(value, dict) else {}

    async def request_host(
        self,
        op: HostOp | str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Emit a Host op, await the desktop, return its ``value`` dict."""
        op_name = str(op)
        request_id = new_id()
        payload_args = dict(args or {})
        deadline = self.timeout_seconds if timeout is None else timeout
        try:
            result = await self.registry.suspend(
                request_id,
                self.conversation_id,
                kind=InteractionKind.CLIENT_TOOL,
                payload=client_tool_payload(
                    CHANNEL_HOST,
                    EventType.HOST_OP_REQUIRED.value,
                    params={"op": op_name, "args": payload_args},
                    user_id=self.user_id,
                ),
                timeout=deadline,
                on_suspended=lambda: push_client_tool_required(
                    user_id=self.user_id,
                    conversation_id=self.conversation_id,
                    channel=CHANNEL_HOST,
                    root_id=None,
                    event=host_op_required(
                        request_id=request_id,
                        conversation_id=self.conversation_id,
                        op=op_name,
                        args=payload_args,
                    ),
                    registry=self.registry,
                    request_id=request_id,
                    error_kind="HostOpError",
                    error_detail=(
                        f"本机 Host 操作失败（{op_name}）: no fulfiller（无履约方）"
                    ),
                    origin_offline_detail=(
                        f"本机 Host 操作失败（{op_name}）: {ORIGIN_DEVICE_OFFLINE}"
                    ),
                    deadline_seconds=deadline,
                ),
            )
        except TimeoutError as e:
            logger.info(
                "desktop.host_op_timeout",
                conversation_id=self.conversation_id,
                request_id=request_id,
                op=op_name,
            )
            raise HostOpError(f"本机 Host 操作超时（{op_name}：客户端未响应）") from e

        if not isinstance(result, dict) or not result.get("ok"):
            detail = ""
            if isinstance(result, dict):
                err = result.get("error")
                if isinstance(err, dict):
                    detail = str(err.get("detail", "") or "")
                elif err:
                    detail = str(err)
            raise HostOpError(detail or f"本机 Host 操作失败（{op_name}）")
        value = result.get("value")
        return value if isinstance(value, dict) else {}

    async def request_mcp(
        self,
        op: McpOp | str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Emit an MCP Client op, await the desktop, return its ``value`` dict."""
        op_name = str(op)
        request_id = new_id()
        payload_args = dict(args or {})
        deadline = self.timeout_seconds if timeout is None else timeout
        try:
            result = await self.registry.suspend(
                request_id,
                self.conversation_id,
                kind=InteractionKind.CLIENT_TOOL,
                payload=client_tool_payload(
                    CHANNEL_MCP,
                    EventType.MCP_OP_REQUIRED.value,
                    params={"op": op_name, "args": payload_args},
                    user_id=self.user_id,
                ),
                timeout=deadline,
                on_suspended=lambda: push_client_tool_required(
                    user_id=self.user_id,
                    conversation_id=self.conversation_id,
                    channel=CHANNEL_MCP,
                    root_id=None,
                    event=mcp_op_required(
                        request_id=request_id,
                        conversation_id=self.conversation_id,
                        op=op_name,
                        args=payload_args,
                    ),
                    registry=self.registry,
                    request_id=request_id,
                    error_kind="McpOpError",
                    error_detail=(
                        f"本机 MCP 操作失败（{op_name}）: no fulfiller（无履约方）"
                    ),
                    origin_offline_detail=(
                        f"本机 MCP 操作失败（{op_name}）: {ORIGIN_DEVICE_OFFLINE}"
                    ),
                    deadline_seconds=deadline,
                ),
            )
        except TimeoutError as e:
            logger.info(
                "desktop.mcp_op_timeout",
                conversation_id=self.conversation_id,
                request_id=request_id,
                op=op_name,
            )
            raise McpOpError(f"本机 MCP 操作超时（{op_name}：客户端未响应）") from e

        if not isinstance(result, dict) or not result.get("ok"):
            detail = ""
            if isinstance(result, dict):
                err = result.get("error")
                if isinstance(err, dict):
                    detail = str(err.get("detail", "") or "")
                elif err:
                    detail = str(err)
            raise McpOpError(detail or f"本机 MCP 操作失败（{op_name}）")
        value = result.get("value")
        return value if isinstance(value, dict) else {}
