"""In-process walk of the exported LV/茉莉奶白 tape (leftover kickoff skip → end).

This is the acceptance HTTP-walk substitute when a live server isn't flagged:
same player path the SSE route uses, with pacing assertions. For a true HTTP
client walk against a running server see ``scripts/demo_tape_http_walk.py``.
"""

from __future__ import annotations

import json

import pytest

from agentcore.config.paths import PROJECT_ROOT
from agentcore.demo_tape.binding import TapeBinding
from agentcore.demo_tape.pacing import sleep_ms_for_gap
from agentcore.demo_tape.player import play_tape_events
from agentcore.demo_tape.schema import event_type
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.journal.writer import TurnJournalWriter

TAPE = PROJECT_ROOT / "demos" / "tapes" / "lv-molihua-trademark.json"


def _leftover_slice(events: list[dict], *, tail: int = 15) -> list[dict]:
    leftover_i = next(
        i for i, e in enumerate(events) if event_type(e) == "team_preview_required"
    )
    return events[: leftover_i + tail]


@pytest.mark.asyncio
async def test_real_tape_double_replay_skips_leftover_kickoff(monkeypatch):
    """真磁带连放两次：leftover team_preview skip，不 persist 开工卡，两次都播完切片。"""
    if not TAPE.exists():
        pytest.skip(f"tape not exported yet: {TAPE}")

    from agentcore.demo_tape import player as player_mod

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)
    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    data = json.loads(TAPE.read_text(encoding="utf-8"))
    events = _leftover_slice(list(data["events"]))
    recorded = next(
        str((e.get("payload") or {}).get("checkpoint_id") or "")
        for e in events
        if event_type(e) == "team_preview_required"
    )
    assert recorded  # the tape faithfully keeps its recorded leftover id

    for i, message_id in enumerate(("walk-msg-a", "walk-msg-b")):
        binding = TapeBinding(
            conversation_id=f"walk-conv-{i}",
            tape_path=TAPE,
            speed=100.0,
            max_gap_ms=20,
        )
        sink = EventSink(conversation_id=f"walk-conv-{i}", message_id=message_id)
        writer = TurnJournalWriter(
            turn_id=message_id, conversation_id=f"walk-conv-{i}", trace_id="b" * 32
        )
        result = await play_tape_events(
            sink=sink,
            events=events,
            start_index=0,
            binding=binding,
            message_id=message_id,
            conversation_id=f"walk-conv-{i}",
            user_id="walk-user",
            user_message="demo",
            folder_id=None,
            journal_writer=writer,
            trace_id="b" * 32,
        )
        assert result["finish_reason"] is FinishReason.END_TURN
        types = [e.type.value for e in sink._history]
        assert "team_preview_required" not in types
        assert "team_preview_resolved" not in types

    assert saved == []


@pytest.mark.asyncio
async def test_real_tape_leftover_skip_and_pacing(monkeypatch):
    if not TAPE.exists():
        pytest.skip(f"tape not exported yet: {TAPE}")

    from agentcore.demo_tape import player as player_mod

    saved: list = []

    async def fake_save(suspension):
        saved.append(suspension)

    async def noop_flush(self):
        return None

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)
    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    data = json.loads(TAPE.read_text(encoding="utf-8"))
    events = list(data["events"])
    assert any(event_type(e) == "team_preview_required" for e in events)
    assert any(event_type(e) == "debate_round_started" for e in events)
    assert any(event_type(e) == "debate_result" for e in events)

    # Pacing math: every original gap must compress under speed/cap.
    speed, max_gap = 8.0, 500
    prev = None
    for ev in events[:200]:
        t = int(ev["t_ms"])
        if prev is not None:
            delay = sleep_ms_for_gap(gap_ms=t - prev, speed=speed, max_gap_ms=max_gap)
            assert delay <= (max_gap / speed) / 1000.0 + 1e-9
        prev = t

    slice_events = _leftover_slice(events)
    binding = TapeBinding(
        conversation_id="walk-conv",
        tape_path=TAPE,
        speed=100.0,
        max_gap_ms=20,
    )
    sink = EventSink(conversation_id="walk-conv", message_id="walk-msg")
    writer = TurnJournalWriter(
        turn_id="walk-msg", conversation_id="walk-conv", trace_id="a" * 32
    )

    result = await play_tape_events(
        sink=sink,
        events=slice_events,
        start_index=0,
        binding=binding,
        message_id="walk-msg",
        conversation_id="walk-conv",
        user_id="walk-user",
        user_message="demo",
        folder_id=None,
        journal_writer=writer,
        trace_id="a" * 32,
    )
    assert result["finish_reason"] is FinishReason.END_TURN
    assert saved == []
    types = [e.type.value for e in sink._history]
    assert "team_preview_required" not in types
    assert "team_preview_resolved" not in types
