"""Resume Phase 1: re-wire channels, tool context, and CEO toolset."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.config import settings
from agentcore.core.types import DEFAULT_PERMISSION_AXES, WorkspaceBoundary, new_id
from agentcore.desktop.channel import DesktopClientChannel
from agentcore.folders.desk import caller_is_desk_member
from agentcore.llm.profiles import TurnProfiles
from agentcore.runtime.context import (
    build_workspace_context,
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
from agentcore.tools.ceo_toolset import wire_worker_consult
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registration import (
    host_class_tool_names,
)
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.locate import workspace_channel_for_tools
from agentcore.workspace.protocol import WorkspaceBackend

if TYPE_CHECKING:
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.runtime.approvals import ApprovalGate

# ApprovalGate / _assemble_ceo_toolset are resolved via ``resume.pipeline`` so
# ``test_resume_autonomy`` can monkeypatch ``resume_pipeline_mod.ApprovalGate``.

_WORKSPACE_CONTEXT_RE = re.compile(
    r"<工作区>.*?</工作区>\n?",
    re.DOTALL,
)


def _workspace_block(text: str) -> str:
    match = _WORKSPACE_CONTEXT_RE.search(text or "")
    return (match.group(0) if match else "").strip()


def _journal_haystack(journal_entries: list[dict[str, Any]]) -> str:
    """Flatten journal payloads that may carry a prior ``<工作区>`` snapshot."""
    chunks: list[str] = []
    for entry in journal_entries or []:
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        for key in ("system_prompt", "user_message", "turn_envelope", "content"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                chunks.append(val)
    return "\n".join(chunks)


def restamp_workspace_facts(prompt: str, facts: str) -> str:
    """Post-history ``[系统提示]`` envelope when frozen ``<工作区>`` is stale.

    ``prompt`` is any haystack that may still hold the last ``<工作区>`` (legacy
    worker system, opening user envelope, journal dump). Empty string = no
    restamp (no prior block, or it already matches). Never rewrites ``prompt``.
    Facts-only — CEO file index is not attached (workers must not receive it).
    """
    from agentcore.runtime.resolve.prompt.envelope import TURN_ENVELOPE_FENCE

    facts_n = (facts or "").strip()
    if not facts_n:
        return ""
    old = _workspace_block(prompt)
    if not old:
        return ""
    new = _workspace_block(facts_n) or facts_n
    if old == new:
        return ""
    return f"{TURN_ENVELOPE_FENCE}\n{facts_n}"


def append_workspace_restamp_envelope(messages: list[Any], envelope: str) -> None:
    """Append a restamp envelope after history. No-op when empty or already last."""
    env = (envelope or "").strip()
    if not env:
        return
    from agentcore.llm.provider.protocol import LLMMessage

    if messages:
        last = messages[-1]
        content = getattr(last, "content", None) or ""
        if getattr(last, "role", None) == "user" and str(content).strip() == env:
            return
    messages.append(LLMMessage(role="user", content=env))


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
    workspace_restamp_envelope: str = ""


async def _wire_continuation_toolset(
    *,
    llm: Any,
    sink: EventSink,
    backend: WorkspaceBackend,
    table_id: str | None = None,
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
    permission_axes: WorkspaceBoundary | None,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
    x_client_platform: str | None,
    folder_binding_injected: bool = False,
    folder_local_root_id: str | None = None,
    folder_local_subpath: str | None = None,
    llm_credentials: LLMCredentials | None = None,
) -> ResumedWiring:
    """Shared CEO/worker toolset rebuild for resume and crash redrive (no parallel path)."""
    from agentcore.runtime.pipeline.errors import raise_if_local_workspace_fulfiller_absent
    from agentcore.runtime.pipeline.resume import pipeline as resume_pipeline_mod
    from agentcore.tools.sandbox.exec_languages import resolve_exec_languages

    raise_if_local_workspace_fulfiller_absent(user_id=user_id, backend=backend)
    auto_desk_folder_id: str | None = None
    if folder_id is None:
        from agentcore.runtime.delegate.target_desktop import adopt_persisted_auto_desk

        adopted = await adopt_persisted_auto_desk(
            birth_folder_id=folder_id,
            user_id=user_id,
            conversation_id=conversation_id,
            birth_backend=backend,
            sink=sink,
        )
        if adopted is not None:
            backend = adopted.backend
            auto_desk_folder_id = adopted.folder_id
    sitting_folder_id = folder_id or auto_desk_folder_id
    exec_languages = await resolve_exec_languages(backend)
    # Host / MCP backfill needs a desktop client — orthogonal to workspace location.
    # Member turns: same client header, but no desktop fulfill (协作桌 · 否决本地共享).
    member_turn = await caller_is_desk_member(user_id=user_id, folder_id=folder_id)
    channel = resolve_channel_profile(x_client_platform).for_turn(
        member_turn=member_turn
    )
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
    from agentcore.tools.mcp import discover_mcp_tools, register_mcp_tools

    mcp_discover = await discover_mcp_tools(
        desktop_channel, cache_scope=user_id, cache_only=True
    )
    skill_registry = build_system_skill_registry()
    from agentcore.runtime.pipeline.prepare import _timed_phase
    from agentcore.tools.sandbox.desk_provision import provision_server_desk

    await _timed_phase(
        "cloud_desk",
        provision_server_desk(
            backend, conversation_id=conversation_id, sink=sink
        ),
    )
    worker_tools = build_worker_registry(
        backend=backend,
        permission_axes=permission_axes,
        languages=exec_languages if backend.location == "local" else None,
        desktop_online=desktop_online,
    )
    register_mcp_tools(worker_tools, mcp_discover)
    await wire_worker_consult(
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
    # still consult (提示词瘦身 P2).
    # The CEO prompt itself is replayed from the stored transcript
    # (already slim + 按需目录), so no directory re-render.
    if table_id is None:
        from agentcore.table.bind import lookup_table_id_for_turn

        try:
            table_id = await lookup_table_id_for_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                folder_id=folder_id,
                attachments=None,
            )
        except Exception:
            table_id = None
    # desktop_channel created earlier (MCP discovery); reuse.
    workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    # Resume has no turn attachments; vision sink stays empty (native-only images).
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
    from agentcore.llm.image_accept import model_accepts_images
    from agentcore.memory import default_memory_store, load_turn_rule_view
    from agentcore.runtime.coordination.session import (
        invalidate_verify_cache_for_execution,
    )

    rule_view = await load_turn_rule_view(
        default_memory_store(),
        user_id,
        folder_id=folder_id,
    )
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
        table_id=table_id,
        desktop_channel=desktop_channel,
        workspace_channel=workspace_channel,
        accepts_images=model_accepts_images(
            profiles.model_for("chat") if profiles is not None else ""
        ),
        shared_workspace=folder_id is not None or auto_desk_folder_id is not None,
        auto_desk_folder_id=auto_desk_folder_id,
        ownership_desk_id=(
            str(folder_id).strip()
            if isinstance(folder_id, str) and folder_id.strip()
            else None
        ),
        material_paths=frozenset(),
        attachment_context="",
        folder_binding_injected=folder_binding_injected,
        folder_local_root_id=folder_local_root_id,
        folder_local_subpath=folder_local_subpath,
        on_file_landed=invalidate_verify_cache_for_execution,
        path_rules=rule_view.path_rules,
    )
    if auto_desk_folder_id:
        base_tool_context.turn_target_desk.note_folder(auto_desk_folder_id)
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
    # Frozen worker system is not rewritten. If ``<工作区>`` drifted (bind-during
    # ask_user), spawn a fresh opening template for *new* workers and append a
    # ``[系统提示]`` envelope after the continuing CEO history.
    git_fact = await detect_workspace_git(backend)
    from agentcore.workspace.desk_empty import desk_is_visibly_empty

    workspace_facts = build_workspace_context(
        backend,
        desktop_online=desktop_online,
        exec_languages=exec_languages,
        permission_axes=permission_axes,
        git_fact=git_fact,
        desk_folder_id=sitting_folder_id,
        desk_folder_label=(getattr(backend, "root_label", None) or "").strip() or None,
        desk_is_birth=folder_id is not None,
        desk_visibly_empty=await desk_is_visibly_empty(backend),
    )
    workspace_restamp_envelope = restamp_workspace_facts(
        "\n".join(
            part
            for part in (base_system_prompt, _journal_haystack(journal_entries))
            if part
        ),
        workspace_facts,
    )
    from agentcore.runtime.resolve.prompt.envelope import render_worker_turn_envelope

    worker_envelope = render_worker_turn_envelope(
        workspace_context=workspace_facts,
        attachment_context="",
    )
    spawn_base = base_system_prompt
    if workspace_restamp_envelope:
        from agentcore.runtime.resolve.prompt.rebuild import rebuild_fresh_worker_base_prompt

        spawn_base = await rebuild_fresh_worker_base_prompt(
            user_id=user_id,
            folder_id=sitting_folder_id,
            backend=backend,
            permission_axes=permission_axes,
            desktop_online=desktop_online,
        )
    # Look up via ``resume.pipeline`` so any module-level monkeypatch on that
    # submodule (parity with fresh-turn ``pipeline.run`` seams) is honoured.
    assemble = resume_pipeline_mod._assemble_ceo_toolset
    delegate_tool, debate_tool, chat_tools = assemble(
        llm=llm,
        sink=sink,
        base_system_prompt=spawn_base,
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
        worker_envelope=worker_envelope,
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

    return ResumedWiring(
        base_tool_context=base_tool_context,
        vision_cost_sink=vision_cost_sink,
        approval_gate=approval_gate,
        delegate_tool=delegate_tool,
        debate_tool=debate_tool,
        chat_tools=chat_tools,
        bound_execution_id=bound_execution_id,
        execution_id_token=execution_id_token,
        workspace_restamp_envelope=workspace_restamp_envelope,
    )


async def wire_resume_turn(
    *,
    suspension: TurnSuspension,
    llm: Any,
    sink: EventSink,
    backend: WorkspaceBackend,
    table_id: str | None = None,
    conversation_id: str,
    message_id: str,
    captain_run_id: str,
    profiles: TurnProfiles,
    permission_axes: WorkspaceBoundary | None,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
    x_client_platform: str | None,
    llm_credentials: LLMCredentials | None = None,
) -> ResumedWiring:
    """Rebuild worker tools, channels, approval gate, and CEO toolset for resume."""
    return await _wire_continuation_toolset(
        llm=llm,
        sink=sink,
        backend=backend,
        table_id=table_id,
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
        llm_credentials=llm_credentials,
    )


async def wire_crash_turn(
    *,
    llm: Any,
    sink: EventSink,
    backend: WorkspaceBackend,
    table_id: str | None = None,
    conversation_id: str,
    message_id: str,
    captain_run_id: str,
    user_id: str,
    folder_id: str | None,
    base_system_prompt: str,
    user_message: str,
    journal_entries: list[dict[str, Any]],
    profiles: TurnProfiles,
    permission_axes: WorkspaceBoundary | None,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
    llm_credentials: LLMCredentials | None = None,
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
        table_id=table_id,
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
        llm_credentials=llm_credentials,
    )
