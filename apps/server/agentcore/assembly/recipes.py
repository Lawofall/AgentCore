"""Official capability assemblies.

Three locked recipes. While a row still carries the recipe key, tools and
envelope lines are resolved from the current catalogs, so a switch added later
stays off on 极简 and 轻量 and comes on for 完整. Editing tools, the envelope,
the factory catalog, or plugs clears the key; after that the stored lists are
the record and a new switch defaults on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

CHAT = "chat"
WEB = "web"
FULL = "full"

CAPABILITY_ORDER: tuple[str, ...] = (CHAT, WEB, FULL)

_NS = uuid.NAMESPACE_URL
_PREFIX = "agentcore:capability-preset:"


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
    from agentcore.runtime.context.envelope_switches import RUNTIME_DATE

    return (
        CapabilityRecipe(
            CHAT,
            "极简",
            frozenset(),
            frozenset(),
            omit_factory_catalog=True,
            omit_desk_rules=True,
        ),
        CapabilityRecipe(
            WEB,
            "轻量",
            frozenset({"web"}),
            frozenset({RUNTIME_DATE}),
            omit_factory_catalog=True,
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
    )


def capability_preset_id(key: str) -> str:
    """Stable virtual id for one official recipe."""
    return str(uuid.uuid5(_NS, f"{_PREFIX}{key}"))


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
    omit_factory_catalog: bool
    omit_desk_rules: bool
    recipe: str | None


def surface_from_recipe(recipe: CapabilityRecipe) -> AssemblySurface:
    from agentcore.runtime.context.envelope_switches import (
        all_projection_ids,
        normalize_omitted_projections,
    )
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
        omit_factory_catalog=recipe.omit_factory_catalog,
        omit_desk_rules=recipe.omit_desk_rules,
        recipe=recipe.key,
    )


def surface_from_row(row: object) -> AssemblySurface:
    """Recipe key wins. A row without one uses the lists stored on it."""
    recipe = capability_recipe_for_key(getattr(row, "recipe", None))
    if recipe is not None:
        return surface_from_recipe(recipe)
    from agentcore.runtime.context.envelope_switches import normalize_omitted_projections
    from agentcore.tools.switchboard import normalize_disabled_tools

    raw_omitted = getattr(row, "omitted_projections", ()) or ()
    if not isinstance(raw_omitted, list):
        raw_omitted = list(raw_omitted) if isinstance(raw_omitted, tuple) else []
    return AssemblySurface(
        disabled_tools=normalize_disabled_tools(getattr(row, "disabled_tools", ())),
        omitted_projections=normalize_omitted_projections(raw_omitted),
        omit_factory_catalog=bool(getattr(row, "omit_factory_catalog", False)),
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


def write_recipe(row: object, recipe: CapabilityRecipe) -> None:
    """Stamp the recipe and a matching snapshot onto an owned assembly."""
    surface = surface_from_recipe(recipe)
    row.recipe = recipe.key  # type: ignore[attr-defined]
    row.disabled_tools = list(surface.disabled_tools)  # type: ignore[attr-defined]
    row.omitted_projections = list(surface.omitted_projections)  # type: ignore[attr-defined]
    row.omit_factory_catalog = surface.omit_factory_catalog  # type: ignore[attr-defined]
    row.omit_desk_rules = surface.omit_desk_rules  # type: ignore[attr-defined]
    row.enabled_mcp_server_ids = []  # type: ignore[attr-defined]


def clear_recipe(row: object) -> None:
    """Drop the live recipe. Stored lists become the record."""
    if getattr(row, "recipe", None):
        row.recipe = None  # type: ignore[attr-defined]
