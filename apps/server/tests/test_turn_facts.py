"""Tests for the execution-level Turn Journal facts (§18.3, 执行级落地).

Covers the six execution-fact dataclasses (their journal-entry shape), the in-memory
:class:`TurnFactLog` recorder (ordering + entry projection), and the guard that keeps
these execution-only facts OUT of the display projection (``runs_from_entries``) — the
property that makes adding them a pure, non-disturbing change.
"""

from agentcore.runtime.facts import (
    EXECUTION_ONLY_KINDS,
    FactKind,
    FactRecorder,
    LlmCallFact,
    MessageFinalFact,
    NoteFact,
    RoundBoundaryFact,
    ToolCallFact,
    TurnFactLog,
    TurnPausedFact,
    TurnStartedFact,
    current_fact_log,
    pre_pause_from_journal,
    snapshot_fact_log,
)
from agentcore.runtime.journal import entries_from_runs, runs_from_entries


def test_turn_started_fact_entry_shape():
    fact = TurnStartedFact(
        system_prompt="你是 CEO",
        user_message="写个脚本",
        model_profile="chat",
        history_len=4,
    ).to_fact()
    entry = fact.entry()
    assert entry["kind"] == "turn_started"
    assert entry["ts"] is None
    assert entry["payload"] == {
        "system_prompt": "你是 CEO",
        "user_message": "写个脚本",
        "model_profile": "chat",
        "history_len": 4,
    }


def test_round_boundary_fact_entry_shape():
    entry = RoundBoundaryFact(round_idx=2, run_id="captain", role="captain").to_fact().entry()
    assert entry["kind"] == "round_boundary"
    assert entry["payload"] == {"round_idx": 2, "run_id": "captain", "role": "captain"}


def test_llm_call_fact_preserves_reasoning_and_tool_calls():
    # The fold must reproduce reasoning_content + tool_calls byte-for-byte (DeepSeek
    # thinking echo), so the fact must carry both verbatim.
    tool_calls = [{"id": "c1", "function": {"name": "delegate", "arguments": "{}"}}]
    usage = {"input_tokens": 10, "output_tokens": 20}
    entry = (
        LlmCallFact(
            run_id="captain",
            round_idx=0,
            content="先规划",
            reasoning_content="让我想想",
            tool_calls=tool_calls,
            usage=usage,
            finish_reason="tool_calls",
        )
        .to_fact()
        .entry()
    )
    assert entry["kind"] == "llm_call"
    assert entry["payload"]["content"] == "先规划"
    assert entry["payload"]["reasoning_content"] == "让我想想"
    assert entry["payload"]["tool_calls"] == tool_calls
    assert entry["payload"]["usage"] == usage
    assert entry["payload"]["finish_reason"] == "tool_calls"


def test_llm_call_fact_defaults_empty_collections():
    # A tool-free final round carries no tool_calls / usage — they normalize to empty
    # containers (never None) so the stored payload shape is stable.
    payload = (
        LlmCallFact(run_id="captain", round_idx=1, content="答案").to_fact().entry()["payload"]
    )
    assert payload["tool_calls"] == []
    assert payload["usage"] == {}
    assert payload["reasoning_content"] == ""
    assert payload["finish_reason"] is None


def test_tool_call_fact_entry_shape():
    # The execution tool_call fact carries the FULL model-facing result (post-annotation)
    # the window folds, scoped by run_id and paired by tool_call_id (执行级落地 边界①).
    entry = (
        ToolCallFact(
            run_id="captain",
            tool_call_id="c1",
            name="search",
            arguments='{"q": "x"}',
            result="结果全文\n\n[来源编号] [1]=https://e.com",
            success=True,
        )
        .to_fact()
        .entry()
    )
    assert entry["kind"] == "tool_call"
    assert entry["payload"] == {
        "run_id": "captain",
        "tool_call_id": "c1",
        "name": "search",
        "arguments": '{"q": "x"}',
        "result": "结果全文\n\n[来源编号] [1]=https://e.com",
        "success": True,
    }


def test_tool_call_fact_omits_empty_code_writes_nonempty():
    empty = ToolCallFact(
        run_id="r", tool_call_id="c", name="git", success=False, result="x"
    ).to_fact().entry()["payload"]
    assert "code" not in empty
    with_code = ToolCallFact(
        run_id="r",
        tool_call_id="c",
        name="git",
        success=False,
        result="x",
        code="git_timeout",
    ).to_fact().entry()["payload"]
    assert with_code["code"] == "git_timeout"


