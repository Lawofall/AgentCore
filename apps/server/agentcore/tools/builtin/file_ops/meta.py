"""Filesystem meta tools: delete / move / copy / mkdir."""

from __future__ import annotations

import time
from typing import Any

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.protocol import (
    AlreadyExists,
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
    _prepare_write_relpath,
    _reject_write_scope,
)


class FileDeleteTool:
    """Delete a file, or a directory and all its contents, within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        # 删除只会让台账里的 path 消失，不产生新产物。
        file_products=FileProductsContract.NO_PRODUCT,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_delete",
            description=(
                "删除工作区文件或目录（递归）。默认可逆；`permanent=true` 才永久删。"
                "工作区根不可删。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区相对路径。",
                    },
                    "permanent": {
                        "type": "boolean",
                        "description": (
                            "true=永久不可恢复；省略或 false=可逆（默认）。"
                        ),
                        "default": False,
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        rel_path = arguments.get("path", "")
        permanent = bool(arguments.get("permanent", False))

        if not rel_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径", start)

        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        rel_path, _shell_note = await rewrite_project_shell_relpath(
            rel_path, context, register=False
        )
        if not rel_path:
            return _error("path 不能为空：请提供工作区内的相对文件路径", start)

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="file_write.scope_rejected"
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
            return _path_missing_error(f"路径不存在：{rel_path}", start)
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
                "云端/sidecar→AgentCore/trash，可工作区一键还原）"
            )

        return ToolResult(
            tool_call_id="",
            success=True,
            output=msg,
            duration_ms=int((time.monotonic() - start) * 1000),
        )


class FileMoveTool:
    """Move or rename a file or directory within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_move",
            description=(
                "工作区内移动或重命名文件/目录。目标已存在则失败（不覆盖）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "要移动的已有文件 / 目录的相对路径",
                    },
                    "destination": {
                        "type": "string",
                        "description": "目标相对路径（必须尚不存在）",
                    },
                },
                "required": ["source", "destination"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        source = arguments.get("source", "")
        requested_dest = arguments.get("destination", "")

        if not source or not requested_dest:
            return _error("'source' 与 'destination' 均为必填", start)

        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        # Dest first: empty-desk first shot may register; source then shares that slug.
        destination, rename_note = await _prepare_write_relpath(requested_dest, context)
        source, _src_note = await rewrite_project_shell_relpath(
            source, context, register=False
        )

        if source == destination:
            # Idempotent: already at the (sanitized) target — e.g. dossier flatten.
            output = "source 与 destination 相同，无需移动"
            if rename_note:
                output = f"{output}。{rename_note}"
            return ToolResult(
                tool_call_id="",
                success=True,
                output=output,
                duration_ms=int((time.monotonic() - start) * 1000),
                file_products=[file_product(destination)],
            )

        for p in (source, destination):
            scope_denied = _reject_write_scope(
                context, p, start, event="file_write.scope_rejected"
            )
            if scope_denied is not None:
                return scope_denied

        # Ownership: source must be ours (or free); destination must not be held by another.
        denied_src, release_src = _claim_write_path(
            context, source, event="file_move.collision", start=start
        )
        if denied_src is not None:
            return denied_src
        denied_dst, release_dst = _claim_write_path(
            context, destination, event="file_move.collision", start=start
        )
        if denied_dst is not None:
            coordinator = context.write_coordinator
            if coordinator is not None and release_src:
                coordinator.release(source, context.run_id)
            return denied_dst
        coordinator = context.write_coordinator

        try:
            await context.backend.move(source, destination)
        except OutsideWorkspace as e:
            if coordinator is not None:
                if release_src:
                    coordinator.release(source, context.run_id)
                if release_dst:
                    coordinator.release(destination, context.run_id)
            return _outside_workspace_error(
                str(e), start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            if coordinator is not None:
                if release_src:
                    coordinator.release(source, context.run_id)
                if release_dst:
                    coordinator.release(destination, context.run_id)
            return _path_missing_error(f"源路径不存在：{source}", start)
        except AlreadyExists:
            if coordinator is not None:
                if release_src:
                    coordinator.release(source, context.run_id)
                if release_dst:
                    coordinator.release(destination, context.run_id)
            return _error(
                f"目标已存在：{destination}。请换一个不存在的路径，或先删除它。",
                start,
            )
        except WorkspaceError as e:
            if coordinator is not None:
                if release_src:
                    coordinator.release(source, context.run_id)
                if release_dst:
                    coordinator.release(destination, context.run_id)
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            return _error(f"移动失败：{e}", start, user_face=False)

        # Successful move: drop source ownership key; destination already claimed.
        if coordinator is not None:
            coordinator.release(source, context.run_id)

        output = f"已把 {source} 移动到 {destination}"
        if rename_note:
            output = f"{output}。{rename_note}"
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            # 搬家不是派生（源不是中间稿），只报落地路径，不填 derived_from。
            file_products=[file_product(destination)],
        )


class FileCopyTool:
    """Copy a file or directory tree (binary-safe); dst may be a granted external mount."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        file_products=FileProductsContract.SELF_REPORT,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_copy",
            description=(
                "复制文件或目录树（工作区内，或到已授权 `external/<别名>/…`）。"
                "目标已存在则失败（不覆盖）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "要复制的已有文件 / 目录的相对路径",
                    },
                    "destination": {
                        "type": "string",
                        "description": (
                            "目标相对路径（必须尚不存在；区外交付写 `external/<别名>/…`）"
                        ),
                    },
                },
                "required": ["source", "destination"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        source = arguments.get("source", "")
        requested_dest = arguments.get("destination", "")

        if not source or not requested_dest:
            return _error("'source' 与 'destination' 均为必填", start)

        from agentcore.workspace.project_shell import rewrite_project_shell_relpath

        # Dest first: empty-desk first shot may register; source then shares that slug.
        destination, rename_note = await _prepare_write_relpath(requested_dest, context)
        source, _src_note = await rewrite_project_shell_relpath(
            source, context, register=False
        )

        if source == destination:
            # Idempotent: already at the (sanitized) target — e.g. dossier flatten.
            output = "source 与 destination 相同，无需复制"
            if rename_note:
                output = f"{output}。{rename_note}"
            return ToolResult(
                tool_call_id="",
                success=True,
                output=output,
                duration_ms=int((time.monotonic() - start) * 1000),
                file_products=[file_product(destination)],
            )

        scope_denied = _reject_write_scope(
            context, destination, start, event="file_write.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        try:
            await context.backend.copy(source, destination)
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                str(e), start, location=context.backend.location, reason=str(e)
            )
        except PathNotFound:
            return _path_missing_error(f"源路径不存在：{source}", start)
        except AlreadyExists:
            return _error(
                f"目标已存在：{destination}。请换一个不存在的路径，或先删除它。",
                start,
            )
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            return _error(f"复制失败：{e}", start, user_face=False)

        output = f"已把 {source} 复制到 {destination}"
        if rename_note:
            output = f"{output}。{rename_note}"
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            file_products=[file_product(destination)],
        )


class MkdirTool:
    """Create an empty directory (with parents) within the workspace."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        # 只建目录：台账记的是文件产物，空目录不是交付物。
        file_products=FileProductsContract.NO_PRODUCT,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="mkdir",
            description=(
                "建工作区根下的结构目录（`src/`、`public/`、`AgentCore/文档/`；"
                "缺上级一并建）。路径相对根；根已是当前工程（对照 `<工作区>`）。"
                "写文件含上级，不必先 mkdir。已存在则失败。"
                "套应用名/话题名当工程根 ≠ 本工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要创建的相对目录路径",
                    },
                },
                "required": ["path"],
            },
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.GRANTABLE,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        rel_path = arguments.get("path", "")

        if not rel_path:
            return _error("path 不能为空：请提供工作区内的相对目录路径", start)

        rel_path, rename_note = await _prepare_write_relpath(
            rel_path, context, register_bare=True
        )
        if not rel_path or rel_path == ".":
            output = rename_note or "目录即工作区根，后续文件直接写在根下即可"
            return ToolResult(
                tool_call_id="",
                success=True,
                output=output,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        scope_denied = _reject_write_scope(
            context, rel_path, start, event="file_write.scope_rejected"
        )
        if scope_denied is not None:
            return scope_denied

        try:
            await context.backend.mkdir(rel_path)
        except OutsideWorkspace as e:
            return _outside_workspace_error(
                rel_path, start, location=context.backend.location, reason=str(e)
            )
        except AlreadyExists:
            return _error(f"路径已存在：{rel_path}", start)
        except WorkspaceError as e:
            dead = _maybe_channel_dead_error(e, start)
            if dead is not None:
                return dead
            return _error(f"创建目录失败：{e}", start, user_face=False)

        output = f"已创建目录 {rel_path}"
        if rename_note:
            output = f"{output}。{rename_note}"
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
