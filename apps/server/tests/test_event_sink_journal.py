"""Tests for the EventSink execution journal (Phase 2 history persistence).

The sink taps every emitted run/tool event into an ordered journal so the turn's
multi-agent team graph can be persisted on the assistant message and replayed on
reload. A turn that never delegated (no ``run_plan``) persists nothing.
"""

from agentcore.runtime.events import (
    EventSink,
    EventType,
    FinishReason,
    content_delta,
    message_end,
    message_start,
    reasoning_delta,
    run_completed,
    run_plan,
    run_started,
    tool_use_end,
    tool_use_progress,
    tool_use_start,
)


def _plan() -> object:
    return run_plan(
        execution_id="exec-1",
        plan_type="multi_agent",
        task_summary="2 个 worker",
        agents=[{"id": "a1", "role": "研究员"}],
        runs=[{"id": "s1", "agent_id": "a1", "task": "调研", "depends_on": []}],
    )


def test_journal_accumulates_run_and_tool_events_in_order():
    sink = EventSink()
    sink.emit(_plan())
    sink.emit(run_started("s1", "a1"))
    sink.emit(tool_use_start("t1", "web_search", {"q": "x"}))
    sink.emit(tool_use_end("t1", "web_search", success=True, output="ok"))
    sink.emit(run_completed("s1", "a1", output_summary="done", duration_ms=12))

    journal = sink.execution_journal()
    assert journal is not None
    types = [e["type"] for e in journal]
    assert types == [
        EventType.RUN_PLAN.value,
        EventType.RUN_STARTED.value,
        EventType.TOOL_USE_START.value,
        EventType.TOOL_USE_END.value,
        EventType.RUN_COMPLETED.value,
    ]
    # Each entry carries the replayable shape: type + payload + timestamp.
    assert all(set(e) == {"type", "payload", "timestamp"} for e in journal)
    assert journal[1]["payload"]["run_id"] == "s1"


def test_non_execution_events_are_not_journalled():
    sink = EventSink()
    sink.emit(message_start("m1", conversation_id="c1"))
    sink.emit(_plan())
    sink.emit(content_delta("hello"))
    sink.emit(message_end(FinishReason.END_TURN))

    journal = sink.execution_journal()
    assert journal is not None
    # Only the run_plan is journalled — message_start / content_delta / message_end
    # are conversation-stream events, not part of the team graph.
    assert [e["type"] for e in journal] == [EventType.RUN_PLAN.value]


def test_no_plan_means_no_runs_payload():
    """A single-agent turn (CEO tool calls but never delegating) persists no runs."""
    sink = EventSink()
    sink.emit(content_delta("just chatting"))
    sink.emit(tool_use_start("t1", "web_search", {"q": "x"}))
    sink.emit(tool_use_end("t1", "web_search", success=True, output="ok"))

    assert sink.execution_journal() is None


def test_approval_alone_is_a_journal_surface():
    # 统一时间线二期 D5: 单聊热审批无 run_plan 时仍须过 journal surface gate，
    # 否则 reload 后 approval_required 从客户端 events 消失、痕迹无法补标记。
    from agentcore.runtime.events import approval_required

    sink = EventSink()
    sink.emit(content_delta("需要你确认一下"))
    sink.emit(
        approval_required(
            approval_id="appr-1",
            conversation_id="c1",
            tool_call_id="t1",
            tool_name="shell",
            arguments={"cmd": "rm -rf /"},
        )
    )
    journal = sink.execution_journal()
    assert journal is not None
    assert [e["type"] for e in journal] == [EventType.APPROVAL_REQUIRED.value]
    assert journal[0]["payload"]["approval_id"] == "appr-1"
    process = sink.process_timeline()
    assert process is not None
    assert {"kind": "approval", "approval_id": "appr-1"} in process


