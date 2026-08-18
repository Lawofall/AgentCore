"""handoff ``motion_card`` 契约：字段校验 + 序列化往返 + CEO 有卡/无卡渲染。"""

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
from agentcore.tools.builtin.handoff import HandoffTool, claims_debate_suggestion
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
async def test_handoff_execute_rejects_invalid_motion_card():
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
    assert res.success is False
    assert res.error
    assert str(STANCE_MAX_CHARS) in (res.error or "")
    assert res.effect is not ToolEffect.HANDOFF


@pytest.mark.asyncio
async def test_handoff_execute_accepts_valid_motion_card():
    t = HandoffTool()
    res = await t.execute({"summary": "调研完成", "motion_card": _valid_card()}, _ctx())
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


def test_claims_debate_suggestion_intent_not_mere_mention():
    """建议开辩才拦；仅提及辩论事实（如一审辩论过程）不拦。"""
    assert claims_debate_suggestion({"summary": "建议开辩以检验对立"}) is True
    assert claims_debate_suggestion(
        {"summary": "调研完成", "next_steps": "建议发起正反辩论"}
    ) is True
    assert claims_debate_suggestion(
        {"summary": "完成", "key_points": ["推荐开辩", "附争议轴"]}
    ) is True
    assert claims_debate_suggestion({"summary": "should debate this conflict"}) is True
    # 误拦边界：事实叙述 / 过程提及
    assert claims_debate_suggestion({"summary": "报告梳理了一审辩论过程与质证要点"}) is False
    assert claims_debate_suggestion(
        {"summary": "各方在法庭辩论中交锋激烈", "key_points": ["庭审记录完整"]}
    ) is False
    assert claims_debate_suggestion({"summary": "本轮不做辩论，仅综述四路"}) is False


@pytest.mark.asyncio
async def test_handoff_rejects_debate_suggest_without_motion_card():
    t = HandoffTool()
    res = await t.execute(
        {
            "summary": "四路交叉后核心对立难消，建议开辩",
            "key_points": ["法律与商业结论冲突"],
            "next_steps": "用户同意后启动辩论",
        },
        _ctx(),
    )
    assert res.success is False
    assert res.effect is not ToolEffect.HANDOFF
    err = res.error or ""
    assert "motion_card" in err
    assert "建议开辩" in err or "结构化" in err
    assert "最小示例" in err
    # 与卡字段校验错误可区分：无 `motion_card.` 字段路径 / 薄立场提示
    assert "motion_card.motion" not in err
    assert "薄立场" not in err


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
async def test_handoff_invalid_card_error_distinct_from_missing_card_gate():
    """卡校验失败 vs 建议开辩无卡：错误信息可区分。"""
    t = HandoffTool()
    bad = await t.execute(
        {
            "summary": "建议开辩",
            "motion_card": {"motion": "只有命题"},
        },
        _ctx(),
    )
    assert bad.success is False
    assert "`motion_card.rationale`" in (bad.error or "") or "motion_card.rationale" in (
        bad.error or ""
    )
    assert "最小示例" not in (bad.error or "")
    assert "已建议开辩" not in (bad.error or "")

    missing = await t.execute({"summary": "建议开辩"}, _ctx())
    assert missing.success is False
    assert "最小示例" in (missing.error or "")
    assert "已建议开辩" in (missing.error or "")
    assert "`motion_card.rationale`" not in (missing.error or "")


def test_handoff_schema_teaches_motion_card_is_sole_structured_carrier():
    """Tool description must make the structured field the only debate-suggest channel."""
    desc = HandoffTool().schema.description
    assert "motion_card" in desc
    assert "唯一" in desc or "一律不算" in desc or "不能代替" in desc
    card_desc = HandoffTool().schema.parameters["properties"]["motion_card"]["description"]
    assert "对象" in card_desc or "结构化" in card_desc
    assert "必填" in card_desc or "省略" in card_desc


# ── serialize ─────────────────────────────────────────────────────


def _handoff_msg(arguments: str, call_id: str = "h1") -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id=call_id, function=ToolCallFunction(name="handoff", arguments=arguments))
        ],
    )


def test_debrief_harvests_motion_card():
    args = {
        "summary": "发现核心争议",
        "motion_card": _valid_card(),
    }
    debrief = debrief_from_transcript([_handoff_msg(json.dumps(args, ensure_ascii=False))])
    assert debrief is not None
    assert debrief["summary"] == "发现核心争议"
    assert debrief["motion_card"]["motion"] == "一审判决是否过重"
    assert debrief["motion_card"]["form"] == "debate"
    assert len(debrief["motion_card"]["sides"]) == 2


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


def test_format_for_ceo_surfaces_motion_card_section():
    # Default-mode guidance (阶段推进卡 / 勿口头征求). Pin CAUTIOUS so
    # managed axes (implies_deep_research_auto) do not flip this fixture onto
    # the auto-adopt guidance branch.
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
    assert "建议开辩" in out
    assert "命题卡" in out
    assert "一审判决是否过重" in out
    assert "支持一审判决正确" in out
    assert "认为判赔过重" in out
    assert "为何必须对抗" in out
    assert "阶段推进卡" in out or "推进卡" in out
    assert "勿口头征求" in out
    assert "不要直接调用 debate" in out or "不要】直接调用 debate" in out
    assert "汇总分析师" in out


def test_format_for_ceo_lists_all_motion_cards():
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
    assert "命题甲" in out and "命题乙" in out
    assert "分析师甲" in out and "分析师乙" in out
    assert "择优" in out


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
