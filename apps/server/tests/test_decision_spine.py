"""Tests for decision_spine contract (default product-AI log evidence surface)."""

from __future__ import annotations

from agentcore.llm.resilience import COUNT_KEYS, LAYER_ORDER
from agentcore.observability.query.decision_spine import (
    SCHEMA_VERSION,
    build_decision_spine,
    compute_drift_l2,
    format_decision_spine,
)
from agentcore.observability.query.timeline import TimelineQueryResult


def _events_delegated_ok(trace_id: str = "a" * 32) -> list[dict]:
    return [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-07-31T10:00:00Z",
            "trace_id": trace_id,
            "conversation_id": "conv-1",
            "preview": "请委派写报告",
            "chars": 12,
            "history": 0,
            "location": "server",
            "via": "cloud",
            "stream_path_reason": "probe_unhealthy",
        },
        {
            "type": "log",
            "event": "delegate.started",
            "timestamp": "2026-07-31T10:00:01Z",
            "trace_id": trace_id,
            "agents": ["writer"],
            "nodes": 1,
            "plan": [{"id": "n1", "role": "writer", "depends_on": []}],
            "waves": [["n1"]],
        },
        {
            "type": "log",
            "event": "delegate.completion_criteria_unmet",  # historical/S3 fixture
            "timestamp": "2026-07-31T10:00:05Z",
            "trace_id": trace_id,
            "criteria": "code_verified",
            "gaps": ["missing_tests"],
            "execution_id": "ex1",
            "escalate": True,
        },
        {
            "type": "log",
            "event": "llm.call",
            "timestamp": "2026-07-31T10:00:06Z",
            "trace_id": trace_id,
            "model": "demo",
            "input_tokens": 10,
            "output_tokens": 20,
            "cost_nano": 100,
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-07-31T10:00:10Z",
            "trace_id": trace_id,
            "finish_reason": "stop",
            "delegated": True,
            "workers": 1,
            "rounds": 2,
            "duration_ms": 10000,
            "input_tokens": 10,
            "output_tokens": 20,
            "boundary_yields": 0,
            "scope_signals": 0,
            "revises": 0,
            "escalations": 0,
            "reply_preview": "done",
        },
    ]


def test_build_decision_spine_covers_key_decisions() -> None:
    spine = build_decision_spine(_events_delegated_ok(), trace_id="a" * 32)
    assert spine["schema_version"] == SCHEMA_VERSION
    assert spine["trace_id"] == "a" * 32
    assert spine["conversation_id"] == "conv-1"
    assert "委派" in (spine["head"].get("preview") or "") or spine["head"]["preview"]
    assert spine["head"]["via"] == "cloud"
    assert spine["head"]["stream_path_reason"] == "probe_unhealthy"
    assert spine["head"]["location"] == "server"
    events = {d["event"] for d in spine["decisions"]}
    assert "delegate.started" in events
    assert "delegate.completion_criteria_unmet" in events  # historical still surfaced
    assert spine["llm"]["calls"] == 1
    assert spine["tail"]["source"] == "jsonl_close"
    assert spine["tail"]["finish_reason"] == "stop"
    assert spine["tail"]["delegated"] is True
    assert spine["health"]["drift_l2"]["reason"] == "turn_metrics_missing"


