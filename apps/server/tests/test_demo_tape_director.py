"""Unit tests for demo-tape director transport / chapters / seek snap."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentcore.demo_tape.chapters import build_chapters, snap_to_event_index
from agentcore.demo_tape.pacing import sleep_ms_for_gap
from agentcore.demo_tape.transport import PlaybackState, PlaybackTransport, TransportRegistry
from agentcore.runtime.events import EventType


def _ev(et: str, t_ms: int, **payload: object) -> dict:
    return {"type": et, "t_ms": t_ms, "payload": payload}


def test_build_chapters_debate_structure():
    events = [
        _ev("run_started", 0, kind="captain"),
        _ev("tool_use_start", 2000, name="web_search"),
        _ev("team_preview_required", 32000, primitive="debate"),
        _ev("debate_round_started", 52000, round_no=1, focus="q1"),
        _ev("run_started", 52000, run_id="debate_x_r1_lv"),
        _ev("run_started", 188000, run_id="debate_x_r1_cx_lv"),
        _ev("debate_round", 319000, round_no=1),
        _ev("debate_round_started", 319000, round_no=2),
        _ev("run_started", 400000, run_id="debate_x_r2_cx_molij"),
        _ev("debate_round", 500000, round_no=2),
        _ev("debate_result", 600000, form="debate"),
    ]
    chapters = build_chapters(events)
    by_id = {c.id: c for c in chapters}
    assert by_id["opening"].event_index == 0
    assert "team_preview" not in by_id
    assert by_id["r1_argument"].label == "第1轮·立论"
    assert by_id["r1_cross"].label == "第1轮·质询"
    assert by_id["r1_score"].label == "第1轮·打分"
    assert by_id["r2_argument"].t_ms == 319000
    assert by_id["r2_cross"].event_index == 8
    assert by_id["verdict"].label == "终审"
    assert [c.id for c in chapters] == [
        "opening",
        "r1_argument",
        "r1_cross",
        "r1_score",
        "r2_argument",
        "r2_cross",
        "r2_score",
        "verdict",
    ]


def test_snap_to_event_index_nearest_boundary():
    events = [_ev("a", 0), _ev("b", 100), _ev("c", 250), _ev("d", 400)]
    assert snap_to_event_index(events, 0) == 0
    assert snap_to_event_index(events, 40) == 0
    assert snap_to_event_index(events, 60) == 1
    assert snap_to_event_index(events, 200) == 2
    assert snap_to_event_index(events, 999) == 3


def test_sleep_ms_for_gap_compatible_with_director_speeds():
    # Director clamps 0.5–8; pacing helper still accepts the same formula.
    assert sleep_ms_for_gap(gap_ms=2000, speed=0.5, max_gap_ms=3000) == pytest.approx(4.0)
    assert sleep_ms_for_gap(gap_ms=2000, speed=8.0, max_gap_ms=3000) == pytest.approx(0.25)
    assert sleep_ms_for_gap(gap_ms=60_000, speed=4.0, max_gap_ms=2000) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_transport_speed_change_interrupts_wait():
    t = PlaybackTransport(
        conversation_id="c1",
        tape_path=Path("x.json"),
        speed=1.0,
        max_gap_ms=60_000,
    )

    async def bump_speed() -> None:
        await asyncio.sleep(0.05)
        t.set_speed(8.0)

    start = asyncio.get_running_loop().time()
    task = asyncio.create_task(bump_speed())
    await t.await_gap(2000, event_index=1)  # would be 2s at 1x; interrupted
    await task
    elapsed = asyncio.get_running_loop().time() - start
    # Should finish well under the original 2s wall wait.
    assert elapsed < 1.2
    assert t.speed == 8.0


@pytest.mark.asyncio
async def test_transport_pause_and_resume():
    t = PlaybackTransport(
        conversation_id="c1",
        tape_path=Path("x.json"),
        speed=8.0,
        max_gap_ms=60_000,
    )
    t.begin_play(message_id="m1")
    t.pause()
    assert t.state is PlaybackState.PAUSED

    async def resume_later() -> None:
        await asyncio.sleep(0.05)
        t.resume()

    start = asyncio.get_running_loop().time()
    task = asyncio.create_task(resume_later())
    await t.await_gap(400, event_index=0)  # 50ms wall at 8x after resume
    await task
    elapsed = asyncio.get_running_loop().time() - start
    assert elapsed >= 0.04
    assert t.state is PlaybackState.PLAYING


@pytest.mark.asyncio
async def test_transport_burst_skips_delay_then_clears():
    t = PlaybackTransport(
        conversation_id="c1",
        tape_path=Path("x.json"),
        speed=1.0,
        max_gap_ms=60_000,
    )
    t.arm_burst(5, auto_resolve=True)
    assert t.should_skip_delay(3) is True
    assert t.should_auto_resolve_at(3) is True
    # Landing on target: no skip, no auto-resolve of the landed event.
    assert t.should_skip_delay(5) is False
    assert t.should_auto_resolve_at(5) is False

    start = asyncio.get_running_loop().time()
    await t.await_gap(10_000, event_index=2)  # burst → immediate
    assert asyncio.get_running_loop().time() - start < 0.05

    t.clear_burst_if_reached(5)
    assert t.burst_until_index is None
    assert t.auto_resolve_pauses is False


@pytest.mark.asyncio
async def test_await_gap_skips_sub_ms_wall_at_high_speed():
    """High binding speeds must not pay Windows wait_for timer tax per gap."""
    t = PlaybackTransport(
        conversation_id="c1",
        tape_path=Path("x.json"),
        speed=500.0,
        max_gap_ms=20,
    )
    t.begin_play(message_id="m1")
    start = asyncio.get_running_loop().time()
    for i in range(5_000):
        await t.await_gap(20, event_index=i)
    assert asyncio.get_running_loop().time() - start < 0.5


def test_transport_attach_preserves_binding_speed_above_director_cap():
    reg = TransportRegistry()
    t = reg.attach(
        conversation_id="fast",
        tape_path=Path("a.json"),
        speed=500.0,
        max_gap_ms=20,
        event_count=10,
        duration_ms=1000,
        tape_id="a",
    )
    assert t.speed == 500.0
    t.set_speed(99.0)
    assert t.speed == 8.0  # director REST still clamps


def test_transport_registry_preserves_speed_and_armed_burst_on_reattach():
    reg = TransportRegistry()
    t1 = reg.attach(
        conversation_id="c1",
        tape_path=Path("a.json"),
        speed=4.0,
        max_gap_ms=2000,
        event_count=10,
        duration_ms=1000,
        tape_id="a",
    )
    t1.set_speed(2.0)
    t1.mark_finished()
    # Rewind path: re-arm burst on the finished transport, then attach a new play.
    t1.arm_burst(7, auto_resolve=True)
    t2 = reg.attach(
        conversation_id="c1",
        tape_path=Path("a.json"),
        speed=4.0,
        max_gap_ms=2000,
        event_count=10,
        duration_ms=1000,
        tape_id="a",
    )
    assert t2.speed == 2.0
    assert t2.burst_until_index == 7
    assert t2.auto_resolve_pauses is True


@pytest.mark.asyncio
async def test_player_burst_skips_leftover_team_preview(monkeypatch):
    """Seek past leftover team_preview skips the retired event (no emit / no pause)."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.demo_tape.transport import PlaybackTransport
    from agentcore.runtime.events import EventSink, EventType, FinishReason
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):  # noqa: ANN001
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    saved: list = []

    async def fake_save(suspension):  # noqa: ANN001
        saved.append(suspension)

    monkeypatch.setattr(player_mod, "save_paused_turn", fake_save)

    events = [
        {"type": "content_delta", "payload": {"delta": "开场"}, "t_ms": 0},
        {
            "type": "team_preview_required",
            "payload": {
                "checkpoint_id": "cp-src",
                "primitive": "debate",
                "workers": [],
                "tools": [],
            },
            "t_ms": 100,
        },
        {"type": "content_delta", "payload": {"delta": "辩论中"}, "t_ms": 200},
    ]
    binding = TapeBinding(
        conversation_id="c", tape_path=Path("unused.json"), speed=1.0, max_gap_ms=50
    )
    transport = PlaybackTransport(
        conversation_id="c",
        tape_path=Path("unused.json"),
        speed=4.0,
        max_gap_ms=50,
        event_count=3,
        duration_ms=200,
    )
    transport.arm_burst(2, auto_resolve=True)

    sink = EventSink(conversation_id="c", message_id="m")
    writer = TurnJournalWriter(turn_id="m", conversation_id="c", trace_id="t" * 32)
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
        transport=transport,
    )
    assert result["finish_reason"] is FinishReason.END_TURN
    assert result["content"] == "开场辩论中"
    assert saved == []
    types = [e.type.value for e in sink._history]
    assert "team_preview_required" not in types
    assert "team_preview_resolved" not in types
    assert EventType.CONTENT_DELTA.value in types
    assert transport.state is PlaybackState.FINISHED


