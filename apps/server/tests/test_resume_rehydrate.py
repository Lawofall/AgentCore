"""Batch-5 resume pipeline: single-point turn_paused rehydration (G1–G5).

Covers display-state seed, citations dual落点, controller seed + settle补标,
finish/terminal reasoning join (multi-cycle), and legacy frames without turn_paused.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.engine import join_segments
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.facts import TurnPausedFact
from agentcore.runtime.loop_controller import LoopController
from agentcore.runtime.pipeline.resume.finish import finish_resume_turn, finish_terminal_resume
from agentcore.runtime.pipeline.resume.rehydrate import (
    batch_shape_for_settled_suspension,
    mark_controller_after_settle,
    rehydrate_from_turn_paused,
)
from agentcore.runtime.pipeline.resume.window import pre_pause_content
from agentcore.runtime.suspension import (
    AskUserSuspension,
    turn_citations,
)
from tests.llm_helpers import make_profile_params


def _ask_frame(**kwargs) -> AskUserSuspension:
    defaults = dict(
        message_id="m1",
        conversation_id="c1",
        user_id="u1",
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_ask",
        base_system_prompt="sys",
        user_message="hi",
        transcript=[],
        question="?",
        questions=[],
    )
    defaults.update(kwargs)
    return AskUserSuspension(**defaults)


def _paused_entry(**payload_overrides) -> dict:
    base = dict(
        checkpoint_id="ck1",
        suspension_kind="ask_user",
        content="挂起前正文",
        reasoning="思考段1",
        process=[
            {"kind": "reasoning", "text": "想"},
            {"kind": "content", "text": "挂起前正文"},
            {"kind": "checkpoint", "checkpoint_id": "ck1"},
        ],
        run_processes={"w1": [{"kind": "content", "text": "worker"}]},
        citations=[{"url": "https://a.example", "title": "A"}],
        controller={
            "post_delegate": False,
            "delegate_count": 0,
            "audit_gate_fired": False,
            "first_batch_substantial": False,
        },
    )
    base.update(payload_overrides)
    return TurnPausedFact(**base).to_fact().entry()


# --- rehydrate_from_turn_paused -------------------------------------------------


def test_rehydrate_seeds_process_citations_controller():
    sink = EventSink()
    sink.seed_journal(
        [{"type": EventType.CHECKPOINT_REQUIRED.value, "payload": {}, "timestamp": "t"}]
    )
    frame = _ask_frame(
        journal_entries=[_paused_entry()],
        citations=[{"url": "https://frame-only.example", "title": "frame"}],
    )
    hydrated = rehydrate_from_turn_paused(sink=sink, suspension=frame)

    assert hydrated.from_turn_paused is True
    assert hydrated.pre_pause_content == "挂起前正文"
    assert hydrated.pre_pause_reasoning == "思考段1"
    assert hydrated.citations == [{"url": "https://a.example", "title": "A"}]
    assert hydrated.controller_seed is not None
    assert hydrated.controller_seed["post_delegate"] is False

    timeline = sink.process_timeline()
    assert timeline is not None
    assert [s["kind"] for s in timeline] == ["reasoning", "content", "checkpoint"]
    assert sink.raw_run_processes()["w1"][0]["text"] == "worker"
    # Seeded zone only — live streamed_* stays empty until resume deltas.
    assert sink.streamed_content() == ""
    assert sink.streamed_reasoning() == ""


def test_rehydrate_citations_dual_sink_same_list():
    """citation_sink list and turn_citations contextvar must be the same object."""
    sink = EventSink()
    frame = _ask_frame(journal_entries=[_paused_entry()])
    hydrated = rehydrate_from_turn_paused(sink=sink, suspension=frame)
    token = turn_citations.set(hydrated.citations)
    try:
        pool = turn_citations.get()
        assert pool is hydrated.citations
        pool.append({"url": "https://b.example", "title": "B"})
        assert hydrated.citations[-1]["title"] == "B"
    finally:
        turn_citations.reset(token)


def test_rehydrate_citations_fall_back_to_frame_when_fact_empty():
    sink = EventSink()
    frame = _ask_frame(
        journal_entries=[_paused_entry(citations=[])],
        citations=[{"url": "https://frame.example", "title": "F"}],
    )
    hydrated = rehydrate_from_turn_paused(sink=sink, suspension=frame)
    assert hydrated.citations == [{"url": "https://frame.example", "title": "F"}]


def test_rehydrate_legacy_frame_no_turn_paused():
    """Old journals without turn_paused → no process seed, heuristic content path."""
    sink = EventSink()
    frame = _ask_frame(
        journal_entries=[
            {"kind": "turn_started", "payload": {"system_prompt": "s", "user_message": "u"}},
        ],
        citations=[{"url": "https://legacy.example", "title": "L"}],
        transcript=[
            LLMMessage(role="user", content="问"),
            LLMMessage(role="assistant", content="启发式正文"),
        ],
    )
    hydrated = rehydrate_from_turn_paused(sink=sink, suspension=frame)

    assert hydrated.from_turn_paused is False
    assert hydrated.pre_pause_content is None  # caller keeps transcript heuristic
    assert hydrated.pre_pause_reasoning == ""
    assert hydrated.controller_seed is None
    assert hydrated.citations == [{"url": "https://legacy.example", "title": "L"}]
    assert sink.process_timeline() is None
    assert sink.raw_process() == []
    # Heuristic still works for legacy.
    assert pre_pause_content(frame.transcript) == "启发式正文"


# --- settle 侧补标 (G5) ---------------------------------------------------------


def test_batch_shape_ask_user_is_zero():
    assert batch_shape_for_settled_suspension(_ask_frame()) == (0, False)


def test_settle_mark_skips_ask_user():
    seed = {"post_delegate": False, "delegate_count": 0}
    frame = _ask_frame()
    assert mark_controller_after_settle(seed, frame) is seed


def test_settle_mark_without_shape_would_leave_substantial_false():
    """Regression lock: bare mark_post_delegate() (no shape) keeps latch False."""
    c = LoopController()
    c.mark_post_delegate()  # no node_count / has_deps
    assert c.has_delegated is True
    assert c.first_batch_substantial is False


# --- finish / terminal reasoning join (G3) --------------------------------------


async def test_finish_resume_joins_pre_pause_reasoning_multi_cycle():
    """join(join(r1, r2), live) must not drop segments."""
    r1 = "周期1思考"
    r2 = "周期2思考"
    accumulated = join_segments(r1, r2)
    live = "恢复后思考"
    # Pure join contract (multi-cycle): no segment dropped.
    assert join_segments(accumulated, live) == join_segments(join_segments(r1, r2), live)
    assert "周期1思考" in join_segments(accumulated, live)
    assert "周期2思考" in join_segments(accumulated, live)
    assert "恢复后思考" in join_segments(accumulated, live)

    captain_state = SimpleNamespace(
        content="恢复后正文",
        reasoning=live,
        rounds=1,
        usage={},
        finish_override=None,
        cost={
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache_hit": 0,
            "cache_miss": 0,
            "total": 0,
        },
        model="m",
        duration_ms=0,
    )
    result = await finish_resume_turn(
        message_id="m1",
        captain_run_id="cap1",
        captain_state=captain_state,
        pre_pause_content=join_segments("正文1", "正文2"),
        delegate_tool=MagicMock(
            usage={},
            run_ledger=[],
            citations=[],
            collab={},
            continuation_count=0,
            user_continuation_count=0,
            dispose_open_supervised=AsyncMock(return_value=None),
        ),
        debate_tool=MagicMock(usage={}, run_ledger=[], citations=[]),
        profile=make_profile_params(max_rounds=20),
        citations=[],
        sink=EventSink(),
        fact_log=None,
        audit_recorder=SimpleNamespace(drops=0, flush=AsyncMock()),
        roster_writer=None,
        journal_writer=SimpleNamespace(flush=AsyncMock()),
        pre_pause_reasoning=accumulated,
    )
    assert result["reasoning_content"] == join_segments(accumulated, live)
    assert "正文1" in result["content"]
    assert "恢复后正文" in result["content"]


def test_finish_terminal_preserves_pre_pause_reasoning():
    result = finish_terminal_resume(
        message_id="m1",
        pre_pause_content="阶段成果",
        closing="先到这",
        sink=EventSink(),
        pre_pause_reasoning="停前思考",
    )
    assert result["content"] == "阶段成果\n\n先到这"
    assert result["reasoning_content"] == "停前思考"


def test_finish_terminal_no_reasoning_stays_none():
    result = finish_terminal_resume(
        message_id="m1",
        pre_pause_content="",
        closing="停",
        sink=EventSink(),
    )
    assert result["reasoning_content"] is None