def test_post_close_reject_events_are_distinct_on_spine() -> None:
    """补跑超限与收口后冷开整团拒在 spine 上分事件，且保留 error 正文。"""
    tid = "b" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-15T17:00:00Z",
            "trace_id": tid,
            "preview": "【系统收口】",
            "chars": 8,
        },
        {
            "type": "log",
            "event": "delegate.post_close_gap_fill_rejected",
            "timestamp": "2026-08-15T17:00:01Z",
            "trace_id": tid,
            "kind": "gap_fill",
            "error": "补跑一次最多追加 1 个缺口点名节点（缺口 1，上限 3，收到 2）",
            "nodes": 2,
            "call": 2,
        },
        {
            "type": "log",
            "event": "delegate.post_close_redelegation_rejected",
            "timestamp": "2026-08-15T17:00:02Z",
            "trace_id": tid,
            "kind": "cold_open",
            "error": "收口后拒绝整团重派：本批有 3 个既不续派、也不补缺口的冷开节点。",
            "nodes": 3,
            "call": 3,
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-15T17:00:03Z",
            "trace_id": tid,
            "finish_reason": "stop",
            "delegated": False,
            "duration_ms": 100,
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    names = [d["event"] for d in spine["decisions"]]
    assert "delegate.post_close_gap_fill_rejected" in names
    assert "delegate.post_close_redelegation_rejected" in names
    by_event = {d["event"]: d["detail"] for d in spine["decisions"]}
    assert by_event["delegate.post_close_gap_fill_rejected"]["kind"] == "gap_fill"
    assert "补跑一次最多" in (
        by_event["delegate.post_close_gap_fill_rejected"].get("error") or ""
    )
    assert by_event["delegate.post_close_redelegation_rejected"]["kind"] == "cold_open"
    assert "收口后拒绝整团重派" in (
        by_event["delegate.post_close_redelegation_rejected"].get("error") or ""
    )


def test_head_via_orthogonal_to_location() -> None:
    """via is execution path; location remains workspace locality."""
    tid = "c" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-07-31T10:00:00Z",
            "trace_id": tid,
            "preview": "local sidecar turn",
            "chars": 5,
            "history": 1,
            "location": "local",
            "via": "sidecar",
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-07-31T10:00:02Z",
            "trace_id": tid,
            "finish_reason": "stop",
            "delegated": False,
            "duration_ms": 100,
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    assert spine["head"]["via"] == "sidecar"
    assert spine["head"]["location"] == "local"
    text = format_decision_spine(spine)
    assert "via=sidecar" in text
    assert "location=local" in text


def test_tail_prefers_turn_metrics_and_l2_aligned() -> None:
    tid = "b" * 32
    events = _events_delegated_ok(tid)
    metrics = {
        "trace_id": tid,
        "status": "ok",
        "finish_reason": "stop",
        "delegated": True,
        "workers": 1,
        "rounds": 2,
        "duration_ms": 10000,
        "input_tokens": 10,
        "output_tokens": 20,
        "boundary_yields": 0,
        "scope_signals": 0,
        "revises": 0,
        "escalations": 0,
        "kind": "turn",
        "mode": "cloud",
        "turn_id": "t1",
    }
    spine = build_decision_spine(events, turn_metrics=metrics, cost_events={"total_nano": 42})
    assert spine["tail"]["source"] == "turn_metrics"
    assert spine["tail"]["mode"] == "cloud"
    assert spine["tail"]["finish_reason"] == "stop"
    assert spine["tail"]["delegated"] is True
    assert spine["cost"]["source"] == "cost_events"
    assert spine["cost"]["total_nano"] == 42
    assert spine["health"]["drift_l2"]["ok"] is True
    assert spine["health"]["drift_l2"]["compared"] is True
    text = format_decision_spine(spine)
    assert "mode=cloud" in text
    assert "Cost  source=cost_events  total_nano=42" in text
    assert "estimated_nano" not in text
    assert "billing=" not in text


def test_spine_cost_line_shows_byok_estimate() -> None:
    events = _events_delegated_ok()
    spine = build_decision_spine(
        events,
        cost_events={
            "total_nano": 0,
            "estimated_nano": 9_001,
            "estimated_currency": "USD",
            "billing": "BYOK",
            "runs": 3,
        },
    )
    assert spine["cost"]["total_nano"] == 0
    assert spine["cost"]["estimated_nano"] == 9_001
    assert spine["cost"]["billing"] == "BYOK"
    text = format_decision_spine(spine)
    assert (
        "Cost  source=cost_events  total_nano=0  estimated_nano=9001  "
        "billing=BYOK  estimated_currency=USD  runs=3"
    ) in text


def test_drift_l2_marks_mismatch() -> None:
    tid = "c" * 32
    events = _events_delegated_ok(tid)
    metrics = {
        "trace_id": tid,
        "finish_reason": "stop",
        "delegated": True,
        "workers": 1,
        "rounds": 2,
        "input_tokens": 10,
        "output_tokens": 20,
        "boundary_yields": 0,
        "scope_signals": 0,
        "revises": 0,
        "escalations": 9,  # diverge from JSONL close (=0)
    }
    drift = compute_drift_l2(
        turn_metrics=metrics,
        close=events[-1],
        recomputed={"escalations": 0, "yields": 0, "scope_boundaries": 0, "revise": 0},
    )
    assert drift["ok"] is False
    assert any(m["field"] == "escalations" for m in drift["mismatches"])
    spine = build_decision_spine(events, turn_metrics=metrics)
    text = format_decision_spine(spine)
    assert "Drift L2" in text
    assert "escalations" in text


def test_timeline_json_default_is_decision_spine_not_firehose() -> None:
    spine = build_decision_spine(_events_delegated_ok())
    result = TimelineQueryResult(
        mode="trace",
        trace_id="a" * 32,
        log_events=[{"event": "noise"}] * 5,
        decision_spine=spine,
        meta={"traffic": None},
    )
    payload = result.to_json_dict(raw=False)
    assert "decision_spine" in payload
    assert "log_events" not in payload
    assert payload["decision_spine"]["schema_version"] == SCHEMA_VERSION
    raw_payload = result.to_json_dict(raw=True)
    assert "log_events" in raw_payload
    assert len(raw_payload["log_events"]) == 5


def test_format_decision_spine_readable() -> None:
    text = format_decision_spine(build_decision_spine(_events_delegated_ok()))
    assert "Decision Spine" in text
    assert "delegate.started" in text
    assert "delegate.completion_criteria_unmet" in text
    assert "historical/S3" in text
    assert "finish=stop" in text
    assert "via=cloud" in text
    assert "path_reason=probe_unhealthy" in text


def test_token_accounting_marks_full_trace_vs_resume_settlement() -> None:
    """llm = full trace; resume turn_metrics = settlement segment (may diverge)."""
    tid = "d" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-07-31T10:00:00Z",
            "trace_id": tid,
            "preview": "pause then resume",
        },
        {
            "type": "log",
            "event": "llm.call",
            "timestamp": "2026-07-31T10:00:01Z",
            "trace_id": tid,
            "model": "demo",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_nano": 1,
        },
        {
            "type": "log",
            "event": "llm.call",
            "timestamp": "2026-07-31T10:00:08Z",
            "trace_id": tid,
            "model": "demo",
            "input_tokens": 20,
            "output_tokens": 10,
            "cost_nano": 1,
        },
        {
            "type": "log",
            "event": "chat.resume_complete",
            "timestamp": "2026-07-31T10:00:10Z",
            "trace_id": tid,
            "finish_reason": "stop",
            "delegated": False,
            "workers": 0,
            "rounds": 1,
            "duration_ms": 1000,
            "boundary_yields": 0,
            "scope_signals": 0,
            "revises": 0,
            "escalations": 0,
        },
    ]
    metrics = {
        "trace_id": tid,
        "finish_reason": "stop",
        "status": "ok",
        "kind": "resume",
        "delegated": False,
        "workers": 0,
        "rounds": 1,
        "duration_ms": 1000,
        "input_tokens": 20,
        "output_tokens": 10,
        "boundary_yields": 0,
        "scope_signals": 0,
        "revises": 0,
        "escalations": 0,
    }
    spine = build_decision_spine(events, turn_metrics=metrics)
    assert spine["llm"]["token_scope"] == "full_trace"
    assert spine["llm"]["input_tokens"] == 120
    assert spine["llm"]["output_tokens"] == 60
    assert spine["tail"]["token_scope"] == "settlement_segment"
    assert spine["tail"]["input_tokens"] == 20
    assert spine["health"]["token_accounting"]["llm"] == "full_trace_llm_call_sum"
    text = format_decision_spine(spine)
    assert "Token口径" in text
    assert "全trace" in text or "full_trace" in text or "resume" in text.lower()


