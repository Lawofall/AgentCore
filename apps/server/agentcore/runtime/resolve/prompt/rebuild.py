"""Rebuild worker base system prompt without a suspension frame.

Same helpers as :func:`prepare_fresh_turn` / crash-delegate redrive: fresh rules,
workspace facts, and ``<按需目录>`` from MergedConsultSource — not the CEO chat
prompt captured on ``turn_started``.
"""

from __future__ import annotations

from typing import Any

from agentcore.memory import assemble_turn_rules, default_memory_store
from agentcore.runtime.capability_packs import enabled_packs
from agentcore.runtime.context import (
    build_workspace_context,
    collect_outlet_inventory,
    detect_workspace_git,
)
from agentcore.runtime.context.consult_sources import build_merged_consult_source
from agentcore.runtime.resolve.prompt.compose import (
    assemble_system_prompt,
    compose_worker_base_prompt,
)
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin import build_worker_registry
from agentcore.tools.sandbox.exec_languages import resolve_exec_languages
from agentcore.workspace.protocol import WorkspaceBackend


async def rebuild_fresh_worker_base_prompt(
    *,
    user_id: str,
    folder_id: str | None,
    backend: WorkspaceBackend,
    permission_axes: Any = None,
    desktop_online: bool = False,
) -> str:
    """Fresh worker base (no suspension frame): same path as crash-delegate redrive."""
    memory_store = default_memory_store()
    rules_markdown = await assemble_turn_rules(
        memory_store,
        user_id,
        folder_id=folder_id,
        enabled=True,
    )
    exec_languages = await resolve_exec_languages(backend)
    workspace_facts = build_workspace_context(
        backend,
        desktop_online=desktop_online,
        exec_languages=exec_languages,
        permission_axes=permission_axes,
        git_fact=await detect_workspace_git(backend),
        outlet_inventory=await collect_outlet_inventory(backend),
        desk_folder_id=folder_id,
        desk_folder_label=(getattr(backend, "root_label", None) or "").strip() or None,
        desk_is_birth=True,
    )
    system_prompt = assemble_system_prompt(
        rules_markdown=rules_markdown,
    )
    skill_registry = build_system_skill_registry(enabled_packs=enabled_packs())
    provisional_tools = build_worker_registry(
        backend=backend,
        permission_axes=permission_axes,
        languages=exec_languages if backend.location == "local" else None,
        desktop_online=desktop_online,
    )
    on_demand_entries = list(
        await build_merged_consult_source(
            skill_registry=skill_registry,
            tool_names={s.name for s in provisional_tools.list_all()},
            memory_store=memory_store,
            folder_id=folder_id,
            skill_audience="worker",
            tool_registry=provisional_tools,
        ).list_directory(user_id)
    )
    return compose_worker_base_prompt(
        system_prompt,
        on_demand_entries=on_demand_entries,
        attachment_context="",
        workspace_context=workspace_facts,
    )
