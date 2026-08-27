"""System prompt assembly for CEO chat and shared worker base.

Composes shared base + optional memory/rules + CEO-only sections
(core routing, citation, visualization hook, on-demand directory). Skill HOW
bodies live in ``runtime.skills`` and are pulled via ``consult``.

Package layout (fragment seams): ``base`` / ``ceo_core`` / ``citation`` /
``visualization`` / ``memory_rules`` / ``cold_start`` + ``compose`` entry.
Public import path stays ``agentcore.runtime.resolve.prompt``.
"""

from agentcore.runtime.resolve.prompt.base import (
    _DEFAULT_SYSTEM_PROMPT,
    _RUNTIME_CONTEXT_TEMPLATE,
)
from agentcore.runtime.resolve.prompt.ceo_core import (
    _ATTACHMENT_MATERIAL_HINT,
    _CEO_CORE_HINT,
    _CEO_CORE_HINT_TEMPLATE,
    _attachment_material_block,
    assemble_ceo_core,
    attachment_material_scene,
    capability_how_suffix,
)
from agentcore.runtime.resolve.prompt.citation import CHAT_CITATION_HINT
from agentcore.runtime.resolve.prompt.cold_start import (
    _COLD_START_EXPLORE_HINT_EMPTY,
    _COLD_START_EXPLORE_HINT_REBIND,
    _COLD_START_EXPLORE_HINT_REFRESH,
    _FOLDER_NAV_STALE_HINT,
    _FOLDER_PROFILE_EMPTY_SOFT_HINT,
    _explore_act_block,
)
from agentcore.runtime.resolve.prompt.compose import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    derive_ceo_addon,
    render_on_demand_directory,
)
from agentcore.runtime.resolve.prompt.memory_rules import (
    _MEMORY_ROUTING_FENCE,
    _RULES_ROUTING_FENCE,
    _RULES_TEMPLATE,
    _format_rules,
)
from agentcore.runtime.resolve.prompt.visualization import _CEO_VISUALIZATION_HINT

__all__ = [
    "CHAT_CITATION_HINT",
    "_ATTACHMENT_MATERIAL_HINT",
    "_CEO_CORE_HINT",
    "_CEO_CORE_HINT_TEMPLATE",
    "_CEO_VISUALIZATION_HINT",
    "_COLD_START_EXPLORE_HINT_EMPTY",
    "_COLD_START_EXPLORE_HINT_REBIND",
    "_COLD_START_EXPLORE_HINT_REFRESH",
    "_DEFAULT_SYSTEM_PROMPT",
    "_FOLDER_NAV_STALE_HINT",
    "_FOLDER_PROFILE_EMPTY_SOFT_HINT",
    "_MEMORY_ROUTING_FENCE",
    "_RULES_ROUTING_FENCE",
    "_RULES_TEMPLATE",
    "_RUNTIME_CONTEXT_TEMPLATE",
    "_attachment_material_block",
    "_explore_act_block",
    "_format_rules",
    "assemble_ceo_core",
    "assemble_system_prompt",
    "attachment_material_scene",
    "capability_how_suffix",
    "compose_ceo_chat_prompt",
    "compose_worker_base_prompt",
    "derive_ceo_addon",
    "render_on_demand_directory",
]
