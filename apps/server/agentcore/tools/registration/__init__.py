"""Declarative tool registration — class metadata + surface rosters.

Mirrors the LLM vendor chain (prefix table + settings ⇒ access): a new built-in
tool is **implement class (with ``registration``) + append to the matching
surface roster under ``registration.rosters`` + test**. Runtime registries, the
capability catalog, and board / zero-arg ALWAYS wiring **collect** from
declarations instead of maintaining parallel hand lists.

CEO orchestration tools with heavy ``__init__`` deps (delegate / debate / ask_user
/ memory gates / coordination) are still constructed in
``tools.ceo_toolset._assemble_ceo_toolset`` / coordination surface, but **which**
tools exist and their audience / wire gate come from ``ToolRegistration`` — not
a second tuple in ``catalog.py``. Zero/light-arg ALWAYS tools share
``register_always_ceo_tools`` (same entry as assemble + resume).

Roster files (append here when adding a tool class)::

    registration/rosters/builtin.py
    registration/rosters/worker_only.py
    registration/rosters/ceo_orchestration.py

Authoritative order = concat of those three ``load_roster()`` results (builtin →
worker_only → ceo_orchestration). That order is the public surface (registry /
catalog / OpenAI defs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.tools.registration.meta import (
    AUDIENCE_BOTH,
    AUDIENCE_CEO,
    AUDIENCE_CEO_ONLY,
    AUDIENCE_WORKER,
    AUDIENCE_WORKER_ONLY,
    CeoWire,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
    declared_tool_name,
    declared_tool_schema,
    instantiate_declared,
    read_static_schema,
    tool_registration,
)

if TYPE_CHECKING:
    from agentcore.tools.registry import ToolRegistry

__all__ = [
    "AUDIENCE_BOTH",
    "AUDIENCE_CEO",
    "AUDIENCE_CEO_ONLY",
    "AUDIENCE_WORKER",
    "AUDIENCE_WORKER_ONLY",
    "CeoWire",
    "FileProductsContract",
    "ToolRegistration",
    "ToolSurface",
    "declared_tool_name",
    "declared_tool_names",
    "declared_tool_schema",
    "declared_tools",
    "execution_class_tool_names",
    "host_class_tool_names",
    "instantiate_declared",
    "read_static_schema",
    "register_always_ceo_tools",
    "register_board_ceo_tools",
    "tool_registration",
    "worker_only_tool_names",
]


def _load_declared_tools() -> tuple[type, ...]:
    """Import tool classes and return the ordered declaration roster.

    Order is part of the public surface (registry / catalog / OpenAI defs).
    Implemented as concat of per-surface rosters (see ``rosters``).
    """
    from agentcore.tools.registration.rosters import load_all_declared_tools

    return load_all_declared_tools()


# Lazy so importing ``registration`` from a tool module during class body does not
# recurse through every tool import. Populated on first ``declared_tools()`` call.
_DECLARED_TOOLS: tuple[type, ...] | None = None


def declared_tools(*, surface: ToolSurface | None = None) -> tuple[type, ...]:
    global _DECLARED_TOOLS
    if _DECLARED_TOOLS is None:
        _DECLARED_TOOLS = _load_declared_tools()
    if surface is None:
        return _DECLARED_TOOLS
    return tuple(cls for cls in _DECLARED_TOOLS if tool_registration(cls).surface is surface)


def execution_class_tool_names() -> frozenset[str]:
    """Tools flagged ``execution_class`` (code_execute / test_run / terminal / browser_*)."""
    return frozenset(
        declared_tool_name(cls)
        for cls in declared_tools()
        if tool_registration(cls).execution_class
    )


def host_class_tool_names() -> frozenset[str]:
    """Tools flagged ``host_class`` (Host face; not execution_class / not kickoff)."""
    return frozenset(
        declared_tool_name(cls)
        for cls in declared_tools()
        if tool_registration(cls).host_class
    )


def declared_tool_names() -> frozenset[str]:
    """Every name on the declaration roster (any surface / audience)."""
    return frozenset(declared_tool_name(cls) for cls in declared_tools())


def worker_only_tool_names() -> frozenset[str]:
    """Tools whose audience excludes CEO (write / execute / worker orchestration)."""
    return frozenset(
        declared_tool_name(cls)
        for cls in declared_tools()
        if AUDIENCE_CEO not in tool_registration(cls).audience
    )


# Heavy-dep ALWAYS tools stay handwritten in ``_assemble_ceo_toolset``.
# Everything else with ``ceo_wire=ALWAYS`` is declaration-loop wired
# (zero-arg via ``instantiate_declared``).
_ALWAYS_HAND_WIRE_NAMES = frozenset({"delegate", "debate"})


def register_always_ceo_tools(
    chat_tools: ToolRegistry,
    *,
    skill_registry: Any,
    include_vision: bool = True,
) -> None:
    """Register zero/light-arg CEO ALWAYS tools — shared by assemble + resume.

    Consumed only from ``tools.ceo_toolset._assemble_ceo_toolset`` so fresh turn
    and 2b resume cannot diverge. Skips ``delegate`` / ``debate`` (heavy deps).
    ``skill_registry`` is retained for call-site compatibility (consult no longer
    takes it here — merged consult is hand-wired with has_entries).

    ``include_vision`` gates ``read_image`` the same way a dead local channel
    retires it (``WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS``): catalog still lists the
    tool; a surface that cannot succeed only invites failed rounds.
    """
    del skill_registry  # consult wiring moved; keep kwarg so callers need not change
    for cls in declared_tools(surface=ToolSurface.CEO_ORCHESTRATION):
        if tool_registration(cls).ceo_wire is not CeoWire.ALWAYS:
            continue
        name = declared_tool_name(cls)
        if name in _ALWAYS_HAND_WIRE_NAMES:
            continue
        if name == "read_image" and not include_vision:
            continue
        chat_tools.register(instantiate_declared(cls))


def register_board_ceo_tools(chat_tools: ToolRegistry) -> None:
    """Register CEO board tools (``ceo_wire=BOARD``) — shared by assemble + resume."""
    for cls in declared_tools(surface=ToolSurface.CEO_ORCHESTRATION):
        if tool_registration(cls).ceo_wire is CeoWire.BOARD:
            chat_tools.register(instantiate_declared(cls))
