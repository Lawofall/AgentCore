"""Thin registry assembly + 按需目录 rendering for system skills."""

from __future__ import annotations

from collections.abc import Collection

from agentcore.runtime.skills.ask_user import _ASKING_THE_USER
from agentcore.runtime.skills.data_file_landing import _DATA_FILE_LANDING
from agentcore.runtime.skills.debate_and_review import _DEBATE_AND_REVIEW
from agentcore.runtime.skills.lead_subteam import _LEAD_SUBTEAM
from agentcore.runtime.skills.long_form_writing import _LONG_FORM_LANDING
from agentcore.runtime.skills.product_help import _PRODUCT_HELP
from agentcore.runtime.skills.registry import (
    AUDIENCE_CEO_ONLY,
    AUDIENCE_WORKER_ONLY,
    SkillRegistry,
    SystemSkill,
)
from agentcore.runtime.skills.run import _RUN
from agentcore.runtime.skills.team_cross_folder import _TEAM_CROSS_FOLDER
from agentcore.runtime.skills.team_delivery_env import _TEAM_DELIVERY_ENV
from agentcore.runtime.skills.team_local_desk import _TEAM_LOCAL_DESK
from agentcore.runtime.skills.team_orchestration import (
    _TEAM_ORCHESTRATION_ADVANCED,
)

# --- The system skills (single source of truth) -----------------------------
# Catalog summaries: name-like (what this is), not a 19-way scene classifier.
# Python len ≤80; HOW lives in the body.
_SYSTEM_SKILLS: tuple[SystemSkill, ...] = (
    SystemSkill(
        name="team_orchestration_advanced",
        summary="团队拆法",
        body=_TEAM_ORCHESTRATION_ADVANCED,
        # 根 CEO 编制 / 协调。嵌套 lead 的拆法不在此本 → ``lead_subteam``。
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="lead_subteam",
        summary="子队拆法",
        body=_LEAD_SUBTEAM,
        # 持 delegate 的队员队长才进目录；叶子与 CEO 都不广告。
        audience=AUDIENCE_WORKER_ONLY,
        requires_tools=("delegate",),
    ),
    SystemSkill(
        name="team_cross_folder",
        summary="跨文件夹",
        body=_TEAM_CROSS_FOLDER,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="team_delivery_env",
        summary="交付环境",
        body=_TEAM_DELIVERY_ENV,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="team_local_desk",
        summary="本机进桌",
        body=_TEAM_LOCAL_DESK,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="product_help",
        summary="本产品用法",
        body=_PRODUCT_HELP,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="debate_and_review",
        summary="正反辩论",
        body=_DEBATE_AND_REVIEW,
        requires_tools=("debate",),
    ),
    SystemSkill(
        name="asking_the_user",
        summary="向用户提问",
        body=_ASKING_THE_USER,
        requires_tools=("ask_user",),
    ),
    SystemSkill(
        name="run",
        summary="跑命令 / 启服",
        body=_RUN,
        requires_tools=("run",),
    ),
    SystemSkill(
        name="long_form_landing",
        summary="超长落盘",
        body=_LONG_FORM_LANDING,
        audience=AUDIENCE_WORKER_ONLY,
    ),
    SystemSkill(
        name="data_file_landing",
        summary="表格落盘",
        body=_DATA_FILE_LANDING,
        # Consult is CEO+worker. Body is the worker loop; CEO still consults to brief.
        # Do not gate on ``run``: this turn may have no execution assembled; the
        # brief still belongs in the supervisor catalog.
    ),
)


def build_system_skill_registry(
    *,
    enabled_packs: Collection[str] = (),
    include_legal: bool = False,
) -> SkillRegistry:
    """Register the platform's built-in (system) skills — the single source of truth.

    Mirrors ``build_builtin_registry`` for tools: code-defined, always available to
    the CEO via ``consult``. Future market skills register into the SAME
    registry shape (单一机制、多类来源).

    ``enabled_packs`` layers deployment-gated capability packs (e.g. ``\"legal\"``)
    into the SAME registry. Call sites pass
    :func:`agentcore.runtime.capability_packs.enabled_packs` (listing gate = activation).
    ``include_legal=True`` remains a test/convenience alias for ``enabled_packs``
    containing ``\"legal\"``. Deferred import keeps the module graph free of a
    core→domain edge when no vertical pack is enabled.
    """
    packs = set(enabled_packs)
    if include_legal:
        packs.add("legal")
    registry = SkillRegistry()
    for skill in _SYSTEM_SKILLS:
        registry.register(skill)
    if packs:
        from agentcore.runtime.capability_packs import pack_skills

        for skill in pack_skills(sorted(packs)):
            registry.register(skill)
    return registry


def render_skill_directory(registry: SkillRegistry, tool_names: set[str]) -> str:
    """Backward-compat wrapper → unified ``<按需目录>`` (skills only).

    Prefer building entries via :class:`MergedConsultSource` so directory and
    ``consult`` fetch cannot drift. Kept for tests / capability catalog that only
    need the skill slice.
    """
    from agentcore.runtime.context.consultable import ConsultDirectoryEntry
    from agentcore.runtime.resolve.prompt.compose import render_on_demand_directory

    skills = registry.available(tool_names)
    if not skills:
        return ""
    entries = [
        ConsultDirectoryEntry(name=skill.name, summary=skill.summary, section="skill")
        for skill in skills
    ]
    return render_on_demand_directory(entries, with_summaries=True)
