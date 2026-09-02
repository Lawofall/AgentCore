"""阶段推进卡 ``research_first`` 回灌文案，以及调研链证据探测。

开赛前开工卡「先调研再辩」按键已退役（庭前取证内化为辩论固有阶段）。
本模块不再提供 offer / recommend 闸。``research_first_tool_result`` 仍服务
阶段推进卡「先补充调研」与旧 journal fold。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentcore.memory.followups import select_motion_card_from_journal


def has_research_chain_evidence(
    entries: Sequence[Mapping[str, Any]] | None,
    *,
    has_research_artifacts: bool = False,
) -> bool:
    """是否已有调研链证据（命题卡 / 约定文档）——原 offer 判据的逆命题素材。"""
    if select_motion_card_from_journal(entries) is not None:
        return True
    return bool(has_research_artifacts)


def research_first_tool_result(*, motion: str = "", user_message: str = "") -> str:
    """``research_first`` 决议的固定回灌文案（topic 取 motion，否则用户原话）。

    本决议的形状是公共事件多维交叉核验：手写异质透镜 + 汇总，不套具名 playbook。
    """
    topic = (motion or "").strip() or (user_message or "").strip() or "（从用户原话提炼主题）"
    # Strip quotes so the imperative blob stays a single readable command line.
    topic = topic.replace('"', "'")
    return (
        "用户在开赛确认中选择「先多视角调研再辩」。本场辩论未授权，请勿再次调用 debate。"
        f"本回合必须立即手写 delegate："
        f"围绕【{topic}】异质透镜并行 + 汇总分析师 depends_on 全部透镜"
        "（未点名视角可用法律 / 品牌商业 / 舆情公关 / 文化社会）；"
        "调研与呈报完成、用户拍板后再开辩。"
    )