def test_stage_card_alone_is_a_journal_surface():
    # Derived from INTERACTION_KIND_SPECS.journal_surface — stage_card_required used
    # to be missing from the hand-copied surface set, so a host turn that only
    # posted the card would vanish from reload events.
    from agentcore.runtime.events import stage_card_required

    sink = EventSink()
    sink.emit(content_delta("调研收束，是否开辩？"))
    sink.emit(
        stage_card_required(
            stage_card_id="sc-1",
            conversation_id="c1",
            motion="要不要开一场辩论",
            sides=[{"id": "pro", "label": "正方"}],
            form="debate",
            rationale="事实已齐",
        )
    )
    journal = sink.execution_journal()
    assert journal is not None
    assert [e["type"] for e in journal] == [EventType.STAGE_CARD_REQUIRED.value]
    assert journal[0]["payload"]["stage_card_id"] == "sc-1"


def test_durable_events_after_close_still_journal_display():
    """Pillar A: after sink.close, DURABLE display facts still update the in-memory
    journal (host/execution persist); SSE / history stay closed."""
    sink = EventSink()
    sink.emit(_plan())
    sink.close()
    sink.emit(run_started("s1", "a1"))  # no SSE / history, but journal keeps the fact

    journal = sink.execution_journal()
    assert journal is not None
    assert [e["type"] for e in journal] == [
        EventType.RUN_PLAN.value,
        EventType.RUN_STARTED.value,
    ]
    # Post-close emit must not grow history / enqueue (only journal + host persist).
    assert all(e.type != EventType.RUN_STARTED for e in sink._history)
    queued_types = [
        e.type for e in list(sink._queue._queue) if e is not None  # noqa: SLF001
    ]
    assert EventType.RUN_STARTED not in queued_types


def test_tool_use_end_carries_capped_display():
    # 工具结果富渲染: a tool's structured display rides the event when present and is
    # size-capped (it is journaled / persisted); an absent display omits the key.
    plain = tool_use_end("t1", "file_read", success=True, output="ok")
    assert "display" not in plain.payload

    ev = tool_use_end(
        "t2",
        "code_execute",
        success=True,
        output="ok",
        display={"stdout": "x" * 9000, "results": list(range(80)), "exit_code": 0},
    )
    d = ev.payload["display"]
    assert d["stdout"].endswith("…")
    assert len(d["stdout"]) == 6001  # _DISPLAY_STR_CAP (6000) + ellipsis
    assert len(d["results"]) == 50  # _DISPLAY_LIST_CAP
    assert d["exit_code"] == 0


def test_tool_use_progress_factory_shape():
    # 工具执行阶段进度: the factory carries call id + name + phase; run_id only for a worker's call.
    ev = tool_use_progress("t1", "web_search", "querying")
    assert ev.type is EventType.TOOL_USE_PROGRESS
    assert ev.payload == {
        "tool_call_id": "t1",
        "tool_name": "web_search",
        "phase": "querying",
    }
    assert "run_id" not in ev.payload

    worker = tool_use_progress("t1", "web_search", "fallback", run_id="run-9")
    assert worker.payload["run_id"] == "run-9"


def test_tool_use_progress_is_not_journalled():
    # Transport-only liveliness: the phase ping rides the LIVE stream only — the team-graph
    # journal keeps the start/end pair but never the progress ping (a reloaded turn's tool is
    # already resolved).
    sink = EventSink()
    sink.emit(_plan())
    sink.emit(run_started("s1", "a1"))
    sink.emit(tool_use_start("t1", "web_search", {"query": "x"}))
    sink.emit(tool_use_progress("t1", "web_search", "querying"))
    sink.emit(tool_use_end("t1", "web_search", success=True, output="ok"))

    journal = sink.execution_journal()
    assert journal is not None
    types = [e["type"] for e in journal]
    assert EventType.TOOL_USE_PROGRESS.value not in types
    assert EventType.TOOL_USE_START.value in types
    assert EventType.TOOL_USE_END.value in types


