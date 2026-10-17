"""Rebuild worker base system prompt without a suspension frame.

Same helpers as :func:`prepare_fresh_turn` / crash-delegate redrive: fresh rules
and ``<按需目录>`` from MergedConsultSource — not the CEO chat prompt captured
on ``turn_started``. Workspace facts ride the worker envelope, not this string.
"""

from __future__ import annotations

from typing import Any

from agentcore.memory import load_turn_rule_view
from agentcore.runtime.context.consult_sources import build_merged_consult_source_for_user
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
    rule_view = await load_turn_rule_view(
        user_id,
        folder_id=folder_id,
        reader=backend,
    )
    exec_languages = await resolve_exec_languages(backend)
    system_prompt = assemble_system_prompt(
        rules_markdown=rule_view.settings,
        path_index=rule_view.path_index,
    )
    skill_registry = build_system_skill_registry()
    provisional_tools = build_worker_registry(
        backend=backend,
        permission_axes=permission_axes,
        languages=exec_languages if backend.location == "local" else None,
        desktop_online=desktop_online,
    )
    source = await build_merged_consult_source_for_user(
        user_id=user_id,
        skill_registry=skill_registry,
        tool_names={s.name for s in provisional_tools.list_all()},
        folder_id=folder_id,
        skill_audience="worker",
        tool_registry=provisional_tools,
        workspace_reader=backend,
    )
    on_demand_entries = list(await source.list_directory(user_id))
    return compose_worker_base_prompt(
        system_prompt,
        on_demand_entries=on_demand_entries,
    )
