"""Durable suspension and resume_plan tests."""

import asyncio

import pytest

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.runs.types import RunPhase, RunState
from tests.delegate.conftest import (
    CKPT_DAG,
    Provider,
    ctx,
    resume_plan,
    tool,
    tool_durable,
)


async def _resolve_when_pending(registry, conversation_id, decision, note=""):
    from agentcore.runtime.checkpoints import CheckpointResponse

    for _ in range(500):
        pending = registry.list_pending(conversation_id)
        if pending:
            registry.resolve(
                pending[0].id,
                CheckpointResponse(decision=decision, note=note),
                conversation_id=conversation_id,
            )
            return pending[0]
        await asyncio.sleep(0.005)
    raise AssertionError("no pending plan_review appeared")


async def test_durable_pause_persists_frame_on_finalize(monkeypatch):
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.suspension import TurnSuspension, captain_transcript

    registry = InteractionRegistry()
    sink = EventSink()
    saved: list[TurnSuspension] = []
    dropped: list[str] = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(mid):
        dropped.append(mid)

    t = tool_durable(Provider(["S1OUT", "S2OUT"]), sink, registry, _save, _drop)
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
        # ②: the durable checkpoint finalizes in place (SUSPEND) — no live resolve, no drop.
        result = await t.execute({"tasks": CKPT_DAG, "coordinate": False}, ctx())
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert result.effect is ToolEffect.SUSPEND
    assert len(saved) == 1
    frame = saved[0]
    assert frame.message_id == "m1"
    assert frame.conversation_id == "conv1"
    assert frame.captain_run_id == "CEO"
    assert frame.tool_call_id == "call_del"
    assert len(frame.plan.nodes) == 2
    assert frame.completed
    assert any(s["role"] == "研究员" for s in frame.steps)
    assert any(p["role"] == "写手" for p in frame.pending)
    # journal_entries is the 唯一权威载体; the display seed derives (P0-B Phase 3).
    assert any(e["kind"] == "plan_review_required" for e in frame.journal_entries)
    assert any(e["type"] == "plan_review_required" for e in frame.journal)
    assert dropped == []  # finalize never drops the frame — it IS the resume record


async def test_durable_pause_captures_resume_scope():
    # The turn's project scope rides the durable frame so a fresh-process resume
    # re-wires consult to the same project (项目主题 first, then global).
    from agentcore.runtime.suspension import captain_transcript

    registry = InteractionRegistry()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(mid):
        pass

    t = tool_durable(
        Provider(["S1OUT", "S2OUT"]),
        EventSink(),
        registry,
        _save,
        _drop,
        folder_id="proj_42",
    )
    transcript = [
        LLMMessage(role="user", content="原始请求"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="call_del", function=ToolCallFunction(name="delegate", arguments="{}")),
            ],
        ),
    ]
    token = captain_transcript.set(transcript)
    try:
        await t.execute({"tasks": CKPT_DAG, "coordinate": False}, ctx())  # ②: finalizes in place
    finally:
        captain_transcript.reset(token)

    assert saved
    assert saved[0].folder_id == "proj_42"


async def test_durable_capture_skipped_without_transcript():
    """无 transcript ⇒ persist 返回 False ⇒ 跳过挂起放行（配置态不可用，D11）。"""
    registry = InteractionRegistry()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(mid):
        pass

    t = tool_durable(Provider(["S1OUT", "S2OUT"]), EventSink(), registry, _save, _drop)
    result = await t.execute({"tasks": CKPT_DAG, "coordinate": False}, ctx())
    assert saved == []
    assert registry.list_pending("conv1") == []
    assert "S1OUT" in result.output
    assert "S2OUT" in result.output


async def test_durable_saver_runtime_failure_terminates(monkeypatch):
    """saver 抛异常（运行态失败）⇒ 显式报错终止，不得 PROCEED 继续烧钱。"""
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.suspension import captain_transcript
    from agentcore.runtime.suspension.capture import SuspensionPersistError

    registry = InteractionRegistry()

    async def _save(_frame):
        raise RuntimeError("db down")

    async def _drop(_mid):
        pass

    t = tool_durable(Provider(["S1OUT", "S2OUT"]), EventSink(), registry, _save, _drop)
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
        with pytest.raises(SuspensionPersistError, match="db down"):
            await t.execute({"tasks": CKPT_DAG, "coordinate": False}, ctx())
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(fl_token)

    assert registry.list_pending("conv1") == []


