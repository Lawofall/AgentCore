"""主持人开场白 prompt 契约（零 LLM）：钉住合同，不钉语体课。"""

from __future__ import annotations

import asyncio

from agentcore.runtime.debate import (
    DebateConfig,
    DebateForm,
    DebateSide,
    JudgeVerdict,
    RoundPolicy,
    RoundResult,
)
from agentcore.runtime.debate.moderator_agenda import _FRAME_SYSTEM, _OPENING_SPEC, frame_round
from agentcore.runtime.debate.research_dossier import format_research_dossier_index


class _CaptureJson:
    def __init__(self) -> None:
        self.user = ""

    async def __call__(self, system: str, user: str, step: str) -> dict:
        self.user = user
        return {"focus": "成本净影响", "opening": "开场白占位"}


def test_opening_spec_is_formal_moderator_register():
    """开场白规格：面向全场、不站队；语体课 / 字数带已撤。"""
    assert "面向全场" in _OPENING_SPEC
    assert "不站队" in _OPENING_SPEC
    assert "不剧透结论" in _OPENING_SPEC
    assert "80–120" not in _OPENING_SPEC
    assert "真实辩论赛主持人" not in _OPENING_SPEC
    assert "禁网络梗" not in _OPENING_SPEC
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
    """frame system 钉焦点合同，不钉主持语体课。"""
    assert "小标题" in _FRAME_SYSTEM
    assert "专业辩论主持同语域" not in _FRAME_SYSTEM


def test_frame_round_injects_research_dossier_agenda_hint():
    """首轮定焦 brief：有材料索引则注入，并提示可用汇总分歧作议程参考。"""
    sides = [
        DebateSide(key="pro", name="正方", stance="支持"),
        DebateSide(key="con", name="反方", stance="反对"),
    ]
    idx = format_research_dossier_index(
        ["notes/法律透镜报告.md", "notes/汇总与命题卡.md"]
    )
    cfg = DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=sides,
        policy=RoundPolicy(max_rounds=3),
        research_dossier_index=idx,
    )
    cap = _CaptureJson()
    focus, opening = asyncio.run(frame_round(cap, cfg, []))
    assert focus == "成本净影响"
    assert opening == "开场白占位"
    assert "【工作区材料索引】" in cap.user
    assert "notes/汇总与命题卡.md" in cap.user
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
        policy=RoundPolicy(max_rounds=3),
    )
    cap = _CaptureJson()
    asyncio.run(frame_round(cap, cfg, []))
    assert "工作区材料索引" not in cap.user


def test_later_round_frame_omits_roundtable_exception():
    """后续轮定焦不再教圆桌例外；形态 hint 已在 _frame_form_hint。"""
    sides = [
        DebateSide(key="pro", name="正方", stance="支持"),
        DebateSide(key="con", name="反方", stance="反对"),
    ]
    cfg = DebateConfig(
        motion="该不该做 X",
        form=DebateForm.DEBATE,
        sides=sides,
        policy=RoundPolicy(max_rounds=3),
    )
    history = [
        RoundResult(
            round_no=1,
            focus="成本净影响",
            turns=[],
            verdict=JudgeVerdict(
                real_clash=True, new_arguments=True, converged=False, next_focus="更深的点"
            ),
            summary="本轮小结",
        )
    ]
    cap = _CaptureJson()
    asyncio.run(frame_round(cap, cfg, history))
    assert "多方圆桌例外" not in cap.user
