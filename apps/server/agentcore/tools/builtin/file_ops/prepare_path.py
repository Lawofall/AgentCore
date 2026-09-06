"""Rewrite host OS paths on file tools to ``external/<alias>/…`` before sanitize."""

from __future__ import annotations

import time
from typing import Any

from agentcore.desktop.channel import ExternalMountError
from agentcore.runtime.facts import CrossTurnRetry
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace.ensure_host_path import (
    HostPathDeniedError,
    ensure_external_upgrade,
    ensure_host_path,
    format_external_mount_error,
    format_host_path_denied,
)
from agentcore.workspace.host_path import GrantMode, classify_tool_path

from .errors import _error


async def prepare_tool_path(
    raw: Any,
    context: ToolContext,
    *,
    start: float | None = None,
    as_directory: bool = False,
    grant_mode: GrantMode = "readonly",
) -> str | ToolResult:
    """Return a workspace-relative path, or a failed ToolResult.

    Workspace-relative paths pass through. Already-``external/`` read paths
    pass through; write/organize upgrades the same mount by ``root_id``.
    Host paths mint a session grant (readonly silently; organize/attach_rw
    confirm on the desktop) and rewrite.
    """
    t0 = time.monotonic() if start is None else start
    path = str(raw or "").strip()
    root_label = getattr(context.backend, "root_label", None)
    classified = classify_tool_path(path, root_label=root_label)
    if classified.kind == "workspace":
        return path or "."
    try:
        if classified.kind == "external_ns":
            if grant_mode == "readonly":
                return path or "."
            return await ensure_external_upgrade(
                path, context, grant_mode=grant_mode
            )
        return await ensure_host_path(
            classified,
            context,
            as_directory=as_directory,
            grant_mode=grant_mode,
        )
    except HostPathDeniedError as e:
        reason = (e.reason or "").strip() or None
        return _error(
            format_host_path_denied(e),
            t0,
            contract_failure=True,
            failure_code=reason,
            product_face=format_host_path_denied(e),
            cross_turn_retry=CrossTurnRetry.FUTILE,
        )
    except ExternalMountError as e:
        reason = (e.reason or "").strip() or None
        return _error(
            format_external_mount_error(e),
            t0,
            contract_failure=True,
            failure_code=reason,
            product_face=format_external_mount_error(e),
            metadata={"code": reason} if reason else {},
            cross_turn_retry=CrossTurnRetry.FUTILE,
        )
