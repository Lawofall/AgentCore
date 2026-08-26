"""Tests for log_timeline jsonl gap → journal hint + ID heuristics / empty-hit copy."""

from __future__ import annotations

from scripts.log_timeline import (
    detect_jsonl_timeline_gap,
    format_conversation_context,
    format_empty_hit_hint,
    format_timeline,
    format_trace,
    is_bare_trace_id,
    normalize_trace_id_arg,
)


def test_detect_gap_on_timestamp_hole() -> None:
    events = [
        {"event": "chat.turn_start", "timestamp": "2026-07-22T10:00:00Z", "type": "log"},
        {"event": "react.round_end", "timestamp": "2026-07-22T10:05:00Z", "type": "log"},
    ]
    gap = detect_jsonl_timeline_gap(events, min_gap_seconds=120)
    assert gap is not None
    assert gap["reason"] == "timestamp_gap"
    assert gap["gap_seconds"] == 300.0


def test_detect_gap_on_rollover_failed_event() -> None:
    events = [
        {
            "event": "logging.rollover_failed",
            "timestamp": "2026-07-22T10:00:00Z",
            "type": "log",
        },
        {"event": "chat.turn_start", "timestamp": "2026-07-22T10:00:01Z", "type": "log"},
    ]
    gap = detect_jsonl_timeline_gap(events)
    assert gap is not None
    assert gap["reason"] == "rollover_failed"


def test_no_gap_for_dense_timeline() -> None:
    events = [
        {"event": "a", "timestamp": "2026-07-22T10:00:00Z", "type": "log"},
        {"event": "b", "timestamp": "2026-07-22T10:00:30Z", "type": "log"},
    ]
    assert detect_jsonl_timeline_gap(events, min_gap_seconds=120) is None


def test_format_trace_includes_journal_hint() -> None:
    events = [
        {"event": "chat.turn_start", "timestamp": "2026-07-22T10:00:00Z", "type": "log"},
        {"event": "chat.turn_complete", "timestamp": "2026-07-22T10:10:00Z", "type": "log"},
    ]
    out = format_trace("abc", events)
    assert "以 Postgres journal 为准" in out
    assert "gap≈600s" in out


def test_format_timeline_includes_journal_hint_on_rollover() -> None:
    events = [
        {
            "event": "logging.rollover_failed",
            "timestamp": "2026-07-22T10:00:00Z",
            "type": "log",
        },
    ]
    out = format_timeline(
        {"id": "c1", "title": "t", "agent_id": "a", "created_at": "2026-07-22"},
        [],
        events,
    )
    assert "以 Postgres journal 为准" in out


def test_is_bare_trace_id() -> None:
    assert is_bare_trace_id("a" * 32)
    assert is_bare_trace_id("0123456789abcdefABCDEF0123456789")
    assert not is_bare_trace_id("a" * 31)
    assert not is_bare_trace_id("a" * 33)
    assert not is_bare_trace_id("01234567-89ab-cdef-0123-456789abcdef")
    assert not is_bare_trace_id("not-hex-but-thirty-two-chars!!!!!!")


def test_normalize_trace_id_arg() -> None:
    bare = "0123456789abcdef0123456789abcdef"
    uuid = "01234567-89ab-cdef-0123-456789abcdef"
    assert normalize_trace_id_arg(bare) == bare
    assert normalize_trace_id_arg(uuid) == "0123456789abcdef0123456789abcdef"
    assert normalize_trace_id_arg("not-a-uuid") == "not-a-uuid"
    assert normalize_trace_id_arg("short-id") == "short-id"


def test_format_empty_hit_hint() -> None:
    hint = format_empty_hit_hint(using_export_dir=False)
    assert "pnpm sync:logs" in hint
    assert "--export-dir ../../logs/prod-export" in hint
    assert "32-hex" in hint
    assert "conversation_id" in hint
    assert format_empty_hit_hint(using_export_dir=True) == ""


def test_format_trace_empty_hit_hint() -> None:
    out = format_trace("deadbeef" * 4, [], using_export_dir=False)
    assert "Log events: 0" in out
    assert "pnpm sync:logs" in out
    assert format_empty_hit_hint(using_export_dir=False) in out

    out_export = format_trace("deadbeef" * 4, [], using_export_dir=True)
    assert "Log events: 0" in out_export
    assert "pnpm sync:logs" not in out_export


def test_format_conversation_context_incomplete_wording() -> None:
    spine = [
        {
            "event": "chat.turn_start",
            "trace_id": "t1",
            "timestamp": "2026-07-22T10:00:00Z",
            "preview": "hello",
        },
    ]
    out = format_conversation_context("conv-1", spine, "t1")
    assert "⚠️ 未完成（进行中或仅 kickoff）" in out
    assert "⚠️ 未完成\n" not in out


def test_format_trace_incomplete_status_header() -> None:
    events = [
        {
            "event": "chat.turn_start",
            "timestamp": "2026-07-22T10:00:00Z",
            "type": "log",
            "trace_id": "t1",
        },
    ]
    out = format_trace("t1", events)
    assert "Status: ⚠️ 未完成（进行中或仅 kickoff）" in out


def test_attach_failure_pack_meta(monkeypatch) -> None:
    from scripts.log_timeline import _attach_failure_pack_meta

    tid = "a" * 32
    monkeypatch.setattr(
        "scripts.log_timeline.failure_pack_pointer",
        lambda _tid: {"kind": "journal_only", "path": f"logs/packs/{tid}"},
    )
    out = _attach_failure_pack_meta({"mode": "trace", "meta": {}}, tid)
    assert out["meta"]["failure_pack"] == {
        "kind": "journal_only",
        "path": f"logs/packs/{tid}",
    }
    monkeypatch.setattr("scripts.log_timeline.failure_pack_pointer", lambda _tid: None)
    bare = _attach_failure_pack_meta({"mode": "trace", "meta": {"k": 1}}, tid)
    assert "failure_pack" not in bare["meta"]
    assert bare["meta"]["k"] == 1
