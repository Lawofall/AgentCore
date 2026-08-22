"""Unit tests for the thin team_preview gate (方案 A)."""

from __future__ import annotations

import asyncio

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.coordination.session import (
    active_coordination,
    clear_active_coordination,
)
from agentcore.runtime.delegate.preview import (
    should_preview_delegate_plan,
    worker_rows,
)
from agentcore.runtime.delegate.steer import apply_steer
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import TurnFactLog, current_fact_log
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunSpec
from agentcore.runtime.suspension import TeamPreviewSuspension, captain_transcript
from tests.delegate.conftest import Provider, ctx, tool_durable


def _plan(*nodes: RunSpec) -> RunPlan:
    plan = RunPlan()
    for n in nodes:
        plan.add(n)
    return plan


def test_should_preview_multi_worker():
    plan = _plan(
        RunSpec(run_id="r1", task="a", role="调研"),
        RunSpec(run_id="r2", task="b", role="撰写", depends_on=["r1"]),
    )
    assert should_preview_delegate_plan(plan) is True


def test_should_preview_skips_solo():
    plan = _plan(RunSpec(run_id="r1", task="alone", role="写手"))
    assert should_preview_delegate_plan(plan) is False


def test_should_preview_skips_solo_even_with_runtime_tags():
    """stance/round on RunSpec are runtime display tags — not kickoff hang marks."""
    plan = _plan(RunSpec(run_id="r1", task="辩", role="正方", stance="pro", round=1))
    assert should_preview_delegate_plan(plan) is False


async def test_confirmed_ask_still_suspends_team_preview():
    """journal 已有 checkpoint_resolved 时 ≥2 worker 也不再挂 team_preview。"""
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
    assert not any(e.type is EventType.TEAM_PREVIEW_REQUIRED for e in sink._history)
    session = active_coordination("e")
    if session is not None and session.drive_task is not None:
        await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination()


def test_worker_rows_shape():
    plan = _plan(
        RunSpec(run_id="r1", task="调研方案", role="调研"),
        RunSpec(run_id="r2", task="写", role="撰写", depends_on=["r1"], stance="con"),
    )
    rows = worker_rows(plan)
    assert rows[0]["role"] == "调研"
    assert "debate" not in rows[0]
    assert rows[1]["depends_on"] == ["r1"]
    assert "debate" not in rows[1]
    # D4: omitted form → can write files (legacy)
    assert rows[0]["write_capability"] == "can_write_files"
    assert rows[0]["write_capability_label"] == "可改文件"
    # 无显式 model → 行上不透出（跟槽）
    assert "model" not in rows[0]
    # 无 target / 无会话桌 → 仅显示名「本会话工作区」（勿留空）
    assert "target_folder_id" not in rows[0]
    assert rows[0]["target_folder_name"] == "本会话工作区"
    assert rows[1]["target_folder_name"] == "本会话工作区"


def test_worker_rows_desk_from_node_target():
    plan = _plan(
        RunSpec(run_id="r1", task="读 A", role="调研", target_folder_id="fold-a"),
        RunSpec(run_id="r2", task="读 B", role="调研", target_folder_id="fold-b"),
    )
    rows = worker_rows(plan)
    assert rows[0]["target_folder_id"] == "fold-a"
    assert rows[1]["target_folder_id"] == "fold-b"
    # enrich 前的占位；生产路径会换成名册名
    assert rows[0]["target_folder_name"] == "未命名文件夹"


def test_worker_rows_desk_falls_back_to_session():
    plan = _plan(
        RunSpec(run_id="r1", task="做", role="写手"),
        RunSpec(run_id="r2", task="做", role="校对", target_folder_id="fold-x"),
    )
    rows = worker_rows(plan, session_folder_id="fold-session")
    assert rows[0]["target_folder_id"] == "fold-session"
    assert rows[1]["target_folder_id"] == "fold-x"


async def test_enrich_worker_desk_names_resolves_and_scratch():
    from unittest.mock import patch

    from agentcore.runtime.kickoff.summary import enrich_worker_desk_names

    rows = [
        {"run_id": "r1", "target_folder_id": "fold-a", "target_folder_name": "未命名文件夹"},
        {"run_id": "r2", "target_folder_name": "本会话工作区"},
    ]

    async def _fake_lookup(folder_ids, *, user_id):
        assert user_id == "u1"
        assert folder_ids == {"fold-a"}
        return {"fold-a": "云项目甲"}

    with patch(
        "agentcore.runtime.delegate.target_desktop.lookup_folder_display_names",
        _fake_lookup,
    ):
        await enrich_worker_desk_names(rows, user_id="u1")

    assert rows[0]["target_folder_name"] == "云项目甲"
    assert rows[1]["target_folder_name"] == "本会话工作区"
    assert "target_folder_id" not in rows[1]


def test_worker_rows_emits_model_identity():
    plan = _plan(
        RunSpec(
            run_id="r1",
            task="调研",
            role="调研",
            model="platform/deepseek-v4-pro",
        ),
        RunSpec(
            run_id="r2",
            task="写",
            role="撰写",
            model="prov-1/gpt-4o",
        ),
    )
    rows = worker_rows(plan)
    assert rows[0]["model"] == "deepseek-v4-pro"
    assert rows[0]["origin"] == "platform"
    assert "provider_id" not in rows[0]
    assert rows[1]["model"] == "gpt-4o"
    assert rows[1]["origin"] == "byok"
    assert rows[1]["provider_id"] == "prov-1"


