"""CEO coordinator toolset assembly — ownership lives in ``tools/``.

Runtime (pipeline / resume / prepare) only consumes this; monkeypatch seams
re-export the same symbol from historical import paths.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentcore.config import settings
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.context.consult_sources import build_merged_consult_source
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.runtime.sessions import (
    SessionLoader,
    SessionSaver,
)
from agentcore.runtime.skills import (
    SkillRegistry,
)
from agentcore.runtime.skills.registry import AUDIENCE_CEO, AUDIENCE_WORKER
from agentcore.runtime.suspension import (
    SuspensionDeleter,
    SuspensionSaver,
)
from agentcore.tools.builtin import (
    build_ceo_tool_registry,
)
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.builtin.promote_product import PromoteProductTool
from agentcore.tools.builtin.remember import RememberTool
from agentcore.tools.builtin.update_folder_profile import UpdateFolderProfileTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registration import register_always_ceo_tools
from agentcore.tools.registry import ToolRegistry


def _delivery_republisher(sink: EventSink) -> Callable[[dict[str, Any]], None]:
    """Re-emit ``delivery_status`` after 成品归位 rewrote its paths.

    Folds keep the LATEST per ``execution_id``, so republishing the same id lands
    as an update: the card now names where each product actually lives, plus the
    ``promoted`` rows. Tools stay off the event vocabulary (引擎纯化) — this
    closure owns the event shape.
    """

    def _publish(payload: dict[str, Any]) -> None:
        from agentcore.runtime.events import delivery_status

        sink.emit(delivery_status(**payload))

    return _publish


async def _wire_consult_if_entries(
    registry: ToolRegistry,
    *,
    skill_registry: SkillRegistry,
    folder_id: str | None,
    memory_enabled: bool,
    user_id: str,
    skill_audience: str,
) -> bool:
    """Register unified ``consult`` when the merged catalog is non-empty.

    Returns whether the tool was wired (for prompt directory↔tool gate).
    Prompt listing and fetch share the same :class:`MergedConsultSource` instance.
    ``skill_audience`` is the reader role (``ceo`` / ``worker``) — not a task guess.
    """
    from agentcore.runtime.resolve.prepare import default_memory_store

    # Tool names already on the registry gate skill visibility (ask_user / debate / …).
    tool_names = {schema.name for schema in registry.list_all()}
    source = build_merged_consult_source(
        skill_registry=skill_registry,
        tool_names=tool_names,
        memory_store=default_memory_store(),
        folder_id=folder_id,
        memory_enabled=memory_enabled,
        skill_audience=skill_audience,
        tool_registry=registry,
    )
    entries = await source.list_directory(user_id)
    if not entries:
        return False
    registry.register(ConsultTool(source=source))
    return True


def _assemble_ceo_toolset(
    *,
    llm,
    sink: EventSink,
    base_system_prompt: str,
    user_message: str,
    history: list[dict],
    worker_tools: ToolRegistry,
    base_tool_context: ToolContext,
    profiles: ProfileSet,
    approval_gate: ApprovalGate | None,
    session_store,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    conversation_id: str,
    captain_run_id: str,
    checkpoint_enabled: bool,
    message_id: str,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
    backend_location: str,
    skill_registry: SkillRegistry,
    memory_enabled: bool = True,
    conversation_history_access: bool = True,
    folder_id: str | None = None,
    permission_axes=None,
    advertise_bind_local_folder: bool = False,
    desktop_online: bool = False,
) -> tuple[DelegateTool, Any, ToolRegistry]:
    """Wire the CEO coordinator's toolset (delegate + read/retrieval + consult + …).

    ``consult`` is registered asynchronously by the caller via
    :func:`wire_ceo_consult` after this sync assemble (needs ``user_id`` + await
    ``list_directory``). Returns ``(delegate_tool, debate_tool, chat_tools)``.
    """
    delegate_tool = DelegateTool(
        llm=llm,
        sink=sink,
        system_prompt=base_system_prompt,
        user_message=user_message,
        history=history,
        tools=worker_tools,
        base_tool_context=base_tool_context,
        captain_run_id=captain_run_id,
        approval_gate=approval_gate,
        profile_set=profiles,
        session_store=session_store,
        session_saver=session_saver,
        session_loader=session_loader,
        conversation_id=conversation_id,
        registry=default_interaction_registry(),
        checkpoint_timeout_seconds=settings.checkpoint_timeout_seconds,
        checkpoint_enabled=checkpoint_enabled,
        message_id=message_id,
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
        folder_id=folder_id,
        memory_enabled=memory_enabled,
        conversation_history_access=conversation_history_access,
        permission_axes=permission_axes,
    )
    chat_tools = build_ceo_tool_registry(
        desktop_online=desktop_online,
        permission_axes=permission_axes,
        backend_location=backend_location,
        include_browser="browser" in worker_tools.names,
        # The worker roster already asked ``git_execution_enabled_for`` with the live
        # backend; reuse its verdict so CEO and workers never disagree on git.
        include_git="git" in worker_tools.names,
    )
    chat_tools.register(delegate_tool)
    from agentcore.tools.builtin.debate import DebateTool

    debate_tool = DebateTool(
        llm=llm,
        sink=sink,
        system_prompt=base_system_prompt,
        user_message=user_message,
        tools=worker_tools,
        base_tool_context=base_tool_context,
        profile_set=profiles,
        captain_run_id=captain_run_id,
        approval_gate=approval_gate,
        conversation_id=conversation_id,
        ambient_armed=checkpoint_enabled,
        message_id=message_id,
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
        folder_id=folder_id,
        memory_enabled=memory_enabled,
        conversation_history_access=conversation_history_access,
        permission_axes=permission_axes,
        registry=default_interaction_registry(),
        session_store=session_store,
        session_loader=session_loader,
    )
    chat_tools.register(debate_tool)
    from agentcore.runtime.resolve.ceo_surface import (
        coordination_surface_active,
        register_coordination_surface,
    )

    register_coordination_surface(
        chat_tools,
        delegate_tool=delegate_tool,
        sink=sink,
        include=coordination_surface_active(
            execution_id=base_tool_context.execution_id
        ),
    )
    from agentcore.llm.model_metadata import model_has_curated_vision
    from agentcore.vision import vision_capability_available

    main_model = profiles.model_for("chat") if profiles is not None else ""
    include_vision = vision_capability_available(
        vision_reader=base_tool_context.vision_reader,
        main_native_vision=model_has_curated_vision(main_model),
    )
    register_always_ceo_tools(
        chat_tools,
        skill_registry=skill_registry,
        include_vision=include_vision,
    )
    chat_tools.register(PromoteProductTool(on_promoted=_delivery_republisher(sink)))
    if memory_enabled:
        from agentcore.runtime.resolve.prepare import default_memory_store

        mem_store = default_memory_store()
        chat_tools.register(RememberTool(folder_id=folder_id))
        if folder_id:
            chat_tools.register(
                UpdateFolderProfileTool(
                    folder_id=folder_id,
                    store=mem_store,
                    prompt_holders=[delegate_tool, debate_tool],
                )
            )
    if checkpoint_enabled:
        chat_tools.register(
            AskUserTool(
                sink=sink,
                conversation_id=conversation_id,
                timeout_seconds=settings.checkpoint_timeout_seconds,
                captain_run_id=captain_run_id,
                base_system_prompt=base_system_prompt,
                user_message=user_message,
                history=history,
                message_id=message_id,
                suspension_saver=suspension_saver,
                suspension_deleter=suspension_deleter,
                folder_id=folder_id,
                memory_enabled=memory_enabled,
                conversation_history_access=conversation_history_access,
                advertise_bind_local_folder=advertise_bind_local_folder,
            )
        )
    return delegate_tool, debate_tool, chat_tools


async def wire_ceo_consult(
    chat_tools: ToolRegistry,
    *,
    skill_registry: SkillRegistry,
    folder_id: str | None,
    memory_enabled: bool,
    user_id: str,
) -> bool:
    """Async companion to :func:`_assemble_ceo_toolset` — wires ``consult`` if catalog nonempty."""
    return await _wire_consult_if_entries(
        chat_tools,
        skill_registry=skill_registry,
        folder_id=folder_id,
        memory_enabled=memory_enabled,
        user_id=user_id,
        skill_audience=AUDIENCE_CEO,
    )


async def wire_worker_consult(
    worker_tools: ToolRegistry,
    *,
    skill_registry: SkillRegistry,
    memory_enabled: bool = True,
    folder_id: str | None = None,
    user_id: str,
) -> bool:
    """Register unified ``consult`` on the worker toolset when the merged catalog is nonempty."""
    return await _wire_consult_if_entries(
        worker_tools,
        skill_registry=skill_registry,
        folder_id=folder_id,
        memory_enabled=memory_enabled,
        user_id=user_id,
        skill_audience=AUDIENCE_WORKER,
    )


# Historical name used by tests / resume imports.
_wire_worker_consult_tools = wire_worker_consult
