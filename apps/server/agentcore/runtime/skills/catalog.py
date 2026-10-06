"""Thin registry assembly + 按需目录 rendering for system skills."""

from __future__ import annotations

from agentcore.runtime.skills.data_file_landing import _DATA_FILE_LANDING
from agentcore.runtime.skills.page_ui import _PAGE_UI
from agentcore.runtime.skills.product_help import build_product_help_body
from agentcore.runtime.skills.registry import (
    AUDIENCE_CEO_ONLY,
    GROUP_DELIVERY,
    GROUP_PRODUCT,
    SkillRegistry,
    SystemSkill,
)

# --- The system skills (single source of truth) -----------------------------
# Catalog summary is the only trigger before consult: when to open, plus the
# usual false friend. Python len ≤80. HOW stays in the body. ``blurb`` is
# toolbox-card only. A bare noun ("整理表") is opened on unrelated turns.
_SYSTEM_SKILLS: tuple[SystemSkill, ...] = (
    SystemSkill(
        name="data_file_landing",
        summary="用户要把数据文件交成可打开的表时才查阅。成篇、做页面、问产品不查阅。",
        blurb="把数据文件整理成打开扫得懂的表",
        body=_DATA_FILE_LANDING,
        # Consult is CEO+worker. Body is the worker loop; CEO still consults to brief.
        # Do not gate on ``run``: this turn may have no execution assembled; the
        # brief still belongs in the supervisor catalog.
        group=GROUP_DELIVERY,
    ),
    SystemSkill(
        name="page_ui",
        summary="用户要打开的展示页、落地页、工具壳、仪表盘或原型才查阅。脚本、文档、接口、只改逻辑不查阅。",
        blurb="页面长什么样、交互怎么铺",
        body=_PAGE_UI,
        # CEO+worker：主管把方向写进 task，工人铺像素。无工具门。
        group=GROUP_DELIVERY,
    ),
    SystemSkill(
        name="product_help",
        # 行首「本产品是什么」会被读成开工前要补的身份，所以不以问句或「本产品」开头。
        summary="用户在问这个产品怎么用、入口在哪或为什么这样时才查阅。交代任务去干活不查阅。",
        blurb="这个产品能做什么、入口在哪",
        body=build_product_help_body(),
        audience=AUDIENCE_CEO_ONLY,
        group=GROUP_PRODUCT,
    ),
)


def build_system_skill_registry() -> SkillRegistry:
    """Register the platform's built-in (system) skills — the single source of truth.

    Mirrors ``build_builtin_registry`` for tools: code-defined, always available to
    the CEO via ``consult``. Domain SOPs are user skills, not layered into this registry.
    """
    registry = SkillRegistry()
    for skill in _SYSTEM_SKILLS:
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
        ConsultDirectoryEntry(
            name=skill.name,
            summary=skill.summary,
            section="skill",
            group=skill.group,
        )
        for skill in skills
    ]
    return render_on_demand_directory(entries, with_summaries=True)