def _events_local_turn(trace_id: str = "e" * 32) -> list[dict]:
    """Sidecar write-back sample: recorded + tool_failures (no turn_start/complete)."""
    return [
        {
            "type": "log",
            "event": "chat.local_turn_tool_failures",
            "timestamp": "2026-08-11T12:00:00Z",
            "trace_id": trace_id,
            "conversation_id": "conv-local",
            "message_id": "m-local",
            "count": 2,
            "codes": ["searxng_unreachable", "egress_connect"],
            "tools": ["web_search", "web_fetch"],
        },
        {
            "type": "log",
            "event": "chat.local_turn_recorded",
            "timestamp": "2026-08-11T12:00:01Z",
            "trace_id": trace_id,
            "conversation_id": "conv-local",
            "message_id": "m-local",
            "chars": 42,
            "rounds": 3,
            "finish_reason": "stop",
        },
    ]


def test_local_turn_spine_head_and_tool_failure_codes() -> None:
    """Local write-back: spine has non-none head; failures project without fake tool.execute_end."""
    tid = "e" * 32
    spine = build_decision_spine(_events_local_turn(tid), trace_id=tid)
    assert spine["head"]["source"] == "chat.local_turn_recorded"
    assert spine["head"]["source"] != "none"
    assert spine["head"]["chars"] == 42
    assert spine["tail"]["source"] == "jsonl_close"
    assert spine["tail"]["event"] == "chat.local_turn_recorded"
    assert spine["tail"]["finish_reason"] == "stop"
    assert spine["health"]["incomplete"] is False
    events = {d["event"] for d in spine["decisions"]}
    assert "chat.local_turn_tool_failures" in events
    assert "tool.execute_end" not in events
    fail = next(d for d in spine["decisions"] if d["event"] == "chat.local_turn_tool_failures")
    assert fail["detail"]["codes"] == ["searxng_unreachable", "egress_connect"]
    assert fail["detail"]["tools"] == ["web_search", "web_fetch"]
    text = format_decision_spine(spine)
    assert "chat.local_turn_tool_failures" in text
    assert "searxng_unreachable" in text


