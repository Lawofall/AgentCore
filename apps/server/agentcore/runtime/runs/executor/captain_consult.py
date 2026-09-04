"""Refresh a nested captain's consult source so ``lead_subteam`` is listable and fetchable.

Prepare-time worker consult is frozen without ``delegate`` in the skill filter
(leaves must not see 子队拆法). After the executor forks a captain registry that
holds ``delegate``, listing and fetch must include that name — and the baked
``<按需目录>`` in the worker base must match (directory ≡ fetch).
"""

from __future__ import annotations

from agentcore.runtime.context.consult_sources import expand_skill_tool_names
from agentcore.runtime.resolve.prompt.compose import (
    render_on_demand_directory,
    splice_on_demand_directory,
)
from agentcore.runtime.runs.executor.shared import _registry_without
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.registry import ToolRegistry


async def offer_nested_lead_consult(
    registry: ToolRegistry,
    system_prompt: str,
    *,
    user_id: str,
) -> tuple[ToolRegistry, str]:
    """Fork consult onto this captain registry; splice the matching directory block."""
    consult = registry.get_optional("consult")
    source = getattr(consult, "source", None) if consult is not None else None
    if source is None:
        return registry, system_prompt
    extra = {schema.name for schema in registry.list_all()}
    new_source = expand_skill_tool_names(source, extra)
    if new_source is source:
        return registry, system_prompt
    refreshed = _registry_without(registry, "consult")
    refreshed.register(ConsultTool(source=new_source))
    entries = await new_source.list_directory(user_id)
    block = render_on_demand_directory(entries, with_summaries=True)
    return refreshed, splice_on_demand_directory(system_prompt, block)
