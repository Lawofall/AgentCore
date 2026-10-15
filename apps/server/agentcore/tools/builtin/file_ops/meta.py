"""Filesystem meta tools: delete."""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.tools.write_replay import is_write_replay
from agentcore.workspace.protocol import (
    OutsideWorkspace,
    PathNotFound,
    WorkspaceError,
)

from .errors import (
    _error,
    _maybe_channel_dead_error,
    _outside_workspace_error,
    _path_missing_error,
)
from .integrity import (
    _claim_write_path,
    _reject_write_scope,
)
from .prepare_path import prepare_tool_path


class FileDeleteTool:
    """Delete a file, or a directory and all its contents, within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        # 删除只会让台账里的 path 消失，不产生新产物。
        file_products=FileProductsContract.NO_PRODUCT,
        workspace_io=True,
        catalog_summary="删工作区文件或目录",
        blurb="从工作区拿走指定路径",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_delete",
            description="删除工作区文件或目录。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区相对路径。",
                    },
                },
                "required": ["path"],
            },
            face=ToolFace.FILE,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        rel_path = arguments.get("path", "")
        # Leftover ``permanent`` still hard-deletes and still trips always-confirm.
        permanent = bool(arguments.get("permanent", False))

        if not rel_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径", start)

        prepared = await prepare_tool_path(
            rel_path, context, start=start, grant_mode="organize"
        )
        if isinstance(prepared, ToolResult):
            return prepared
        rel_path = prepared

        if not rel_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径", start)

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="write.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        denied, release_on_fail = _claim_write_path(
            context, rel_path, event="file_delete.collision", start=start
        )
        if denied is not None:
            return denied
        coordinator = context.write_coordinator

        try:
            await context.backend.delete(rel_path, permanent=permanent)
        except OutsideWorkspace as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            if is_write_replay():
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output=f"已删除 {rel_path}（重驱时路径已不在，未再改写）",
                    duration_ms=int((time.monotonic() - start) * 1000),
                    metadata={"already_applied": True},
                )
            return _path_missing_error(
                f"路径不存在：{rel_path}", start, path=rel_path
            )
        except WorkspaceError as e:
            if coordinator is not None and release_on_fail:
                coordinator.release(rel_path, context.run_id)
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            return _error(f"删除失败：{e}", start, user_face=False)

        if permanent:
            msg = f"已永久删除 {rel_path}"
        else:
            msg = (
                f"已可逆删除 {rel_path}"
                "（本地通道→系统回收站，请在本机手动恢复；"
                "云端/sidecar→GAgent/trash，可工作区一键还原）"
            )

        return ToolResult(
            tool_call_id="",
            success=True,
            output=msg,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
