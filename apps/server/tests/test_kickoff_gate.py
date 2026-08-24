"""Orchestration-layer kickoff gate — shared rules for delegate + debate + ask_user."""

from __future__ import annotations

import asyncio
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

_KICKOFF_RULES = PermissionAxes(
    FileWriteAxis.SESSION,
    CommandAxis.AUTO,
    HostAxis.ASK,
)
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import TurnFactLog, current_fact_log
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.kickoff import (
    debate_kickoff_summary,
    has_unfulfilled_kickoff_adjust,
    kickoff_adjust_state,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunSpec
from agentcore.runtime.suspension import TeamPreviewSuspension, captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.builtin.debate import DebateTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import Provider, ctx, tool_durable


async def test_ask_user_allows_after_verbal_affirm():
    """User「认可」after a collaboration plan → still may short-ask (no verbal skip)."""
    history = [
        {"role": "user", "content": "讨论下协作结构"},
        {
            "role": "assistant",
            "content": "完整协作方案：四路并行调研员 + 汇总，分工如下……",
        },
    ]
    tool = AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=1.0,
        user_message="认可",
        history=history,
    )
    result = await tool.execute(
        {"message": "交付形态再确认一下？", "assumptions": ["按四路并行开干"]},
        ToolContext.create(
            execution_id="e",
            run_id="s",
            agent_id="a",
            backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
            user_id="u",
        ),
    )
    assert "勿再开开工提案卡" not in (result.error or "")


async def test_ask_user_allows_after_team_preview_resolved():
    """team_preview_resolved 不再拒 ask_user 短问（开工提案拒调已拆）。"""
    sink = EventSink()
    sink.seed_journal(
        [
            {
                "type": "team_preview_required",
                "payload": {"checkpoint_id": "tp1"},
                "timestamp": "t0",
            },
            {
                "type": "team_preview_resolved",
                "payload": {"checkpoint_id": "tp1", "decision": "continue"},
                "timestamp": "t1",
            },
        ]
    )
    tool = AskUserTool(
        sink=sink,
        conversation_id="c1",
        timeout_seconds=1.0,
        user_message="继续",
    )

    result = await tool.execute(
        {"message": "交付形态再确认一下？"},
        ToolContext.create(
            execution_id="e",
            run_id="s",
            agent_id="a",
            backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
            user_id="u",
        ),
    )
    # Without durable frame → explicit fail path may apply; must NOT be kickoff-refuse.
    assert "勿再开开工提案卡" not in (result.error or "")


async def _drain_coord(execution_id: str = "e") -> None:
    from agentcore.runtime.coordination.session import (
        active_coordination,
        clear_active_coordination,
    )

    session = active_coordination(execution_id)
    if session is not None and session.drive_task is not None:
        await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination()


async def _fake_debate_run(_config, _usage_metadata):
    return SimpleNamespace(
        tool_call_id="",
        success=True,
        output="ok",
        effect=ToolEffect.CONTINUE,
        metadata={},
    )


