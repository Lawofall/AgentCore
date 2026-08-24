"""Batch-4 turn_paused capture: three faces, multi-cycle join, ask_user absorb, 同源."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.engine.loop import CaptainLoopMirror, current_captain_loop
from agentcore.runtime.engine.segments import join_segments
from agentcore.runtime.events import EventSink, content_delta
from agentcore.runtime.facts import (
    TurnFactLog,
    TurnPausedFact,
    TurnStartedFact,
    current_fact_log,
    pre_pause_from_journal,
)
from agentcore.runtime.loop_controller import LoopController
from agentcore.runtime.suspension import AskUserSuspension, captain_transcript, turn_citations
from agentcore.runtime.suspension.capture import persist_suspension_capture
from agentcore.runtime.turn.paused_capture import build_turn_paused_fact


def _required(event_type: str, checkpoint_id: str = "cp-1") -> SimpleNamespace:
    return SimpleNamespace(
        type=SimpleNamespace(value=event_type),
        payload={"checkpoint_id": checkpoint_id},
        timestamp="t1",
    )


def _bind_fact_log() -> object:
    log = TurnFactLog()
    log.record_fact(
        TurnStartedFact(system_prompt="sys", user_message="hi", model_profile="m").to_fact()
    )
    return current_fact_log.set(log)


async def _capture(
    *,
    suspension_kind: str,
    required_event: SimpleNamespace,
    sink: EventSink | None,
    checkpoint_id: str = "cp-1",
) -> tuple[AskUserSuspension, str]:
    transcript = [LLMMessage(role="user", content="hi")]
    ct_token = captain_transcript.set(transcript)
    saved: list[AskUserSuspension] = []
    paused_content = ""

    def build_frame(capture):
        nonlocal paused_content
        paused_content = capture.paused_content
        return AskUserSuspension(
            message_id="msg-1",
            conversation_id="conv-1",
            user_id="user-1",
            captain_run_id="run-1",
            checkpoint_id=checkpoint_id,
            tool_call_id="tc-1",
            base_system_prompt="sys",
            user_message="hello",
            question="q",
            journal_entries=capture.journal_entries,
            transcript=capture.transcript,
            history=capture.history,
            citations=capture.citations,
            trace_id=capture.trace_id,
        )

    async def saver(frame: AskUserSuspension) -> None:
        saved.append(frame)

    try:
        ok = await persist_suspension_capture(
            checkpoint_id=checkpoint_id,
            required_event=required_event,
            build_frame=build_frame,
            saver=saver,
            sink=sink,
            suspension_kind=suspension_kind,
        )
    finally:
        captain_transcript.reset(ct_token)

    assert ok is True
    assert len(saved) == 1
    return saved[0], paused_content


def _last_turn_paused(frame: AskUserSuspension) -> TurnPausedFact:
    snap = pre_pause_from_journal(frame.journal_entries)
    assert snap is not None
    return snap


@pytest.mark.asyncio
async def test_ask_user_capture_assembles_turn_paused_with_checkpoint_marker() -> None:
    fl_token = _bind_fact_log()
    sink = EventSink()
    sink.emit(content_delta("live-prose"))
    sink._process.append({"kind": "reasoning", "text": "想A"})
    controller = LoopController()
    controller.mark_post_delegate(node_count=2, has_deps=True)
    mirror = CaptainLoopMirror(
        controller=controller,
        content_before_round="气泡基底",
        final_content="同轮将被吸收的散文",
        ask_user_content_folded=True,
    )
    mirror_token = current_captain_loop.set(mirror)
    cit_token = turn_citations.set([{"url": "https://a.example", "title": "A"}])
    try:
        frame, paused_content = await _capture(
            suspension_kind="ask_user",
            required_event=_required("checkpoint_required", "cp-ask"),
            sink=sink,
            checkpoint_id="cp-ask",
        )
    finally:
        current_captain_loop.reset(mirror_token)
        turn_citations.reset(cit_token)
        current_fact_log.reset(fl_token)

    kinds = [e["kind"] for e in frame.journal_entries]
    assert kinds[-2] == "checkpoint_required"
    assert kinds[-1] == "turn_paused"
    # Display journal still surfaces the card only (turn_paused is EXECUTION_ONLY).
    assert [e["type"] for e in frame.journal] == ["checkpoint_required"]

    snap = _last_turn_paused(frame)
    assert snap.checkpoint_id == "cp-ask"
    assert snap.suspension_kind == "ask_user"
    assert snap.content == "气泡基底"
    assert snap.reasoning == "想A"
    # Process timeline is journaled as process_* — not dual-written into turn_paused.
    assert snap.process == []
    assert any(
        e.get("kind") == "process_checkpoint"
        and (e.get("payload") or {}).get("checkpoint_id") == "cp-ask"
        for e in frame.journal_entries
    )
    assert snap.citations == [{"url": "https://a.example", "title": "A"}]
    assert snap.controller.get("post_delegate") is True
    # G4 暂停收口同源：absorb 后落库 message content == turn_paused.content
    assert paused_content == snap.content == "气泡基底"


@pytest.mark.asyncio
async def test_ask_user_explicit_message_keeps_round_prose_in_bubble() -> None:
    """Model-owned ``message`` does not fold: capture keeps same-round guidance."""
    fl_token = _bind_fact_log()
    sink = EventSink()
    controller = LoopController()
    prose = "这段同轮引导句应留在气泡"
    mirror = CaptainLoopMirror(
        controller=controller,
        content_before_round="",
        final_content=prose,
        ask_user_content_folded=False,
    )
    mirror_token = current_captain_loop.set(mirror)
    try:
        frame, paused_content = await _capture(
            suspension_kind="ask_user",
            required_event=_required("checkpoint_required"),
            sink=sink,
        )
    finally:
        current_captain_loop.reset(mirror_token)
        current_fact_log.reset(fl_token)

    snap = _last_turn_paused(frame)
    assert snap.content == prose
    assert paused_content == prose


@pytest.mark.asyncio
async def test_team_preview_capture_uses_final_content_and_marker_before_team() -> None:
    fl_token = _bind_fact_log()
    sink = EventSink()
    sink._process.extend(
        [
            {"kind": "content", "text": "开场"},
            {"kind": "team", "execution_id": "ex-1"},
            {"kind": "reasoning", "text": "规划中"},
        ]
    )
    controller = LoopController()
    mirror = CaptainLoopMirror(
        controller=controller,
        content_before_round="不应采用",
        final_content="开工前交付正文",
    )
    mirror_token = current_captain_loop.set(mirror)
    try:
        frame, paused_content = await _capture(
            suspension_kind="team_preview",
            required_event=_required("team_preview_required", "cp-team"),
            sink=sink,
            checkpoint_id="cp-team",
        )
    finally:
        current_captain_loop.reset(mirror_token)
        current_fact_log.reset(fl_token)

    snap = _last_turn_paused(frame)
    assert snap.suspension_kind == "team_preview"
    assert snap.content == "开工前交付正文"
    assert paused_content == snap.content
    assert snap.process == []
    assert not any(e.get("kind") == "process_team_preview" for e in frame.journal_entries)
    assert not any(
        s.get("kind") == "team_preview" for s in (sink.raw_process() or [])
    )


@pytest.mark.asyncio
async def test_plan_review_capture_includes_run_processes() -> None:
    fl_token = _bind_fact_log()
    sink = EventSink()
    sink._process.append({"kind": "content", "text": "复核前"})
    sink._run_processes["w1"] = [{"kind": "tool", "name": "search", "text": "查了"}]
    controller = LoopController()
    mirror = CaptainLoopMirror(
        controller=controller,
        content_before_round="旧",
        final_content="计划复核前正文",
    )
    mirror_token = current_captain_loop.set(mirror)
    try:
        frame, _ = await _capture(
            suspension_kind="plan_review",
            required_event=_required("plan_review_required", "cp-plan"),
            sink=sink,
            checkpoint_id="cp-plan",
        )
    finally:
        current_captain_loop.reset(mirror_token)
        current_fact_log.reset(fl_token)

    snap = _last_turn_paused(frame)
    assert snap.suspension_kind == "plan_review"
    assert snap.content == "计划复核前正文"
    assert snap.run_processes == {}
    assert snap.process == []
    assert any(
        e.get("kind") == "run_process_tool"
        and (e.get("payload") or {}).get("run_id") == "w1"
        for e in frame.journal_entries
    )
    assert any(
        e.get("kind") == "process_plan_review"
        and (e.get("payload") or {}).get("checkpoint_id") == "cp-plan"
        for e in frame.journal_entries
    )


@pytest.mark.asyncio
async def test_multi_cycle_capture_joins_content_reasoning_and_process() -> None:
    fl_token = _bind_fact_log()
    sink1 = EventSink()
    sink1._process.append({"kind": "reasoning", "text": "第一段思考"})
    sink1._process.append({"kind": "content", "text": "第一段过程"})
    controller = LoopController()
    mirror1 = CaptainLoopMirror(
        controller=controller,
        content_before_round="",
        final_content="第一段正文",
    )
    t1 = current_captain_loop.set(mirror1)
    try:
        frame1, _ = await _capture(
            suspension_kind="plan_review",
            required_event=_required("plan_review_required", "cp-1"),
            sink=sink1,
            checkpoint_id="cp-1",
        )
    finally:
        current_captain_loop.reset(t1)

    first = _last_turn_paused(frame1)
    assert first.content == "第一段正文"
    assert first.reasoning == "第一段思考"
    assert first.process == []

    # Simulate resume: inherit journal + seed prior process from process_* (not turn_paused).
    inherited = list(frame1.journal_entries)
    log2 = TurnFactLog(inherited_entries=inherited)
    fl2 = current_fact_log.set(log2)
    sink2 = EventSink()
    from agentcore.runtime.pipeline.resume.rehydrate import _process_lanes_from_journal

    prior_process, _ = _process_lanes_from_journal(inherited)
    sink2.seed_process(prior_process)
    sink2._process.append({"kind": "reasoning", "text": "第二段思考"})
    sink2._process.append({"kind": "content", "text": "第二段过程"})
    mirror2 = CaptainLoopMirror(
        controller=controller,
        content_before_round="",
        final_content="第二段正文",
    )
    t2 = current_captain_loop.set(mirror2)
    try:
        frame2, paused_content = await _capture(
            suspension_kind="plan_review",
            required_event=_required("plan_review_required", "cp-2"),
            sink=sink2,
            checkpoint_id="cp-2",
        )
    finally:
        current_captain_loop.reset(t2)
        current_fact_log.reset(fl2)
        current_fact_log.reset(fl_token)

    second = _last_turn_paused(frame2)
    assert second.content == join_segments("第一段正文", "第二段正文")
    # streamed_reasoning is live-only; prior reasoning comes from journal join.
    assert second.reasoning == join_segments("第一段思考", "第二段思考")
    assert paused_content == second.content
    assert second.process == []
    process_payloads = [
        e.get("payload") or {}
        for e in frame2.journal_entries
        if str(e.get("kind") or "").startswith("process_")
    ]
    texts = [s.get("text") for s in process_payloads]
    assert "第一段过程" in texts
    assert "第二段过程" in texts
    assert any(s.get("checkpoint_id") == "cp-1" for s in process_payloads)
    assert any(s.get("checkpoint_id") == "cp-2" for s in process_payloads)


@pytest.mark.asyncio
async def test_capture_best_effort_without_mirror_or_sink() -> None:
    fl_token = _bind_fact_log()
    try:
        frame, paused_content = await _capture(
            suspension_kind="ask_user",
            required_event=_required("checkpoint_required"),
            sink=None,
        )
    finally:
        current_fact_log.reset(fl_token)

    snap = _last_turn_paused(frame)
    assert snap.content == ""
    assert snap.reasoning == ""
    assert snap.process == []
    assert snap.controller == {}
    assert paused_content == ""


def test_build_turn_paused_fact_direct_ask_user_vs_other() -> None:
    controller = LoopController()
    mirror = CaptainLoopMirror(
        controller=controller,
        content_before_round="absorb-base",
        final_content="keep-deliverable",
        ask_user_content_folded=True,
    )
    token = current_captain_loop.set(mirror)
    sink = EventSink()
    sink._process.append({"kind": "reasoning", "text": "r"})
    try:
        ask = build_turn_paused_fact(
            checkpoint_id="c1",
            suspension_kind="ask_user",
            required_event=_required("checkpoint_required", "c1"),
            journal_entries_before_trailing=[],
            sink=sink,
        )
        plan = build_turn_paused_fact(
            checkpoint_id="c2",
            suspension_kind="plan_review",
            required_event=_required("plan_review_required", "c2"),
            journal_entries_before_trailing=[],
            sink=sink,
        )
    finally:
        current_captain_loop.reset(token)

    assert ask.content == "absorb-base"
    assert plan.content == "keep-deliverable"
    assert ask.reasoning == plan.reasoning == "r"


@pytest.mark.asyncio
async def test_paused_message_content_same_origin_with_prior_cycle() -> None:
    """Paused message content (capture.paused_content) matches turn_paused across cycles."""
    fl_token = _bind_fact_log()
    prior = TurnPausedFact(
        checkpoint_id="cp-0",
        suspension_kind="ask_user",
        content="上周期气泡",
        reasoning="上周期思考",
        process=[{"kind": "content", "text": "旧步"}],
    ).to_fact()
    log = current_fact_log.get()
    assert log is not None
    log.record_fact(prior)

    sink = EventSink()
    sink._process.append({"kind": "reasoning", "text": "本段思考"})
    controller = LoopController()
    mirror = CaptainLoopMirror(
        controller=controller,
        content_before_round="本段基底",
        final_content="不应出现",
        ask_user_content_folded=True,
    )
    t = current_captain_loop.set(mirror)
    try:
        frame, paused_content = await _capture(
            suspension_kind="ask_user",
            required_event=_required("checkpoint_required", "cp-n"),
            sink=sink,
            checkpoint_id="cp-n",
        )
    finally:
        current_captain_loop.reset(t)
        current_fact_log.reset(fl_token)

    snap = _last_turn_paused(frame)
    expected = join_segments("上周期气泡", "本段基底")
    assert snap.content == expected
    assert paused_content == expected
    assert snap.reasoning == join_segments("上周期思考", "本段思考")
