"""Consultable adapters + merge for the unified ``consult`` tool (步 1 · 按需三合一).

Four sources (skill / on-demand tool / rule / memory) each implement :class:`Consultable`.
:class:`MergedConsultSource` is the **single** source shared by prompt ``<按需目录>``
and tool ``fetch_by_name`` — directory listing and name resolution cannot drift.

On-demand **tools** ride this directory without sharing a Tool base class: they stay
on the registry (execute / catalog / permission axes); ``consult`` only promotes them
onto the OpenAI table. Namespace priority on collision: skill → tool → rule → memory.
Shadowed names log ``consult.name_shadowed``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import DocumentRepository
from agentcore.memory.rules_injection import (
    lookup_on_demand_rule_body_from_cloud,
    rule_consult_name,
)
from agentcore.memory.store import TOPIC_DIR, MemoryStore, topic_path
from agentcore.runtime.context.consultable import ConsultDirectoryEntry
from agentcore.runtime.skills.registry import SkillRegistry

logger = get_logger(__name__)

# Fixed resolve order (winner first). Do not reorder without a product decision.
_SOURCE_PRIORITY: tuple[str, ...] = ("skill", "tool", "rule", "memory")


@dataclass
class SkillConsultSource:
    """System skills filtered by the caller's live tool names and reader role.

    ``audience`` is ``\"ceo\"`` / ``\"worker\"`` in production so listing and fetch
    cannot advertise a CEO-only manual to a worker. ``None`` keeps the tools-only
    filter (unit tests that exercise CEO hits without a wire path).
    """

    registry: SkillRegistry
    tool_names: Collection[str]
    audience: str | None = None

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        del user_id  # skills are code-defined, not per-user
        names = set(self.tool_names)
        return [
            ConsultDirectoryEntry(name=s.name, summary=s.summary)
            for s in self.registry.available(names, audience=self.audience)
        ]

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        del user_id
        key = name.strip()
        if not key:
            return None
        names = set(self.tool_names)
        for skill in self.registry.available(names, audience=self.audience):
            if skill.name == key:
                return skill.body
        return None


@dataclass
class ToolConsultSource:
    """Registered on-demand tools: directory row + consult promotes the family.

    ``registry`` is the live CEO/worker toolset for this turn. Listing only includes
    tools that are actually assembled (host withheld → no host_* rows). Fetch offers
    the family so the next LLM round sees the OpenAI schemas.
    """

    registry: Any
    audience: str | None = None

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        del user_id
        from agentcore.tools.on_demand import is_on_demand_tool, on_demand_summary

        entries: list[ConsultDirectoryEntry] = []
        for name in self.registry.names:
            if not is_on_demand_tool(name):
                continue
            tool = self.registry.get_optional(name)
            description = tool.schema.description if tool is not None else ""
            entries.append(
                ConsultDirectoryEntry(
                    name=name,
                    summary=on_demand_summary(name, description=description),
                )
            )
        return entries

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        del user_id
        from agentcore.tools.on_demand import (
            family_of,
            is_on_demand_tool,
            render_tool_consult_body,
        )

        key = name.strip()
        if not key or not is_on_demand_tool(key):
            return None
        if self.registry.get_optional(key) is None:
            return None
        self.registry.offer(key)
        enabled = [
            n
            for n in self.registry.names
            if n in family_of(key, registry=self.registry)
            and n not in self.registry.deferred_names
        ]
        tool = self.registry.get(key)
        return render_tool_consult_body(
            key,
            description=tool.schema.description,
            audience=self.audience,
            enabled=enabled,
        )


@dataclass
class MemoryConsultSource:
    """On-demand TOPIC notes (``主题/<slug>.md``); nearest-folder-then-global resolve."""

    store: MemoryStore
    folder_id: str | None = None
    enabled: bool = True

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        if not self.enabled:
            return ()
        from agentcore.memory.injection import load_memory_topics

        topics = await load_memory_topics(
            self.store, user_id, folder_id=self.folder_id, enabled=True
        )
        return [ConsultDirectoryEntry(name=t.name, summary=t.summary) for t in topics]

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        """Current folder → ancestors innermost-first → global (§5.4 近的覆盖远的)."""
        if not self.enabled:
            return None
        slug = _memory_slug(name)
        if not slug:
            return None
        from agentcore.memory.scope_chain import resolve_scope_chain

        chain = await resolve_scope_chain(user_id, self.folder_id)
        for scope in reversed(chain):
            body = await self.store.load(user_id, topic_path(slug), scope=scope)
            if body.strip():
                return body
        body = await self.store.load(user_id, topic_path(slug))
        return body if body.strip() else None


@dataclass
class RuleConsultSource:
    """On-demand user rules; nearest-folder-then-global resolve (cloud list or local DB)."""

    folder_id: str | None = None

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        from agentcore.memory.rules_injection import load_on_demand_user_rules

        rules = await load_on_demand_user_rules(user_id, folder_id=self.folder_id)
        return [ConsultDirectoryEntry(name=r.name, summary=r.summary) for r in rules]

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        key = rule_consult_name(name)
        if not key:
            return None
        try:
            payload = await self._cloud_rules_payload()
            if payload is not None:
                return lookup_on_demand_rule_body_from_cloud(
                    payload, folder_id=self.folder_id, name=key
                )
            async with async_session_factory() as session:
                from agentcore.memory.scope_chain import db_scope_chain

                repo = DocumentRepository(session)
                chain = await db_scope_chain(user_id, self.folder_id, session=session)
                for scope in reversed(chain):
                    body = await self._load_named(repo, user_id, scope, key)
                    if body is not None:
                        return body
                return await self._load_named(repo, user_id, None, key)
        except Exception as e:  # noqa: BLE001 — never break consult over rules IO
            logger.warning(
                "consult.rule_fetch_failed", user_id=user_id, name=key, error=str(e)
            )
            return None

    async def _cloud_rules_payload(self) -> Mapping[str, object] | None:
        from agentcore.account.credentials import (
            cloud_list_user_rules,
            get_account_credentials,
        )

        creds = get_account_credentials()
        if creds is None:
            return None
        return await cloud_list_user_rules(creds, folder_id=self.folder_id)

    @staticmethod
    async def _load_named(
        repo: DocumentRepository, user_id: str, folder_id: str | None, key: str
    ) -> str | None:
        for doc in await repo.list_on_demand_user_rules(user_id, folder_id):
            if rule_consult_name(doc.name) == key:
                body = doc.content or ""
                return body if body.strip() else None
        return None


@dataclass
class MergedConsultSource:
    """Skill → tool → rule → memory merge; prompt directory and fetch share this instance."""

    skill: SkillConsultSource | None = None
    tool: ToolConsultSource | None = None
    rule: RuleConsultSource | None = None
    memory: MemoryConsultSource | None = None

    def _iters(self) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for kind in _SOURCE_PRIORITY:
            src = getattr(self, kind)
            if src is not None:
                out.append((kind, src))
        return out

    async def list_directory(self, user_id: str) -> Sequence[ConsultDirectoryEntry]:
        ordered: list[ConsultDirectoryEntry] = []
        winners: dict[str, str] = {}
        for kind, src in self._iters():
            for entry in await src.list_directory(user_id):
                if entry.name in winners:
                    logger.warning(
                        "consult.name_shadowed",
                        name=entry.name,
                        winner=winners[entry.name],
                        shadowed=kind,
                    )
                    continue
                winners[entry.name] = kind
                ordered.append(entry)
        return ordered

    async def fetch_by_name(self, user_id: str, name: str) -> str | None:
        raw = name.strip()
        if not raw:
            return None
        # Try each source with its own normalization; first hit wins (priority order).
        # ``kind`` is logged here because this is the only place it is known — it must not
        # reach the model or the UI: the three-way split lives in storage, not in reading.
        for kind, src in self._iters():
            body = await src.fetch_by_name(user_id, raw)
            if body is not None:
                logger.info("consult.hit", name=raw, kind=kind)
                return body
        return None


def build_merged_consult_source(
    *,
    skill_registry: SkillRegistry | None,
    tool_names: Collection[str],
    memory_store: MemoryStore | None,
    folder_id: str | None,
    memory_enabled: bool = True,
    include_rules: bool = True,
    skill_audience: str | None = None,
    tool_registry: Any | None = None,
) -> MergedConsultSource:
    """Assemble the turn's unified consult source (CEO or worker)."""
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
    memory = (
        MemoryConsultSource(
            store=memory_store, folder_id=folder_id, enabled=memory_enabled
        )
        if memory_store is not None
        else None
    )
    rule = RuleConsultSource(folder_id=folder_id) if include_rules else None
    return MergedConsultSource(skill=skill, tool=tool, rule=rule, memory=memory)


def _memory_slug(raw: str) -> str:
    return raw.removeprefix(f"{TOPIC_DIR}/").removesuffix(".md").strip()