async def test_durable_resume_drives_tail_from_journal_not_frame(monkeypatch):
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal import completed_from_journal, plan_from_journal
    from agentcore.runtime.pipeline.resume import settle_resumed_suspension
    from agentcore.runtime.suspension import (
        PlanReviewSuspension,
        captain_transcript,
        suspension_from_json,
    )

    registry = InteractionRegistry()
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    async def _drop(mid):
        pass

    pause_tool = tool_durable(Provider(["S1OUT", "S2OUT"]), EventSink(), registry, _save, _drop)
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
    log_token = current_fact_log.set(log)
    ct_token = captain_transcript.set(transcript)
    try:
        await pause_tool.execute({"tasks": CKPT_DAG, "coordinate": False}, ctx())  # ②: finalizes in place
    finally:
        captain_transcript.reset(ct_token)
        current_fact_log.reset(log_token)

    assert saved
    captured = saved[0]

    restored = suspension_from_json(captured.to_json())
    assert isinstance(restored, PlanReviewSuspension)
    assert restored.plan.nodes == []
    assert restored.completed == {}
    restored.journal_entries = list(captured.journal_entries)

    projected_plan = plan_from_journal(restored.journal_entries)
    assert projected_plan is not None and len(projected_plan.nodes) == 2
    assert len(completed_from_journal(restored.journal_entries)) == 1

    resume_sink = EventSink()
    resume_sink.seed_journal(
        [{"type": EventType.PLAN_REVIEW_REQUIRED.value, "payload": {}, "timestamp": "t"}]
    )
    resume_provider = Provider(["S2OUT"])
    resume_tool = tool(resume_provider, resume_sink)
    settled = await settle_resumed_suspension(
        restored,
        decision=CheckpointDecision.CONTINUE,
        note="",
        selected=[],
        sink=resume_sink,
        delegate_tool=resume_tool,
        execution_id="e_resume",
    )
    assert "S1OUT" in settled.output
    assert "S2OUT" in settled.output
    assert resume_provider.calls == 1


async def test_settle_resume_reuses_journal_execution_id():
    """Resume settle recovers the pause execution_id from journal run_plan.

    Pipeline prefers journal-projected execution_id (identity invariant); settle must
    also keep that id when re-emitting run_plan so ingestPlan does not remount frames.
    """
    from agentcore.runtime.pipeline.resume import settle_resumed_suspension
    from agentcore.runtime.suspension import PlanReviewSuspension

    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    frame = PlanReviewSuspension(
        message_id="m1",
        conversation_id="conv1",
        user_id="u1",
        captain_run_id="CEO",
        checkpoint_id="ck1",
        tool_call_id="call_del",
        base_system_prompt="SYS",
        user_message="req",
        plan=plan,
        completed=seed,
        steps=[{"run_id": plan.nodes[0].run_id, "role": "研究员", "summary": "S1OUT"}],
        pending=[{"run_id": plan.nodes[1].run_id, "role": "写手"}],
        journal_entries=[
            {"kind": EventType.RUN_PLAN.value, "payload": {"execution_id": "e_pause"}, "ts": "t0"},
        ],
    )
    resume_sink = EventSink()
    resume_tool = tool(Provider(["S2OUT"]), resume_sink)
    await settle_resumed_suspension(
        frame,
        decision=CheckpointDecision.CONTINUE,
        note="",
        selected=[],
        sink=resume_sink,
        delegate_tool=resume_tool,
        execution_id="e_fresh_mint",
    )
    run_plans = [e for e in resume_sink._history if e.type is EventType.RUN_PLAN]
    assert len(run_plans) == 1
    assert run_plans[0].payload["execution_id"] == "e_pause"


async def test_resume_plan_continue_runs_only_the_tail():
    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    provider = Provider(["S2OUT"])
    sink = EventSink()
    t = tool(provider, sink)
    result = await t.resume_plan(
        plan,
        seed,
        decision=CheckpointDecision.CONTINUE,
        note="",
        checkpoint_run_ids={plan.nodes[0].run_id},
        execution_id="e",
    )
    assert "S1OUT" in result.output
    assert "S2OUT" in result.output
    assert provider.calls == 1
    run_plans = [e for e in sink._history if e.type is EventType.RUN_PLAN]
    assert len(run_plans) == 1
    assert run_plans[0].payload["execution_id"] == "e"