@pytest.mark.asyncio
async def test_player_lands_on_checkpoint_without_auto_resolve(monkeypatch):
    """Seek *to* checkpoint (not past) still durable-pauses."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.demo_tape.transport import PlaybackTransport
    from agentcore.runtime.events import EventSink, FinishReason
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def fake_pause(**kwargs):  # noqa: ANN003
        transport = kwargs.get("transport")
        if transport is not None:
            transport.mark_awaiting_interaction(event_index=1, t_ms=100)
        return {
            "message_id": "m",
            "content": "开场",
            "reasoning_content": None,
            "finish_reason": FinishReason.PAUSED,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "rounds": 0,
            "citations": None,
            "cost_runs": [],
            "journal_entries": None,
            "collab": {},
            "audit_drops": 0,
        }

    monkeypatch.setattr(player_mod, "_pause_durable", fake_pause)

    events = [
        {"type": "content_delta", "payload": {"delta": "开场"}, "t_ms": 0},
        {
            "type": "checkpoint_required",
            "payload": {"checkpoint_id": "cp", "question": "继续？"},
            "t_ms": 100,
        },
        {"type": "content_delta", "payload": {"delta": "后"}, "t_ms": 200},
    ]
    binding = TapeBinding(
        conversation_id="c", tape_path=Path("unused.json"), speed=1.0, max_gap_ms=50
    )
    transport = PlaybackTransport(
        conversation_id="c",
        tape_path=Path("unused.json"),
        speed=4.0,
        max_gap_ms=50,
    )
    # Burst until the pause index — land on it, do not cross.
    transport.arm_burst(1, auto_resolve=False)

    sink = EventSink(conversation_id="c", message_id="m")
    writer = TurnJournalWriter(turn_id="m", conversation_id="c", trace_id="t" * 32)
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
        transport=transport,
    )
    assert result["finish_reason"] is FinishReason.PAUSED
    assert transport.state is PlaybackState.AWAITING_INTERACTION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("required_type", "resolved_type"),
    [
        ("checkpoint_required", EventType.CHECKPOINT_RESOLVED),
        ("plan_review_required", EventType.PLAN_REVIEW_RESOLVED),
    ],
)
async def test_player_burst_auto_resolves_cold_path_pauses(
    monkeypatch, required_type: str, resolved_type: EventType
):
    """Seek past checkpoint / plan_review emits required+resolved (no durable hang)."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.demo_tape.transport import PlaybackTransport
    from agentcore.runtime.events import EventSink, FinishReason
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):  # noqa: ANN001
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    payload: dict
    if required_type == "checkpoint_required":
        payload = {"checkpoint_id": "cp-src", "question": "继续？"}
        required_et = EventType.CHECKPOINT_REQUIRED
    else:
        payload = {
            "checkpoint_id": "pr-src",
            "steps": [{"run_id": "r1"}],
            "pending": [],
        }
        required_et = EventType.PLAN_REVIEW_REQUIRED

    events = [
        {"type": "content_delta", "payload": {"delta": "开场"}, "t_ms": 0},
        {"type": required_type, "payload": payload, "t_ms": 100},
        {"type": "content_delta", "payload": {"delta": "后续"}, "t_ms": 200},
    ]
    binding = TapeBinding(
        conversation_id="c", tape_path=Path("unused.json"), speed=1.0, max_gap_ms=50
    )
    transport = PlaybackTransport(
        conversation_id="c",
        tape_path=Path("unused.json"),
        speed=4.0,
        max_gap_ms=50,
        event_count=3,
        duration_ms=200,
    )
    transport.arm_burst(2, auto_resolve=True)

    sink = EventSink(conversation_id="c", message_id="m")
    writer = TurnJournalWriter(turn_id="m", conversation_id="c", trace_id="t" * 32)
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
        transport=transport,
    )
    assert result["finish_reason"] is FinishReason.END_TURN
    assert result["content"] == "开场\n\n后续"
    types = [e.type for e in sink._history]
    assert required_et in types
    assert resolved_type in types
    assert types.count(EventType.CONTENT_DELTA) == 2
    assert transport.state is PlaybackState.FINISHED


