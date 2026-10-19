"""Tests for motion_card journal selection used by research_first."""

from __future__ import annotations


def _sample_card(**overrides):
    base = {
        "motion": "一审判决是否过重",
        "sides": [
            {"key": "pro", "name": "正方", "stance": "过重"},
            {"key": "con", "name": "反方", "stance": "适当"},
        ],
        "fact_pointers": ["#r1"],
        "rationale": "价值判断分歧",
        "form": "debate",
    }
    base.update(overrides)
    return base


def test_select_motion_card_last_wins():
    from agentcore.memory.followups import select_motion_card_from_journal

    entries = [
        {
            "kind": "run_completed",
            "payload": {
                "debrief": {"summary": "a", "motion_card": _sample_card(motion="先到的命题")}
            },
        },
        {
            "type": "run_completed",  # sink shape uses type=
            "payload": {
                "debrief": {"summary": "b", "motion_card": _sample_card(motion="后到的命题")}
            },
        },
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}},
    ]
    card = select_motion_card_from_journal(entries)
    assert card is not None
    assert card["motion"] == "后到的命题"


def test_select_motion_card_skips_invalid_and_absent():
    from agentcore.memory.followups import select_motion_card_from_journal

    assert select_motion_card_from_journal(None) is None
    assert select_motion_card_from_journal([]) is None
    assert (
        select_motion_card_from_journal(
            [{"kind": "run_completed", "payload": {"debrief": {"summary": "无卡"}}}]
        )
        is None
    )
    assert (
        select_motion_card_from_journal(
            [
                {
                    "kind": "run_completed",
                    "payload": {"debrief": {"motion_card": {"motion": "残缺卡"}}},
                }
            ]
        )
        is None
    )


def test_select_motion_card_from_display_runs_journal_shape():
    """Cloud/local finalize: display run_completed.debrief → journal entries → stage_card."""
    from agentcore.memory.followups import select_motion_card_from_journal
    from agentcore.runtime.journal import journal_entries_from_display_runs

    card = _sample_card(motion="一审判决是否站得住脚")
    runs = {
        "events": [
            {
                "type": "run_completed",
                "payload": {
                    "run_id": "synth",
                    "agent_id": "synth",
                    "debrief": {"summary": "有核心争议", "motion_card": card},
                },
            }
        ]
    }
    entries = journal_entries_from_display_runs(runs)
    selected = select_motion_card_from_journal(entries)
    assert selected is not None
    assert selected["motion"] == "一审判决是否站得住脚"
    # Local fallback also accepts raw SSE events (type=) with the same payload nesting.
    assert select_motion_card_from_journal(runs["events"])["motion"] == selected["motion"]


def test_message_detail_projects_origin_from_usage():
    """usage.origin（如 execution_harvest）投影到 MessageDetail.origin。"""
    from datetime import datetime

    from agentcore.api.schemas.messages import MessageDetail

    detail = MessageDetail(
        id="m1",
        conversation_id="c1",
        role="user",
        content="【系统收口】后台团队任务已全部完成。",
        created_at=datetime(2026, 1, 1),
        origin="execution_harvest",
    )
    assert detail.origin == "execution_harvest"

