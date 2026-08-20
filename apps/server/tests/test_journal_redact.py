"""Redacted turn_journal rows never ship user/LLM bodies."""

from __future__ import annotations

from agentcore.observability.query.journal_redact import (
    JOURNAL_REDACT_SCHEMA,
    redact_journal_row,
    summarize_redacted_journal,
)


def test_redact_drops_turn_started_dialogue() -> None:
    row = redact_journal_row(
        {
            "turn_id": "t1",
            "seq": 0,
            "band": "live",
            "kind": "turn_started",
            "trace_id": "a" * 32,
            "payload": {
                "system_prompt": "You are the CEO. User said SECRET_PROMPT",
                "user_message": "please write my diary",
                "model_profile": "chat",
                "history_len": 3,
            },
        }
    )
    payload = row["payload"]
    assert payload["model_profile"] == "chat"
    assert payload["history_len"] == 3
    assert "system_prompt" not in payload
    assert "user_message" not in payload
    assert "SECRET_PROMPT" not in str(row)
    assert "diary" not in str(row)
    assert row["_omitted_chars"] > 0


def test_redact_llm_call_keeps_metrics_drops_content() -> None:
    row = redact_journal_row(
        {
            "turn_id": "t1",
            "seq": 2,
            "kind": "llm_call",
            "payload": {
                "run_id": "cap",
                "round_idx": 1,
                "finish_reason": "stop",
                "content": "here is the full assistant reply",
                "reasoning": "chain of thought the user must not see",
                "usage": {"input": 10, "output": 20, "reasoning": 4},
            },
        }
    )
    payload = row["payload"]
    assert payload["run_id"] == "cap"
    assert payload["finish_reason"] == "stop"
    assert payload["usage"]["input"] == 10
    assert "content" not in payload
    assert payload.get("reasoning") != "chain of thought the user must not see"
    dumped = str(row)
    assert "full assistant reply" not in dumped
    assert "chain of thought" not in dumped


def test_redact_tool_use_end_drops_result_keeps_status() -> None:
    row = redact_journal_row(
        {
            "turn_id": "t1",
            "seq": 3,
            "kind": "tool_use_end",
            "payload": {
                "tool_call_id": "tc1",
                "tool_name": "web_search",
                "status": "error",
                "result": "search hits containing user query",
                "arguments": {"query": "my private question"},
                "failure": {"code": "timeout", "message": "中文失败文案"},
                "run_id": "w1",
            },
        }
    )
    payload = row["payload"]
    assert payload["tool_name"] == "web_search"
    assert payload["status"] == "error"
    assert payload["run_id"] == "w1"
    assert payload["failure"]["code"] == "timeout"
    assert "result" not in payload
    assert "arguments" not in payload or "private" not in str(payload.get("arguments"))
    assert "my private question" not in str(row)
    assert "search hits" not in str(row)


def test_summarize_counts_failures() -> None:
    rows = [
        redact_journal_row({"kind": "run_failed", "payload": {"run_id": "w1", "error": "boom"}}),
        redact_journal_row(
            {
                "kind": "tool_call",
                "payload": {"name": "web_search", "success": False, "result": "x" * 50},
            }
        ),
        redact_journal_row({"kind": "llm_call", "payload": {"content": "hi", "run_id": "c"}}),
    ]
    summary = summarize_redacted_journal(rows)
    assert summary["schema_version"] == JOURNAL_REDACT_SCHEMA
    assert summary["rows"] == 3
    assert summary["failed_runs"] == 1
    assert summary["failed_tools"] == 1
    assert summary["llm_facts"] == 1
    assert summary["omitted_chars"] > 0
    assert "boom" not in str(summary)