def test_local_turn_recorded_does_not_mask_cloud_turn_close() -> None:
    """If both local_turn_recorded and turn_complete exist, prefer primary close/start."""
    tid = "f" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-11T12:00:00Z",
            "trace_id": tid,
            "preview": "cloud",
            "via": "cloud",
        },
        {
            "type": "log",
            "event": "chat.local_turn_recorded",
            "timestamp": "2026-08-11T12:00:01Z",
            "trace_id": tid,
            "chars": 1,
            "rounds": 1,
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-11T12:00:02Z",
            "trace_id": tid,
            "finish_reason": "stop",
            "rounds": 2,
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    assert spine["head"]["source"] == "chat.turn_start"
    assert spine["tail"]["event"] == "chat.turn_complete"
    assert spine["tail"]["rounds"] == 2


def test_execution_groups_tools_and_keeps_failures_on_decisions() -> None:
    tid = "g" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-20T10:00:00Z",
            "trace_id": tid,
            "preview": "run tools",
        },
        *[
            {
                "type": "log",
                "event": "tool.execute_end",
                "timestamp": f"2026-08-20T10:00:{i:02d}Z",
                "trace_id": tid,
                "tool": "read_file",
                "status": "ok",
                "duration_ms": 10,
            }
            for i in range(1, 21)
        ],
        {
            "type": "log",
            "event": "tool.execute_end",
            "timestamp": "2026-08-20T10:00:21Z",
            "trace_id": tid,
            "tool": "web_search",
            "status": "error",
            "reason": "timeout",
            "duration_ms": 2100,
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-20T10:00:22Z",
            "trace_id": tid,
            "finish_reason": "stop",
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    decision_events = [d["event"] for d in spine["decisions"]]
    assert decision_events.count("tool.execute_end") == 1
    assert spine["execution"]["tools"]["calls"] == 21
    assert spine["execution"]["tools"]["ok"] == 20
    assert spine["execution"]["tools"]["error"] == 1
    by_name = {row["tool"]: row for row in spine["execution"]["tools"]["by_tool"]}
    assert by_name["read_file"]["ok"] == 20
    assert by_name["web_search"]["error"] == 1
    text = format_decision_spine(spine)
    assert "Exec  tools=21 ok=20 err=1" in text
    assert text.count("tool.execute_end") == 1
    assert len(text.splitlines()) < 40


def test_allowlist_deny_counts_as_exec_error_and_lands_on_decisions() -> None:
    """Patrol and spine share is_tool_failure: deny is a failure; redirect is a steer."""
    tid = "k" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-09-01T04:00:00Z",
            "trace_id": tid,
            "preview": "deny vs steer",
        },
        {
            "type": "log",
            "event": "tool.execute_end",
            "timestamp": "2026-09-01T04:00:01Z",
            "trace_id": tid,
            "tool": "glob",
            "status": "ok",
            "duration_ms": 5,
        },
        {
            "type": "log",
            "event": "tool.execute_end",
            "timestamp": "2026-09-01T04:00:02Z",
            "trace_id": tid,
            "tool": "glob",
            "status": "allowlist_deny",
            "reason": "工具 'glob' 不在本 run 的允许列表中，未执行。",
            "duration_ms": 0,
        },
        {
            "type": "log",
            "event": "tool.execute_end",
            "timestamp": "2026-09-01T04:00:03Z",
            "trace_id": tid,
            "tool": "code_execute",
            "status": "redirect",
            "reason": "use run",
            "duration_ms": 0,
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-09-01T04:00:04Z",
            "trace_id": tid,
            "finish_reason": "stop",
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    tool_decisions = [
        d for d in spine["decisions"] if d["event"] == "tool.execute_end"
    ]
    statuses = [d["detail"]["status"] for d in tool_decisions]
    assert statuses == ["allowlist_deny", "redirect"]
    tools = spine["execution"]["tools"]
    assert tools["calls"] == 3
    assert tools["ok"] == 2
    assert tools["error"] == 1
    by_name = {row["tool"]: row for row in tools["by_tool"]}
    assert by_name["glob"]["ok"] == 1
    assert by_name["glob"]["error"] == 1
    assert by_name["code_execute"]["ok"] == 1
    assert by_name["code_execute"]["error"] == 0
    text = format_decision_spine(spine)
    assert "allowlist_deny" in text
    assert "Exec  tools=3 ok=2 err=1" in text


def test_llm_call_failed_and_prepare_surface_without_listing_every_call() -> None:
    tid = "h" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-20T11:00:00Z",
            "trace_id": tid,
            "preview": "llm fail",
        },
        {
            "type": "log",
            "event": "chat.prepare_phase",
            "timestamp": "2026-08-20T11:00:01Z",
            "trace_id": tid,
            "phase": "rules",
            "ms": 12,
        },
        {
            "type": "log",
            "event": "chat.prepare_phase",
            "timestamp": "2026-08-20T11:00:02Z",
            "trace_id": tid,
            "phase": "assemble",
            "ms": 80,
        },
        {
            "type": "log",
            "event": "llm.call",
            "timestamp": "2026-08-20T11:00:03Z",
            "trace_id": tid,
            "model": "demo",
            "scenario": "chat",
            "latency_ms": 100,
            "input_tokens": 10,
            "output_tokens": 5,
        },
        {
            "type": "log",
            "event": "llm.call",
            "timestamp": "2026-08-20T11:00:04Z",
            "trace_id": tid,
            "model": "demo",
            "scenario": "agent",
            "latency_ms": 8000,
            "input_tokens": 40,
            "output_tokens": 20,
        },
        {
            "type": "log",
            "event": "llm.call_failed",
            "timestamp": "2026-08-20T11:00:05Z",
            "trace_id": tid,
            "level": "error",
            "model": "demo",
            "scenario": "chat",
            "error": "429 rate limit",
            "latency_ms": 50,
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-20T11:00:06Z",
            "trace_id": tid,
            "finish_reason": "error",
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    assert "llm.call_failed" in {d["event"] for d in spine["decisions"]}
    assert spine["llm"]["calls"] == 2
    assert spine["llm"]["failed"] == 1
    assert spine["llm"]["slowest"]["latency_ms"] == 8000
    assert spine["llm"]["slowest"]["scenario"] == "agent"
    scenarios = {row["scenario"]: row for row in spine["llm"]["by_scenario"]}
    assert scenarios["chat"]["failed"] == 1
    assert spine["execution"]["prepare"]["ms_sum"] == 92
    text = format_decision_spine(spine)
    assert "llm.call_failed" in text
    assert "Prep  rules=12ms assemble=80ms" in text
    assert "slowest" in text
    assert "scenario" in text


def test_llm_round_exception_surfaces_on_spine_with_error_type() -> None:
    tid = "j" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-31T01:53:26Z",
            "trace_id": tid,
            "preview": "图片中是什么内容",
        },
        {
            "type": "log",
            "event": "llm.call_failed",
            "timestamp": "2026-08-31T01:53:28Z",
            "trace_id": tid,
            "level": "error",
            "model": "flash",
            "scenario": "title",
            "error": "平台模型暂时不可用",
            "error_type": "LLMInsufficientBalanceError",
        },
        {
            "type": "log",
            "event": "engine.llm_round_exception",
            "timestamp": "2026-08-31T01:53:32Z",
            "trace_id": tid,
            "level": "error",
            "error_type": "TypeError",
            "error_code": "LLM_ERROR",
            "classified": False,
            "origin": "stream_round",
            "error": 'can only concatenate str (not "list") to str',
        },
        {
            "type": "log",
            "event": "engine.llm_failed_terminal",
            "timestamp": "2026-08-31T01:53:33Z",
            "trace_id": tid,
            "level": "warning",
            "reason": "error",
            "has_content": False,
            "error_type": "TypeError",
            "origin": "stream_round",
            "classified": False,
            "error_code": "LLM_ERROR",
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-31T01:53:33Z",
            "trace_id": tid,
            "finish_reason": "error",
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    names = [d["event"] for d in spine["decisions"]]
    assert "engine.llm_round_exception" in names
    assert "engine.llm_failed_terminal" in names
    round_exc = next(d for d in spine["decisions"] if d["event"] == "engine.llm_round_exception")
    assert round_exc["detail"]["error_type"] == "TypeError"
    assert round_exc["detail"]["classified"] is False
    assert round_exc["detail"]["origin"] == "stream_round"
    text = format_decision_spine(spine)
    assert "engine.llm_round_exception" in text
    assert "TypeError" in text


def test_obs_turn_spans_runs_fold_without_tool_children() -> None:
    tid = "i" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-20T12:00:00Z",
            "trace_id": tid,
            "preview": "spans",
        },
        {
            "type": "log",
            "event": "obs.turn_spans",
            "timestamp": "2026-08-20T12:00:10Z",
            "trace_id": tid,
            "span_count": 4,
            "truncated": False,
            "dropped": 0,
            "spans": [
                {"span_id": "turn", "operation": "chat", "status": "ok"},
                {
                    "span_id": "run:cap",
                    "operation": "invoke_agent",
                    "name": "invoke_agent captain",
                    "status": "ok",
                    "duration_ms": 1200,
                    "attributes": {
                        "agentcore.run.id": "cap",
                        "agentcore.run.kind": "captain",
                    },
                },
                {
                    "span_id": "run:w1",
                    "operation": "invoke_agent",
                    "name": "invoke_agent writer",
                    "status": "error",
                    "duration_ms": 9000,
                    "attributes": {
                        "agentcore.run.id": "w1",
                        "agentcore.run.kind": "agent",
                        "agentcore.run.role": "writer",
                    },
                },
                {
                    "span_id": "tool:t1",
                    "operation": "execute_tool",
                    "name": "execute_tool web_search",
                    "status": "ok",
                    "duration_ms": 400,
                },
            ],
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-20T12:00:11Z",
            "trace_id": tid,
            "finish_reason": "stop",
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    runs = spine["execution"]["runs"]
    assert runs["source"] == "obs.turn_spans"
    assert [row["run_id"] for row in runs["items"]] == ["w1", "cap"]  # error first
    assert all(row.get("name", "").startswith("invoke_agent") for row in runs["items"])
    text = format_decision_spine(spine)
    assert "invoke_agent writer" in text
    assert "execute_tool web_search" not in text


def test_spine_omits_degradation_without_events() -> None:
    spine = build_decision_spine(_events_delegated_ok(), trace_id="a" * 32)
    assert "degradation" not in spine
    assert "Degraded" not in format_decision_spine(spine)


def test_spine_degradation_keys_complete_when_present() -> None:
    tid = "j" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-26T07:00:00Z",
            "trace_id": tid,
            "preview": "retry then stall",
        },
        {
            "type": "log",
            "event": "llm.call_retried",
            "timestamp": "2026-08-26T07:00:01Z",
            "trace_id": tid,
            "reason": "upstream_502",
            "attempt": 1,
        },
        {
            "type": "log",
            "event": "platform_pool.failover",
            "timestamp": "2026-08-26T07:00:02Z",
            "trace_id": tid,
            "from_credential_id": "a",
            "to_credential_id": "b",
        },
        {
            "type": "log",
            "event": "llm.stream_stalled",
            "timestamp": "2026-08-26T07:00:03Z",
            "trace_id": tid,
            "committed": False,
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-26T07:00:04Z",
            "trace_id": tid,
            "finish_reason": "stop",
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    deg = spine["degradation"]
    assert set(deg) == {"layers", "counts", "summary"}
    assert deg["layers"] == list(LAYER_ORDER)
    assert set(deg["counts"]) == set(COUNT_KEYS)
    assert deg["counts"]["leaf_retry"] == 1
    assert deg["counts"]["credential_pool"] == 1
    assert deg["counts"]["stream_stall_precommit"] == 1
    assert deg["counts"]["admission"] == 0
    text = format_decision_spine(spine)
    assert "Degraded" in text
    assert "leaf_retry=1" in text
    assert "credential_pool=1" in text
    assert "stream_stall_precommit=1" in text


def test_spine_omits_replay_persist_when_saved() -> None:
    spine = build_decision_spine(_events_delegated_ok(), trace_id="a" * 32)
    assert "replay_persist" not in spine
    assert "这一轮的回放没存上" not in format_decision_spine(spine)


def test_spine_replay_persist_failed_human_line() -> None:
    tid = "k" * 32
    events = [
        {
            "type": "log",
            "event": "chat.turn_start",
            "timestamp": "2026-08-26T08:00:00Z",
            "trace_id": tid,
            "preview": "ok reply",
        },
        {
            "type": "log",
            "event": "journal.persist_failed",
            "timestamp": "2026-08-26T08:00:02Z",
            "trace_id": tid,
            "message_id": "m1",
            "error": "disk full",
        },
        {
            "type": "log",
            "event": "chat.turn_complete",
            "timestamp": "2026-08-26T08:00:03Z",
            "trace_id": tid,
            "finish_reason": "stop",
        },
    ]
    spine = build_decision_spine(events, trace_id=tid)
    assert spine["replay_persist"] == {
        "saved": False,
        "summary": "这一轮的回放没存上",
    }
    text = format_decision_spine(spine)
    assert "Replay  这一轮的回放没存上" in text
    assert "journal.persist_failed" not in text