async def test_resume_plan_stop_skips_the_tail():
    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    provider = Provider(["SHOULD_NOT_RUN"])
    t = tool(provider)
    result = await t.resume_plan(
        plan,
        seed,
        decision=CheckpointDecision.STOP,
        note="",
        checkpoint_run_ids={plan.nodes[0].run_id},
        execution_id="e",
    )
    assert "S1OUT" in result.output
    assert "SHOULD_NOT_RUN" not in result.output
    assert provider.calls == 0
    assert "写手" in result.output


async def test_resume_plan_adjust_steers_the_tail():
    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    provider = Provider(["S2OUT"])
    t = tool(provider)
    result = await t.resume_plan(
        plan,
        seed,
        decision=CheckpointDecision.ADJUST,
        note="把重点放在风险上",
        checkpoint_run_ids={plan.nodes[0].run_id},
        execution_id="e",
    )
    assert "S2OUT" in result.output
    s2_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写" in (m.content or "")
    )
    assert "把重点放在风险上" in s2_user


async def test_resume_plan_continue_llm_gate_notes_in_tail_prompt():
    """plan_review CONTINUE + llm ceo_review → 下游 prompt 含 gate_notes（非 steer）。"""
    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    provider = Provider(["S2OUT"])
    t = tool(provider)
    await t.resume_plan(
        plan,
        seed,
        decision=CheckpointDecision.CONTINUE,
        note="这条备注不应进 steer",
        checkpoint_run_ids={plan.nodes[0].run_id},
        execution_id="e",
        ceo_review={
            "source": "llm",
            "conclusion": "规格可过",
            "risks": ["缺回滚预案"],
            "suggestions": ["先灰度"],
        },
    )
    s2 = plan.by_id(plan.nodes[1].run_id)
    assert "规格可过" in s2.gate_notes
    assert s2.steer == ""  # CONTINUE+note 不 apply_steer
    s2_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写" in (m.content or "")
    )
    assert "规格可过" in (s2_user or "")
    assert "缺回滚预案" in (s2_user or "")
    assert "非否决" in (s2_user or "")
    assert "这条备注不应进 steer" not in (s2_user or "")


async def test_resume_plan_continue_deterministic_skips_gate_notes():
    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    provider = Provider(["S2OUT"])
    t = tool(provider)
    await t.resume_plan(
        plan,
        seed,
        decision=CheckpointDecision.CONTINUE,
        note="",
        checkpoint_run_ids={plan.nodes[0].run_id},
        execution_id="e",
        ceo_review={
            "source": "deterministic",
            "conclusion": "回落摘要",
            "risks": ["x"],
            "suggestions": ["y"],
        },
    )
    assert plan.by_id(plan.nodes[1].run_id).gate_notes == ""
    s2_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写" in (m.content or "")
    )
    assert "回落摘要" not in (s2_user or "")
    assert "用户已放行" not in (s2_user or "")

async def test_resume_plan_kickoff_continue_with_note_steers_all_unrun():
    """Kickoff CONTINUE + non-empty note ≡ former adjust (steer all unrun workers)."""
    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    provider = Provider(["S2OUT"])
    t = tool(provider)
    result = await t.resume_plan(
        plan,
        seed,
        decision=CheckpointDecision.CONTINUE,
        note="把重点放在风险上",
        checkpoint_run_ids={plan.nodes[0].run_id},
        execution_id="e",
        apply_kickoff_grant=True,
    )
    assert "S2OUT" in result.output
    s2_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写" in (m.content or "")
    )
    assert "把重点放在风险上" in s2_user


async def test_resume_plan_continue_with_note_without_kickoff_does_not_steer():
    """plan_review CONTINUE+note must not steer (UI still has a separate 调整)."""
    plan = resume_plan()
    seed = {plan.nodes[0].run_id: RunState(phase=RunPhase.COMPLETED, content="S1OUT")}
    provider = Provider(["S2OUT"])
    t = tool(provider)
    await t.resume_plan(
        plan,
        seed,
        decision=CheckpointDecision.CONTINUE,
        note="不应注入",
        checkpoint_run_ids={plan.nodes[0].run_id},
        execution_id="e",
        apply_kickoff_grant=False,
    )
    s2_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写" in (m.content or "")
    )
    assert "不应注入" not in s2_user
    assert plan.by_id(plan.nodes[1].run_id).steer in (None, "")
