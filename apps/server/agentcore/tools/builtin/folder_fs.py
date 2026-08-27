"""CEO-only read-only cross-folder tools: list / read inside a registered Folder.

Per-call ``folder_id`` → ``load_target_folder_binding`` + ``build_target_backend``
(WorkspaceChannel for local). Does **not** rewrite session ``folder_id``, and does
**not** call ``apply_target_desktop`` (no target-desk memory rewrite).

The reach is the folder's own root **and everything nested under it** — same as
opening a folder in an editor (双模式工作区 §5.4). Write / heavy work stays on
``delegate`` + ``target_folder_id``. Generic ``file_*`` stay birth-desk only (no
``folder_id`` param).
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.runtime.delegate.target_desktop import (
    TargetDesktopError,
    build_target_backend,
    load_target_folder_binding,
)
from agentcore.runtime.events import EventSink
from agentcore.tools.builtin.file_ops.listing import folder_dir_leftover_error
from agentcore.tools.builtin.file_ops.read import FileListTool, FileReadTool
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_CEO_ONLY,
    CeoWire,
    ToolRegistration,
    ToolSurface,
)
from agentcore.workspace.locate import workspace_channel_for_tools

logger = get_logger(__name__)

LIST_FOLDER_DIR_TOOL_NAME = "list_folder_dir"
READ_FOLDER_FILE_TOOL_NAME = "read_folder_file"
# CEO recon window. FileReadTool omit-limit follows the worker full-read default;
# inject this before delegating so cross-desk sampling cannot inherit that cap.
_READ_FOLDER_SAMPLE_LINES = 500

_MISSING_FOLDER_ID = "缺少 folder_id（目标文件夹 id；见文件夹清单行内 id / resolve_folder）。"
_DENIED_MSG = (
    "目标文件夹 `{folder_id}` 不存在或无权访问；请重新列/解析文件夹后再读。"
)


async def _open_target_folder(
    *,
    folder_id: str,
    context: ToolContext,
) -> tuple[ToolContext | None, ToolResult | None]:
    """Bind a per-call target backend; never mutates session desk / memory.

    Returns ``(target_ctx, None)`` on success, or ``(None, error_result)`` on failure.
    """
    cleaned = folder_id.strip()
    if not cleaned:
        return None, ToolResult(
            tool_call_id="",
            success=False,
            output=_MISSING_FOLDER_ID,
            error="missing folder_id",
        )

    try:
        binding = await load_target_folder_binding(
            folder_id=cleaned,
            user_id=context.user_id,
        )
    except TargetDesktopError as e:
        logger.warning(
            "folder_fs.bind_failed",
            folder_id=cleaned,
            user_id=context.user_id,
            error=e.message,
        )
        return None, ToolResult(
            tool_call_id="",
            success=False,
            output=e.message,
            error="target_desktop_error",
        )

    if binding is None:
        msg = _DENIED_MSG.format(folder_id=cleaned)
        return None, ToolResult(
            tool_call_id="",
            success=False,
            output=msg,
            error="folder_denied",
        )

    # build_workspace still accepts a display sink; CLIENT_TOOL no longer uses it.
    sink = EventSink()

    backend = build_target_backend(
        user_id=context.user_id,
        folder_id=binding.folder_id,
        folder_rel_path=binding.rel_path,
        conversation_id=context.conversation_id,
        sink=sink,
        local_binding=binding.local_binding,
    )
    workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=context.user_id,
        conversation_id=context.conversation_id,
    )
    # Ephemeral desk: do not share birth-desk file_read ceilings / materials;
    # do not rewrite context.backend (session mount stays put). Fresh slot so
    # this read does not follow (or cause) a parent rebind.
    from agentcore.tools.protocol import fork_workspace_slot, isolate_file_read_ceiling

    target_ctx = isolate_file_read_ceiling(
        replace(
            context,
            _workspace=fork_workspace_slot(backend, material_paths=frozenset()),
            workspace_channel=workspace_channel,
            shared_workspace=True,
        )
    )
    logger.info(
        "folder_fs.target_opened",
        folder_id=binding.folder_id,
        folder_name=binding.name,
        location=getattr(backend, "location", None),
        local=bool(binding.local_binding),
        run_id=context.run_id,
        conversation_untouched=True,
    )
    return target_ctx, None


def _readonly_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop ``folder_id`` before delegating to birth-desk file_* implementations."""
    return {k: v for k, v in arguments.items() if k != "folder_id"}


