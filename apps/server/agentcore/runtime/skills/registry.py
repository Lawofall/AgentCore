"""SystemSkill dataclass + SkillRegistry (name lookup / catalog filter)."""

from __future__ import annotations

from dataclasses import dataclass

# Same tokens as tools.registration.meta — kept local so skills stay free of tools/.
AUDIENCE_CEO = "ceo"
AUDIENCE_WORKER = "worker"
AUDIENCE_CEO_ONLY: tuple[str, ...] = (AUDIENCE_CEO,)
AUDIENCE_WORKER_ONLY: tuple[str, ...] = (AUDIENCE_WORKER,)
AUDIENCE_BOTH: tuple[str, ...] = (AUDIENCE_CEO, AUDIENCE_WORKER)

# Decision-moment sort for the skill catalog. Empty groups are omitted.
# Labels are not printed in ``<按需目录>``; the summary is the trigger.
GROUP_ORCHESTRATION = "编排"
GROUP_WORKSPACE = "工作区"
GROUP_DELIVERY = "交付"
GROUP_PRODUCT = "产品"
GROUP_TOOLS = "工具"
SKILL_GROUP_ORDER: tuple[str, ...] = (
    GROUP_ORCHESTRATION,
    GROUP_WORKSPACE,
    GROUP_DELIVERY,
    GROUP_PRODUCT,
    GROUP_TOOLS,
)


@dataclass(frozen=True)
class SystemSkill:
    """One code-defined capability doc, surfaced in the catalog and pulled by consult.

    ``summary`` is the consult-directory trigger (when to open, plus one false
    friend). ``blurb`` is the toolbox card description only — never injected
    into ``<按需目录>`` or consult.
    ``body`` is HOW, returned only when ``consult(name)`` is called.
    ``requires_tools`` gates the catalog entry: the skill appears only when every
    named tool is wired this turn (e.g. ``run`` needs the ``run`` tool), so the
    prompt never advertises a capability the CEO cannot act
    on. ``audience`` is who may *see* the entry (CEO vs worker). Default both.
    Directory listing and ``consult`` fetch share this filter — do not advertise
    a name the same source cannot fetch. Not a task-intent classifier.
    ``group`` is the decision-moment sort key (编排 / 工作区 / 交付 / 产品 / 工具).
    The model directory orders by it and does not print the label.
    """

    name: str
    summary: str
    body: str
    requires_tools: tuple[str, ...] = ()
    audience: tuple[str, ...] = AUDIENCE_BOTH
    group: str = ""
    blurb: str = ""


class SkillRegistry:
    """Name → :class:`SystemSkill` lookup (single source of truth, mirrors ToolRegistry)."""

    def __init__(self) -> None:
        self._skills: dict[str, SystemSkill] = {}

    def register(self, skill: SystemSkill) -> None:
        """Register a skill. Raises ValueError if the name is already registered."""
        if skill.name in self._skills:
            raise ValueError(f"Skill '{skill.name}' is already registered")
        self._skills[skill.name] = skill

    def get(self, name: str) -> SystemSkill | None:
        """Resolve a skill by name, or None if unknown (consult degrades on miss)."""
        return self._skills.get(name)

    def list_all(self) -> list[SystemSkill]:
        """Every registered skill (registration order)."""
        return list(self._skills.values())

    def available(
        self, tool_names: set[str], *, audience: str | None = None
    ) -> list[SystemSkill]:
        """Skills whose ``requires_tools`` are wired, optionally narrowed by reader.

        ``audience=None`` keeps the tools-only filter (CEO directory tests).
        Production consult sources pass ``\"ceo\"`` or ``\"worker\"``.
        """
        return [
            skill
            for skill in self._skills.values()
            if all(tool in tool_names for tool in skill.requires_tools)
            and (audience is None or audience in skill.audience)
        ]
