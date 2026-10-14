"""Open a worker desk at Run start. The folder id is already decided."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.desk.binding import (
    LocalRootClaimBook,
    backend_local_root_id,
    build_target_backend,
    load_target_folder_binding,
)
from agentcore.runtime.runs.desk.errors import TargetDesktopError
from agentcore.runtime.runs.desk.worker import (
    rebuild_worker_prompt_for_target,
    registry_rewire_consult_tools,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

logger = get_logger(__name__)

_TARGET_ROOT_PING_TIMEOUT_S = 5.0


async def _assert_target_local_root_ready(backend: Any, *, folder_id: str) -> None:
    """Fail the worker before ReAct when the target local root is not usable."""
    if getattr(backend, "location", None) != "local":
        return
    channel = getattr(backend, "_channel", None)
    request = getattr(channel, "request", None)
    if not callable(request):
        return
    from agentcore.workspace.channel import WorkspaceOp
    from agentcore.workspace.limits import workspace_channel_failure_kind
    from agentcore.workspace.protocol import WorkspaceIOError

    try:
        await request(
            WorkspaceOp.EXISTS, {"path": "."}, timeout=_TARGET_ROOT_PING_TIMEOUT_S
        )
    except WorkspaceIOError as exc:
        if workspace_channel_failure_kind(exc) == "liveness":
            raise TargetDesktopError(
                f"目标文件夹 `{folder_id}` 本机通道无响应；请确认桌面在线后重派。"
            ) from exc
        raise TargetDesktopError(
            f"目标文件夹 `{folder_id}` 不存在或无权访问；请重新列/解析文件夹后再派。"
        ) from exc


@dataclass(frozen=True)
class AppliedTargetDesktop:
    """Outputs of applying a target desk onto one worker preparation."""

    tool_ctx: ToolContext
    worker_tools: ToolRegistry
    system_prompt: str
    worker_envelope: str
    target_folder_id: str


async def apply_target_desktop(
    *,
    target_folder_id: str,
    session_folder_id: str | None,
    env_system_prompt: str,
    base_tool_context: ToolContext,
    worker_tools: ToolRegistry,
    sink: Any,
    local_root_claims: LocalRootClaimBook | None,
    permission_axes: Any = None,
) -> AppliedTargetDesktop:
    """Swap backend + memory scope for a worker whose task named a target Folder."""
    if session_folder_id and target_folder_id == session_folder_id:
        return AppliedTargetDesktop(
            tool_ctx=base_tool_context,
            worker_tools=worker_tools,
            system_prompt=env_system_prompt,
            worker_envelope="",
            target_folder_id=target_folder_id,
        )

    binding = await load_target_folder_binding(
        folder_id=target_folder_id,
        user_id=base_tool_context.user_id,
    )
    if binding is None:
        raise TargetDesktopError(
            f"目标文件夹 `{target_folder_id}` 不存在或无权访问；请重新列/解析文件夹后再派。"
        )

    backend = build_target_backend(
        user_id=base_tool_context.user_id,
        folder_id=binding.folder_id,
        folder_rel_path=binding.rel_path,
        conversation_id=base_tool_context.conversation_id,
        sink=sink,
        local_binding=binding.local_binding,
    )
    await _assert_target_local_root_ready(backend, folder_id=binding.folder_id)

    target_root = backend_local_root_id(backend)
    if target_root and local_root_claims is not None:
        await local_root_claims.try_claim(target_root)

    from agentcore.tools.sandbox.desk_provision import provision_server_desk

    await provision_server_desk(
        backend,
        conversation_id=base_tool_context.conversation_id,
        sink=sink,
    )

    desktop_online = base_tool_context.desktop_channel is not None
    attachment_context = base_tool_context.attachment_context or None
    worker_prompt, worker_envelope = await rebuild_worker_prompt_for_target(
        user_id=base_tool_context.user_id,
        folder_id=binding.folder_id,
        backend=backend,
        attachment_context=attachment_context,
        desktop_online=desktop_online,
        permission_axes=permission_axes,
    )
    from agentcore.runtime.runs.executor.shared import _registry_without
    from agentcore.tools.builtin import execution_class_enabled_for
    from agentcore.tools.registration import execution_class_tool_names

    tools_for_desk = worker_tools
    if not execution_class_enabled_for(backend, permission_axes):
        tools_for_desk = _registry_without(
            worker_tools, *sorted(execution_class_tool_names())
        )
    tools = await registry_rewire_consult_tools(
        tools_for_desk,
        folder_id=binding.folder_id,
        user_id=base_tool_context.user_id,
    )
    from agentcore.workspace.locate import workspace_channel_for_tools

    workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=base_tool_context.user_id,
        conversation_id=base_tool_context.conversation_id,
    )
    from agentcore.tools.protocol import fork_workspace_slot

    tool_ctx = replace(
        base_tool_context,
        _workspace=fork_workspace_slot(
            backend,
            material_paths=base_tool_context.material_paths,
        ),
        workspace_channel=workspace_channel,
        shared_workspace=True,
    )
    logger.info(
        "delegate.target_desktop_applied",
        folder_id=binding.folder_id,
        folder_name=binding.name,
        location=getattr(backend, "location", None),
        local=bool(binding.local_binding),
    )
    return AppliedTargetDesktop(
        tool_ctx=tool_ctx,
        worker_tools=tools,
        system_prompt=worker_prompt,
        worker_envelope=worker_envelope,
        target_folder_id=binding.folder_id,
    )