async def test_confirmed_ask_does_not_skip_delegate_team_preview():
    """同回合阻塞 ask continue 后 ≥2 worker 也不再挂开工卡。"""
    from agentcore.runtime.coordination.session import clear_active_coordination

    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
    sink.seed_journal(
        [
            {
                "type": EventType.CHECKPOINT_REQUIRED.value,
                "payload": {"checkpoint_id": "ask1"},
                "timestamp": "t0",
            },
            {
                "type": EventType.CHECKPOINT_RESOLVED.value,
                "payload": {"checkpoint_id": "ask1", "decision": "continue"},
                "timestamp": "t1",
            },
        ]
    )
    saved: list[TeamPreviewSuspension] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["AOUT", "BOUT"]), sink, registry, _save, _drop)
    transcript = [
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_del",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog()
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await t.execute(
            {
                "tasks": [
                    {"role": "研究员", "task": "做A"},
                    {"role": "写手", "task": "做B"},
                ],
            },
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)
    await _drain_coord()


async def test_confirmed_ask_does_not_skip_debate_team_preview():
    """同回合 ask continue 后顶层 debate 也不再挂开工卡。"""
    registry = InteractionRegistry()
    sink = EventSink()
    sink.seed_journal(
        [
            {
                "type": EventType.CHECKPOINT_REQUIRED.value,
                "payload": {"checkpoint_id": "ask1"},
                "timestamp": "t0",
            },
            {
                "type": EventType.CHECKPOINT_RESOLVED.value,
                "payload": {"checkpoint_id": "ask1", "decision": "continue"},
                "timestamp": "t1",
            },
        ]
    )
    saved: list[TeamPreviewSuspension] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    tool = _debate_tool(sink, registry, _save, _drop)
    tool._run_moderator = _fake_debate_run  # type: ignore[method-assign]
    transcript = [
        LLMMessage(role="user", content="辩一下"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_debate",
                    function=ToolCallFunction(name="debate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog()
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await tool.execute(
            {
                "motion": "该不该上四天工作制？",
                "form": "debate",
                "sides": [
                    {"key": "pro", "name": "正方", "stance": "应推广"},
                    {"key": "con", "name": "反方", "stance": "暂缓"},
                ],
                "thorough": True,
            },
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)


def test_debate_kickoff_summary_shape():
    from agentcore.runtime.debate import DebateConfig, DebateForm, DebateSide, RoundPolicy
    from agentcore.runtime.debate.models import allocate_debate_run_ids

    config = DebateConfig(
        motion="该不该上四天工作制？",
        form=DebateForm.DEBATE,
        sides=[
            DebateSide(key="pro", name="正方", stance="应推广"),
            DebateSide(key="con", name="反方", stance="暂缓"),
        ],
        policy=RoundPolicy(thorough=True, max_rounds=5),
    )
    args = {
        "motion": config.motion,
        "form": "debate",
        "sides": [
            {"key": "pro", "name": "正方", "stance": "应推广"},
            {"key": "con", "name": "反方", "stance": "暂缓"},
        ],
        "thorough": True,
    }
    allocate_debate_run_ids(config, args)
    summary = debate_kickoff_summary(config, arguments=args)
    assert summary.primitive == "debate"
    assert summary.motion == config.motion
    assert len(summary.sides) == 2
    assert summary.max_rounds == 5
    assert summary.workers == []
    card = summary.card_payload()
    assert card["primitive"] == "debate"
    assert card["thorough"] is True
    assert summary.headline == "预计 2 方开赛"
    assert card["headline"] == "预计 2 方开赛"
    assert card["moderator_run_id"] == config.moderator_run_id
    assert all(s.get("run_id") for s in card["sides"])
    assert card["sides"][0]["run_id"] == f"{config.moderator_run_id}_pro"
    assert summary.debate_arguments["moderator_run_id"] == config.moderator_run_id
    assert summary.debate_arguments["sides"][0]["run_id"] == card["sides"][0]["run_id"]


def test_delegate_kickoff_headline_intensity_and_fallback():
    from agentcore.runtime.kickoff import delegate_kickoff_summary
    from agentcore.runtime.kickoff.summary import (
        format_kickoff_headline,
        intensity_short_label,
    )

    assert intensity_short_label("lean") == "MVP主流程"
    assert intensity_short_label("solo") is None
    assert intensity_short_label("standard") is None
    assert intensity_short_label("full") == "模块流水线"
    assert intensity_short_label("unknown") is None
    assert intensity_short_label(None) is None

    assert format_kickoff_headline(headcount=3, intensity="lean") == (
        "MVP主流程 · 预计 3 人"
    )
    assert format_kickoff_headline(headcount=2) == "预计 2 人开工"
    assert format_kickoff_headline(
        headcount=2, intensity="bogus"
    ) == "预计 2 人开工"

    plan = RunPlan(
        nodes=[
            RunSpec(run_id="r1", role="甲", task="做 A", depends_on=[]),
            RunSpec(run_id="r2", role="乙", task="做 B", depends_on=["r1"]),
            RunSpec(run_id="r3", role="丙", task="做 C", depends_on=["r2"]),
        ]
    )
    with_tier = delegate_kickoff_summary(plan, intensity="lean")
    assert with_tier.headline == "MVP主流程 · 预计 3 人"
    assert with_tier.card_payload()["headline"] == "MVP主流程 · 预计 3 人"

    plain = delegate_kickoff_summary(plan)
    assert plain.headline == "预计 3 人开工"

    # Old-shaped KickoffSummary without headline stays absent on the wire.
    from agentcore.runtime.kickoff.summary import KickoffSummary

    legacy = KickoffSummary(primitive="delegate", workers=[{"run_id": "r1"}])
    assert "headline" not in legacy.card_payload()


def _debate_tool(
    sink: EventSink,
    registry: InteractionRegistry,
    save,
    drop,
    *,
    permission_axes=None,
) -> DebateTool:
    if permission_axes is None:
        permission_axes = _KICKOFF_RULES
    return DebateTool(
        llm=Provider([]),
        sink=sink,
        system_prompt="sys",
        user_message="辩一下",
        tools=ToolRegistry(),
        base_tool_context=ctx(),
        conversation_id="c",
        ambient_armed=True,
        message_id="m1",
        suspension_saver=save,
        suspension_deleter=drop,
        permission_axes=permission_axes,
        registry=registry,
        captain_run_id="ceo",
        approval_gate=None,
    )


async def test_debate_top_level_must_kickoff():
    """顶层 debate 不再 await team_preview；直接开跑。"""
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list[TeamPreviewSuspension] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    tool = _debate_tool(sink, registry, _save, _drop)
    tool._run_moderator = _fake_debate_run  # type: ignore[method-assign]
    transcript = [
        LLMMessage(role="user", content="辩一下"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_debate",
                    function=ToolCallFunction(name="debate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog()
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await tool.execute(
            {
                "motion": "该不该上四天工作制？",
                "form": "debate",
                "sides": [
                    {"key": "pro", "name": "正方", "stance": "应推广"},
                    {"key": "con", "name": "反方", "stance": "暂缓"},
                ],
                "thorough": True,
            },
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)


async def test_debate_full_auto_skips_kickoff():
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    tool = _debate_tool(
        sink, registry, _save, _drop, permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED)
    )
    # skip_kickoff path isn't what we test — full_auto must not suspend before moderator.
    # Without LLM we can't finish moderator; patch _run_moderator.
    async def _fake_run(config, usage_metadata):
        return SimpleNamespace(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
            metadata={},
        )

    tool._run_moderator = _fake_run  # type: ignore[method-assign]
    result = await tool.execute(
        {
            "motion": "命题",
            "form": "debate",
            "sides": [
                {"key": "pro", "name": "正方", "stance": "a"},
                {"key": "con", "name": "反方", "stance": "b"},
            ],
        },
        ctx(),
    )
    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []


async def test_delegate_full_auto_multi_skips_card():
    """Regression: full_auto + ≥2 workers no longer pauses for plan half."""
    from agentcore.runtime.coordination.session import clear_active_coordination

    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["AOUT", "BOUT"]), sink, registry, _save, _drop)
    t._permission_axes = recipe_to_axes(AutonomyPolicy.MANAGED)
    transcript = [
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_del",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog()
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await t.execute(
            {
                "tasks": [
                    {"role": "研究员", "task": "做A"},
                    {"role": "写手", "task": "做B"},
                ],
            },
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    await _drain_coord()


def _adjust_journal(
    *,
    fulfilled: bool = False,
    note: str = "人太多，改成一个人做",
    first_id: str = "tp1",
    second_id: str = "tp2",
) -> list[dict]:
    entries = [
        {
            "kind": "team_preview_required",
            "payload": {"checkpoint_id": first_id, "revision": 1},
            "ts": "t0",
        },
        {
            "kind": "team_preview_resolved",
            "payload": {"checkpoint_id": first_id, "decision": "adjust", "note": note},
            "ts": "t1",
        },
    ]
    if fulfilled:
        entries.append(
            {
                "kind": "team_preview_required",
                "payload": {
                    "checkpoint_id": second_id,
                    "revision": 2,
                    "revised_from": first_id,
                    "revision_note": note,
                },
                "ts": "t2",
            }
        )
    return entries


def test_kickoff_adjust_state_lineage_and_fulfillment():
    note = "人太多，改成一个人做"
    pending = _adjust_journal(note=note)
    assert has_unfulfilled_kickoff_adjust(pending) is True
    pending_state = kickoff_adjust_state(pending)
    assert pending_state.revision == 2
    assert pending_state.revised_from == "tp1"
    assert pending_state.revision_note == note

    done = _adjust_journal(fulfilled=True, note=note)
    assert has_unfulfilled_kickoff_adjust(done) is False
    done_state = kickoff_adjust_state(done)
    assert done_state.revision == 1
    assert done_state.revised_from is None
    assert done_state.revision_note is None

    # 第二轮 adjust：谱系接到上一张卡。
    second = [
        *done,
        {
            "kind": "team_preview_resolved",
            "payload": {"checkpoint_id": "tp2", "decision": "adjust", "note": "再瘦"},
            "ts": "t3",
        },
    ]
    second_state = kickoff_adjust_state(second)
    assert second_state.unfulfilled is True
    assert second_state.revision == 3
    assert second_state.revised_from == "tp2"
    assert second_state.revision_note == "再瘦"

    assert has_unfulfilled_kickoff_adjust([]) is False
    assert kickoff_adjust_state([]).revision == 1


async def test_unfulfilled_adjust_solo_still_hangs_card():
    """修订后只剩 1 人也不再挂新开工卡。"""
    from agentcore.runtime.coordination.session import clear_active_coordination

    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
    note = "人太多，改成一个人做"
    sink.seed_journal(
        [
            {
                "type": "team_preview_required",
                "payload": {"checkpoint_id": "tp1", "revision": 1},
                "timestamp": "t0",
            },
            {
                "type": "team_preview_resolved",
                "payload": {
                    "checkpoint_id": "tp1",
                    "decision": "adjust",
                    "note": note,
                },
                "timestamp": "t1",
            },
        ]
    )
    saved: list[TeamPreviewSuspension] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["AOUT"]), sink, registry, _save, _drop)
    transcript = [
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_del",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog(
        inherited_entries=[
            {
                "kind": "team_preview_required",
                "payload": {"checkpoint_id": "tp1", "revision": 1},
                "ts": "t0",
            },
            {
                "kind": "team_preview_resolved",
                "payload": {
                    "checkpoint_id": "tp1",
                    "decision": "adjust",
                    "note": note,
                },
                "ts": "t1",
            },
        ]
    )
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await t.execute(
            {"tasks": [{"role": "写手", "task": "一个人做完"}]},
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(
        str(e.type) == "team_preview_required" and e.payload.get("revision") == 2
        for e in sink._history
    )
    await _drain_coord()


async def test_fulfilled_adjust_does_not_force_solo_card():
    """已兑现（此后已再出过开工卡）后，1 人不再强制挂卡。"""
    from agentcore.runtime.coordination.session import clear_active_coordination

    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
    note = "人太多，改成一个人做"
    sink.seed_journal(
        [
            {
                "type": "team_preview_required",
                "payload": {"checkpoint_id": "tp1", "revision": 1},
                "timestamp": "t0",
            },
            {
                "type": "team_preview_resolved",
                "payload": {
                    "checkpoint_id": "tp1",
                    "decision": "adjust",
                    "note": note,
                },
                "timestamp": "t1",
            },
            {
                "type": "team_preview_required",
                "payload": {
                    "checkpoint_id": "tp2",
                    "revision": 2,
                    "revised_from": "tp1",
                    "revision_note": note,
                },
                "timestamp": "t2",
            },
        ]
    )
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["AOUT"]), sink, registry, _save, _drop)
    transcript = [
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_del",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
    ]
    log = TurnFactLog(
        inherited_entries=[
            {
                "kind": "team_preview_required",
                "payload": {"checkpoint_id": "tp1", "revision": 1},
                "ts": "t0",
            },
            {
                "kind": "team_preview_resolved",
                "payload": {
                    "checkpoint_id": "tp1",
                    "decision": "adjust",
                    "note": note,
                },
                "ts": "t1",
            },
            {
                "kind": "team_preview_required",
                "payload": {
                    "checkpoint_id": "tp2",
                    "revision": 2,
                    "revised_from": "tp1",
                    "revision_note": note,
                },
                "ts": "t2",
            },
        ]
    )
    fl_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        result = await t.execute(
            {"tasks": [{"role": "写手", "task": "一个人做完"}]},
            ctx(),
        )
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is not ToolEffect.SUSPEND
    assert saved == []
    assert not any(str(e.type) == "team_preview_required" for e in sink._history)
    await _drain_coord()
