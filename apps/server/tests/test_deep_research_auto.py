"""深度研究自治 — helper、ceo_format 指引分叉、debate 开赛卡放行域与上限降级。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentcore.core.types import (
    AutonomyPolicy,
    CommandAxis,
    FileWriteAxis,
    HostAxis,
    PermissionAxes,
    ToolEffect,
    recipe_to_axes,
)
from agentcore.runtime.deep_research_auto import (
    AUTO_DEBATE_SESSION_LIMIT,
    deep_research_auto_active,
    may_auto_debate,
    tool_may_auto_debate,
)
from agentcore.runtime.delegate.ceo_format import (
    format_for_ceo,
    motion_cards_block,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.builtin.debate import DebateTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import Provider, tool

_ASK_RULES = PermissionAxes(
    FileWriteAxis.SESSION,
    CommandAxis.ASK,
    HostAxis.ASK,
)


def _valid_card() -> dict:
    return {
        "motion": "一审判决是否过重",
        "sides": [
            {"key": "pro", "name": "正方", "stance": "支持一审判决正确"},
            {"key": "con", "name": "反方", "stance": "认为判赔过重"},
        ],
        "fact_pointers": ["#r1"],
        "rationale": "双方对赔偿数额的法律适用存在根本对立。",
        "form": "debate",
    }


# ── helper 蕴含关系 ───────────────────────────────────────────────


def test_helper_flag_only_no_recipe_implication():
    managed = recipe_to_axes(AutonomyPolicy.MANAGED)
    less_interrupt = recipe_to_axes(AutonomyPolicy.LESS_INTERRUPT)
    cautious = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    assert deep_research_auto_active(deep_research_auto=True) is True
    assert deep_research_auto_active(permission_axes=managed) is False
    assert deep_research_auto_active(permission_axes=less_interrupt) is False
    assert deep_research_auto_active(
        deep_research_auto=False,
        permission_axes=_ASK_RULES,
    ) is False
    assert deep_research_auto_active(permission_axes=cautious) is False


def test_helper_may_auto_debate_respects_session_cap():
    assert (
        may_auto_debate(deep_research_auto=True, auto_debate_count=0) is True
    )
    assert (
        may_auto_debate(
            deep_research_auto=True,
            auto_debate_count=AUTO_DEBATE_SESSION_LIMIT,
        )
        is False
    )
    assert (
        may_auto_debate(
            permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
            auto_debate_count=0,
        )
        is False
    )
    assert (
        may_auto_debate(
            deep_research_auto=True,
            permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
            auto_debate_count=0,
        )
        is True
    )


# ── ceo_format 消费指引两态 ───────────────────────────────────────


def test_motion_cards_block_default_vs_auto_guidance():
    products = [
        {
            "role": "汇总",
            "run_id": "w1",
            "motion_card": _valid_card(),
        }
    ]
    default = motion_cards_block(products, auto_adopt=False)
    assert "消费指引·默认模式" in default
    assert "不要】直接调用 debate" in default or "不要直接调用 debate" in default
    assert "深度研究自治" not in default

    auto = motion_cards_block(products, auto_adopt=True)
    assert "消费指引·深度研究自治" in auto
    assert "可直接调 debate" in auto
    assert "不得装观点" in auto
    assert "不要】直接调用 debate" not in auto


def test_format_for_ceo_auto_adopt_guidance_when_flag_under_cap():
    t = tool(Provider([]))
    t._base_tool_context.deep_research_auto = True
    t._base_tool_context.deep_research_auto_debate_count = 0
    assert tool_may_auto_debate(t) is True
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="汇总", role="汇总")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="分析",
            debrief={"summary": "争议", "motion_card": _valid_card()},
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "消费指引·深度研究自治" in out
    assert "可直接调 debate" in out
    assert "本回合不要直接调用 debate" not in out


def test_format_for_ceo_falls_back_when_over_cap():
    t = tool(Provider([]))
    t._base_tool_context.deep_research_auto = True
    t._base_tool_context.deep_research_auto_debate_count = 1
    assert tool_may_auto_debate(t) is False
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="汇总", role="汇总")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="分析",
            debrief={"summary": "争议", "motion_card": _valid_card()},
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "消费指引·默认模式" in out
    assert "不要直接调用 debate" in out or "不要】直接调用 debate" in out


def test_format_for_ceo_managed_axes_do_not_imply_auto_guidance():
    t = tool(Provider([]))
    t._permission_axes = recipe_to_axes(AutonomyPolicy.MANAGED)
    t._base_tool_context.deep_research_auto = False
    t._base_tool_context.deep_research_auto_debate_count = 0
    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="汇总", role="汇总")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="分析",
            debrief={"summary": "争议", "motion_card": _valid_card()},
        )
    }
    out = format_for_ceo(t, plan, results)
    assert "消费指引·默认模式" in out
    assert "消费指引·深度研究自治" not in out


# ── 开赛卡放行域 ─────────────────────────────────────────────────


def _ctx(**kwargs) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c-dra",
        **kwargs,
    )


def _debate_tool(
    *,
    permission_axes=None,
    deep_research_auto: bool = False,
    debate_count: int = 0,
) -> tuple[DebateTool, list, EventSink]:
    if permission_axes is None:
        permission_axes = _ASK_RULES
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    base = _ctx(
        deep_research_auto=deep_research_auto,
        deep_research_auto_debate_count=debate_count,
    )
    tool = DebateTool(
        llm=Provider([]),
        sink=sink,
        system_prompt="sys",
        user_message="辩一下",
        tools=ToolRegistry(),
        base_tool_context=base,
        conversation_id="c-dra",
        ambient_armed=True,
        message_id="m1",
        suspension_saver=_save,
        suspension_deleter=_drop,
        permission_axes=permission_axes,
        registry=registry,
        captain_run_id="ceo",
        approval_gate=None,
    )
    return tool, saved, sink


_DEBATE_ARGS = {
    "motion": "该不该上四天工作制？",
    "form": "debate",
    "sides": [
        {"key": "pro", "name": "正方", "stance": "应推广"},
        {"key": "con", "name": "反方", "stance": "暂缓"},
    ],
}


def _debate_args() -> dict:
    """Per-call copy: allocate_debate_run_ids mutates arguments (run_id / model sync)."""
    return {
        "motion": _DEBATE_ARGS["motion"],
        "form": _DEBATE_ARGS["form"],
        "sides": [dict(s) for s in _DEBATE_ARGS["sides"]],
    }


async def test_debate_flag_skips_kickoff_under_cap():
    tool, saved, sink = _debate_tool(deep_research_auto=True, debate_count=0)

    async def _fake_run(config, usage_metadata):
        return SimpleNamespace(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
            metadata={},
        )

    tool._run_moderator = _fake_run  # type: ignore[method-assign]
    result = await tool.execute(_debate_args(), tool._base_tool_context)
    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)
    # in-memory count bumped (DB may be unavailable in unit tests)
    assert tool._base_tool_context.deep_research_auto_debate_count >= 1


async def test_debate_flag_restores_kickoff_over_cap():
    """超 cap 也不再挂新 team_preview（跳过开工卡已是默认路径）。"""
    tool, saved, sink = _debate_tool(deep_research_auto=True, debate_count=1)

    async def _fake_run(config, usage_metadata):
        return SimpleNamespace(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
            metadata={},
        )

    tool._run_moderator = _fake_run  # type: ignore[method-assign]
    result = await tool.execute(_debate_args(), tool._base_tool_context)
    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)


async def test_debate_full_trust_still_skips_over_cap():
    """full_trust 不因计数上限开始挂卡（行为不回归）。"""
    tool, saved, _sink = _debate_tool(
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED), debate_count=1
    )

    async def _fake_run(config, usage_metadata):
        return SimpleNamespace(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
            metadata={},
        )

    tool._run_moderator = _fake_run  # type: ignore[method-assign]
    result = await tool.execute(_debate_args(), tool._base_tool_context)
    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