def test_tool_use_progress_absent_from_process_timeline():
    # The single-agent timeline folds start→end into one tool step; the phase ping in between
    # never appears (transport-only) and leaves the folded step phase-less.
    sink = EventSink()
    sink.emit(content_delta("查一下"))
    sink.emit(tool_use_start("t1", "web_search", {"query": "x"}))
    sink.emit(tool_use_progress("t1", "web_search", "querying"))
    sink.emit(tool_use_end("t1", "web_search", success=True, output="ok"))

    timeline = sink.process_timeline()
    assert timeline is not None
    tool_step = next(s for s in timeline if s.get("kind") == "tool")
    assert tool_step["status"] == "success"
    assert "phase" not in tool_step


def test_process_timeline_resolves_tool_display():
    # The single-agent process timeline folds the tool's display onto its step so a
    # reloaded turn renders the same rich result.
    sink = EventSink()
    sink.emit(tool_use_start("t1", "web_search", {"query": "x"}))
    sink.emit(
        tool_use_end(
            "t1",
            "web_search",
            success=True,
            output="ok",
            display={"query": "x", "results": [{"title": "A"}]},
        )
    )
    timeline = sink.process_timeline()
    assert timeline is not None
    tool_step = next(s for s in timeline if s.get("kind") == "tool")
    assert tool_step["status"] == "success"
    assert tool_step["display"] == {"query": "x", "results": [{"title": "A"}]}


def test_process_timeline_interleaves_content_with_thinking_and_tools():
    # The inline timeline (前端UX设计.md §一B) folds the CEO's reply text into the
    # process steps in true emission order, so 思考→正文→工具→思考→正文 round-trips as
    # ordered reasoning/content/tool steps — the trailing content step is the final
    # answer (no separate answer block).
    sink = EventSink()
    sink.emit(reasoning_delta("think-1"))
    sink.emit(content_delta("先查一下"))
    sink.emit(tool_use_start("t1", "web_search", {"query": "x"}))
    sink.emit(tool_use_end("t1", "web_search", success=True, output="ok"))
    sink.emit(reasoning_delta("think-2"))
    sink.emit(content_delta("最终答案"))

    timeline = sink.process_timeline()
    assert timeline is not None
    assert [s["kind"] for s in timeline] == [
        "reasoning",
        "content",
        "tool",
        "reasoning",
        "content",
    ]
    assert timeline[1]["text"] == "先查一下"
    assert timeline[-1]["text"] == "最终答案"


def test_process_content_deltas_coalesce_into_one_step():
    # Consecutive content deltas coalesce into the trailing content step (one segment
    # per 正文 run), mirroring the reasoning coalescing — not one node per token.
    sink = EventSink()
    sink.emit(tool_use_start("t1", "grep", {"pattern": "x"}))
    sink.emit(tool_use_end("t1", "grep", success=True, output="ok"))
    sink.emit(content_delta("答"))
    sink.emit(content_delta("案"))

    timeline = sink.process_timeline()
    assert timeline is not None
    content_steps = [s for s in timeline if s["kind"] == "content"]
    assert len(content_steps) == 1
    assert content_steps[0]["text"] == "答案"


def test_content_only_turn_persists_no_process():
    # A tool-less turn has no interleaving to preserve, so even though content folds
    # into the live process list, process_timeline gates it off (the client replays
    # from reasoning_content + the message content instead).
    sink = EventSink()
    sink.emit(reasoning_delta("just thinking"))
    sink.emit(content_delta("just an answer"))
    assert sink.process_timeline() is None


def test_user_interjection_drops_positional_marker_once():
    # Mid-turn steer: first received pins a zero-width marker; later status updates
    # for the same id do not duplicate. Marker splits trailing content coalesce —
    # causal order (pre / interjection / post) is the point.
    from agentcore.runtime.events import user_interjection

    sink = EventSink()
    sink.emit(content_delta("收到，"))
    sink.emit(
        user_interjection(
            interjection_id="ij-1",
            execution_id="exec-1",
            content="让他停止",
            status="received",
        )
    )
    sink.emit(
        user_interjection(
            interjection_id="ij-1",
            execution_id="exec-1",
            content="让他停止",
            status="injected",
        )
    )
    sink.emit(content_delta("这就让他停下。"))

    timeline = sink.process_timeline()
    assert timeline is not None
    assert [s["kind"] for s in timeline] == [
        "content",
        "user_interjection",
        "content",
    ]
    assert timeline[0]["text"] == "收到，"
    assert timeline[1] == {"kind": "user_interjection", "interjection_id": "ij-1"}
    assert timeline[2]["text"] == "这就让他停下。"


