"""Desktop Client Tools SSE event factories."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.events.types import EventType, SSEEvent


def external_mount_required(
    *,
    request_id: str,
    conversation_id: str,
    path: str | None = None,
    well_known: str | None = None,
    target_name: str | None = None,
    mode: str | None = None,
    root_id: str | None = None,
) -> SSEEvent:
    """Ask the bound desktop to mount a local directory.

    Path transport exception: may carry ``path`` and/or ``well_known``+
    ``target_name`` for desktop resolve. Success settle must not include abs.
    ``mode`` is omitted on silent readonly; ``organize`` / ``attach_rw`` confirm.
    ``root_id`` upgrades an existing session root (no folder picker).
    """
    payload: dict[str, Any] = {
        "request_id": request_id,
        "conversation_id": conversation_id,
    }
    if path:
        payload["path"] = path
    if well_known:
        payload["well_known"] = well_known
    if target_name:
        payload["target_name"] = target_name
    if mode and mode != "readonly":
        payload["mode"] = mode
    if root_id:
        payload["root_id"] = root_id
    return SSEEvent(
        type=EventType.EXTERNAL_MOUNT_REQUIRED,
        payload=payload,
    )


def host_op_required(
    *,
    request_id: str,
    conversation_id: str,
    op: str,
    args: dict[str, Any] | None = None,
) -> SSEEvent:
    """Ask the bound desktop to run a Host op and report back (transport-only)."""
    return SSEEvent(
        type=EventType.HOST_OP_REQUIRED,
        payload={
            "request_id": request_id,
            "conversation_id": conversation_id,
            "op": op,
            "args": args or {},
        },
    )


def mcp_op_required(
    *,
    request_id: str,
    conversation_id: str,
    op: str,
    args: dict[str, Any] | None = None,
) -> SSEEvent:
    """Ask the bound desktop to run an MCP Client op (list/call) and report back."""
    return SSEEvent(
        type=EventType.MCP_OP_REQUIRED,
        payload={
            "request_id": request_id,
            "conversation_id": conversation_id,
            "op": op,
            "args": args or {},
        },
    )
