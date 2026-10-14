"""Leftover plan_review: extra checkpoint_after does not pause; resume_plan still drives tails."""

import pytest

from agentcore.core.types import ToolEffect
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


def test_leftover_plan_review_from_json_is_unknown_kind():
    """存量 kind=plan_review 帧：无 codec、from_json → ValueError。"""
    from agentcore.runtime.suspension import is_live_suspension_kind, suspension_from_json

    assert is_live_suspension_kind("plan_review") is False
    with pytest.raises(ValueError, match="unknown suspension kind"):
        suspension_from_json(
            {
                "kind": "plan_review",
                "message_id": "m1",
                "conversation_id": "conv1",
                "user_id": "u1",
                "captain_run_id": "CEO",
                "checkpoint_id": "ck1",
                "tool_call_id": "call_del",
                "base_system_prompt": "SYS",
                "user_message": "req",
            }
        )


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
    assert "撰写" in result.output


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


async def test_resume_plan_continue_with_note_without_kickoff_does_not_steer():
    """CONTINUE+note must not steer (UI still has a separate 调整)."""
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
    )
    s2_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写" in (m.content or "")
    )
    assert "不应注入" not in s2_user
    assert plan.by_id(plan.nodes[1].run_id).steer in (None, "")
