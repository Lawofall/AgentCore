"""辩论「先多视角调研再辩」— 判据 / 回灌文案。开工卡 resume 已退役。
"""

from __future__ import annotations

import pytest

from agentcore.runtime.kickoff.research_first import (
    has_research_chain_evidence,
    research_first_tool_result,
)
from agentcore.runtime.suspension import suspension_from_json
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
                    '{"playbook": "lens_crosscheck", "playbook_args": {"topic": "T"}}'
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
                "arguments": '{"playbook": "lens_crosscheck"}',
                "success": False,
                "result": "err",
                "tool_call_id": "dc1",
                "run_id": "captain",
            },
        }
    ]
    assert has_research_chain_evidence(failed) is False
    old_id = [
        {
            "kind": "tool_call",
            "payload": {
                "name": "delegate",
                "arguments": '{"playbook": "multi_lens_research"}',
                "success": True,
                "result": "done",
                "tool_call_id": "dc-old",
                "run_id": "captain",
            },
        }
    ]
    assert has_research_chain_evidence(old_id) is False


def test_research_first_tool_result_fills_motion_topic():
    text = research_first_tool_result(motion="该不该上四天工作制？", user_message="忽略我")
    assert "先多视角调研再辩" in text
    assert "请勿再次调用 debate" in text
    assert 'playbook="lens_crosscheck"' in text
    assert '"topic": "该不该上四天工作制？"' in text
    assert '"lenses"' in text
    assert "法律" in text
    assert "忽略我" not in text


def test_research_first_tool_result_falls_back_to_user_message():
    text = research_first_tool_result(motion="", user_message="帮我分析 LV 案")
    assert '"topic": "帮我分析 LV 案"' in text


def test_leftover_team_preview_frame_refuses_hydrate():
    """存量开工卡：from_json 走 410，不进 recover。"""
    from agentcore.core.errors import GoneError
    from agentcore.runtime.kickoff.retired import TEAM_PREVIEW_UNRECOVERABLE

    with pytest.raises(GoneError, match=TEAM_PREVIEW_UNRECOVERABLE):
        suspension_from_json(
            {
                "kind": "team_preview",
                "message_id": "m1",
                "conversation_id": "c1",
                "user_id": "u1",
                "captain_run_id": "cap",
                "checkpoint_id": "tp1",
                "tool_call_id": "dc1",
                "base_system_prompt": "",
                "user_message": "调研一下",
                "primitive": "delegate",
            }
        )
