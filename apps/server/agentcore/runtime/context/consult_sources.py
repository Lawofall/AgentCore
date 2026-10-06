"""Consultable adapters + merge for the unified ``consult`` tool.

Three sources (skill / on-demand tool / rule) each implement listing + fetch.
:class:`MergedConsultSource` is the **single** source shared by prompt ``<按需目录>``
and tool ``fetch_by_name`` — directory listing and name resolution cannot drift.

Namespace priority on collision: skill → tool → rule.
Shadowed names log ``consult.name_shadowed``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from agentcore.core.logging import get_logger
from agentcore.memory.rules_injection import rule_consult_name
from agentcore.runtime.context.consultable import ConsultDirectoryEntry
from agentcore.runtime.skills.product_help import (
    PRODUCT_HELP_NAME,
    PRODUCT_HELP_SECTION_SEP,
    fetch_product_help_section,
)
from agentcore.runtime.skills.registry import SkillRegistry

logger = get_logger(__name__)

# Fixed resolve order (winner first). Do not reorder without a product decision.
_SOURCE_PRIORITY: tuple[str, ...] = ("skill", "tool", "rule")
ConsultOrigin = Literal["system", "user"]
_ORIGIN_BY_KIND: dict[str, ConsultOrigin] = {
    "skill": "system",
    "tool": "system",
    "rule": "user",
}


@dataclass(frozen=True)
class ConsultHit:
    """Merged fetch for ConsultTool: model-facing body + two-bucket display origin."""

    body: str
    origin: ConsultOrigin


@dataclass
class SkillConsultSource:
    """System skills filtered by the caller's live tool names and reader role.

    ``audience`` is ``\"ceo\"`` / ``\"worker\"`` in production so listing and fetch
    cannot advertise a CEO-only manual to a worker. ``None`` keeps the tools-only
    filter (unit tests that exercise CEO hits without a wire path).

    Official HOW is factory-only. Unbound same-name user rules stay shadowed
    by this source.
    """

    registry: SkillRegistry
    tool_names: Collection[str]
    audience: str | None = None

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        del user_id
        from agentcore.assembly.bind import current_omit_factory_catalog

        if current_omit_factory_catalog():
            return []
        names = set(self.tool_names)
        return [
            ConsultDirectoryEntry(
                name=skill.name,
                summary=skill.summary,
                section="skill",
                group=skill.group,
            )
            for skill in self.registry.available(names, audience=self.audience)
        ]

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        del user_id
        from agentcore.assembly.bind import current_omit_factory_catalog

        if current_omit_factory_catalog():
            return None
        key = name.strip()
        if not key:
            return None
        names = set(self.tool_names)
        prefix = PRODUCT_HELP_NAME + PRODUCT_HELP_SECTION_SEP
        if key.startswith(prefix):
            skill = self.registry.get(PRODUCT_HELP_NAME)
            if skill is None:
                return None
            if skill not in self.registry.available(names, audience=self.audience):
                return None
            return fetch_product_help_section(key[len(prefix) :])
        for skill in self.registry.available(names, audience=self.audience):
            if skill.name == key:
                return skill.body
        return None


@dataclass
class ToolConsultSource:
    """HOW-bearing assembled tools: directory row + consult body, never a gate.

    ``registry`` is the live CEO/worker toolset for this turn. Listing only includes
    tools that are assembled **and** have a consult HOW (none today). MCP and
    other on-demand names stay on the FC table but off this catalog.
    """

    registry: Any
    audience: str | None = None

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        del user_id
        from agentcore.tools.on_demand import (
            family_catalog_meta,
            has_consult_how,
            is_mcp_tool_name,
            is_on_demand_tool,
            on_demand_face,
            on_demand_summary,
        )

        entries: list[ConsultDirectoryEntry] = []
        for name in self.registry.names:
            if not is_on_demand_tool(name) or not has_consult_how(name):
                continue
            tool = self.registry.get_optional(name)
            description = tool.schema.description if tool is not None else ""
            family, family_label = family_catalog_meta(name)
            if is_mcp_tool_name(name) and tool is not None:
                family = str(getattr(tool, "mcp_server_id", "") or "")
                server_label = str(getattr(tool, "mcp_server_name", "") or "").strip()
                family_label = f"MCP · {server_label}" if server_label else family
            entries.append(
                ConsultDirectoryEntry(
                    name=name,
                    summary=on_demand_summary(name, description=description),
                    section="tool",
                    family=family,
                    family_label=family_label,
                    face="" if is_mcp_tool_name(name) else on_demand_face(name),
                )
            )
        return entries

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        del user_id
        from agentcore.tools.on_demand import (
            family_of,
            has_consult_how,
            is_on_demand_tool,
            render_tool_consult_body,
            resolve_on_demand_name,
        )

        key = name.strip()
        if not key:
            return None
        resolved = resolve_on_demand_name(self.registry, key)
        if resolved is None or not is_on_demand_tool(resolved):
            return None
        if self.registry.get_optional(resolved) is None:
            return None
        key = resolved
        if not has_consult_how(key):
            return None
        enabled = [
            n
            for n in self.registry.names
            if n in family_of(key, registry=self.registry)
        ]
        tool = self.registry.get(key)
        return render_tool_consult_body(
            key,
            description=tool.schema.description,
            audience=self.audience,
            enabled=enabled,
        )


@dataclass
class RuleConsultSource:
    """On-demand user rules; nearest-folder-then-global resolve.

    Ticketed sidecar turns share the prepare snapshot with the directory
    (``load_on_demand_user_rules``): miss → empty, no live ``/rules/list``.
    Unticketed turns read the local document session.
    """

    folder_id: str | None = None
    skip_names: Collection[str] = field(default_factory=frozenset)

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        from agentcore.memory.rules_injection import load_on_demand_user_rules

        skip = set(self.skip_names)
        rules = await load_on_demand_user_rules(user_id, folder_id=self.folder_id)
        return [
            ConsultDirectoryEntry(name=r.name, summary=r.summary, section="rule")
            for r in rules
            if r.name not in skip
        ]

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        from agentcore.memory.rules_injection import lookup_on_demand_rule_body

        key = rule_consult_name(name)
        if not key or key in set(self.skip_names):
            return None
        return await lookup_on_demand_rule_body(
            user_id, folder_id=self.folder_id, name=key
        )


@dataclass
class MergedConsultSource:
    """Skill → tool → rule merge; prompt directory and fetch share this instance.

    Ticketed rule listing and body lookup both read the prepare snapshot — same
    payload, not a live cloud list on fetch.
    """

    skill: SkillConsultSource | None = None
    tool: ToolConsultSource | None = None
    rule: RuleConsultSource | None = None

    def _iters(self) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for kind in _SOURCE_PRIORITY:
            src = getattr(self, kind)
            if src is not None:
                out.append((kind, src))
        return out

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        pairs = self._iters()
        listed = await asyncio.gather(*(src.list_directory(user_id) for _, src in pairs))
        ordered: list[ConsultDirectoryEntry] = []
        winners: dict[str, str] = {}
        for (kind, _), entries in zip(pairs, listed, strict=True):
            for entry in entries:
                if entry.name in winners:
                    logger.warning(
                        "consult.name_shadowed",
                        name=entry.name,
                        winner=winners[entry.name],
                        shadowed=kind,
                    )
                    continue
                winners[entry.name] = kind
                ordered.append(
                    ConsultDirectoryEntry(
                        name=entry.name,
                        summary=entry.summary,
                        section=kind,
                        family=entry.family,
                        family_label=entry.family_label,
                        face=entry.face,
                        group=entry.group,
                    )
                )
        return ordered

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        hit = await self.fetch_hit(user_id, name)
        return None if hit is None else hit.body

    async def fetch_hit(self, user_id: str, name: str) -> ConsultHit | None:
        """Resolve name → body + two-bucket origin. Fine ``kind`` stays log-only."""
        raw = name.strip()
        if not raw:
            return None
        # First hit wins (priority order). Fine kind (skill/tool/rule) is
        # logged here — it must not reach the model or ``display``. Display only
        # gets two-bucket ``origin`` (system | user), computed at this same site.
        for kind, src in self._iters():
            body = await src.fetch_by_name(user_id, raw)
            if body is not None:
                origin = _ORIGIN_BY_KIND[kind]
                logger.info("consult.hit", name=raw, kind=kind, origin=origin)
                if kind == "rule":
                    body = _strip_rule_consult_frontmatter(body)
                return ConsultHit(body=body, origin=origin)
        return None


def expand_skill_tool_names(
    source: MergedConsultSource, extra_tools: Collection[str]
) -> MergedConsultSource:
    """Copy a merged source with extra names on the skill filter (nested ``delegate``).

    Does not mutate the original — leaf workers share the prepare-time source.
    Rule / on-demand-tool slices stay as-is.
    """
    skill = source.skill
    if skill is None or not extra_tools:
        return source
    return MergedConsultSource(
        skill=SkillConsultSource(
            registry=skill.registry,
            tool_names=set(skill.tool_names) | set(extra_tools),
            audience=skill.audience,
        ),
        tool=source.tool,
        rule=source.rule,
    )


def build_merged_consult_source(
    *,
    skill_registry: SkillRegistry | None,
    tool_names: Collection[str],
    memory_store: object | None,
    folder_id: str | None,
    include_rules: bool = True,
    skill_audience: str | None = None,
    tool_registry: Any | None = None,
    skip_rule_names: Collection[str] | None = None,
) -> MergedConsultSource:
    """Assemble the turn's unified consult source (CEO or worker)."""
    del memory_store
    skill = (
        SkillConsultSource(
            registry=skill_registry,
            tool_names=tool_names,
            audience=skill_audience,
        )
        if skill_registry is not None
        else None
    )
    tool = (
        ToolConsultSource(registry=tool_registry, audience=skill_audience)
        if tool_registry is not None
        else None
    )
    rule = (
        RuleConsultSource(
            folder_id=folder_id, skip_names=skip_rule_names or frozenset()
        )
        if include_rules
        else None
    )
    return MergedConsultSource(skill=skill, tool=tool, rule=rule)


async def build_merged_consult_source_for_user(
    *,
    user_id: str,
    skill_registry: SkillRegistry | None,
    tool_names: Collection[str],
    memory_store: object | None,
    folder_id: str | None,
    include_rules: bool = True,
    skill_audience: str | None = None,
    tool_registry: Any | None = None,
) -> MergedConsultSource:
    """Same as :func:`build_merged_consult_source`. Official HOW is factory-only."""
    del user_id
    return build_merged_consult_source(
        skill_registry=skill_registry,
        tool_names=tool_names,
        memory_store=memory_store,
        folder_id=folder_id,
        include_rules=include_rules,
        skill_audience=skill_audience,
        tool_registry=tool_registry,
    )


def _strip_rule_consult_frontmatter(body: str) -> str:
    """Peel entry frontmatter so consult does not leak apply / description keys."""
    from agentcore.documents.frontmatter import strip_entry_frontmatter

    stripped = strip_entry_frontmatter(body)
    return body if stripped is None else stripped
