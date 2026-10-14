"""Worker prompt + consult-tool rewire when sitting on a target Folder desk."""

from __future__ import annotations

from typing import Any

from agentcore.memory import default_memory_store
from agentcore.memory.rules_injection import load_turn_rule_view
from agentcore.runtime.context import (
    build_workspace_context,
    detect_workspace_git,
)
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_worker_base_prompt,
    render_worker_turn_envelope,
)
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.protocol import WorkspaceBackend


async def registry_rewire_consult_tools(
    base: ToolRegistry,
    *,
    folder_id: str,
    user_id: str,
) -> ToolRegistry:
    """Fresh registry: drop birth-desk consult, wire target-scoped unified consult."""
    from agentcore.runtime.context.consult_sources import (
        build_merged_consult_source_for_user,
    )
    from agentcore.runtime.runs.executor.shared import _registry_without
    from agentcore.runtime.skills import build_system_skill_registry
    from agentcore.tools.builtin.consult import ConsultTool

    registry = _registry_without(base, "consult")
    registry = _registry_without(registry, "consult_rule")
    registry = _registry_without(registry, "consult_skill")
    tool_names = {schema.name for schema in registry.list_all()}
    source = await build_merged_consult_source_for_user(
        user_id=user_id,
        skill_registry=build_system_skill_registry(),
        tool_names=tool_names,
        memory_store=default_memory_store(),
        folder_id=folder_id,
        skill_audience="worker",
        tool_registry=registry,
    )
    if await source.list_directory(user_id):
        registry.register(ConsultTool(source=source))
    return registry


async def rebuild_worker_prompt_for_target(
    *,
    user_id: str,
    folder_id: str,
    backend: WorkspaceBackend,
    attachment_context: str | None = None,
    desktop_online: bool = False,
    permission_axes: Any = None,
) -> tuple[str, str]:
    """Reassemble worker frozen system + envelope for a target-folder desk."""
    from agentcore.runtime.context.consult_sources import (
        build_merged_consult_source_for_user,
    )
    from agentcore.runtime.skills import build_system_skill_registry
    from agentcore.tools.builtin import build_worker_registry

    memory_store = default_memory_store()
    rule_view = await load_turn_rule_view(
        memory_store,
        user_id,
        folder_id=folder_id,
    )
    from agentcore.tools.sandbox.exec_languages import resolve_exec_languages

    exec_languages = await resolve_exec_languages(backend)
    git_fact = await detect_workspace_git(backend)
    from agentcore.workspace.desk_empty import desk_is_visibly_empty

    workspace_facts = build_workspace_context(
        backend,
        desktop_online=desktop_online,
        exec_languages=exec_languages,
        permission_axes=permission_axes,
        git_fact=git_fact,
        desk_folder_id=folder_id,
        desk_folder_label=(getattr(backend, "root_label", None) or "").strip() or None,
        desk_is_birth=False,
        desk_visibly_empty=await desk_is_visibly_empty(backend),
    )
    shared_base = assemble_system_prompt(
        rules_markdown=rule_view.settings,
        path_index=rule_view.path_index,
    )
    provisional = build_worker_registry(
        backend=backend,
        permission_axes=permission_axes,
        desktop_online=desktop_online,
        languages=exec_languages if backend.location == "local" else None,
    )
    source = await build_merged_consult_source_for_user(
        user_id=user_id,
        skill_registry=build_system_skill_registry(),
        tool_names={s.name for s in provisional.list_all()},
        memory_store=memory_store,
        folder_id=folder_id,
        skill_audience="worker",
        tool_registry=provisional,
    )
    entries = list(await source.list_directory(user_id))
    system = compose_worker_base_prompt(
        shared_base,
        on_demand_entries=entries,
    )
    envelope = render_worker_turn_envelope(
        workspace_context=workspace_facts,
        attachment_context=attachment_context,
    )
    return system, envelope