def test_tool_call_fact_omits_unknown_cross_turn_retry():
    empty = (
        ToolCallFact(run_id="r", tool_call_id="c", name="git", success=False, result="x")
        .to_fact()
        .entry()["payload"]
    )
    assert "cross_turn_retry" not in empty
    invalid = (
        ToolCallFact(
            run_id="r",
            tool_call_id="c",
            name="git",
            success=False,
            result="x",
            cross_turn_retry="maybe",
        )
        .to_fact()
        .entry()["payload"]
    )
    assert "cross_turn_retry" not in invalid
    stamped = (
        ToolCallFact(
            run_id="r",
            tool_call_id="c",
            name="git",
            success=False,
            result="x",
            cross_turn_retry="futile",
        )
        .to_fact()
        .entry()["payload"]
    )
    assert stamped["cross_turn_retry"] == "futile"


def test_tool_call_fact_omits_empty_working_set_digest():
    empty = (
        ToolCallFact(run_id="r", tool_call_id="c", name="file_read", result="x")
        .to_fact()
        .entry()["payload"]
    )
    assert "working_set_digest" not in empty
    stamped = (
        ToolCallFact(
            run_id="r",
            tool_call_id="c",
            name="file_read",
            result="x",
            working_set_digest="Foo, bar()",
        )
        .to_fact()
        .entry()["payload"]
    )
    assert stamped["working_set_digest"] == "Foo, bar()"


def test_note_and_message_final_fact_shapes():
    note = (
        NoteFact(role="user", content="停止使用工具", reason="finalize", run_id="captain")
        .to_fact()
        .entry()
    )
    assert note["kind"] == "note"
    # run_id rides the note so a captain note injected mid-delegate folds into the captain
    # window (边界②); it defaults to "" for a note recorded outside a scoped run.
    assert note["payload"] == {
        "role": "user",
        "content": "停止使用工具",
        "reason": "finalize",
        "run_id": "captain",
    }

    final = (
        MessageFinalFact(run_id="w1", content="全文产出", reasoning="思考全文").to_fact().entry()
    )
    assert final["kind"] == "message_final"
    assert final["payload"] == {"run_id": "w1", "content": "全文产出", "reasoning": "思考全文"}


def test_to_fact_accepts_optional_timestamp():
    fact = NoteFact(role="user", content="x").to_fact(ts="2026-06-18T00:00:00.000Z")
    assert fact.ts == "2026-06-18T00:00:00.000Z"
    assert fact.entry()["ts"] == "2026-06-18T00:00:00.000Z"


def test_execution_only_kinds_match_enum():
    assert {
        "turn_started",
        # Worker / continuation window anchor (RunHeadFact) — per-run head distinct
        # from turn_started so workers never fold under the CEO prompt.
        "run_head",
        "round_boundary",
        "llm_call",
        "tool_call",
        "note",
        "message_final",
        # 执行级事件溯源 Phase 2 (frame.plan 退场): the delegate's DAG snapshot — a value
        # distinct from the display ``run_plan`` event so the display gate is untouched.
        "plan_snapshot",
        "coordination_snapshot",
        # Journals written before the style ledger was removed.
        "website_style_confirmed",
        # 演讲/PPT 交付形态双闸：结构化 format_id 确认（resume / full_auto）。
        "presentation_format_confirmed",
        # Agent / 自动化交付形态双闸：结构化 format_id 确认（resume / full_auto）。
        "automation_delivery_confirmed",
        # 回合态挂起归宿: resumable turn-state snapshot (process / controller / content).
        "turn_paused",
    } == EXECUTION_ONLY_KINDS
    assert frozenset(k.value for k in FactKind) | {"website_style_confirmed"} == (
        EXECUTION_ONLY_KINDS
    )


def test_turn_paused_fact_round_trip():
    # §二契约 payload — serialize via to_fact, rebuild via from_payload / pre_pause.
    controller = {
        "post_delegate": True,
        "delegate_count": 1,
        "audit_gate_fired": False,
        "first_batch_substantial": True,
    }
    process = [{"kind": "reasoning", "text": "想一步"}]
    run_processes = {"w1": [{"kind": "tool", "name": "search"}]}
    citations = [{"url": "https://e.com", "title": "来源"}]
    fact = TurnPausedFact(
        checkpoint_id="cp-1",
        suspension_kind="ask_user",
        content="挂起前正文",
        reasoning="CEO 思考",
        process=process,
        run_processes=run_processes,
        citations=citations,
        evidence_ledger=[],
        controller=controller,
    )
    entry = fact.to_fact().entry()
    assert entry["kind"] == "turn_paused"
    assert entry["payload"] == {
        "checkpoint_id": "cp-1",
        "suspension_kind": "ask_user",
        "content": "挂起前正文",
        "reasoning": "CEO 思考",
        "process": process,
        "run_processes": run_processes,
        "citations": citations,
        "evidence_ledger": [],
        "controller": controller,
    }
    rebuilt = TurnPausedFact.from_payload(entry["payload"])
    assert rebuilt == fact
    assert pre_pause_from_journal([entry]) == fact