def test_has_pause_before_covers_all_wired_kinds():
    from agentcore.demo_tape.director import _has_pause_before

    events = [
        {"type": "content_delta", "t_ms": 0},
        {"type": "checkpoint_required", "t_ms": 10},
        {"type": "content_delta", "t_ms": 20},
        {"type": "plan_review_required", "t_ms": 30},
        {"type": "team_preview_required", "t_ms": 40},
        {"type": "approval_required", "t_ms": 50},
    ]
    assert _has_pause_before(events, 0, 2) is True
    assert _has_pause_before(events, 2, 4) is True
    assert _has_pause_before(events, 0, 1) is False
    # leftover team_preview is retired — not a wired interactive pause.
    assert _has_pause_before(events, 4, 5) is False
    assert _has_pause_before(events, 5, 6) is True


@pytest.mark.asyncio
async def test_player_burst_auto_resolves_approval(monkeypatch):
    """Seek past approval_required emits required+resolved and continues (no hang)."""
    from agentcore.demo_tape import player as player_mod
    from agentcore.demo_tape.binding import TapeBinding
    from agentcore.demo_tape.player import play_tape_events
    from agentcore.demo_tape.transport import PlaybackTransport
    from agentcore.runtime.events import EventSink, EventType, FinishReason
    from agentcore.runtime.journal.writer import TurnJournalWriter

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(player_mod, "pacing_sleep", fake_sleep)

    async def noop_flush(self):  # noqa: ANN001
        return None

    monkeypatch.setattr(TurnJournalWriter, "flush", noop_flush)

    events = [
        {"type": "content_delta", "payload": {"delta": "开场"}, "t_ms": 0},
        {
            "type": "approval_required",
            "payload": {
                "approval_id": "ap-src",
                "tool_call_id": "tc-src",
                "tool_name": "file_write",
                "arguments": {"path": "x"},
            },
            "t_ms": 100,
        },
        {"type": "content_delta", "payload": {"delta": "后续"}, "t_ms": 200},
    ]
    binding = TapeBinding(
        conversation_id="c-ap", tape_path=Path("unused.json"), speed=1.0, max_gap_ms=50
    )
    transport = PlaybackTransport(
        conversation_id="c-ap",
        tape_path=Path("unused.json"),
        speed=4.0,
        max_gap_ms=50,
        event_count=3,
        duration_ms=200,
    )
    transport.arm_burst(2, auto_resolve=True)

    sink = EventSink(conversation_id="c-ap", message_id="m")
    writer = TurnJournalWriter(turn_id="m", conversation_id="c-ap", trace_id="t" * 32)
    result = await play_tape_events(
        sink=sink,
        events=events,
        start_index=0,
        binding=binding,
        message_id="m",
        conversation_id="c-ap",
        user_id="u",
        user_message="go",
        folder_id=None,
        journal_writer=writer,
        transport=transport,
    )
    assert result["finish_reason"] is FinishReason.END_TURN
    # Hot-path approval is not a durable content seam — no join_segments joiner.
    assert result["content"] == "开场后续"
    types = [e.type for e in sink._history]
    assert EventType.APPROVAL_REQUIRED in types
    assert EventType.APPROVAL_RESOLVED in types
    assert types.count(EventType.CONTENT_DELTA) == 2
    assert transport.state is PlaybackState.FINISHED


def test_real_tape_chapters_lv_molihua():
    from agentcore.config.paths import PROJECT_ROOT
    from agentcore.demo_tape.export import load_tape

    path = PROJECT_ROOT / "demos" / "tapes" / "lv-molihua-trademark.json"
    if not path.exists():
        pytest.skip("lv-molihua tape not in workspace")
    events = list(load_tape(path).get("events") or [])
    chapters = build_chapters(events)
    labels = [c.label for c in chapters]
    assert "开场检索" in labels
    assert "组队授权" not in labels
    assert "终审" in labels
    assert any("立论" in x for x in labels)
    assert any("质询" in x for x in labels)
    assert any("打分" in x for x in labels)
    # Monotonic in tape time.
    ts = [c.t_ms for c in chapters]
    assert ts == sorted(ts)
