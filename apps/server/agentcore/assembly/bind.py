"""Publish one conversation's assembly onto the turn (tools, envelope, plugs, 交代)."""

from __future__ import annotations

from contextvars import ContextVar, Token

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import AssemblySkill, LlmModelProfile

_MCP_IDS: ContextVar[frozenset[str] | None] = ContextVar(
    "assembly_mcp_server_ids", default=None
)
_SKILLS: ContextVar[dict[str, str] | None] = ContextVar(
    "assembly_skill_membership", default=None
)
_OMIT_FACTORY: ContextVar[bool] = ContextVar(
    "omit_factory_catalog", default=False
)
_OMIT_DESK: ContextVar[bool] = ContextVar(
    "omit_desk_rules", default=False
)


def current_mcp_server_ids() -> frozenset[str] | None:
    """Server ids this turn may register. None means the caller did not bind an assembly."""
    return _MCP_IDS.get()


def current_skill_membership() -> dict[str, str] | None:
    """document id → apply mode. None means this call is not inside an assembly turn."""
    return _SKILLS.get()


def current_omit_factory_catalog() -> bool:
    """True when this turn's assembly leaves the three factory skill rows off."""
    return _OMIT_FACTORY.get()


def bind_omit_factory_catalog(omitted: bool) -> Token[bool]:
    return _OMIT_FACTORY.set(bool(omitted))


def reset_omit_factory_catalog(token: Token[bool]) -> None:
    _OMIT_FACTORY.reset(token)


def current_omit_desk_rules() -> bool:
    """True when this turn does not read folder rules (极简)."""
    return _OMIT_DESK.get()


def bind_omit_desk_rules(omitted: bool) -> Token[bool]:
    return _OMIT_DESK.set(bool(omitted))


def reset_omit_desk_rules(token: Token[bool]) -> None:
    _OMIT_DESK.reset(token)


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
        return _bound(surface, skills={}, plugs=frozenset())
    result = await session.execute(
        select(AssemblySkill).where(AssemblySkill.assembly_id == aid)
    )
    skills = {skill.document_id: skill.apply_mode for skill in result.scalars()}
    surface = surface_for(aid, row)
    assert surface is not None
    plugs = frozenset(str(item) for item in (row.enabled_mcp_server_ids or []))
    return _bound(surface, skills=skills, plugs=plugs)


def _bound(
    surface: object,
    *,
    skills: dict[str, str],
    plugs: frozenset[str],
) -> tuple[Token, Token, Token, Token, Token, Token]:
    from agentcore.runtime.context.envelope_switches import bind_envelope_omissions
    from agentcore.tools.switchboard import bind_disabled_tool_groups

    return (
        bind_disabled_tool_groups(list(surface.disabled_tools)),  # type: ignore[attr-defined]
        bind_envelope_omissions(list(surface.omitted_projections)),  # type: ignore[attr-defined]
        _MCP_IDS.set(plugs),
        _SKILLS.set(skills),
        bind_omit_factory_catalog(bool(surface.omit_factory_catalog)),  # type: ignore[attr-defined]
        bind_omit_desk_rules(bool(surface.omit_desk_rules)),  # type: ignore[attr-defined]
    )


def _unbound() -> tuple[Token, Token, Token, Token, Token, Token]:
    """No assembly row: tools and envelope stay all-on, and 交代 / plugs are unfiltered."""
    from agentcore.runtime.context.envelope_switches import bind_envelope_omissions
    from agentcore.tools.switchboard import bind_disabled_tool_groups

    return (
        bind_disabled_tool_groups([]),
        bind_envelope_omissions([]),
        _MCP_IDS.set(None),
        _SKILLS.set(None),
        bind_omit_factory_catalog(False),
        bind_omit_desk_rules(False),
    )


def disarm_conversation_assembly(token: object) -> None:
    if token is None:
        return
    from typing import cast

    from agentcore.runtime.context.envelope_switches import reset_envelope_omissions
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
            Token[bool],
            Token[bool],
        ],
        token,
    )
    reset_disabled_tool_groups(tool_token)
    reset_envelope_omissions(envelope_token)
    _MCP_IDS.reset(mcp_token)
    _SKILLS.reset(skill_token)
    reset_omit_factory_catalog(factory_token)
    reset_omit_desk_rules(desk_token)
