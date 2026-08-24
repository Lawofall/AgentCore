"""EventSink resume-seed / marker / content_reset reinjection (turn_paused batch 3).

Covers G1 seeded zone isolation, G7 marker helper (insert order + dedup), and G6
display-only reinjection. Unseeded / hook-unset paths must match status-quo behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agentcore.runtime.events import (
    EventSink,
    EventType,
    content_delta,
    content_reset,
    reasoning_delta,
    run_plan,
    tool_use_end,
    tool_use_start,
)
from agentcore.runtime.events.interaction import (
    checkpoint_required,
    plan_review_required,
)
from agentcore.runtime.events.sink import synthesize_required_marker


def _plan(execution_id: str = "exec-1") -> object:
    return run_plan(
        execution_id=execution_id,
        plan_type="multi_agent",
        task_summary="workers",
        agents=[{"id": "a1", "role": "研究员"}],
        runs=[{"id": "s1", "agent_id": "a1", "task": "调研", "depends_on": []}],
    )


def test_seed_isolation_streamed_excludes_seeded():
    sink = EventSink()
    sink.seed_process(
        [
            {"kind": "reasoning", "text": "pre-think"},
            {"kind": "content", "text": "pre-pause body"},
            {"kind": "checkpoint", "checkpoint_id": "cp0"},
        ]
    )
    sink.emit(reasoning_delta("live-think"))
    sink.emit(content_delta("live body"))

    assert sink.streamed_content() == "live body"
    assert sink.streamed_reasoning() == "live-think"
    # Persist projection merges seeded⊕live and keeps structural gate.
    timeline = sink.process_timeline()
    assert timeline is not None
    assert [s["kind"] for s in timeline] == [
        "reasoning",
        "content",
        "checkpoint",
        "reasoning",
        "content",
    ]


def test_seed_deep_copy_independent_of_caller():
    steps = [{"kind": "content", "text": "a"}, {"kind": "tool", "id": "t1"}]
    run_map = {"r1": [{"kind": "content", "text": "w"}]}
    sink = EventSink()
    sink.seed_process(steps)
    sink.seed_run_processes(run_map)
    steps[0]["text"] = "mutated"
    run_map["r1"][0]["text"] = "mutated"
    assert sink.raw_process()[0]["text"] == "a"
    assert sink.raw_run_processes()["r1"][0]["text"] == "w"


def test_content_reset_pops_live_only():
    sink = EventSink()
    sink.seed_process(
        [
            {"kind": "content", "text": "seeded body"},
            {"kind": "checkpoint", "checkpoint_id": "cp0"},
        ]
    )
    sink.emit(content_delta("live body"))
    assert sink.streamed_content() == "live body"
    sink.emit(content_reset("finish_guard"))
    assert sink.streamed_content() == ""
    # Seeded content step survives; structural marker keeps process_timeline alive.
    timeline = sink.process_timeline()
    assert timeline is not None
    assert timeline[0] == {"kind": "content", "text": "seeded body"}
    assert timeline[1]["kind"] == "checkpoint"
    assert not any(s.get("kind") == "content" and s.get("text") == "live body" for s in timeline)


def test_raw_process_no_structural_gate():
    """G1 ⚠️: capture must not reuse process_timeline's None gate on pure prose."""
    sink = EventSink()
    sink.emit(reasoning_delta("think"))
    sink.emit(content_delta("answer"))
    assert sink.process_timeline() is None
    raw = sink.raw_process()
    assert [s["kind"] for s in raw] == ["reasoning", "content"]
    assert raw[1]["text"] == "answer"


def test_raw_process_merges_seeded_and_live():
    sink = EventSink()
    sink.seed_process([{"kind": "content", "text": "pre"}])
    sink.emit(content_delta("post"))
    assert sink.raw_process() == [
        {"kind": "content", "text": "pre"},
        {"kind": "content", "text": "post"},
    ]


def test_seed_run_processes_merge_and_stream_isolation():
    from agentcore.runtime.events import run_output_delta, run_reasoning_delta, run_started

    sink = EventSink()
    sink.seed_run_processes(
        {"r1": [{"kind": "reasoning", "text": "seeded-think"}, {"kind": "content", "text": "seeded-out"}]}
    )
    sink.emit(run_started("r1", "w1"))
    sink.emit(run_reasoning_delta("r1", "w1", "live-think"))
    sink.emit(run_output_delta("r1", "w1", "live-out"))

    merged = sink.run_process_timelines()
    assert merged is not None
    assert [s["kind"] for s in merged["r1"]] == [
        "reasoning",
        "content",
        "reasoning",
        "content",
    ]
    assert merged["r1"][0]["text"] == "seeded-think"
    assert merged["r1"][-1]["text"] == "live-out"
    # raw accessor has no empty-gate either
    assert sink.raw_run_processes()["r1"] == merged["r1"]


def test_synthesize_retired_team_preview_is_noop_before_team():
    """Leftover team_preview_required does not insert a team_preview marker."""
    steps: list[dict] = [
        {"kind": "content", "text": "intro"},
        {"kind": "team", "execution_id": "exec-1"},
        {"kind": "content", "text": "after"},
    ]
    assert not synthesize_required_marker(
        steps,
        "team_preview_required",
        {"checkpoint_id": "cp-tp"},
    )
    assert [s["kind"] for s in steps] == [
        "content",
        "team",
        "content",
    ]
    assert not any(s.get("kind") == "team_preview" for s in steps)


