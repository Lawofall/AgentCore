"""Tests for the execution span tree projection (D2 可观测性, runtime/spans.py).

``spans_from_entries`` is one more projection of the §18.3 Turn Journal (the idiom of
``journal.runs_from_entries`` / ``window_from_journal``): it folds the recorded run /
tool facts into an OTel-GenAI-semconv-aligned span tree — root ``chat`` span → one
``invoke_agent`` span per run node (captain + workers, linked by parent_run_id) → one
``execute_tool`` span per tool call. These build journals by hand to pin the fold rules
+ assert the OTel attribute mapping, the run tree linkage, durations, and the
best-effort exporter posture.
"""

import json

import agentcore.runtime.spans as spans_mod
from agentcore.runtime.spans import (
    LogSpanExporter,
    NoopSpanExporter,
    Span,
    export_turn_spans,
    spans_from_entries,
)
from tests.conftest import LogSpy


def _fact(kind: str, payload: dict, ts=None) -> dict:
    return {"kind": kind, "payload": payload, "ts": ts}


def _started(model_profile="chat") -> dict:
    return _fact(
        "turn_started",
        {
            "system_prompt": "S",
            "user_message": "go",
            "model_profile": model_profile,
            "history_len": 0,
        },
    )


def _run_started(
    run_id, agent_id, *, kind="agent", parent=None, continues_run_id=None, ts="t"
) -> dict:
    payload = {
        "run_id": run_id,
        "agent_id": agent_id,
        "parent_run_id": parent,
        "kind": kind,
    }
    if continues_run_id:
        payload["continues_run_id"] = continues_run_id
    return _fact("run_started", payload, ts=ts)


def _run_completed(
    run_id, agent_id, *, duration_ms=0, model="", role="member", usage=None, cost=None, ts="t"
) -> dict:
    return _fact(
        "run_completed",
        {
            "run_id": run_id,
            "agent_id": agent_id,
            "duration_ms": duration_ms,
            "model": model,
            "role": role,
            "usage": usage or {"input": 0, "output": 0, "reasoning": 0},
            "cost": cost or {"total": 0, "currency": "USD"},
        },
        ts=ts,
    )


def _by_id(root: Span) -> dict[str, Span]:
    return {s.span_id: s for s in root.flatten()}


# ── projection: the multi-agent execution tree ───────────────────────────────────


