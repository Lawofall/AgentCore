"""Official capabilities: locked 标准, 完整, and 极简 recipes.

Conversations may pin a virtual recipe id; that is not a copy. User assemblies
are snapshots of 标准. A switch added later stays off on 标准 until it is
listed in STANDARD_TOOLS, stays off on 极简, and comes on for 完整. Official
recipes cannot be written.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

STANDARD = "standard"
FULL = "full"
CHAT = "chat"

CAPABILITY_ORDER: tuple[str, ...] = (STANDARD, FULL, CHAT)

# Daily working set. New switches stay off here until they are added.
STANDARD_TOOLS: frozenset[str] = frozenset(
    {
        "files",
        "run",
        "web",
        "ask_user",
        "delegate",
        "host",
        "look",
    }
)

_NS = uuid.NAMESPACE_URL
_PREFIX = "agentcore:capability-preset:"

OFFICIAL_READONLY = "这份不能改。要自己关工具，点新建。"


@dataclass(frozen=True, slots=True)
class CapabilityRecipe:
    """One official way of working. ``allowed_tools`` / ``keep_projections`` None = all on."""

    key: str
    name: str
    allowed_tools: frozenset[str] | None
    keep_projections: frozenset[str] | None
    omit_factory_catalog: bool
    omit_desk_rules: bool


def _recipes() -> tuple[CapabilityRecipe, ...]:
    return (
        CapabilityRecipe(
            STANDARD,
            "标准",
            STANDARD_TOOLS,
            None,
            omit_factory_catalog=False,
            omit_desk_rules=False,
        ),
        CapabilityRecipe(
            FULL,
            "完整",
            None,
            None,
            omit_factory_catalog=False,
            omit_desk_rules=False,
        ),
        CapabilityRecipe(
            CHAT,
            "极简",
            frozenset(),
            frozenset(),
            omit_factory_catalog=True,
            omit_desk_rules=True,
        ),
    )


def capability_preset_id(key: str) -> str:
    """Stable virtual id for one official recipe."""
    return str(uuid.uuid5(_NS, f"{_PREFIX}{key}"))


def factory_default_id() -> str:
    """Virtual id of the factory fallback recipe (标准)."""
    return capability_preset_id(STANDARD)


def capability_recipe_for_key(key: object) -> CapabilityRecipe | None:
    if not isinstance(key, str):
        return None
    for recipe in _recipes():
        if recipe.key == key:
            return recipe
    return None


def capability_recipe_for_id(profile_id: str | None) -> CapabilityRecipe | None:
    if not profile_id:
        return None
    for recipe in _recipes():
        if capability_preset_id(recipe.key) == profile_id:
            return recipe
    return None


@dataclass(frozen=True, slots=True)
class AssemblySurface:
    """What this turn actually turns on, after a recipe wins over stored lists."""

    disabled_tools: tuple[str, ...]
    omitted_projections: tuple[str, ...]
    omitted_factory_skills: tuple[str, ...]
    omit_desk_rules: bool
    recipe: str | None

    @property
    def omit_factory_catalog(self) -> bool:
        """True when every current official HOW name is on the omit list."""
        from agentcore.runtime.skills.factory_switches import all_factory_skill_names

        names = all_factory_skill_names()
        omitted = set(self.omitted_factory_skills)
        return bool(names) and all(name in omitted for name in names)


def surface_from_recipe(recipe: CapabilityRecipe) -> AssemblySurface:
    from agentcore.runtime.context.envelope_switches import (
        all_projection_ids,
        normalize_omitted_projections,
    )
    from agentcore.runtime.skills.factory_switches import omitted_factory_skills_from_flag
    from agentcore.tools.switchboard import all_switch_ids, normalize_disabled_tools

    if recipe.allowed_tools is None:
        disabled: tuple[str, ...] = ()
    else:
        disabled = normalize_disabled_tools(
            [switch_id for switch_id in all_switch_ids() if switch_id not in recipe.allowed_tools]
        )
    if recipe.keep_projections is None:
        omitted: tuple[str, ...] = ()
    else:
        omitted = normalize_omitted_projections(
            [
                projection_id
                for projection_id in all_projection_ids()
                if projection_id not in recipe.keep_projections
            ]
        )
    return AssemblySurface(
        disabled_tools=disabled,
        omitted_projections=omitted,
        omitted_factory_skills=omitted_factory_skills_from_flag(recipe.omit_factory_catalog),
        omit_desk_rules=recipe.omit_desk_rules,
        recipe=recipe.key,
    )


def surface_from_row(row: object) -> AssemblySurface:
    """Recipe key wins. A row without one uses the lists stored on it."""
    recipe = capability_recipe_for_key(getattr(row, "recipe", None))
    if recipe is not None:
        return surface_from_recipe(recipe)
    from agentcore.runtime.context.envelope_switches import normalize_omitted_projections
    from agentcore.runtime.skills.factory_switches import (
        normalize_omitted_factory_skills,
    )
    from agentcore.tools.switchboard import normalize_disabled_tools

    raw_omitted = getattr(row, "omitted_projections", ()) or ()
    if not isinstance(raw_omitted, list):
        raw_omitted = list(raw_omitted) if isinstance(raw_omitted, tuple) else []
    raw_factory = getattr(row, "omitted_factory_skills", ()) or ()
    if not isinstance(raw_factory, list):
        raw_factory = list(raw_factory) if isinstance(raw_factory, tuple) else []
    return AssemblySurface(
        disabled_tools=normalize_disabled_tools(getattr(row, "disabled_tools", ())),
        omitted_projections=normalize_omitted_projections(raw_omitted),
        omitted_factory_skills=normalize_omitted_factory_skills(raw_factory),
        omit_desk_rules=bool(getattr(row, "omit_desk_rules", False)),
        recipe=None,
    )


def surface_for(assembly_id: str | None, row: object | None) -> AssemblySurface | None:
    """Row first. A virtual preset id with no row still has a surface."""
    if row is not None:
        return surface_from_row(row)
    recipe = capability_recipe_for_id(assembly_id)
    if recipe is None:
        return None
    return surface_from_recipe(recipe)


def clear_recipe(row: object) -> None:
    """Drop the live recipe. Stored lists become the record."""
    if getattr(row, "recipe", None):
        row.recipe = None  # type: ignore[attr-defined]
