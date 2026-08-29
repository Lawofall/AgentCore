"""主持人开场白 prompt 契约（零 LLM）：钉住语体 / 三拍 / 禁词，防回落「大众解说腔」。"""

from __future__ import annotations

import asyncio

from agentcore.runtime.debate import DebateConfig, DebateForm, DebateSide, RoundPolicy
from agentcore.runtime.debate.moderator_agenda import _FRAME_SYSTEM, _OPENING_SPEC, frame_round
from agentcore.runtime.debate.research_dossier import (
    SYNTHESIZER_FILE,
    format_research_dossier_index,
)


class _CaptureJson:
    def __init__(self) -> None:
        self.user = ""

    async def __call__(self, system: str, user: str, step: str) -> dict:
        self.user = user
        return {"focus": "成本净影响", "opening": "开场白占位"}


def test_opening_spec_is_formal_moderator_register():
    """开场白规格：赛制主持语域 + 三拍 + 长度带；旧唠嗑腔锚已拆除。"""
    assert "真实辩论赛主持人" in _OPENING_SPEC
    assert "面向全场" in _OPENING_SPEC
    assert "不对用户称「你」" in _OPENING_SPEC
    assert "80–120" in _OPENING_SPEC
    # 三拍要素
    assert "宣题" in _OPENING_SPEC
    assert "亮场" in _OPENING_SPEC
    assert "定焦" in _OPENING_SPEC
    # 形态适配亮场措辞
    assert "正方主张" in _OPENING_SPEC
    assert "红队将审查" in _OPENING_SPEC
    assert "视角展开" in _OPENING_SPEC
    assert "不剧透结论" in _OPENING_SPEC
    assert "不站队" in _OPENING_SPEC
    assert "禁网络梗" in _OPENING_SPEC
    assert "首轮焦点" in _OPENING_SPEC
    # 旧大众解说腔 / 个案命题示范不得回潮
    for banned in (
        "普通观众",
        "大白话",
        "说人话",
        "先帮你把最要紧",
        "帮你定的",
        "口吻示范",
        "远程办公",
    ):
        assert banned not in _OPENING_SPEC, f"旧口吻或个案示范残留: {banned!r}"
    assert "帮你" not in _OPENING_SPEC


def test_frame_system_anchors_moderator_register():
    """frame system 锚定开场白与专业辩论主持同语域。"""
    assert "开场白与专业辩论主持同语域" in _FRAME_SYSTEM


def test_frame_round_injects_research_dossier_agenda_hint():
    """首轮定焦 brief：有约定文档索引则注入，并提示可用汇总分歧作议程参考。"""
    sides = [
        DebateSide(key="pro", name="正方", stance="支持"),
        DebateSide(key="con", name="反方", stance="反对"),
    ]
    idx = format_research_dossier_index(
        ["AgentCore/文档/research/法律透镜报告.md", SYNTHESIZER_FILE]
    )
    cfg = DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=sides,
        policy=RoundPolicy(thorough=True, max_rounds=3),
        research_dossier_index=idx,
    )
    cap = _CaptureJson()
    focus, opening = asyncio.run(frame_round(cap, cfg, []))
    assert focus == "成本净影响"
    assert opening == "开场白占位"
    assert "【工作区约定文档索引·AgentCore/文档/research/】" in cap.user
    assert SYNTHESIZER_FILE in cap.user
    assert "分歧作议程" in cap.user or "议程线索" in cap.user


def test_frame_round_omits_dossier_when_empty():
    sides = [
        DebateSide(key="pro", name="正方", stance="支持"),
        DebateSide(key="con", name="反方", stance="反对"),
    ]
    cfg = DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=sides,
        policy=RoundPolicy(thorough=True, max_rounds=3),
    )
    cap = _CaptureJson()
    asyncio.run(frame_round(cap, cfg, []))
    assert "工作区约定文档索引" not in cap.user