def test_delegated_turn_builds_root_captain_worker_tool_tree():
    # CEO delegates to one worker; the worker calls a tool. The tree is:
    #   root(chat) → captain(invoke_agent) → worker(invoke_agent) → tool(execute_tool)
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", parent=None, ts="t0"),
        _fact("round_boundary", {"round_idx": 0, "run_id": "cap", "role": "captain"}),
        _fact(
            "llm_call",
            {
                "run_id": "cap",
                "round_idx": 0,
                "tool_calls": [{"id": "d1"}],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        ),
        _fact("run_plan", {"execution_id": "e1"}, ts="t1"),
        _run_started("w1", "researcher", kind="agent", parent="cap", ts="t2"),
        _fact("round_boundary", {"round_idx": 0, "run_id": "w1", "role": "worker"}),
        _fact("llm_call", {"run_id": "w1", "round_idx": 0, "tool_calls": [{"id": "c1"}]}),
        _fact(
            "tool_use_start",
            {"tool_call_id": "c1", "tool_name": "web_search"},
            ts="2026-06-18T08:00:00.000Z",
        ),
        _fact(
            "tool_call",
            {
                "run_id": "w1",
                "tool_call_id": "c1",
                "name": "web_search",
                "result": "r",
                "success": True,
            },
        ),
        _fact(
            "tool_use_end",
            {"tool_call_id": "c1", "status": "success"},
            ts="2026-06-18T08:00:03.000Z",
        ),
        _run_completed(
            "w1",
            "researcher",
            duration_ms=4200,
            model="deepseek-v4-flash",
            usage={"input": 500, "output": 300},
            cost={"total": 1234},
            ts="t3",
        ),
        _run_completed(
            "cap",
            "cap",
            duration_ms=9000,
            model="deepseek-v4-flash",
            usage={"input": 100, "output": 50},
            ts="t4",
        ),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    assert root is not None
    assert root.operation == "chat"
    assert root.attributes["agentcore.finish_reason"] == "end_turn"
    assert root.status == "ok"
    assert root.attributes["agentcore.workers"] == 1
    # Root duration ≈ the captain run's wall clock.
    assert root.duration_ms == 9000

    spans = _by_id(root)
    cap = spans["run:cap"]
    assert cap.parent_span_id == "turn"
    assert cap.operation == "invoke_agent"
    assert cap.attributes["agentcore.run.kind"] == "captain"
    assert cap.attributes["gen_ai.request.model"] == "deepseek-v4-flash"

    worker = spans["run:w1"]
    assert worker.parent_span_id == "run:cap"
    assert worker.attributes["gen_ai.agent.id"] == "researcher"
    assert worker.duration_ms == 4200
    assert worker.attributes["gen_ai.usage.input_tokens"] == 500
    assert worker.attributes["gen_ai.usage.output_tokens"] == 300
    assert worker.attributes["agentcore.cost.total_nano"] == 1234
    assert worker.attributes["agentcore.rounds"] == 1
    assert worker.attributes["agentcore.tool_calls"] == 1

    tool = spans["tool:c1"]
    assert tool.parent_span_id == "run:w1"
    assert tool.operation == "execute_tool"
    assert tool.attributes["gen_ai.tool.name"] == "web_search"
    assert tool.status == "ok"
    # Best-effort duration from the tool_use_start/end timestamp pair (3s).
    assert tool.duration_ms == 3000


def test_single_agent_tool_turn_nests_tool_under_captain():
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _fact("round_boundary", {"round_idx": 0, "run_id": "cap", "role": "captain"}),
        _fact("llm_call", {"run_id": "cap", "round_idx": 0, "tool_calls": [{"id": "c1"}]}),
        _fact(
            "tool_call",
            {
                "run_id": "cap",
                "tool_call_id": "c1",
                "name": "read_url",
                "result": "r",
                "success": True,
            },
        ),
        _run_completed("cap", "cap", duration_ms=1500, ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    spans = _by_id(root)
    assert root.attributes.get("agentcore.workers") is None  # no workers, single agent
    assert spans["tool:c1"].parent_span_id == "run:cap"
    assert spans["run:cap"].attributes["agentcore.tool_calls"] == 1


def test_failed_worker_span_marked_error_with_message():
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _fact("run_plan", {"execution_id": "e1"}, ts="t1"),
        _run_started("w1", "w1", kind="agent", parent="cap", ts="t2"),
        _fact("run_failed", {"run_id": "w1", "agent_id": "w1", "error": "boom"}, ts="t3"),
        _run_completed("cap", "cap", ts="t4"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    worker = _by_id(root)["run:w1"]
    assert worker.status == "error"
    assert worker.attributes["error.message"] == "boom"


def test_failed_tool_marks_run_tool_failures():
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _fact(
            "tool_call",
            {
                "run_id": "cap",
                "tool_call_id": "c1",
                "name": "read_url",
                "result": "err",
                "success": False,
            },
        ),
        _run_completed("cap", "cap", ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    spans = _by_id(root)
    assert spans["run:cap"].attributes["agentcore.tool_failures"] == 1
    assert spans["tool:c1"].status == "error"


def test_cancelled_finish_marks_root_error():
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_completed("cap", "cap", ts="t1"),
        _fact("turn_end", {"finish_reason": "cancelled"}),
    ]
    root = spans_from_entries(entries)
    assert root.status == "error"
    assert root.attributes["agentcore.finish_reason"] == "cancelled"


def test_display_only_journal_still_builds_run_spans():
    # A display-only / salvage journal (entries_from_runs output: run_* + tool_use_* events,
    # NO execution facts). The span tree still forms from the run events; it simply
    # lacks the per-round aggregates the execution facts would add.
    entries = [
        _fact("run_plan", {"execution_id": "e1"}, ts="t0"),
        _run_started("w1", "w1", kind="agent", parent=None, ts="t1"),
        _run_completed("w1", "w1", duration_ms=2000, ts="t2"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    spans = _by_id(root)
    # No parent_run_id → the worker attaches to the synthetic root.
    assert spans["run:w1"].parent_span_id == "turn"
    assert spans["run:w1"].duration_ms == 2000
    assert "agentcore.rounds" not in spans["run:w1"].attributes


def test_continuation_run_carries_continues_attr():
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_started("w1", "w1", kind="agent", parent="cap", continues_run_id="w0", ts="t1"),
        _run_completed("w1", "w1", ts="t2"),
        _run_completed("cap", "cap", ts="t3"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    assert _by_id(root)["run:w1"].attributes["agentcore.run.continues_run_id"] == "w0"


# ── projection: nothing-to-trace + empties ───────────────────────────────────────


def test_empty_and_none_return_none():
    assert spans_from_entries(None) is None
    assert spans_from_entries([]) is None


def test_turn_end_only_journal_has_nothing_to_trace():
    # A salvaged turn with only a finish_reason (no runs, no tools) → no children → None.
    entries = [_fact("turn_end", {"finish_reason": "cancelled"})]
    assert spans_from_entries(entries) is None


def test_run_completed_for_unknown_run_is_ignored():
    # A run_completed whose run_started never appeared must not crash or create a span.
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_completed("ghost", "ghost", ts="t1"),  # no matching run_started
        _run_completed("cap", "cap", ts="t2"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    assert "run:ghost" not in _by_id(root)


# ── flatten + exporters ──────────────────────────────────────────────────────────


def test_flatten_is_preorder_depth_first():
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_started("w1", "w1", kind="agent", parent="cap", ts="t1"),
        _fact(
            "tool_call",
            {"run_id": "w1", "tool_call_id": "c1", "name": "t", "result": "r", "success": True},
        ),
        _run_completed("w1", "w1", ts="t2"),
        _run_completed("cap", "cap", ts="t3"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    assert [s.span_id for s in root.flatten()] == ["turn", "run:cap", "run:w1", "tool:c1"]


def test_log_exporter_emits_structured_line(monkeypatch):
    # Asserted at the module's logger seam, not on rendered stdout: whether the line
    # reaches stdout depends on process-wide structlog state, which any earlier
    # ``setup_logging()`` in the session re-points at stdlib logging (level WARNING here),
    # dropping this INFO line. The exporter's own contract is the event name + fields.
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_completed("cap", "cap", duration_ms=1200, ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    spy = LogSpy()
    monkeypatch.setattr(spans_mod, "logger", spy)

    LogSpanExporter().export(root, trace_id="tr", conversation_id="c1", message_id="m1")

    line = spy.get("obs.turn_spans")  # exactly one line per turn
    assert line["span_count"] == 2
    assert line["finish_reason"] == "end_turn"
    assert line["duration_ms"] == 1200
    # trace_id is emitted explicitly (the line's「greppable by trace_id」promise) and the
    # assistant row id is labelled message_id — not mislabelled as turn_id (which is the
    # log-context turn id, joined from the spine, not the message id).
    assert line["trace_id"] == "tr"
    assert line["message_id"] == "m1"
    assert "turn_id" not in line
    assert line["conversation_id"] == "c1"
    assert line["truncated"] is False
    assert line["dropped"] == 0
    # The file handler always renders JSON Lines (logs/dev.jsonl is parsed line by line),
    # so the whole payload — span tree included — has to survive a JSON round trip.
    assert json.loads(json.dumps(line))["spans"][0]["span_id"] == "turn"


def test_log_exporter_truncates_with_markers_and_keeps_late_failures(monkeypatch):
    """DFS[:N] used to drop later members' tools (and their failures) with no marker."""
    monkeypatch.setattr(spans_mod, "_MAX_LOGGED_SPANS", 6)
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_started("w1", "w1", kind="agent", parent="cap", ts="t1"),
        _fact(
            "tool_call",
            {"run_id": "w1", "tool_call_id": "ok1", "name": "read_url", "success": True},
        ),
        _run_started("w2", "w2", kind="agent", parent="cap", ts="t2"),
        _fact(
            "tool_call",
            {"run_id": "w2", "tool_call_id": "err2", "name": "file_read", "success": False},
        ),
        _run_started("w3", "w3", kind="agent", parent="cap", ts="t3"),
        _fact(
            "tool_call",
            {"run_id": "w3", "tool_call_id": "ok3", "name": "read_url", "success": True},
        ),
        _run_completed("w1", "w1", ts="t4"),
        _run_completed("w2", "w2", ts="t5"),
        _run_completed("w3", "w3", ts="t6"),
        _run_completed("cap", "cap", ts="t7"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    assert [s.span_id for s in root.flatten()] == [
        "turn",
        "run:cap",
        "run:w1",
        "tool:ok1",
        "run:w2",
        "tool:err2",
        "run:w3",
        "tool:ok3",
    ]
    spy = LogSpy()
    monkeypatch.setattr(spans_mod, "logger", spy)
    LogSpanExporter().export(root, trace_id="tr", conversation_id="c1", message_id="m1")
    line = spy.get("obs.turn_spans")
    assert line["span_count"] == 8
    assert line["truncated"] is True
    assert line["dropped"] == 2
    ids = [s["span_id"] for s in line["spans"]]
    assert ids == ["turn", "run:cap", "run:w1", "run:w2", "tool:err2", "run:w3"]
    assert "tool:ok1" not in ids
    assert "tool:ok3" not in ids
    assert "tool:err2" in ids


def test_select_logged_spans_keeps_in_flight_over_ok_tools(monkeypatch):
    from agentcore.runtime.spans import _select_logged_spans

    monkeypatch.setattr(spans_mod, "_MAX_LOGGED_SPANS", 3)
    root = Span(span_id="turn", parent_span_id=None, name="chat", operation="chat")
    run = Span(
        span_id="run:w1",
        parent_span_id="turn",
        name="invoke_agent w1",
        operation="invoke_agent",
    )
    hang = Span(
        span_id="tool:hang",
        parent_span_id="run:w1",
        name="execute_tool host_shell",
        operation="execute_tool",
        status="unset",
        attributes={"agentcore.tool.in_flight": True},
    )
    ok = Span(
        span_id="tool:ok",
        parent_span_id="run:w1",
        name="execute_tool read_url",
        operation="execute_tool",
        status="ok",
    )
    kept, dropped = _select_logged_spans([root, run, hang, ok])
    assert dropped == 1
    assert [s.span_id for s in kept] == ["turn", "run:w1", "tool:hang"]


def test_export_turn_spans_uses_injected_exporter():
    captured: list[Span] = []

    class _Capture:
        def export(self, root, **kwargs):
            captured.append(root)

    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_completed("cap", "cap", ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    export_turn_spans(
        entries, trace_id="tr", conversation_id="c1", message_id="m1", exporter=_Capture()
    )
    assert len(captured) == 1
    assert captured[0].span_id == "turn"


def test_export_turn_spans_is_best_effort_on_exporter_error():
    class _Boom:
        def export(self, root, **kwargs):
            raise RuntimeError("exporter down")

    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_completed("cap", "cap", ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    # Must NOT raise — observability never breaks the turn.
    export_turn_spans(
        entries, trace_id="tr", conversation_id="c1", message_id="m1", exporter=_Boom()
    )


def test_export_turn_spans_noop_on_empty():
    # Nothing to trace → the exporter is never invoked (no raise, no log).
    class _Boom:
        def export(self, root, **kwargs):
            raise AssertionError("should not export an empty tree")

    export_turn_spans([], trace_id="tr", conversation_id="c1", message_id="m1", exporter=_Boom())


def test_export_turn_spans_logs_no_batch_without_workers(monkeypatch):
    """零人回合：有 facts、无队员编制 → obs.turn_spans 仍带 no_batch。"""
    entries = [
        _started(),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_completed("cap", "cap", duration_ms=50, ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    spy = LogSpy()
    monkeypatch.setattr(spans_mod, "logger", spy)
    export_turn_spans(entries, trace_id="tr", conversation_id="c1", message_id="m1")
    line = spy.get("obs.turn_spans")
    assert line["team_batch"] == {"kind": "no_batch"}


def test_export_turn_spans_paused_in_flight_team_batch(monkeypatch):
    """Paused mid-flight still exports ``team_batch`` (persist choke point, not turn-end only)."""
    entries = [
        _started(),
        {
            "kind": "run_plan",
            "payload": {
                "execution_id": "e1",
                "runs": [
                    {"id": "cap", "kind": "captain"},
                    {"id": "w1", "kind": "agent"},
                ],
            },
        },
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_started("w1", "w1", kind="agent", parent="cap", ts="t1"),
        _fact("turn_end", {"finish_reason": "paused"}),
    ]
    spy = LogSpy()
    monkeypatch.setattr(spans_mod, "logger", spy)
    export_turn_spans(entries, trace_id="tr", conversation_id="c1", message_id="m1")
    line = spy.get("obs.turn_spans")
    assert line["team_batch"] == {"kind": "in_flight", "worker_count": 1}
    assert line["finish_reason"] == "paused"


def test_noop_exporter_returns_none():
    root = spans_from_entries(
        [
            _started(),
            _run_started("cap", "cap", kind="captain", ts="t0"),
            _run_completed("cap", "cap", ts="t1"),
            _fact("turn_end", {"finish_reason": "end_turn"}),
        ]
    )
    assert NoopSpanExporter().export(root, trace_id="t", conversation_id="c", turn_id="m") is None


def test_orphan_tool_use_start_surfaces_in_flight_span():
    """Cancelled mid-approval/execute: start without tool_call fact still projects."""
    entries = [
        _started(),
        _run_started("w1", "coder", kind="agent", parent=None, ts="t0"),
        _fact(
            "tool_use_start",
            {
                "tool_call_id": "hang1",
                "tool_name": "host_shell",
                "run_id": "w1",
            },
            ts="2026-06-18T08:00:00.000Z",
        ),
        _fact("turn_end", {"finish_reason": "cancelled"}),
    ]
    root = spans_from_entries(entries)
    spans = _by_id(root)
    tool = spans["tool:hang1"]
    assert tool.parent_span_id == "run:w1"
    assert tool.status == "unset"
    assert tool.attributes["gen_ai.tool.name"] == "host_shell"
    assert tool.attributes.get("agentcore.tool.in_flight") is True
    assert tool.attributes.get("agentcore.tool.orphan_start") is True


def test_spans_gen_ai_system_follows_run_model():
    entries = [
        _started("deepseek-v4-flash"),
        _run_started("cap", "cap", kind="captain", ts="t0"),
        _run_completed("cap", "cap", model="deepseek-v4-pro", ts="t1"),
        _fact("turn_end", {"finish_reason": "end_turn"}),
    ]
    root = spans_from_entries(entries)
    spans = _by_id(root)
    assert root.attributes["gen_ai.system"] == "deepseek"
    assert spans["run:cap"].attributes["gen_ai.system"] == "deepseek"