def test_synthesize_retired_team_preview_is_noop_without_team():
    steps: list[dict] = [{"kind": "content", "text": "only"}]
    assert not synthesize_required_marker(
        steps,
        "team_preview_required",
        {"checkpoint_id": "cp-tp"},
    )
    assert steps == [{"kind": "content", "text": "only"}]


def test_synthesize_marker_dedup_within_steps():
    steps: list[dict] = [{"kind": "checkpoint", "checkpoint_id": "cp1"}]
    assert not synthesize_required_marker(
        steps,
        EventType.CHECKPOINT_REQUIRED,
        {"checkpoint_id": "cp1"},
    )
    assert len(steps) == 1


def test_live_emit_marker_dedups_against_seeded():
    sink = EventSink()
    sink.seed_process([{"kind": "checkpoint", "checkpoint_id": "cp1"}])
    sink.emit(
        checkpoint_required(
            checkpoint_id="cp1",
            conversation_id="c1",
            question="again?",
        )
    )
    timeline = sink.process_timeline()
    assert timeline is not None
    assert sum(1 for s in timeline if s.get("kind") == "checkpoint") == 1


def test_live_retired_team_preview_does_not_insert_marker():
    """New turns do not emit a team_preview marker; leftover type is skipped."""
    sink = EventSink()
    sink.emit(_plan())
    sink.persist_required_marker(
        "team_preview_required",
        {"checkpoint_id": "cp-tp"},
    )
    timeline = sink.process_timeline()
    assert timeline is not None
    kinds = [s["kind"] for s in timeline]
    assert kinds == ["team"]
    assert "team_preview" not in kinds


def test_synthesize_plan_review_append():
    steps: list[dict] = [{"kind": "content", "text": "x"}]
    assert synthesize_required_marker(
        steps, EventType.PLAN_REVIEW_REQUIRED, {"checkpoint_id": "pr-1"}
    )
    assert [s["kind"] for s in steps] == ["content", "plan_review"]


def test_content_reset_reinjection_history_and_sse_skip_process_and_checkpointer():
    import asyncio

    sink = EventSink()
    ck = MagicMock()
    sink._checkpointer = ck
    sink.set_content_reset_reinjection("pre_pause\n\n")

    sink.emit(content_delta("draft"))
    assert sink.streamed_content() == "draft"
    sink.emit(content_reset("finish_guard"))

    # Live process: reset popped draft; reinjected delta did NOT re-enter process.
    assert sink.streamed_content() == ""
    assert sink.process_timeline() is None
    assert sink.raw_process() == []

    # Checkpointer saw draft + reset — not the reinjected delta.
    observed_types = [call.args[0].type for call in ck.observe.call_args_list]
    assert observed_types == [EventType.CONTENT_DELTA, EventType.CONTENT_RESET]

    # History: draft, reset, then a fresh reinjected delta (not coalesced into draft).
    assert [e.type for e in sink._history] == [
        EventType.CONTENT_DELTA,
        EventType.CONTENT_RESET,
        EventType.CONTENT_DELTA,
    ]
    assert sink._history[0].payload["delta"] == "draft"
    assert sink._history[-1].payload["delta"] == "pre_pause\n\n"

    # SSE queue: draft → reset → reinjected delta.
    queued: list[tuple[EventType, str | None]] = []
    while True:
        try:
            ev = sink._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if ev is None:
            break
        queued.append((ev.type, (ev.payload or {}).get("delta")))
    assert queued == [
        (EventType.CONTENT_DELTA, "draft"),
        (EventType.CONTENT_RESET, None),
        (EventType.CONTENT_DELTA, "pre_pause\n\n"),
    ]


def test_reinjection_hook_unset_is_status_quo():
    sink = EventSink()
    sink.emit(content_delta("draft"))
    sink.emit(content_reset("finish_guard"))
    assert sink.streamed_content() == ""
    # No extra content delta after reset in history.
    after_reset = False
    extra_delta = False
    for e in sink._history:
        if e.type is EventType.CONTENT_RESET:
            after_reset = True
            continue
        if after_reset and e.type is EventType.CONTENT_DELTA:
            extra_delta = True
    assert after_reset
    assert not extra_delta


def test_unseeded_behaviour_regression():
    """No seed / no hook → identical to pre-batch-3 projection semantics."""
    sink = EventSink()
    sink.emit(reasoning_delta("t"))
    sink.emit(content_delta("a"))
    assert sink.process_timeline() is None
    assert sink.streamed_content() == "a"
    assert sink.streamed_reasoning() == "t"
    assert sink.run_process_timelines() is None

    sink2 = EventSink()
    sink2.emit(tool_use_start("t1", "web_search", {"q": "x"}))
    sink2.emit(tool_use_end("t1", "web_search", success=True, output="ok"))
    sink2.emit(content_delta("ans"))
    tl = sink2.process_timeline()
    assert tl is not None
    assert [s["kind"] for s in tl] == ["tool", "content"]
    assert sink2.raw_process() == tl


def test_seeded_structural_gate_still_none_for_pure_prose():
    sink = EventSink()
    sink.seed_process([{"kind": "reasoning", "text": "t"}, {"kind": "content", "text": "c"}])
    assert sink.process_timeline() is None
    assert len(sink.raw_process()) == 2


def test_plan_review_required_lands_marker():
    sink = EventSink()
    sink.emit(
        plan_review_required(
            checkpoint_id="pr-1",
            conversation_id="c1",
            steps=[],
            pending=[],
        )
    )
    tl = sink.process_timeline()
    assert tl is not None
    assert tl == [{"kind": "plan_review", "checkpoint_id": "pr-1"}]
