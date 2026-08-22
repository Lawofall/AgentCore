"""Target-desktop wiring for shape-甲 cross-project delegate (P0 桶 B · C0 多 local).

Task carries ``target_folder_id`` → worker tools sit on that Folder root;
memory / rules / ``consult`` follow the same folder (not session birth).
Session birth ``folder_id`` is never rewritten. Bare-chat auto cloud desks
persist on ``Conversation.auto_desk_folder_id`` (orthogonal) for cross-turn
reuse and CEO file visibility. Distinct local roots may run in the same turn
(one LocalWorkspace + channel per target); ClaimBook only records.

Public API is re-exported here; cohesive pieces live in
``target_desktop_gate`` / ``target_desktop_binding`` /
``target_desktop_auto_cloud`` / ``target_desktop_worker``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.delegate.target_desktop_auto_cloud import (
    auto_cloud_desk_name as _auto_cloud_desk_name,
)
from agentcore.runtime.delegate.target_desktop_auto_cloud import (
    bare_chat_write_tasks_need_target as _bare_chat_write_tasks_need_target,
)
from agentcore.runtime.delegate.target_desktop_auto_cloud import (
    clear_stale_auto_desk_folder_id as _clear_stale_auto_desk_folder_id,
)
from agentcore.runtime.delegate.target_desktop_auto_cloud import (
    load_auto_desk_folder_id as _load_auto_desk_folder_id,
)
from agentcore.runtime.delegate.target_desktop_auto_cloud import (
    load_conversation_title as _load_conversation_title,
)
from agentcore.runtime.delegate.target_desktop_auto_cloud import (
    persist_auto_desk_folder_id as _persist_auto_desk_folder_id,
)
from agentcore.runtime.delegate.target_desktop_auto_cloud import (
    reclaim_orphan_auto_desk_folder as _reclaim_orphan_auto_desk_folder,
)
from agentcore.runtime.delegate.target_desktop_binding import (
    LocalRootClaimBook,
    TargetFolderBinding,
    build_target_backend,
    load_target_folder_binding,
    lookup_folder_display_names,
)
from agentcore.runtime.delegate.target_desktop_binding import (
    backend_local_root_id as _backend_local_root_id,
)
from agentcore.runtime.delegate.target_desktop_gate import (
    NO_TARGET_SCRATCH_GATE_MSG,
    SCRATCH_NO_WRITE_IDENTITY_HINT,
    TargetDesktopError,
    effective_target_folder_id,
    format_bare_chat_no_target_error,
    gate_bare_chat_requires_target,
    resolve_bare_chat_write_scope,
    task_structurally_requires_write_desk,
)
from agentcore.runtime.delegate.target_desktop_worker import (
    rebuild_worker_prompt_for_target,
)
from agentcore.runtime.delegate.target_desktop_worker import (
    registry_rewire_consult_tools as _registry_rewire_consult_tools,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry

logger = get_logger(__name__)

__all__ = [
    "NO_TARGET_SCRATCH_GATE_MSG",
    "SCRATCH_NO_WRITE_IDENTITY_HINT",
    "AppliedTargetDesktop",
    "LocalRootClaimBook",
    "TargetDesktopError",
    "TargetFolderBinding",
    "apply_target_desktop",
    "bind_tool_context_to_landing_desk",
    "build_target_backend",
    "effective_target_folder_id",
    "ensure_bare_chat_auto_cloud_desk",
    "format_bare_chat_no_target_error",
    "gate_bare_chat_requires_target",
    "load_target_folder_binding",
    "lookup_folder_display_names",
    "rebuild_worker_prompt_for_target",
    "resolve_bare_chat_write_scope",
    "task_structurally_requires_write_desk",
]


async def bind_tool_context_to_landing_desk(
    context: ToolContext,
    *,
    folder_id: str,
) -> bool:
    """Point CEO file tools at the landing Folder without rewriting birth affiliation.

    Mutates ``context.backend`` / ``workspace_channel`` / ``auto_desk_folder_id`` in
    place. Returns False when the Folder cannot be bound (caller keeps birth desk).
    """
    cleaned = folder_id.strip() if isinstance(folder_id, str) else ""
    if not cleaned:
        return False
    try:
        binding = await load_target_folder_binding(folder_id=cleaned, user_id=context.user_id)
    except TargetDesktopError as e:
        # Infra / cloud failure — do not clear the pointer (folder may still exist).
        logger.warning(
            "delegate.auto_desk_bind_failed",
            folder_id=cleaned,
            error=e.message,
        )
        return False
    except Exception as e:  # noqa: BLE001 — never break delegate on bind miss
        logger.warning(
            "delegate.auto_desk_bind_failed",
            folder_id=cleaned,
            error=str(e),
        )
        return False
    if binding is None:
        # Folder gone / soft-deleted / denied — clear pointer so the next turn remints.
        logger.warning(
            "delegate.auto_desk_bind_failed",
            folder_id=cleaned,
            error="folder missing or denied",
        )
        if getattr(context, "auto_desk_folder_id", None) == cleaned:
            context.auto_desk_folder_id = None
        await _clear_stale_auto_desk_folder_id(
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            folder_id=cleaned,
        )
        return False

    sink = None
    for holder in (context.workspace_channel, context.desktop_channel):
        if holder is None:
            continue
        candidate = getattr(holder, "sink", None)
        if candidate is not None:
            sink = candidate
            break
    if sink is None:
        from agentcore.runtime.events import EventSink

        sink = EventSink()

    backend = build_target_backend(
        user_id=context.user_id,
        folder_id=binding.folder_id,
        folder_rel_path=binding.rel_path,
        conversation_id=context.conversation_id,
        sink=sink,
        local_binding=binding.local_binding,
    )
    from agentcore.workspace.locate import workspace_channel_for_tools
    from agentcore.workspace.migrate_tree import migrate_and_transfer_cloud_backend

    old_backend = context.backend
    moved = migrate_and_transfer_cloud_backend(old_backend, backend)
    # Keep ToolContext.material_paths (shared slot) — relative paths still apply
    # after the tree move; stamp the same set onto the new backend above.

    context.backend = backend
    context.workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=context.user_id,
        conversation_id=context.conversation_id,
    )
    context.auto_desk_folder_id = binding.folder_id
    context.shared_workspace = True
    logger.info(
        "delegate.auto_desk_ceo_bound",
        folder_id=binding.folder_id,
        conversation_id=context.conversation_id,
        conversation_untouched=True,
        scratch_entries_moved=moved,
    )
    return True


async def ensure_bare_chat_auto_cloud_desk(
    *,
    session_folder_id: str | None,
    tasks_raw: list[dict[str, Any]],
    default_target_folder_id: str | None,
    turn_target_desk: Any,
    user_id: str,
    conversation_id: str | None = None,
    conversation_title: str | None = None,
    tool_context: ToolContext | None = None,
    persisted_auto_desk_folder_id: str | None = None,
    sink: Any = None,
) -> str | None:
    """Create or reuse a cloud desk for bare-chat write tasks lacking a target.

    Trigger: no session ``folder_id`` + structural write-desk task + no effective
    target + no unique ``turn_target_desk``. Only cloud. Never rewrites conversation
    birth ``folder_id`` (归属). Persists ``Conversation.auto_desk_folder_id`` on first
    mint so later turns reuse the same desk. At most one mint per turn
    (``auto_cloud_provisioned``). Does not ask the user. Returns provisioned /
    reused folder id, or ``None`` when skipped / failed.

    A fresh mint emits ``auto_folder_created`` on ``sink`` so the user is TOLD where
    their files landed and can rename it on the spot (双模式工作区 §5.4 裸聊行). That
    is a notice, not a gate: the turn never waits on it. Reuse and race-loss stay
    silent — the desk was already announced by the turn that minted it.
    """
    if session_folder_id:
        return None
    if not user_id:
        return None
    if not _bare_chat_write_tasks_need_target(
        session_folder_id=session_folder_id,
        tasks_raw=tasks_raw if isinstance(tasks_raw, list) else [],
        default_target_folder_id=default_target_folder_id,
    ):
        return None
    if turn_target_desk is None:
        return None
    # Multi-project same turn already cleared the unique hint — do not mint a third
    # and do not force-reuse persisted as a unique default.
    seen = getattr(turn_target_desk, "_seen", None)
    if isinstance(seen, set) and seen and not getattr(turn_target_desk, "folder_id", None):
        return None

    persisted = (
        persisted_auto_desk_folder_id.strip()
        if isinstance(persisted_auto_desk_folder_id, str) and persisted_auto_desk_folder_id.strip()
        else None
    )
    if persisted is None:
        ctx_persisted = getattr(tool_context, "auto_desk_folder_id", None) if tool_context else None
        if isinstance(ctx_persisted, str) and ctx_persisted.strip():
            persisted = ctx_persisted.strip()
    if persisted is None:
        persisted = await _load_auto_desk_folder_id(
            user_id=user_id, conversation_id=conversation_id
        )
    if persisted:
        bound = True
        if tool_context is not None:
            bound = await bind_tool_context_to_landing_desk(
                tool_context, folder_id=persisted
            )
        if bound:
            turn_target_desk.note_folder(persisted)
            logger.info(
                "delegate.auto_cloud_desk_reused",
                folder_id=persisted,
                conversation_id=conversation_id,
                conversation_untouched=True,
            )
            return persisted
        # Bind cleared a dead pointer — fall through to remint this turn.

    if getattr(turn_target_desk, "auto_cloud_provisioned", False):
        return None

    turn_target_desk.auto_cloud_provisioned = True
    title = conversation_title
    if not (isinstance(title, str) and title.strip()):
        title = await _load_conversation_title(user_id=user_id, conversation_id=conversation_id)
    name = _auto_cloud_desk_name(conversation_title=title)
    try:
        from agentcore.tools.builtin.folders import create_cloud_folder

        created = await create_cloud_folder(user_id=user_id, name=name)
    except Exception as e:  # noqa: BLE001 — fall through to gate reject
        logger.warning(
            "delegate.auto_cloud_desk_provision_failed",
            user_id=user_id,
            conversation_id=conversation_id,
            error=str(e),
        )
        return None

    folder_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(folder_id, str) or not folder_id.strip():
        logger.warning(
            "delegate.auto_cloud_desk_provision_failed",
            user_id=user_id,
            conversation_id=conversation_id,
            error="missing folder id",
        )
        return None
    folder_id = folder_id.strip()
    persist = await _persist_auto_desk_folder_id(
        user_id=user_id,
        conversation_id=conversation_id,
        folder_id=folder_id,
    )
    announce = True
    if persist.outcome == "lost" and persist.effective_id:
        # Race: another turn wrote first — reuse winner, reclaim this turn's mint.
        # The winning turn announces its own desk; a second notice here would name
        # a folder this turn just threw away.
        announce = False
        orphan_id = folder_id
        folder_id = persist.effective_id
        await _reclaim_orphan_auto_desk_folder(user_id=user_id, folder_id=orphan_id)
    elif persist.outcome == "failed":
        # Keep minted desk for this turn (do not block). Outcome is explicit — not a
        # silent None — so we never confuse this with a race loss / reclaim path.
        logger.warning(
            "delegate.auto_desk_persist_failed_using_mint",
            conversation_id=conversation_id,
            folder_id=folder_id,
        )
    turn_target_desk.note_folder(folder_id)
    if tool_context is not None:
        await bind_tool_context_to_landing_desk(tool_context, folder_id=folder_id)
    if announce and sink is not None:
        from agentcore.runtime.events import auto_folder_created

        sink.emit(auto_folder_created(folder_id=folder_id, name=name))
    logger.info(
        "delegate.auto_cloud_desk_provisioned",
        folder_id=folder_id,
        name=name,
        conversation_id=conversation_id,
        conversation_untouched=True,
        announced=announce and sink is not None,
    )
    return folder_id


@dataclass(frozen=True)
class AppliedTargetDesktop:
    """Outputs of applying a target desk onto one worker preparation."""

    tool_ctx: ToolContext
    worker_tools: ToolRegistry
    system_prompt: str
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
    """Swap backend + memory scope for a worker whose task named a target Folder.

    No-op path (same as session birth desk) still returns applied bag with the
    existing backend when ``target_folder_id == session_folder_id``.
    """
    # Same desk as birth → keep turn wiring (prefix cache + shared tools).
    if session_folder_id and target_folder_id == session_folder_id:
        return AppliedTargetDesktop(
            tool_ctx=base_tool_context,
            worker_tools=worker_tools,
            system_prompt=env_system_prompt,
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

    # C0: record local root; never reject a second distinct root (sidecar same).
    target_root = _backend_local_root_id(backend)
    if target_root and local_root_claims is not None:
        await local_root_claims.try_claim(target_root)

    desktop_online = base_tool_context.desktop_channel is not None
    attachment_context = base_tool_context.attachment_context or None
    worker_prompt = await rebuild_worker_prompt_for_target(
        user_id=base_tool_context.user_id,
        folder_id=binding.folder_id,
        backend=backend,
        attachment_context=attachment_context,
        desktop_online=desktop_online,
        permission_axes=permission_axes,
    )
    tools = await _registry_rewire_consult_tools(
        worker_tools,
        folder_id=binding.folder_id,
        user_id=base_tool_context.user_id,
    )
    from agentcore.workspace.locate import workspace_channel_for_tools

    workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=base_tool_context.user_id,
        conversation_id=base_tool_context.conversation_id,
    )
    # Intentional other-desk: fresh slot so this worker does not follow a later
    # parent rebind (and parent does not follow this fork).
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
        target_folder_id=binding.folder_id,
    )
