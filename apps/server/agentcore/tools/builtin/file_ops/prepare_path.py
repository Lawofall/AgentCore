"""Rewrite host OS paths on file tools before sanitize.

Working-root grants become relatives; extra grants keep the internal
``external/<alias>/`` router so backends can address them.
"""

from __future__ import annotations

import time
from typing import Any

from agentcore.desktop.channel import ExternalMountError
from agentcore.tools.cross_turn_retry import CrossTurnRetry
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.workspace.ensure_host_path import (
    HostPathDeniedError,
    ensure_external_upgrade,
    ensure_host_path,
    format_external_mount_error,
    format_host_path_denied,
)
from agentcore.workspace.host_path import (
    GrantMode,
    classify_tool_path,
    workspace_rel_under_disk_root,
)

from .errors import _error

# Model-facing path contract: rewrite at the tool entry, not a second dialect.
TOOL_PATH_DESCRIPTION = (
    "干活根相对 POSIX（`.`=根），或本机绝对路径 / ~/Desktop|Downloads|Documents。"
    "授权不是改名：不要把盘符路径和别名拼在一起。"
)


def _sidecar_rel_under_workspace_root(path: str, backend: Any) -> str | None:
    """Rewrite abs-on-this-disk workspace paths. Sidecar only — not ``location=local``."""
    from agentcore.sidecar.server_pkg.core import is_sidecar_process

    if not is_sidecar_process():
        return None
    if getattr(backend, "location", None) != "local":
        return None
    root = getattr(backend, "root", None)
    if root is None:
        return None
    return workspace_rel_under_disk_root(path, root)


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
    Host paths mint a session grant (well-known Desktop/Downloads/Documents
    and already-granted roots silently; other first-time readonly abs paths
    confirm on the desktop; organize/attach_rw always confirm) and rewrite.
    Sidecar (not cloud ``location=local``):
    an OS-absolute path on/under ``backend.root`` becomes workspace-relative
    and is not minted as external.
    """
    t0 = time.monotonic() if start is None else start
    path = str(raw or "").strip()
    under_root = _sidecar_rel_under_workspace_root(path, context.backend)
    if under_root is not None:
        return under_root
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
