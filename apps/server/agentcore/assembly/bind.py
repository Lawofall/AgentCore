"""Publish one conversation's assembly onto the turn (tools, envelope, plugs, 交代)."""

from __future__ import annotations

from contextvars import ContextVar, Token

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import AssemblySkill, LlmModelProfile

_MCP_IDS: ContextVar[frozenset[str] | None] = ContextVar("assembly_mcp_server_ids", default=None)
_SKILLS: ContextVar[dict[str, str] | None] = ContextVar("assembly_skill_membership", default=None)
_OMIT_DESK: ContextVar[bool] = ContextVar("omit_desk_rules", default=False)


def current_mcp_server_ids() -> frozenset[str] | None:
    """Server ids this turn may register. None means the caller did not bind an assembly."""
    return _MCP_IDS.get()


def current_skill_membership() -> dict[str, str] | None:
    """document id → apply mode. None means this call is not inside an assembly turn."""
    return _SKILLS.get()


def current_omit_desk_rules() -> bool:
    """True when this turn does not read folder rules (blank snapshot)."""
    return _OMIT_DESK.get()


def bind_omit_desk_rules(omitted: bool) -> Token[bool]:
    return _OMIT_DESK.set(bool(omitted))


def reset_omit_desk_rules(token: Token[bool]) -> None:
    _OMIT_DESK.reset(token)


def arm_assembly_bind(
    bind: object | None,
) -> tuple[Token, Token, Token, Token, Token, Token]:
    """Bind a sidecar session's capability surface. None stays unbound (evals)."""
    from agentcore.assembly.recipes import (
        AssemblySurface,
        capability_recipe_for_id,
        surface_from_recipe,
    )
    from agentcore.runtime.context.envelope_switches import normalize_omitted_projections
    from agentcore.runtime.skills.factory_switches import normalize_omitted_factory_skills
    from agentcore.tools.switchboard import normalize_disabled_tools

    if bind is None:
        return _unbound()
    aid = getattr(bind, "assembly_id", None)
    recipe = capability_recipe_for_id(aid) if isinstance(aid, str) else None
    if recipe is not None:
        # Official recipe: id wins. Client lists are not a second copy.
        return _bound(surface_from_recipe(recipe), skills=None, plugs=frozenset())
    surface = AssemblySurface(
        disabled_tools=normalize_disabled_tools(list(getattr(bind, "disabled_tools", ()) or ())),
        omitted_projections=normalize_omitted_projections(
            list(getattr(bind, "omitted_projections", ()) or ())
        ),
        omitted_factory_skills=normalize_omitted_factory_skills(
            list(getattr(bind, "omitted_factory_skills", ()) or ())
        ),
        omit_desk_rules=bool(getattr(bind, "omit_desk_rules", False)),
        recipe=None,
    )
    skills = getattr(bind, "skill_membership", None)
    if skills is None:
        skills = {}
    elif not isinstance(skills, dict):
        skills = {}
    plugs = frozenset(str(item) for item in (getattr(bind, "enabled_mcp_server_ids", ()) or ()))
    return _bound(surface, skills=skills, plugs=plugs)


async def arm_conversation_assembly(
    session: AsyncSession, conv: object
) -> tuple[Token, Token, Token, Token, Token, Token]:
    """Bind the assembly this conversation points at.

    A missing row leaves 交代 and plugs unfiltered (tools and envelope all on),
    unless the id is an official recipe. A real row binds its surface: a recipe
    key wins over the stored lists. Skill membership may be empty.
    """
    from agentcore.assembly.recipes import surface_for

    aid = getattr(conv, "assembly_id", None)
    owner = getattr(conv, "user_id", None)
    if not (isinstance(aid, str) and aid and isinstance(owner, str)):
        return _unbound()
    row = await session.get(LlmModelProfile, aid)
    if row is not None and row.user_id != owner:
        row = None
    if row is None:
        surface = surface_for(aid, None)
        if surface is None:
            return _unbound()
        # Official recipes have no membership row: account 交代 follow the documents.
        return _bound(surface, skills=None, plugs=frozenset())
    result = await session.execute(select(AssemblySkill).where(AssemblySkill.assembly_id == aid))
    skills = {skill.document_id: skill.apply_mode for skill in result.scalars()}
    surface = surface_for(aid, row)
    assert surface is not None
    plugs = frozenset(str(item) for item in (row.enabled_mcp_server_ids or []))
    return _bound(surface, skills=skills, plugs=plugs)


def _bound(
    surface: object,
    *,
    skills: dict[str, str] | None,
    plugs: frozenset[str],
) -> tuple[Token, Token, Token, Token, Token, Token]:
    from agentcore.runtime.context.envelope_switches import bind_envelope_omissions
    from agentcore.runtime.skills.factory_switches import bind_omitted_factory_skills
    from agentcore.tools.switchboard import bind_disabled_tool_groups

    return (
        bind_disabled_tool_groups(list(surface.disabled_tools)),  # type: ignore[attr-defined]
        bind_envelope_omissions(list(surface.omitted_projections)),  # type: ignore[attr-defined]
        _MCP_IDS.set(plugs),
        _SKILLS.set(skills),
        bind_omitted_factory_skills(list(surface.omitted_factory_skills)),  # type: ignore[attr-defined]
        bind_omit_desk_rules(bool(surface.omit_desk_rules)),  # type: ignore[attr-defined]
    )


def _unbound() -> tuple[Token, Token, Token, Token, Token, Token]:
    """No assembly row: tools and envelope stay all-on, and 交代 / plugs are unfiltered."""
    from agentcore.runtime.context.envelope_switches import bind_envelope_omissions
    from agentcore.runtime.skills.factory_switches import bind_omitted_factory_skills
    from agentcore.tools.switchboard import bind_disabled_tool_groups

    return (
        bind_disabled_tool_groups([]),
        bind_envelope_omissions([]),
        _MCP_IDS.set(None),
        _SKILLS.set(None),
        bind_omitted_factory_skills([]),
        bind_omit_desk_rules(False),
    )


def disarm_conversation_assembly(token: object) -> None:
    if token is None:
        return
    from typing import cast

    from agentcore.runtime.context.envelope_switches import reset_envelope_omissions
    from agentcore.runtime.skills.factory_switches import reset_omitted_factory_skills
    from agentcore.tools.switchboard import reset_disabled_tool_groups

    (
        tool_token,
        envelope_token,
        mcp_token,
        skill_token,
        factory_token,
        desk_token,
    ) = cast(
        tuple[
            Token[frozenset[str]],
            Token[frozenset[str]],
            Token[frozenset[str] | None],
            Token[dict[str, str] | None],
            Token[frozenset[str]],
            Token[bool],
        ],
        token,
    )
    reset_disabled_tool_groups(tool_token)
    reset_envelope_omissions(envelope_token)
    _MCP_IDS.reset(mcp_token)
    _SKILLS.reset(skill_token)
    reset_omitted_factory_skills(factory_token)
    reset_omit_desk_rules(desk_token)