def test_turn_paused_fact_defaults_empty_collections():
    payload = (
        TurnPausedFact(checkpoint_id="cp", suspension_kind="plan_review").to_fact().entry()[
            "payload"
        ]
    )
    assert payload["content"] == ""
    assert payload["reasoning"] == ""
    assert payload["process"] == []
    assert payload["run_processes"] == {}
    assert payload["citations"] == []
    assert payload["evidence_ledger"] == []
    assert payload["controller"] == {}


def test_pre_pause_from_journal_takes_last_turn_paused():
    first = (
        TurnPausedFact(
            checkpoint_id="cp-1",
            suspension_kind="ask_user",
            content="第一段",
        )
        .to_fact()
        .entry()
    )
    second = (
        TurnPausedFact(
            checkpoint_id="cp-2",
            suspension_kind="plan_review",
            content="第二段累积",
            reasoning="续思考",
        )
        .to_fact()
        .entry()
    )
    entries = [
        TurnStartedFact("sys", "hi", "chat").to_fact().entry(),
        first,
        RoundBoundaryFact(0, "captain", "captain").to_fact().entry(),
        second,
        {"kind": "turn_end", "payload": {"finish_reason": "paused"}, "ts": None},
    ]
    snap = pre_pause_from_journal(entries)
    assert snap is not None
    assert snap.checkpoint_id == "cp-2"
    assert snap.suspension_kind == "plan_review"
    assert snap.content == "第二段累积"
    assert snap.reasoning == "续思考"


