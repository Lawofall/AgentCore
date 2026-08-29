"""Thin registry assembly + 按需目录 rendering for system skills."""

from __future__ import annotations

from collections.abc import Collection

from agentcore.runtime.skills.ask_user import _ASK_USER_KICKOFF, _ASK_USER_MIDTASK
from agentcore.runtime.skills.building_software import _BUILDING_SOFTWARE
from agentcore.runtime.skills.data_file_landing import _DATA_FILE_LANDING
from agentcore.runtime.skills.debate_and_review import _DEBATE_AND_REVIEW
from agentcore.runtime.skills.deep_multi_lens_research import _DEEP_MULTI_LENS_RESEARCH
from agentcore.runtime.skills.delegate_checkpoint import _DELEGATE_CHECKPOINT
from agentcore.runtime.skills.long_form_writing import (
    _LONG_FORM_LANDING,
    _LONG_FORM_WRITING,
)
from agentcore.runtime.skills.product_help import (
    _PRODUCT_BUG_TRIAGE,
    _PRODUCT_HELP,
    _PRODUCT_HELP_FAQ,
    _PRODUCT_HELP_MAP,
)
from agentcore.runtime.skills.registry import (
    AUDIENCE_CEO_ONLY,
    AUDIENCE_WORKER_ONLY,
    SkillRegistry,
    SystemSkill,
)
from agentcore.runtime.skills.revising_a_product import _REVISING_A_PRODUCT
from agentcore.runtime.skills.team_cross_folder import _TEAM_CROSS_FOLDER
from agentcore.runtime.skills.team_delivery_env import _TEAM_DELIVERY_ENV
from agentcore.runtime.skills.team_orchestration import (
    _TEAM_ORCHESTRATION_ADVANCED,
)
from agentcore.runtime.skills.verify_and_fix import _VERIFY_AND_FIX
from agentcore.runtime.skills.work_discipline import _WORK_DISCIPLINE

# --- The system skills (single source of truth) -----------------------------
# Catalog summaries: name-like (what this is), not a 19-way scene classifier.
# Python len ≤80; HOW lives in the body.
_SYSTEM_SKILLS: tuple[SystemSkill, ...] = (
    SystemSkill(
        name="team_orchestration_advanced",
        summary="团队拆法 / 成文编制",
        body=_TEAM_ORCHESTRATION_ADVANCED,
        # 派单 / 协调 / 跨路复核：队员开场目录与 consult 共用此滤，避免叶子与嵌套 lead 两套前缀。
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="team_cross_folder",
        summary="跨文件夹",
        body=_TEAM_CROSS_FOLDER,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="team_delivery_env",
        summary="Office / 空桌 / 产物路径",
        body=_TEAM_DELIVERY_ENV,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="work_discipline",
        summary="工程纪律 / Windows .bat",
        body=_WORK_DISCIPLINE,
    ),
    SystemSkill(
        name="product_help",
        summary="本产品用法 / 官网 / 下载",
        body=_PRODUCT_HELP,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="product_help_map",
        summary="界面入口地图",
        body=_PRODUCT_HELP_MAP,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="product_help_faq",
        summary="产品 FAQ（官网 / Cursor）",
        body=_PRODUCT_HELP_FAQ,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="product_bug_triage",
        summary="用户报产品故障",
        body=_PRODUCT_BUG_TRIAGE,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="building_software",
        summary="做软件",
        body=_BUILDING_SOFTWARE,
        requires_tools=("delegate",),
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="debate_and_review",
        summary="对抗性多视角辩论",
        body=_DEBATE_AND_REVIEW,
        requires_tools=("debate",),
    ),
    SystemSkill(
        name="revising_a_product",
        summary="续派改稿",
        body=_REVISING_A_PRODUCT,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="ask_user_kickoff",
        summary="向用户提问 / 载体顾问",
        body=_ASK_USER_KICKOFF,
        requires_tools=("ask_user",),
    ),
    SystemSkill(
        name="ask_user_midtask",
        summary="途中拍板 / 落盘前对齐",
        body=_ASK_USER_MIDTASK,
        requires_tools=("ask_user",),
    ),
    # Outline / mid-pipeline user gate: pauses for USER review — only meaningful
    # with a live user. Gate on ``ask_user`` (the live-user proxy, same as the
    # other ask_* skills) so it never advertises on the autonomous path.
    SystemSkill(
        name="delegate_checkpoint",
        summary="提纲过目",
        body=_DELEGATE_CHECKPOINT,
        requires_tools=("ask_user",),
    ),
    SystemSkill(
        name="verify_and_fix",
        summary="改完验测并修失败",
        body=_VERIFY_AND_FIX,
        # Consult is CEO+worker. Body is the worker loop; CEO still consults to brief.
        # Do not gate on ``delegate`` (would hide it from workers) or ``test_run``
        # (CEO has no test_run → skill would vanish from the supervisor catalog).
    ),
    SystemSkill(
        name="long_form_writing",
        summary="超长成篇 / 多源合并",
        body=_LONG_FORM_WRITING,
        requires_tools=("delegate",),
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="long_form_landing",
        summary="超长落盘（队员写文件）",
        body=_LONG_FORM_LANDING,
        audience=AUDIENCE_WORKER_ONLY,
    ),
    SystemSkill(
        name="data_file_landing",
        summary="账单/报表 → 可打开表",
        body=_DATA_FILE_LANDING,
        # Consult is CEO+worker. Body is the worker loop; CEO still consults to brief.
        # Do not gate on ``code_execute`` (CEO has none → skill would vanish from
        # the supervisor catalog).
    ),
    SystemSkill(
        name="deep_multi_lens_research",
        summary="多维公共事件调研",
        body=_DEEP_MULTI_LENS_RESEARCH,
        requires_tools=("delegate",),
        audience=AUDIENCE_CEO_ONLY,
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
