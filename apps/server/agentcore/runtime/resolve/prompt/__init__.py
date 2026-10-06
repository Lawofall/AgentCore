"""System prompt assembly for CEO chat and shared worker base.

Composes shared base + optional memory/rules + CEO-only sections
(core, on-demand directory). Skill HOW bodies live in
``runtime.skills`` and are pulled via ``consult``.

Package layout (fragment seams): ``base`` / ``ceo_core`` /
``memory_rules`` + ``compose`` entry + ``envelope``.
Public import path stays ``agentcore.runtime.resolve.prompt``.
"""

from agentcore.runtime.resolve.prompt.base import (
    _DEFAULT_SYSTEM_PROMPT,
    _RUNTIME_CONTEXT_TEMPLATE,
    render_runtime_date_block,
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
from agentcore.runtime.resolve.prompt.compose import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    derive_ceo_addon,
    render_on_demand_directory,
    splice_on_demand_directory,
)
from agentcore.runtime.resolve.prompt.envelope import (
    TURN_ENVELOPE_FENCE,
    opening_ceo_messages,
    render_ceo_turn_envelope,
    render_worker_turn_envelope,
    resolve_emitted_envelope,
    strip_turn_envelope_fence,
    visualization_system_body,
)
from agentcore.runtime.resolve.prompt.in_history import in_history_system_delta
from agentcore.runtime.resolve.prompt.memory_rules import (
    _MEMORY_ROUTING_FENCE,
    _RULES_ROUTING_FENCE,
    _RULES_TEMPLATE,
    _format_rules,
)

__all__ = [
    "TURN_ENVELOPE_FENCE",
    "_ATTACHMENT_MATERIAL_HINT",
    "_CEO_CORE_HINT",
    "_CEO_CORE_HINT_TEMPLATE",
    "_DEFAULT_SYSTEM_PROMPT",
    "_MEMORY_ROUTING_FENCE",
    "_RULES_ROUTING_FENCE",
    "_RULES_TEMPLATE",
    "_RUNTIME_CONTEXT_TEMPLATE",
    "_attachment_material_block",
    "_format_rules",
    "assemble_ceo_core",
    "assemble_system_prompt",
    "attachment_material_scene",
    "capability_how_suffix",
    "compose_ceo_chat_prompt",
    "compose_worker_base_prompt",
    "derive_ceo_addon",
    "in_history_system_delta",
    "opening_ceo_messages",
    "render_ceo_turn_envelope",
    "render_on_demand_directory",
    "render_runtime_date_block",
    "render_worker_turn_envelope",
    "resolve_emitted_envelope",
    "splice_on_demand_directory",
    "strip_turn_envelope_fence",
    "visualization_system_body",
]