def test_worker_rows_write_capability_from_form():
    from agentcore.runtime.runs.types import Deliverable

    plan = _plan(
        RunSpec(
            run_id="r1",
            task="构建报告",
            role="构建工程师",
            deliverable=Deliverable(form="prose"),
        ),
        RunSpec(
            run_id="r2",
            task="修源码",
            role="修补员",
            deliverable=Deliverable(form="files"),
        ),
    )
    rows = worker_rows(plan)
    assert rows[0]["form"] == "prose"
    assert rows[0]["write_capability"] == "text_only"
    assert rows[0]["write_capability_label"] == "仅文字报告"
    assert rows[1]["form"] == "files"
    assert rows[1]["write_capability"] == "can_write_files"
    assert rows[1]["write_capability_label"] == "可改文件"


def test_apply_steer_empty_roots_targets_all():
    plan = _plan(
        RunSpec(run_id="r1", task="a", role="A"),
        RunSpec(run_id="r2", task="b", role="B", depends_on=["r1"]),
    )
    apply_steer(plan, {}, set(), "请更简洁")
    assert "请更简洁" in (plan.by_id("r1").steer or "")
    assert "请更简洁" in (plan.by_id("r2").steer or "")


async def test_coordinate_team_preview_suspends_before_fork():
    """coordinate + ≥2 worker：不再先挂开工卡，直接臂后台。"""
    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
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
        # Default coordinate=True (≥2 workers) — runs without a team_preview card.
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
    assert "团队已启动" in (result.output or "")
    session = active_coordination("e")
    assert session is not None and session.drive_task is not None
    assert saved == []
    assert not any(e.type is EventType.TEAM_PREVIEW_REQUIRED for e in sink._history)
    await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")


async def test_team_preview_continue_then_arms_coordination():
    """顶层 ≥2 worker execute 直接臂协调，无需 team_preview CONTINUE。"""
    clear_active_coordination()
    registry = InteractionRegistry()
    sink = EventSink()
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
    assert "团队已启动" in result.output
    session = active_coordination("e")
    assert session is not None and session.drive_task is not None
    await asyncio.wait_for(session.drive_task, timeout=10)
    clear_active_coordination("e")


async def test_kickoff_frame_captures_batch_coordination_and_fresh_tool_restores():
    """存量开工卡帧携带 coordination/team_brief/seed_notes；全新工具实例恢复后墙生效。

    真 bug（2026-07-20 P2 手驱真跑抓获）：挂起点在 setup_note_wall 之前，这三样只活在
    DelegateTool 实例上；耐久恢复走全新实例（_coordination 缺省 none），不随帧回灌则
    wall 批降级 → worker 被剥便签三件套、CEO 预贴便签永久丢失。
    """
    from agentcore.runtime.runs import build_run_plan
    from agentcore.runtime.suspension import suspension_from_json

    clear_active_coordination()
    plan, errors = build_run_plan(
        [
            {"role": "观察员", "task": "做A"},
            {"role": "撰稿人", "task": "做B"},
        ],
        valid_tools=set(),
        id_prefix="del_wall_",
        parent_run_id="CEO",
        depth=1,
    )
    assert not errors
    frame = TeamPreviewSuspension(
        message_id="m1",
        conversation_id="conv1",
        user_id="u",
        captain_run_id="CEO",
        checkpoint_id="ck_wall",
        tool_call_id="call_del",
        user_message="原始请求",
        base_system_prompt="SYS",
        journal_entries=[],
        plan=plan,
        workers=[{"run_id": n.run_id, "role": n.role, "task": n.task} for n in plan.nodes],
        coordination="wall",
        team_brief="统一用中文交付",
        seed_notes=[{"kind": "heads_up", "text": "接口用 REST"}],
    )
    assert frame.coordination == "wall"
    assert frame.team_brief == "统一用中文交付"
    assert frame.seed_notes == [{"kind": "heads_up", "text": "接口用 REST"}]
    rehydrated = suspension_from_json(frame.to_json())
    assert rehydrated.coordination == "wall"

    async def _save(_frame):
        return None

    async def _drop(_mid):
        pass

    sink2 = EventSink()
    t2 = tool_durable(Provider(["AOUT", "BOUT"]), sink2, InteractionRegistry(), _save, _drop)
    assert t2._coordination == "none"
    resumed = await t2.resume_plan(
        frame.plan,
        {},
        decision=CheckpointDecision.CONTINUE,
        note="",
        checkpoint_run_ids=frame.checkpoint_run_ids,
        execution_id="e",
        coordinate=False,
        apply_kickoff_grant=True,
        coordination=rehydrated.coordination,
        team_brief=rehydrated.team_brief,
        seed_notes=list(rehydrated.seed_notes),
    )
    assert resumed.success is True
    assert t2._coordination == "wall"
    assert t2._team_brief == "统一用中文交付"
    seeded = [
        e
        for e in sink2._history
        if e.type is EventType.TEAM_NOTE_POSTED and e.payload.get("source") == "ceo"
    ]
    assert len(seeded) == 1
    assert "接口用 REST" in seeded[0].payload.get("text", "")
    clear_active_coordination()