def test_pre_pause_from_journal_missing_returns_none():
    assert pre_pause_from_journal(None) is None
    assert pre_pause_from_journal([]) is None
    # Old journal: execution + display kinds, no turn_paused → legacy fallback path.
    legacy = [
        TurnStartedFact("sys", "hi", "chat").to_fact().entry(),
        RoundBoundaryFact(0, "captain", "captain").to_fact().entry(),
        LlmCallFact("captain", 0, content="ok").to_fact().entry(),
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"},
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    assert pre_pause_from_journal(legacy) is None


def test_display_projection_skips_turn_paused():
    # turn_paused is EXECUTION_ONLY — must not leak into runs.events (client fold).
    # With no process_* lanes, G1 挂起中重载 still projects process from the snapshot.
    entries = [
        TurnStartedFact("sys", "hi", "chat").to_fact().entry(),
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"},
        TurnPausedFact(
            checkpoint_id="cp",
            suspension_kind="team_preview",
            content="正文",
            process=[{"kind": "content", "text": "步"}],
        )
        .to_fact()
        .entry(),
        {"kind": "run_completed", "payload": {"run_id": "s1"}, "ts": "t1"},
        {"kind": "turn_end", "payload": {"finish_reason": "paused"}, "ts": None},
    ]
    runs = runs_from_entries(entries)
    assert runs is not None
    assert [e["type"] for e in runs["events"]] == ["run_plan", "run_completed"]
    assert runs["finish_reason"] == "paused"
    assert runs["process"] == [
        {"kind": "team", "execution_id": "e1"},
        {"kind": "content", "text": "步"},
    ]
    assert pre_pause_from_journal(entries).content == "正文"


def test_turn_fact_log_records_in_order():
    log = TurnFactLog()
    assert not log  # empty is falsy
    log.record_fact(TurnStartedFact("sys", "hi", "chat").to_fact())
    log.record_fact(RoundBoundaryFact(0, "captain", "captain").to_fact())
    log.record_fact(LlmCallFact("captain", 0, content="ok").to_fact())
    assert len(log) == 3
    assert bool(log) is True
    assert [e["kind"] for e in log.entries()] == ["turn_started", "round_boundary", "llm_call"]


def test_turn_fact_log_inherited_entries_prefix():
    inherited = [
        {"kind": "turn_started", "payload": {"user_message": "hi"}, "ts": "t0"},
        {"kind": "round_boundary", "payload": {"round_idx": 0}, "ts": None},
    ]
    log = TurnFactLog(inherited_entries=inherited)
    assert not log  # segment empty — inherited does not count toward len/bool
    log.record_fact(LlmCallFact("captain", 0, content="more").to_fact())
    assert len(log) == 1
    assert [e["kind"] for e in log.entries()] == [
        "turn_started",
        "round_boundary",
        "llm_call",
    ]
    assert [e["kind"] for e in log.segment_entries()] == ["llm_call"]


def test_snapshot_fact_log_includes_inherited_prefix():
    from agentcore.runtime.facts import current_fact_log, snapshot_fact_log

    inherited = [TurnStartedFact("sys", "hi", "chat").to_fact().entry()]
    log = TurnFactLog(inherited_entries=inherited)
    token = current_fact_log.set(log)
    try:
        log.record_fact(RoundBoundaryFact(0, "captain", "captain").to_fact())
        entries = snapshot_fact_log(trailing=[{"kind": "checkpoint_required", "payload": {}}])
        assert [e["kind"] for e in entries] == [
            "turn_started",
            "round_boundary",
            "checkpoint_required",
        ]
    finally:
        current_fact_log.reset(token)


def test_turn_fact_log_seed_from_entries():
    log = TurnFactLog()
    log.seed_from_entries(
        [
            {"kind": "turn_started", "payload": {"user_message": "hi"}, "ts": "t0"},
            {"kind": "round_boundary", "payload": {"round_idx": 0}, "ts": None},
            {"type": "checkpoint_required", "payload": {"id": "cp"}},  # display — skipped
        ]
    )
    log.record_fact(LlmCallFact("captain", 0, content="more").to_fact())
    assert [e["kind"] for e in log.entries()] == [
        "turn_started",
        "round_boundary",
        "llm_call",
    ]


def test_snapshot_fact_log_after_resume_seed_includes_turn_started():
    """Resume seeds the ambient log so a downstream checkpoint sees the full stream."""
    prior = [
        TurnStartedFact("sys", "hi", "chat").to_fact().entry(),
        RoundBoundaryFact(0, "r1", "captain").to_fact().entry(),
    ]
    log = TurnFactLog()
    log.seed_from_entries(prior)
    token = current_fact_log.set(log)
    try:
        log.record_fact(LlmCallFact("r1", 1, content="续跑").to_fact())
        snap = snapshot_fact_log()
    finally:
        current_fact_log.reset(token)
    assert snap[0]["kind"] == "turn_started"
    assert [e["kind"] for e in snap] == ["turn_started", "round_boundary", "llm_call"]


def test_turn_fact_log_is_a_fact_recorder():
    # The in-memory log satisfies the engine-facing write port (runtime_checkable).
    assert isinstance(TurnFactLog(), FactRecorder)


def test_display_projection_skips_execution_facts():
    # Execution facts interleaved with display events must NOT leak into runs.events
    # (the client fold would choke on an unknown event type) — they are skipped, while
    # the genuine display events + turn_end still project as before.
    entries = [
        TurnStartedFact("sys", "hi", "chat").to_fact().entry(),
        {"kind": "run_plan", "payload": {"execution_id": "e1"}, "ts": "t0"},
        RoundBoundaryFact(0, "captain", "captain").to_fact().entry(),
        LlmCallFact("captain", 0, content="ok").to_fact().entry(),
        {"kind": "run_completed", "payload": {"run_id": "s1"}, "ts": "t1"},
        MessageFinalFact("captain", content="全文").to_fact().entry(),
        {"kind": "turn_end", "payload": {"finish_reason": "end_turn"}, "ts": None},
    ]
    runs = runs_from_entries(entries)
    assert runs is not None
    assert [e["type"] for e in runs["events"]] == ["run_plan", "run_completed"]
    assert runs["finish_reason"] == "end_turn"


def test_display_round_trip_unaffected_by_guard():
    # The existing display round-trip (no execution facts) is unchanged by the new skip.
    # Fold synthesizes ``team`` from ``run_plan`` when progressive process_team is absent.
    runs = {
        "events": [
            {"type": "run_plan", "payload": {"execution_id": "e1"}, "timestamp": "t0"},
            {"type": "run_completed", "payload": {"run_id": "s1"}, "timestamp": "t1"},
        ],
        "finish_reason": "end_turn",
    }
    assert runs_from_entries(entries_from_runs(runs)) == {
        **runs,
        "process": [{"kind": "team", "execution_id": "e1"}],
    }