def _readonly_read_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop ``folder_id`` and pin a sampling ``limit`` before FileReadTool.

    Schema maximum is 500; omitting ``limit`` must still inject 500 — otherwise
    FileReadTool treats omit as worker full-read (2000-line cap).
    """
    args = _readonly_args(arguments)
    raw = args.get("limit")
    if raw is None:
        args["limit"] = _READ_FOLDER_SAMPLE_LINES
        return args
    args["limit"] = min(int(raw), _READ_FOLDER_SAMPLE_LINES)
    return args


class ListFolderDirTool:
    """CEO-only: list a directory inside a registered Folder (read-only)."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=LIST_FOLDER_DIR_TOOL_NAME,
            description=(
                "只读跨文件夹：列出【另一张已有桌】某目录当前层（须 folder_id）。"
                "当前出生桌用 file_list（无 folder_id）。范围=该文件夹及其子文件夹。"
                "按次指定，不改本会话出生桌，不写目标桌记忆。"
                "派单前轻量认桌/抽样；成规模跨桌摸底请 delegate 填 target_folder_id"
                "（队员拿不到本工具）。"
                "folder_id 来自文件夹清单行内 id / resolve_folder / create_folder。"
                "HOW→consult(team_cross_folder)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": (
                            "目标文件夹 id（文件夹清单行内 id / resolve_folder / "
                            "create_folder）。当前出生桌勿用本工具。"
                        ),
                    },
                    "directory": {
                        "type": "string",
                        "description": (
                            "相对该文件夹工作区根的 POSIX 目录（默认 `.`；"
                            "`/<根标签>/…` 与裸 `/`、`\\` 视为根；其它绝对路径拒绝）"
                        ),
                        "default": ".",
                    },
                },
                "required": ["folder_id"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        leftover = folder_dir_leftover_error(arguments, start)
        if leftover is not None:
            return leftover
        folder_id = str(arguments.get("folder_id") or "")
        target_ctx, err = await _open_target_folder(folder_id=folder_id, context=context)
        if err is not None:
            return err
        assert target_ctx is not None
        return await FileListTool().execute(_readonly_args(arguments), target_ctx)


class ReadFolderFileTool:
    """CEO-only: read a file inside a registered Folder (read-only)."""

    registration = ToolRegistration(
        surface=ToolSurface.CEO_ORCHESTRATION,
        audience=AUDIENCE_CEO_ONLY,
        ceo_wire=CeoWire.ALWAYS,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=READ_FOLDER_FILE_TOOL_NAME,
            description=(
                "只读跨文件夹：读取【另一张已有桌】内某文件（folder_id + path）。"
                "当前出生桌用 file_read。范围=该文件夹及其子文件夹。"
                "按次指定，不改本会话出生桌，不写目标桌记忆。"
                "派单前轻量认桌/抽样；成规模跨桌摸底请 delegate 填 target_folder_id"
                "（队员拿不到本工具）。"
                "folder_id 来自文件夹清单行内 id / resolve_folder / create_folder。"
                "支持 offset/limit 行窗；Office/PDF 抽文本同 file_read。"
                "HOW→consult(team_cross_folder)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder_id": {
                        "type": "string",
                        "description": (
                            "目标文件夹 id（文件夹清单行内 id / resolve_folder / "
                            "create_folder）。当前出生桌勿用本工具。"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "相对该文件夹工作区根的 POSIX 文件路径（`.`=根；"
                            "`/<根标签>/…` 与裸 `/`、`\\` 视为根；其它绝对路径拒绝）"
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-based，含）。省略则从第 1 行开始。",
                        "minimum": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            f"最多读取行数。省略则默认抽样 {_READ_FOLDER_SAMPLE_LINES} 行"
                            "（派单前轻量认桌，非整读）。"
                        ),
                        "minimum": 1,
                        "maximum": _READ_FOLDER_SAMPLE_LINES,
                    },
                },
                "required": ["folder_id", "path"],
            },
            category=ToolCategory.ORCHESTRATION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        folder_id = str(arguments.get("folder_id") or "")
        target_ctx, err = await _open_target_folder(folder_id=folder_id, context=context)
        if err is not None:
            return err
        assert target_ctx is not None
        return await FileReadTool().execute(_readonly_read_args(arguments), target_ctx)
