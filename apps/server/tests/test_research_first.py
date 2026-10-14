"""调研链证据探测。先调研按键 / 回灌文案已退役。
"""

from __future__ import annotations

import pytest

from agentcore.runtime.debate.research_first import has_research_chain_evidence
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


def test_leftover_team_preview_frame_skips_hydrate():
    """存量开工卡：from_json 未知 kind，不进 recover。"""
    with pytest.raises(ValueError, match="unknown suspension kind"):
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
