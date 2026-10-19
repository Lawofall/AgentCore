"""通用回放 source 适配器（录制回放通用化提案 步③）。"""

from __future__ import annotations

import pytest

from agentcore.demo_tape.identity import remint_interaction_ids
from agentcore.replay import (
    ConsumerKind,
    DocumentKind,
    assert_fold_consumer,
    assert_sink_consumer,
    open_event_document,
    prepare_replay_source,
)


def test_open_turn_fixture_normalizes_contract_fields():
    doc = open_event_document(
        {
            "name": "single_agent_text",
            "description": "d",
            "events": [
                {"type": "content_delta", "payload": {"delta": "hi"}, "timestamp": "2026-01-01T00:00:00.000Z"},
            ],
            "projected": {"status": "completed", "messages": [], "runs": [], "pendingInteraction": None, "cost": None},
        }
    )
    assert doc.kind is DocumentKind.TURN_FIXTURE
    assert doc.events[0]["type"] == "content_delta"
    assert doc.events[0]["timestamp"] == "2026-01-01T00:00:00.000Z"
    assert "kind" not in doc.events[0]
    assert doc.has_pacing is False


def test_open_tape_preserves_pacing_superset():
    doc = open_event_document(
        {
            "version": 2,
            "meta": {"title": "demo"},
            "events": [
                {
                    "type": "content_delta",
                    "payload": {"delta": "x"},
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "t_ms": 120,
                }
            ],
        }
    )
    assert doc.kind is DocumentKind.TAPE
    assert doc.has_pacing is True
    assert doc.events[0]["t_ms"] == 120
    assert doc.name == "demo"


def test_open_recording_stitches_segments():
    doc = open_event_document(
        {
            "kind": "demo_tape_recording",
            "meta": {"message_id": "m1"},
            "segments": [
                {
                    "wall_t0_ms": 0,
                    "events": [
                        {"type": "message_start", "payload": {}, "timestamp": None, "t_ms": 0},
                    ],
                },
                {
                    "wall_t0_ms": 1000,
                    "events": [
                        {"type": "content_delta", "payload": {"delta": "a"}, "timestamp": None, "t_ms": 10},
                    ],
                },
            ],
        }
    )
    assert doc.kind is DocumentKind.RECORDING
    assert len(doc.events) == 2
    assert doc.events[0]["type"] == "message_start"
    assert doc.events[1]["type"] == "content_delta"


def test_fold_prepare_never_remints_interaction_ids():
    events = [
        {
            "type": "team_preview_required",
            "payload": {"checkpoint_id": "cp-fixed"},
            "timestamp": None,
        }
    ]
    source = prepare_replay_source(
        {"name": "fx", "events": events, "projected": {"status": "paused"}},
        consumer=ConsumerKind.FOLD,
    )
    assert source.consumer is ConsumerKind.FOLD
    assert source.events[0]["payload"]["checkpoint_id"] == "cp-fixed"


def test_sink_prepare_remints_and_requires_message_id():
    events = [
        {
            "type": "team_preview_required",
            "payload": {"checkpoint_id": "cp-src"},
            "timestamp": None,
        }
    ]
    with pytest.raises(ValueError, match="message_id"):
        prepare_replay_source({"events": events}, consumer=ConsumerKind.SINK)

    source = prepare_replay_source(
        {"events": events},
        consumer=ConsumerKind.SINK,
        message_id="turn-1",
    )
    expected = remint_interaction_ids(events, message_id="turn-1")
    assert source.events[0]["payload"]["checkpoint_id"] == expected[0]["payload"]["checkpoint_id"]
    assert source.events[0]["payload"]["checkpoint_id"] != "cp-src"


def test_ab_mutual_exclusion_asserts():
    fold = prepare_replay_source(
        {"events": [{"type": "content_delta", "payload": {"delta": "x"}, "timestamp": None}]},
        consumer=ConsumerKind.FOLD,
    )
    sink = prepare_replay_source(
        {"events": [{"type": "content_delta", "payload": {"delta": "x"}, "timestamp": None}]},
        consumer=ConsumerKind.SINK,
        message_id="m",
    )
    assert_fold_consumer(fold)
    assert_sink_consumer(sink)
    with pytest.raises(ValueError, match="mutual exclusion"):
        assert_sink_consumer(fold)
    with pytest.raises(ValueError, match="mutual exclusion"):
        assert_fold_consumer(sink)


def test_sink_prepare_keeps_tool_run_id():
    """SINK remints interaction ids; tool run_id stays as recorded (no v1 captain strip)."""
    events = [
        {"type": "run_started", "payload": {"run_id": "cap1", "kind": "captain"}, "t_ms": 0},
        {
            "type": "tool_use_start",
            "payload": {
                "tool_call_id": "t1",
                "tool_name": "web_search",
                "run_id": "cap1",
            },
            "t_ms": 10,
        },
    ]
    source = prepare_replay_source(
        {"events": events},
        consumer=ConsumerKind.SINK,
        message_id="m",
    )
    tool = next(ev for ev in source.events if ev["type"] == "tool_use_start")
    assert tool["payload"]["run_id"] == "cap1"


def test_fold_prepare_keeps_tool_run_id():
    """A 路不重铸、不改 run_id——向量/深链保持录制面。"""
    events = [
        {"type": "run_started", "payload": {"run_id": "cap1", "kind": "captain"}},
        {
            "type": "tool_use_start",
            "payload": {"tool_call_id": "t1", "tool_name": "web_search", "run_id": "cap1"},
        },
    ]
    source = prepare_replay_source({"events": events}, consumer=ConsumerKind.FOLD)
    tool = next(ev for ev in source.events if ev["type"] == "tool_use_start")
    assert tool["payload"]["run_id"] == "cap1"
