"""辩论「先多视角调研再辩」— 判据 / 回灌文案。开工卡 resume 已退役。
"""

from __future__ import annotations

import pytest

from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.kickoff.research_first import (
    has_research_chain_evidence,
    research_first_tool_result,
)
from agentcore.runtime.recover import recover_turn
from agentcore.runtime.suspension import TeamPreviewSuspension
from agentcore.runtime.turn.state import TurnState
from agentcore.tools.builtin.motion_card import parse_motion_card


def _valid_card(**over: object) -> dict:
    card = {
        "motion": "该不该上四天工作制？",
        "form": "debate",
        "rationale": "双方对立轴清晰",
        "sides": [
            {"key": "pro", "name": "正方", "stance": "应推广"},
            {"key": "con", "name": "反方", "stance": "暂缓"},
        ],
        "fact_pointers": [],
    }
    card.update(over)
    parsed, err = parse_motion_card(card)
    assert parsed is not None and not err
    return parsed


def test_has_research_chain_evidence_preserves_old_offer_sources():
    assert has_research_chain_evidence([]) is False
    assert has_research_chain_evidence([], has_research_artifacts=True) is True
    entries_card = [
        {
            "kind": "run_completed",
            "payload": {
                "debrief": {"summary": "有争议", "motion_card": _valid_card()},
            },
        }
    ]
    assert has_research_chain_evidence(entries_card) is True
    entries_mlr = [
        {
            "kind": "tool_call",
            "payload": {
                "name": "delegate",
                "arguments": (
                    '{"playbook": "multi_lens_research", "playbook_args": {"topic": "T"}}'
                ),
                "success": True,
                "result": "done",
                "tool_call_id": "dc1",
                "run_id": "captain",
            },
        }
    ]
    assert has_research_chain_evidence(entries_mlr) is True
    failed = [
        {
            "kind": "tool_call",
            "payload": {
                "name": "delegate",
                "arguments": '{"playbook": "multi_lens_research"}',
                "success": False,
                "result": "err",
                "tool_call_id": "dc1",
                "run_id": "captain",
            },
        }
    ]
    assert has_research_chain_evidence(failed) is False


def test_research_first_tool_result_fills_motion_topic():
    text = research_first_tool_result(motion="该不该上四天工作制？", user_message="忽略我")
    assert "先多视角调研再辩" in text
    assert "请勿再次调用 debate" in text
    assert 'playbook="multi_lens_research"' in text
    assert '"topic": "该不该上四天工作制？"' in text
    assert "忽略我" not in text


def test_research_first_tool_result_falls_back_to_user_message():
    text = research_first_tool_result(motion="", user_message="帮我分析 LV 案")
    assert '"topic": "帮我分析 LV 案"' in text


@pytest.mark.asyncio
async def test_recover_research_first_on_delegate_kickoff_refuses():
    """非辩论开工卡 research_first：卡已退役，不降级 STOP、不开做。"""
    from agentcore.core.errors import GoneError
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.kickoff.retired import TEAM_PREVIEW_UNRECOVERABLE
    from agentcore.runtime.runs.plan import RunPlan

    sink = EventSink()
    suspension = TeamPreviewSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap",
        checkpoint_id="tp1",
        tool_call_id="dc1",
        base_system_prompt="",
        user_message="调研一下",
        plan=RunPlan(),
        workers=[
            {
                "run_id": "w1",
                "role": "调研",
                "task": "做A",
                "depends_on": [],
            }
        ],
        tools=[],
        primitive="delegate",
    )
    state = TurnState.from_journal([])

    class _FakeDelegate:
        async def resume_plan(self, *_a, **_k):
            raise AssertionError("resume_plan must not run")

    with pytest.raises(GoneError, match=TEAM_PREVIEW_UNRECOVERABLE):
        await recover_turn(
            state=state,
            sink=sink,
            delegate_tool=_FakeDelegate(),  # type: ignore[arg-type]
            execution_id="e1",
            suspension=suspension,
            decision=CheckpointDecision.RESEARCH_FIRST,
            note="",
        )
    assert sink._history == []
