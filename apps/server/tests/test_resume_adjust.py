"""存量 leftover 编制确认 410；resume_plan ADJUST 仍 steer。"""

from __future__ import annotations

import pytest

from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.runs.types import RunPhase, RunState


@pytest.mark.asyncio
async def test_resume_plan_adjust_still_steers():
    """resume_plan ADJUST 仍 steer + drive。"""
    from tests.delegate.conftest import Provider, resume_plan, tool

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
        execution_id="e-review-adjust",
    )
    assert "S2OUT" in result.output
    assert provider.calls == 1
    s2_user = next(
        m.content
        for req in provider.requests
        for m in req.messages
        if m.role == "user" and "撰写" in (m.content or "")
    )
    assert "把重点放在风险上" in s2_user


@pytest.mark.asyncio
async def test_auto_grant_before_workers_does_not_hang_a_card():
    """command=auto 只记 silent grant，不挂卡、不跑 worker。"""
    from agentcore.core.types import WorkspaceBoundary
    from agentcore.runtime.graph.desk.worker_grant import maybe_auto_grant_before_workers
    from tests.delegate.conftest import Provider, tool

    provider = Provider(["SHOULD_NOT_RUN"])
    real = tool(provider)
    real._depth = 0
    real._pending_pause = False
    real._permission_axes = WorkspaceBoundary.FOLDER

    await maybe_auto_grant_before_workers(
        real,
        seed_completed=None,
    )
    assert provider.calls == 0


def test_leftover_team_preview_from_json_skips():
    """存量开工卡：from_json 未知 kind，不 recover。"""
    from agentcore.runtime.suspension import suspension_from_json

    with pytest.raises(ValueError, match="unknown suspension kind"):
        suspension_from_json(
            {
                "kind": "team_preview",
                "message_id": "m1",
                "conversation_id": "c1",
                "user_id": "u1",
                "captain_run_id": "cap1",
                "checkpoint_id": "ck_tp",
                "tool_call_id": "call_del",
                "base_system_prompt": "sys",
                "user_message": "组队",
                "primitive": "delegate",
            }
        )


def test_resume_adjust_requires_non_empty_note():
    """resume API：decision=adjust 必须带非空 note（与两端 UI 对齐）。"""
    from pydantic import ValidationError

    from agentcore.api.schemas.messages import ResumeTurnRequest

    with pytest.raises(ValidationError, match="非空意见"):
        ResumeTurnRequest(decision=CheckpointDecision.ADJUST, note="")
    with pytest.raises(ValidationError, match="非空意见"):
        ResumeTurnRequest(decision=CheckpointDecision.ADJUST, note="   ")
    ok = ResumeTurnRequest(decision=CheckpointDecision.ADJUST, note="人太多")
    assert ok.note == "人太多"
    ResumeTurnRequest(decision=CheckpointDecision.CONTINUE, note="")
    ResumeTurnRequest(decision=CheckpointDecision.STOP, note="")
    with pytest.raises(ValidationError):
        ResumeTurnRequest.model_validate(
            {"decision": CheckpointDecision.CONTINUE.value, "excluded_run_ids": ["a"]}
        )
    with pytest.raises(ValidationError):
        ResumeTurnRequest.model_validate({"decision": "research_first"})
