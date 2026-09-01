"""``parse_motion_card`` 契约（stage_card / journal）+ handoff 字段已撤。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agentcore.core.types import AutonomyPolicy, ToolEffect, recipe_to_axes
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.delegate.ceo_format import format_for_ceo
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.serialize import (
    debrief_from_transcript,
    state_from_json,
    state_to_json,
)
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.builtin.debate.schema import STANCE_MAX_CHARS
from agentcore.tools.builtin.handoff import HandoffTool
from agentcore.tools.builtin.motion_card import parse_motion_card
from agentcore.tools.protocol import ToolContext
from tests.delegate.conftest import Provider, tool


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e1",
        run_id="w1",
        agent_id="a1",
        backend=MagicMock(location="server"),
        user_id="u1",
    )


def _valid_card(**overrides: object) -> dict:
    base: dict = {
        "motion": "一审判决是否过重",
        "sides": [
            {"key": "pro", "name": "正方", "stance": "支持一审判决正确"},
            {"key": "con", "name": "反方", "stance": "认为判赔过重"},
        ],
        "fact_pointers": ["#r1", "notes/case.md", "https://example.com/a"],
        "rationale": "双方对赔偿数额的法律适用存在根本对立，继续调研无法收敛。",
        "form": "debate",
    }
    base.update(overrides)
    return base


# ── parse / validate ──────────────────────────────────────────────


def test_parse_motion_card_accepts_valid():
    card, err = parse_motion_card(_valid_card())
    assert err == ""
    assert card is not None
    assert card["motion"] == "一审判决是否过重"
    assert card["form"] == "debate"
    assert len(card["sides"]) == 2
    assert card["fact_pointers"] == ["#r1", "notes/case.md", "https://example.com/a"]


def test_parse_motion_card_absent_is_ok():
    assert parse_motion_card(None) == (None, "")
    assert parse_motion_card("") == (None, "")


def test_parse_motion_card_defaults_form_to_debate():
    card, err = parse_motion_card(_valid_card(form=None))
    assert err == ""
    assert card is not None
    assert card["form"] == "debate"


def test_parse_motion_card_accepts_legacy_forms():
    """广告 enum 只留 debate；parse 仍接受历史三值。"""
    for form in ("red_team", "roundtable"):
        card, err = parse_motion_card(_valid_card(form=form))
        assert err == ""
        assert card is not None
        assert card["form"] == form


def test_parse_motion_card_rejects_stance_over_limit():
    thick = "甲" * (STANCE_MAX_CHARS + 1)
    card, err = parse_motion_card(
        _valid_card(sides=[{"key": "pro", "name": "正方", "stance": thick}, {"key": "con", "name": "反方", "stance": "反对"}])
    )
    assert card is None
    assert err
    assert str(STANCE_MAX_CHARS) in err
    assert "薄立场" in err or "stance" in err
    assert "重试" in err or "改写" in err


def test_parse_motion_card_rejects_stance_script_cues():
    card, err = parse_motion_card(
        _valid_card(
            sides=[
                {
                    "key": "pro",
                    "name": "正方",
                    "stance": "核心论点包括请从证据角度系统论证",
                },
                {"key": "con", "name": "反方", "stance": "认为判赔过重"},
            ]
        )
    )
    assert card is None
    assert err
    assert "论点清单" in err or "论证剧本" in err


def test_parse_motion_card_rejects_missing_rationale():
    raw = _valid_card()
    del raw["rationale"]
    card, err = parse_motion_card(raw)
    assert card is None
    assert "rationale" in err


def test_parse_motion_card_rejects_sides_lt_two():
    card, err = parse_motion_card(
        _valid_card(sides=[{"key": "pro", "name": "正方", "stance": "支持"}])
    )
    assert card is None
    assert "sides" in err


@pytest.mark.asyncio
async def test_handoff_execute_ignores_extra_motion_card():
    """handoff ``motion_card`` 已撤：额外字段不拒收。"""
    t = HandoffTool()
    thick = "甲" * (STANCE_MAX_CHARS + 1)
    res = await t.execute(
        {
            "summary": "调研完成",
            "motion_card": _valid_card(
                sides=[
                    {"key": "pro", "name": "正方", "stance": thick},
                    {"key": "con", "name": "反方", "stance": "反对"},
                ]
            ),
        },
        _ctx(),
    )
    assert res.success is True
    assert res.effect is ToolEffect.HANDOFF


@pytest.mark.asyncio
async def test_handoff_logs_body_chars_distinct_from_summary_chars():
    """worker.handoff：chars=summary 长；body_chars=同轮交付正文长（勿把 chars 当正文）。"""
    from dataclasses import replace

    from structlog.testing import capture_logs

    t = HandoffTool()
    summary = "简报结论十字"
    body = "这是交付正文，比简报长很多——" + ("字" * 40)
    ctx = replace(_ctx(), round_content_chars=len(body))
    with capture_logs() as logs:
        res = await t.execute({"summary": summary}, ctx)
    assert res.success is True
    handoffs = [e for e in logs if e.get("event") == "worker.handoff"]
    assert len(handoffs) == 1
    assert handoffs[0]["chars"] == len(summary)
    assert handoffs[0]["body_chars"] == len(body)
    assert handoffs[0]["chars"] != handoffs[0]["body_chars"]


@pytest.mark.asyncio
async def test_handoff_allows_debate_suggest_without_motion_card():
    """开辩不再靠命题卡催场：建议开辩而无卡仍可交接。"""
    t = HandoffTool()
    res = await t.execute(
        {
            "summary": "四路交叉后核心对立难消，建议开辩",
            "key_points": ["法律与商业结论冲突"],
            "next_steps": "用户若要对抗可自己说开辩",
        },
        _ctx(),
    )
    assert res.success is True
    assert res.effect is ToolEffect.HANDOFF


@pytest.mark.asyncio
async def test_handoff_allows_debate_suggest_with_valid_motion_card():
    t = HandoffTool()
    res = await t.execute(
        {
            "summary": "建议开辩以对抗检验",
            "motion_card": _valid_card(),
        },
        _ctx(),
    )
    assert res.success is True
    assert res.effect is ToolEffect.HANDOFF


@pytest.mark.asyncio
async def test_handoff_mere_debate_mention_without_card_ok():
    """仅提及辩论事实、未建议开辩 → 无卡仍可交接。"""
    t = HandoffTool()
    res = await t.execute(
        {"summary": "法律路梳理了一审辩论过程，未见必须对抗的新轴"},
        _ctx(),
    )
    assert res.success is True
    assert res.effect is ToolEffect.HANDOFF


@pytest.mark.asyncio
async def test_handoff_extra_or_missing_motion_card_both_ok():
    """handoff 不再硬拒 motion_card；建议开辩而无卡可交接。"""
    t = HandoffTool()
    extra = await t.execute(
        {
            "summary": "建议开辩",
            "motion_card": {"motion": "只有命题"},
        },
        _ctx(),
    )
    assert extra.success is True
    assert extra.effect is ToolEffect.HANDOFF

    missing = await t.execute({"summary": "建议开辩"}, _ctx())
    assert missing.success is True
    assert missing.effect is ToolEffect.HANDOFF


def test_handoff_schema_has_no_motion_card():
    """handoff 不再广告 / 硬拒 motion_card。"""
    schema = HandoffTool().schema
    assert "motion_card" not in schema.parameters["properties"]
    assert "motion_card" not in (schema.description or "")


# ── serialize ─────────────────────────────────────────────────────


def _handoff_msg(arguments: str, call_id: str = "h1") -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id=call_id, function=ToolCallFunction(name="handoff", arguments=arguments))
        ],
    )


def test_debrief_does_not_harvest_motion_card():
    args = {
        "summary": "发现核心争议",
        "motion_card": _valid_card(),
    }
    debrief = debrief_from_transcript([_handoff_msg(json.dumps(args, ensure_ascii=False))])
    assert debrief == {"summary": "发现核心争议"}
    assert "motion_card" not in debrief


def test_debrief_omits_motion_card_when_absent():
    debrief = debrief_from_transcript([_handoff_msg('{"summary": "普通交接"}')])
    assert debrief == {"summary": "普通交接"}
    assert "motion_card" not in debrief


def test_debrief_drops_invalid_motion_card_keeps_other_fields():
    args = {
        "summary": "仍有效的结论",
        "motion_card": {"motion": "只有命题没有其余字段"},
    }
    debrief = debrief_from_transcript([_handoff_msg(json.dumps(args, ensure_ascii=False))])
    assert debrief == {"summary": "仍有效的结论"}
    assert "motion_card" not in debrief


def test_state_json_round_trips_motion_card():
    card = _valid_card()
    state = RunState(
        phase=RunPhase.COMPLETED,
        content="调研正文",
        debrief={"summary": "发现争议", "motion_card": card},
    )
    restored = state_from_json(state_to_json(state))
    assert restored.debrief is not None
    assert restored.debrief["motion_card"] == card


# ── ceo_format ────────────────────────────────────────────────────


def test_format_for_ceo_does_not_surface_leftover_motion_card():
    t = tool(Provider([]))
    t._permission_axes = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="汇总分析", role="汇总分析师")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="分析正文",
            debrief={"summary": "有核心争议", "motion_card": _valid_card()},
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "队员提交的命题卡" not in out
    assert "不要调 debate" not in out
    assert "非开辩入口" not in out
    assert "一审判决是否过重" not in out
    assert "汇总分析师" in out


def test_format_for_ceo_does_not_list_leftover_motion_cards():
    t = tool(Provider([]))
    plan = RunPlan(
        nodes=[
            RunSpec(run_id="w1", task="分析 A", role="分析师甲"),
            RunSpec(run_id="w2", task="分析 B", role="分析师乙"),
        ]
    )
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="A",
            debrief={
                "summary": "卡1",
                "motion_card": _valid_card(motion="命题甲"),
            },
        ),
        "w2": RunState(
            phase=RunPhase.COMPLETED,
            content="B",
            debrief={
                "summary": "卡2",
                "motion_card": _valid_card(motion="命题乙", form="roundtable"),
            },
        ),
    }
    out = format_for_ceo(t, plan, results)
    assert "命题甲" not in out and "命题乙" not in out
    assert "分析师甲" in out and "分析师乙" in out
    assert "队员提交的命题卡" not in out


def test_format_for_ceo_no_motion_card_section_when_absent():
    t = tool(Provider([]))
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="调研", role="研究员")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="普通综述",
            debrief={"summary": "无争议", "next_steps": "可补资料"},
        )
    }
    out = format_for_ceo(t, plan, results)
    # 专节 intro 缺席（收尾指引里的条件句「上方若有【建议开辩】」仍可出现）
    assert "队员提交的命题卡" not in out
    assert "消费指引" not in out
    # 无卡时既有下一步专节仍在
    assert "队员建议的下一步" in out