def test_run_process_timelines_interleave_worker_steps():
    # Worker per-run process (对称 CEO): reasoning → tool → content stays ordered so
    # run-detail reload matches live (not message_final splice).
    from agentcore.runtime.events import (
        run_output_delta,
        run_reasoning_delta,
        run_started,
        tool_use_end,
        tool_use_start,
    )

    sink = EventSink()
    sink.emit(run_started("r1", "w1"))
    sink.emit(run_reasoning_delta("r1", "w1", "think"))
    sink.emit(tool_use_start("t1", "web_search", {"query": "x"}, run_id="r1"))
    sink.emit(tool_use_end("t1", "web_search", success=True, output="ok", run_id="r1"))
    sink.emit(run_output_delta("r1", "w1", "answer"))

    timelines = sink.run_process_timelines()
    assert timelines is not None
    assert list(timelines) == ["r1"]
    assert [s["kind"] for s in timelines["r1"]] == ["reasoning", "tool", "content"]
    # Worker tools must NOT land on the captain process timeline.
    assert sink.process_timeline() is None


def test_run_process_round_trips_via_journal_entries():
    from agentcore.runtime.events import (
        run_output_delta,
        run_reasoning_delta,
        run_started,
        tool_use_end,
        tool_use_start,
    )
    from agentcore.runtime.journal.entries import entries_from_runs
    from agentcore.runtime.journal.fold import runs_from_entries

    sink = EventSink()
    sink.emit(run_started("r1", "w1"))
    sink.emit(run_reasoning_delta("r1", "w1", "t"))
    sink.emit(tool_use_start("t1", "grep", {"pattern": "x"}, run_id="r1"))
    sink.emit(tool_use_end("t1", "grep", success=True, output="hit", run_id="r1"))
    sink.emit(run_output_delta("r1", "w1", "out"))
    payload = {
        "events": sink.execution_journal() or [],
        "finish_reason": "end_turn",
        "run_processes": sink.run_process_timelines(),
    }
    restored = runs_from_entries(entries_from_runs(payload))
    assert restored is not None
    assert restored["run_processes"]["r1"] == sink.run_process_timelines()["r1"]


async def test_emit_updates_in_memory_journal_when_writer_sealed(monkeypatch) -> None:
    """Post-pause emit must still update EventSink display journal; sealed writer no-ops DB."""
    from agentcore.runtime.events import checkpoint_required
    from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer

    written: list[int] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False) -> int | None:
            written.append(seq)
            return 0

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        "agentcore.conversation.store.cloud.telemetry_session_factory", lambda: _Sess()
    )
    monkeypatch.setattr("agentcore.conversation.store.cloud.TurnJournalRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.audit.hooks.on_journal_fact_appended", lambda entry: None
    )

    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")
    token = current_journal_writer.set(writer)
    try:
        sink = EventSink()
        sink.emit(_plan())
        await writer.flush()
        # run_plan DURABLE + progressive process_team (both before seal).
        assert len(written) >= 1
        sealed_at = len(written)

        await writer.seal()
        required = checkpoint_required(
            checkpoint_id="cp1",
            conversation_id="c1",
            question="继续?",
        )
        sink.emit(required)
        await writer.flush()

        # In-memory display journal still got the card; durable writer did not append.
        journal = sink.execution_journal()
        assert journal is not None
        assert journal[-1]["type"] == EventType.CHECKPOINT_REQUIRED.value
        assert len(written) == sealed_at
        assert writer.schedule_append({"kind": "x"}) is None
    finally:
        current_journal_writer.reset(token)


