"""Resume Phase 1: re-wire channels, tool context, and CEO toolset."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.board.channel import BoardChannel
from agentcore.config import settings
from agentcore.core.types import DEFAULT_PERMISSION_AXES, PermissionAxes, new_id
from agentcore.desktop.channel import DesktopClientChannel
from agentcore.llm.profiles import TurnProfiles
from agentcore.runtime.context import (
    build_workspace_context,
    collect_outlet_inventory,
    detect_workspace_git,
    resolve_channel_profile,
)
from agentcore.runtime.costing import RunCost
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.runtime.resolve.prepare import (
    _wire_conversation_log_tools,
)
from agentcore.runtime.sessions import SessionLoader, SessionSaver, default_session_registry
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.runtime.suspension import SuspensionDeleter, SuspensionSaver, TurnSuspension
from agentcore.tools.builtin import (
    approval_class_tool_names,
    build_worker_registry,
    delegation_grantable_tool_names,
    per_call_tool_names,
)
from agentcore.tools.ceo_toolset import wire_worker_consult as _wire_worker_consult_tools
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registration import host_class_tool_names, register_board_ceo_tools
from agentcore.tools.registry import ToolRegistry
from agentcore.vision import resolve_vision_reader_for_conversation
from agentcore.workspace.locate import workspace_channel_for_tools
from agentcore.workspace.protocol import WorkspaceBackend

if TYPE_CHECKING:
    from agentcore.runtime.approvals import ApprovalGate

# ApprovalGate / _assemble_ceo_toolset are resolved via ``resume.pipeline`` so
# ``test_resume_autonomy`` can monkeypatch ``resume_pipeline_mod.ApprovalGate``.

_WORKSPACE_CONTEXT_RE = re.compile(
    r"<工作区>.*?</工作区>\n?",
    re.DOTALL,
)


def restamp_workspace_facts(prompt: str, facts: str) -> str:
    """Replace/append ``<工作区>`` for post-bind resume workers.

    Insertion matches :data:`~agentcore.runtime.context.contributor.SectionOrder.WORKSPACE_FACTS`
    (750): immediately before the attachment volatile tail, not after
    ``</运行时>`` (that was the pre-2026-08-19 slot in front of the core).
    Facts-only — CEO file index is not restamped here (workers must not receive it).
    """
    stripped = _WORKSPACE_CONTEXT_RE.sub("", prompt or "").rstrip()
    if not facts:
        return stripped
    insert_at = -1
    for marker in ("<附件>", "<队员点名>"):
        idx = stripped.find(marker)
        if idx >= 0 and (insert_at < 0 or idx < insert_at):
            insert_at = idx
    if insert_at >= 0:
        head = stripped[:insert_at].rstrip()
        tail = stripped[insert_at:]
        if head:
            return f"{head}\n{facts}\n{tail}"
        return f"{facts}\n{tail}"
    return f"{stripped}\n{facts}" if stripped else facts


@dataclass
class ResumedWiring:
    """Re-wired resume turn: tools, channels, and ambient execution binding."""

    base_tool_context: ToolContext
    vision_cost_sink: list[RunCost]
    approval_gate: ApprovalGate | None
    delegate_tool: Any
    debate_tool: Any
    chat_tools: ToolRegistry
    bound_execution_id: str
    execution_id_token: object
    board_channel: BoardChannel | None


async def _wire_continuation_toolset(
    *,
    llm: Any,
    sink: EventSink,
    backend: WorkspaceBackend,
    board_id: str | None,
    conversation_id: str,
    message_id: str,
    captain_run_id: str,
    user_id: str,
    folder_id: str | None,
    base_system_prompt: str,
    user_message: str,
    journal_entries: list[dict[str, Any]],
    display_journal: list[dict[str, Any]] | None,
    profiles: TurnProfiles,
    permission_axes: PermissionAxes | None,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
    x_client_platform: str | None,
    folder_binding_injected: bool = False,
    folder_local_root_id: str | None = None,
    folder_local_subpath: str | None = None,
) -> ResumedWiring:
    """Shared CEO/worker toolset rebuild for resume and crash redrive (no parallel path)."""
    from agentcore.runtime.pipeline.errors import raise_if_local_workspace_fulfiller_absent
    from agentcore.runtime.pipeline.resume import pipeline as resume_pipeline_mod
    from agentcore.tools.sandbox.exec_languages import resolve_exec_languages

    raise_if_local_workspace_fulfiller_absent(user_id=user_id, backend=backend)
    exec_languages = await resolve_exec_languages(backend)
    # Host / MCP backfill needs a desktop client — orthogonal to workspace location.
    channel = resolve_channel_profile(x_client_platform)
    desktop_online = channel.desktop_online
    desktop_channel = (
        DesktopClientChannel(
            user_id=user_id,
            conversation_id=conversation_id,
            registry=default_interaction_registry(),
            timeout_seconds=settings.board_op_timeout_seconds,
        )
        if desktop_online
        else None
    )
    from agentcore.tools.mcp import discover_mcp_tools, mcp_capability_label, register_mcp_tools

    mcp_discover = await discover_mcp_tools(
        desktop_channel, cache_scope=user_id, cache_only=True
    )
    mcp_label = mcp_capability_label(mcp_discover, desktop_online=desktop_online)
    from agentcore.runtime.capability_packs import enabled_packs

    skill_registry = build_system_skill_registry(enabled_packs=enabled_packs())
    worker_tools = build_worker_registry(
        backend=backend,
        permission_axes=permission_axes,
        languages=exec_languages if backend.location == "local" else None,
        desktop_online=desktop_online,
    )
    register_mcp_tools(worker_tools, mcp_discover)
    await _wire_worker_consult_tools(
        worker_tools,
        skill_registry=skill_registry,
        folder_id=folder_id,
        user_id=user_id,
    )
    _wire_conversation_log_tools(
        worker_tools,
        folder_id=folder_id,
    )
    # Same system-skill registry as a fresh turn so the continued CEO loop can
    # still consult (提示词瘦身 P2), including deployment-gated capability packs.
    # The CEO prompt itself is replayed from the stored transcript
    # (already slim + 按需目录), so no directory re-render.
    # AI 协作白板 (§六 M2): a board-bound turn regains its BoardChannel so the
    # continued CEO loop can still reach the user's open canvas via ``board_ops``.
    # Rebuilt fresh (channels aren't serializable) from the caller's re-derived
    # ``board_id`` + this continuation's sink. ``None`` ⇒ ordinary chat.
    board_channel = (
        BoardChannel(
            user_id=user_id,
            conversation_id=conversation_id,
            board_id=board_id,
            registry=default_interaction_registry(),
            timeout_seconds=settings.board_op_timeout_seconds,
        )
        if board_id
        else None
    )
    # desktop_channel created earlier (MCP discovery); reuse.
    workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    # AI 协作白板 §九.4 Gap ②: vision cost sink shared by reference across derived
    # run contexts — symmetric with the fresh-turn path (run.py).
    vision_cost_sink: list[RunCost] = []
    from agentcore.runtime.journal import execution_id_from_journal

    # Same turn continuation：execution_id 取自 journal 末张 run_plan；无则才铸新。
    resume_execution_id = (
        execution_id_from_journal(journal_entries, display_journal) or new_id()
    )
    from agentcore.runtime.deep_research_auto import load_deep_research_auto_state

    deep_research_auto, deep_research_auto_debate_count = (
        await load_deep_research_auto_state(conversation_id)
    )
    # Resume has no turn attachments carrier — materials empty; attachments/
    # path exemption on the list helpers still applies.
    backend.ai_list_materials = frozenset()
    base_tool_context = ToolContext.create(
        execution_id=resume_execution_id,
        run_id=new_id(),
        agent_id="default",
        backend=backend,
        user_id=user_id,
        conversation_id=conversation_id,
        permission_axes=(
            json.dumps(permission_axes.to_dict()) if permission_axes is not None else None
        ),
        deep_research_auto=deep_research_auto,
        deep_research_auto_debate_count=deep_research_auto_debate_count,
        board_channel=board_channel,
        desktop_channel=desktop_channel,
        workspace_channel=workspace_channel,
        # Profile vision slot → reader; else platform VISION_* when billing_mode=platform.
        vision_reader=await resolve_vision_reader_for_conversation(
            user_id=user_id, conversation_id=conversation_id
        ),
        cost_sink=vision_cost_sink,
        shared_workspace=folder_id is not None,
        material_paths=frozenset(),
        attachment_context="",
        folder_binding_injected=folder_binding_injected,
        folder_local_root_id=folder_local_root_id,
        folder_local_subpath=folder_local_subpath,
    )
    if folder_id is None:
        from agentcore.runtime.delegate.target_desktop import (
            _load_auto_desk_folder_id,
            bind_tool_context_to_landing_desk,
        )

        auto_desk = await _load_auto_desk_folder_id(
            user_id=user_id, conversation_id=conversation_id
        )
        if auto_desk:
            # Bind validates existence; on miss it clears the pointer for remint.
            # Only note the desk after a successful bind (avoid poisoning turn hint).
            ok = await bind_tool_context_to_landing_desk(
                base_tool_context, folder_id=auto_desk
            )
            if ok:
                base_tool_context.turn_target_desk.note_folder(auto_desk)
    from agentcore.runtime.closing_posture import reset_turn_scoped_closing_state
    from agentcore.runtime.coordination.session import current_execution_id

    bound_execution_id = base_tool_context.execution_id
    execution_id_token = current_execution_id.set(bound_execution_id)
    reset_turn_scoped_closing_state(
        promotion_ledger=base_tool_context.promotion_ledger,
    )
    if permission_axes is None:
        permission_axes = DEFAULT_PERMISSION_AXES
    approval_gate = (
        resume_pipeline_mod.ApprovalGate(
            sink=sink,
            conversation_id=conversation_id,
            registry=default_interaction_registry(),
            timeout_seconds=settings.approval_timeout_seconds,
            timeout_overrides=settings.approval_timeout_overrides,
            file_op_tools=approval_class_tool_names(),
            per_call_tools=per_call_tool_names(),
            delegation_grantable_tools=delegation_grantable_tool_names(),
            host_class_tools=host_class_tool_names(),
            permission_axes=permission_axes,
        )
        if settings.approval_gate_enabled
        else None
    )
    session_store = default_session_registry().get_or_create(conversation_id)
    checkpoint_enabled = settings.checkpoint_gate_enabled
    # Re-stamp environment facts onto the worker base: continuation rebuilds the
    # backend from the CURRENT binding, so workers must not inherit a stale cloud
    # ``<工作区>``. Worker restamp is facts-only — do not attach the CEO file index.
    git_fact = await detect_workspace_git(backend)
    refreshed_base = restamp_workspace_facts(
        base_system_prompt,
        build_workspace_context(
            backend,
            desktop_online=desktop_online,
            exec_languages=exec_languages,
            permission_axes=permission_axes,
            mcp_enabled=mcp_discover.tool_count > 0,
            mcp_label=mcp_label,
            git_fact=git_fact,
            outlet_inventory=await collect_outlet_inventory(backend),
            desk_folder_id=folder_id,
            desk_folder_label=(getattr(backend, "root_label", None) or "").strip() or None,
            desk_is_birth=True,
        ),
    )
    # Look up via ``resume.pipeline`` so any module-level monkeypatch on that
    # submodule (parity with fresh-turn ``pipeline.run`` seams) is honoured.
    assemble = resume_pipeline_mod._assemble_ceo_toolset
    delegate_tool, debate_tool, chat_tools = assemble(
        llm=llm,
        sink=sink,
        base_system_prompt=refreshed_base,
        user_message=user_message,
        history=[],
        worker_tools=worker_tools,
        base_tool_context=base_tool_context,
        profiles=profiles,
        approval_gate=approval_gate,
        session_store=session_store,
        session_saver=session_saver,
        session_loader=session_loader,
        conversation_id=conversation_id,
        captain_run_id=captain_run_id,
        checkpoint_enabled=checkpoint_enabled,
        message_id=message_id,
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
        backend_location=backend.location,
        skill_registry=skill_registry,
        folder_id=folder_id,
        permission_axes=permission_axes,
        advertise_bind_local_folder=checkpoint_enabled and channel.can_bind_folder,
        desktop_online=desktop_online,
    )
    from agentcore.tools.ceo_toolset import wire_ceo_consult

    register_mcp_tools(chat_tools, mcp_discover)
    _wire_conversation_log_tools(chat_tools, folder_id=folder_id)

    await wire_ceo_consult(
        chat_tools,
        skill_registry=skill_registry,
        folder_id=folder_id,
        user_id=user_id,
    )

    # AI 协作白板: re-give the CEO board tools (``board_ops`` §六 M2 + ``board_read``
    # §九) so it can keep drawing / reading. Only in a 白板会话.
    if board_channel is not None:
        register_board_ceo_tools(chat_tools)

    # Same explore-pending sink as fresh assemble (resume mid-explore: suppress
    # structured files_written inference + worker write_scope=explore_memory until
    # update_folder_profile clears the flag).
    # Soft-empty / named-refresh via resolve_hard_explore_reason（与 assemble 同源）.
    if folder_id:
        from agentcore.conversation.scratch import resolve_conversation_local_binding
        from agentcore.memory.explore_profile import (
            folder_profile_explore_reason,
            resolve_folder_workspace_key,
            resolve_hard_explore_reason,
        )
        from agentcore.memory.store import default_memory_store

        injected_binding = None
        if folder_binding_injected:
            injected_binding = resolve_conversation_local_binding(
                local_root_id=folder_local_root_id,
                local_subpath=folder_local_subpath,
            )
        # Same resolve boundary as assemble: non-UUID scope → folder:<id>;
        # connectivity/DataError → None (never HARD-kill resume over key resolve).
        current_key = await resolve_folder_workspace_key(
            folder_id,
            binding=injected_binding,
            binding_injected=folder_binding_injected,
        )
        key_for_gates = current_key if current_key is not None else ""
        explore_reason = await folder_profile_explore_reason(
            default_memory_store(),
            user_id,
            folder_id,
            current_workspace_key=key_for_gates,
        )
        explore_reason, _soft = resolve_hard_explore_reason(explore_reason, user_message)
        if explore_reason:
            base_tool_context.cold_start_explore_pending = True
            base_tool_context.write_scope = "explore_memory"
        if current_key:
            upd = chat_tools.get_optional("update_folder_profile")
            if upd is not None and getattr(upd, "workspace_key", None) is None:
                cast_upd: Any = upd
                cast_upd.workspace_key = current_key

    return ResumedWiring(
        base_tool_context=base_tool_context,
        vision_cost_sink=vision_cost_sink,
        approval_gate=approval_gate,
        delegate_tool=delegate_tool,
        debate_tool=debate_tool,
        chat_tools=chat_tools,
        bound_execution_id=bound_execution_id,
        execution_id_token=execution_id_token,
        board_channel=board_channel,
    )


async def wire_resume_turn(
    *,
    suspension: TurnSuspension,
    llm: Any,
    sink: EventSink,
    backend: WorkspaceBackend,
    board_id: str | None,
    conversation_id: str,
    message_id: str,
    captain_run_id: str,
    profiles: TurnProfiles,
    permission_axes: PermissionAxes | None,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
    x_client_platform: str | None,
) -> ResumedWiring:
    """Rebuild worker tools, channels, approval gate, and CEO toolset for resume."""
    return await _wire_continuation_toolset(
        llm=llm,
        sink=sink,
        backend=backend,
        board_id=board_id,
        conversation_id=conversation_id,
        message_id=message_id,
        captain_run_id=captain_run_id,
        user_id=suspension.user_id,
        folder_id=suspension.folder_id,
        base_system_prompt=suspension.base_system_prompt,
        user_message=suspension.user_message,
        journal_entries=suspension.journal_entries,
        display_journal=suspension.journal,
        profiles=profiles,
        permission_axes=permission_axes,
        session_saver=session_saver,
        session_loader=session_loader,
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
        x_client_platform=x_client_platform,
        folder_binding_injected=bool(suspension.folder_binding_injected),
        folder_local_root_id=suspension.folder_local_root_id,
        folder_local_subpath=suspension.folder_local_subpath,
    )


async def wire_crash_turn(
    *,
    llm: Any,
    sink: EventSink,
    backend: WorkspaceBackend,
    board_id: str | None,
    conversation_id: str,
    message_id: str,
    captain_run_id: str,
    user_id: str,
    folder_id: str | None,
    base_system_prompt: str,
    user_message: str,
    journal_entries: list[dict[str, Any]],
    profiles: TurnProfiles,
    permission_axes: PermissionAxes | None,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
) -> ResumedWiring:
    """Crash-lease sibling of :func:`wire_resume_turn` — same assembly, no suspension.

    Process death leaves no paused frame; the factory rebuilds ambient deps from
    ``lease.user_id`` + journal and calls this path so unfinished DAG nodes can
    ``resume_plan`` with a live ``DelegateTool``.
    """
    return await _wire_continuation_toolset(
        llm=llm,
        sink=sink,
        backend=backend,
        board_id=board_id,
        conversation_id=conversation_id,
        message_id=message_id,
        captain_run_id=captain_run_id,
        user_id=user_id,
        folder_id=folder_id,
        base_system_prompt=base_system_prompt,
        user_message=user_message,
        journal_entries=journal_entries,
        display_journal=None,
        profiles=profiles,
        permission_axes=permission_axes,
        session_saver=session_saver,
        session_loader=session_loader,
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
        x_client_platform=None,
    )
