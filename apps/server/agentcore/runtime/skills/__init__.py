"""System Skills: code-defined capability docs the CEO pulls on demand (渐进披露).

提示词瘦身 P2 的落地。CEO 的常驻系统提示词只保留「决定干什么」的路由核心（见
``prompt._CEO_CORE_HINT``）；「怎么干」的进阶机制——团队编排进阶 / 辩论与交叉审查 /
定向唤回 / 向用户发问（开场提案卡 + 途中拍板）/ 委派波间挂起——下沉为 **系统 Skill**：代码定义、随 CEO 常备、以一
张「按需目录」常驻（仅一行触发描述），模型决定要用某能力后才用 ``consult(name)``
把完整指引拉回自己的 ReAct 循环。

这是 ``docs/03-AI核心/工具与能力系统.md §二`` 已定的「Skill 渐进披露」机制（目录模式 +
按名拉取）的第一个实例。系统 Skill 与未来「市场 Skill」并列为两类来源，共用同一套
``SkillRegistry`` + ``consult``（单一机制、多类来源）——正如内置工具与市场工具
共用 ``ToolRegistry``，不另造平行系统。

``requires_tools`` 把现有的 live-user 门（ask_user 仅在有活跃用户时装配）一般化：一个
Skill 只在它依赖的工具全部装配时才进目录，故提示词永不广告 CEO 手里没有的能力（沿用
现有不变量）。``audience`` 再按读者收一层（主管 vs 队员），目录与 ``consult`` 拉取共用
这一滤；不扫任务原文猜意图。

维护约定（防双源漂移）：各 Skill 的 ``body`` 是**面向模型的 HOW 操作指引**的单一真相源；
``docs/03-AI核心`` 各专题只写设计意图与约束（What/Why），不逐字复述 body。改动编排行为时
两处同步：行为语义以设计文档为准，喂给模型的措辞以各 skill 分文件为准。

本包按职责 / skill 名拆正文；``catalog`` 保留薄装配表；公开 API 仍从
``agentcore.runtime.skills`` 导入（与拆前同路径）。
"""

from __future__ import annotations

from agentcore.runtime.skills.catalog import (
    build_system_skill_registry,
    render_skill_directory,
)
from agentcore.runtime.skills.deep_multi_lens_research import MULTI_LENS_COURTROOM_TRIGGERS
from agentcore.runtime.skills.registry import SkillRegistry, SystemSkill
from agentcore.runtime.skills.team_cross_folder import _TEAM_CROSS_FOLDER
from agentcore.runtime.skills.team_delivery_env import _TEAM_DELIVERY_ENV
from agentcore.runtime.skills.team_orchestration import (
    _TEAM_ORCHESTRATION_ADVANCED,
)

__all__ = [
    "MULTI_LENS_COURTROOM_TRIGGERS",
    "SkillRegistry",
    "SystemSkill",
    "_TEAM_CROSS_FOLDER",
    "_TEAM_DELIVERY_ENV",
    "_TEAM_ORCHESTRATION_ADVANCED",
    "build_system_skill_registry",
    "render_skill_directory",
]