async def test_emit_run_completed_after_seal_persists_via_overflow(monkeypatch) -> None:
    """Pause seal must not drop a later worker terminal; overflow writer still appends."""
    from agentcore.runtime.facts import TurnFactLog, current_fact_log
    from agentcore.runtime.journal.writer import TurnJournalWriter, current_journal_writer

    written: list[dict] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def append(self, *, turn_id, seq, conversation_id, trace_id, entry, overflow=False) -> int | None:
            written.append(dict(entry))
            return len(written) - 1

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        "agentcore.conversation.store.cloud.telemetry_session_factory", lambda: _Sess()
    )
    monkeypatch.setattr("agentcore.conversation.store.cloud.TurnJournalRepository", Repo)
    monkeypatch.setattr(
        "agentcore.runtime.audit.hooks.on_journal_fact_appended", lambda entry: None
    )

    writer = TurnJournalWriter(turn_id="m1", conversation_id="c1", trace_id="t1")
    fact_log = TurnFactLog()
    token = current_journal_writer.set(writer)
    fl = current_fact_log.set(fact_log)
    try:
        sink = EventSink()
        sink.emit(_plan())
        await writer.flush()
        await writer.seal()
        sealed_kinds = [e.get("kind") for e in written]

        sink.emit(run_completed("s1", "a1", output_summary="done", duration_ms=12))
        await writer.flush()

        kinds = [e.get("kind") for e in written]
        assert "run_completed" in kinds
        assert kinds[: len(sealed_kinds)] == sealed_kinds
        assert "run_completed" in [e.get("kind") for e in fact_log.entries()]
    finally:
        current_fact_log.reset(fl)
        current_journal_writer.reset(token)


def test_journal_persist_caps_tool_use_end_live_payload_stays_full():
    from agentcore.runtime.events.journal_config import cap_process_result
    from agentcore.runtime.facts import TurnFactLog, current_fact_log

    log = TurnFactLog()
    token = current_fact_log.set(log)
    sink = EventSink()
    try:
        big = "x" * 9000
        sink.emit(_plan())
        sink.emit(tool_use_start("t1", "web_fetch", {"url": "https://example.com"}))
        ev = tool_use_end("t1", "web_fetch", success=True, output=big)
        sink.emit(ev)

        assert ev.payload["result"] == big
        journal = sink.execution_journal()
        assert journal is not None
        end = next(e for e in journal if e["type"] == EventType.TOOL_USE_END.value)
        assert end["payload"]["result"] == cap_process_result(big)
        fact = next(e for e in log.entries() if e["kind"] == "tool_use_end")
        assert fact["payload"]["result"] == cap_process_result(big)
        process = next(e for e in log.entries() if e["kind"] == "process_tool")
        assert process["payload"]["result"] == cap_process_result(big)
    finally:
        current_fact_log.reset(token)


def test_journal_persist_safety_caps_checkpoint_resolved_note():
    from agentcore.runtime.events import checkpoint_resolved
    from agentcore.runtime.events.journal_config import _JOURNAL_PAYLOAD_SAFETY_CAP
    from agentcore.runtime.facts import TurnFactLog, current_fact_log

    log = TurnFactLog()
    token = current_fact_log.set(log)
    sink = EventSink()
    try:
        huge = "注" * (_JOURNAL_PAYLOAD_SAFETY_CAP + 25)
        sink.emit(_plan())
        ev = checkpoint_resolved(checkpoint_id="cp1", decision="continue", note=huge)
        sink.emit(ev)
        assert ev.payload["note"] == huge
        fact = next(e for e in log.entries() if e["kind"] == "checkpoint_resolved")
        note = fact["payload"]["note"]
        assert len(note) == _JOURNAL_PAYLOAD_SAFETY_CAP
        assert "journal_capped" in note
        assert f"original_chars={len(huge)}" in note
    finally:
        current_fact_log.reset(token)
