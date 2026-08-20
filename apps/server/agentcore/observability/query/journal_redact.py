"""Redact ``turn_journal`` rows for deliverable investigation packs / slim sync.

Journal is the SSE replay fact source and includes user dialogue, system prompts,
LLM completions, and full tool results. Packs and default prod sync must never
ship that payload — only structural identity, status, and metrics.

Allowlist + type gate: numbers/bools always keep; strings keep only for code-like
keys (ids, tool names, finish_reason, clipped error). Everything else is dropped
and counted in ``_omitted_chars``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

JOURNAL_REDACT_SCHEMA = "journal_redacted.v0"

_CLIP = 240
_DROP = object()

_ROW_KEEP = (
    "turn_id",
    "seq",
    "band",
    "kind",
    "ts",
    "conversation_id",
    "trace_id",
    "created_at",
)

# Nested containers + metric/identity keys that may hold numbers or enums.
_KEEP_KEYS = frozenset(
    {
        "run_id",
        "parent_run_id",
        "agent_id",
        "execution_id",
        "node_id",
        "tool_call_id",
        "call_id",
        "interaction_id",
        "message_id",
        "host_message_id",
        "kind",
        "role",
        "status",
        "phase",
        "audience",
        "tool",
        "tool_name",
        "name",
        "finish_reason",
        "reason",
        "failure_kind",
        "error_code",
        "code",
        "ok",
        "success",
        "cancelled",
        "orphaned",
        "retryable",
        "partial_failure",
        "product_landed",
        "duration_ms",
        "latency_ms",
        "rounds",
        "depth",
        "round_idx",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "tokens",
        "model",
        "scenario",
        "stream",
        "model_profile",
        "history_len",
        "retry_after",
        "error_type",
        "upstream_status",
        "workers",
        "delegated",
        "continues_run_id",
        "depends_on",
        "error",
        "usage",
        "cost",
        "failure",
        "input",
        "output",
        "reasoning",
        "total",
        "currency",
    }
)

# Strings that are ids / enums / clipped errors — never prompts or message bodies.
_KEEP_STRINGS = frozenset(
    {
        "run_id",
        "parent_run_id",
        "agent_id",
        "execution_id",
        "node_id",
        "tool_call_id",
        "call_id",
        "interaction_id",
        "message_id",
        "host_message_id",
        "kind",
        "role",
        "status",
        "phase",
        "audience",
        "tool",
        "tool_name",
        "name",
        "finish_reason",
        "reason",
        "failure_kind",
        "error_code",
        "code",
        "model",
        "scenario",
        "model_profile",
        "error",
        "error_type",
        "currency",
        "continues_run_id",
        "depends_on",
    }
)


def _omitted_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (dict, list)):
        try:
            return len(json.dumps(value, ensure_ascii=False, default=str))
        except TypeError:
            return 0
    return 0


def _redact_value(value: Any, *, key: str) -> tuple[Any, int]:
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0
    if isinstance(value, str):
        if key not in _KEEP_STRINGS:
            return _DROP, len(value)
        if len(value) <= _CLIP:
            return value, 0
        kept = value[: _CLIP - 1] + "…"
        return kept, len(value) - (_CLIP - 1)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        omitted = 0
        for child_key, child in value.items():
            kept, extra = _redact_value(child, key=str(child_key))
            omitted += extra
            if kept is not _DROP:
                out[str(child_key)] = kept
        return out, omitted
    if isinstance(value, list):
        if key and key not in _KEEP_KEYS:
            return _DROP, _omitted_size(value)
        out_list: list[Any] = []
        omitted = 0
        for item in value:
            kept, extra = _redact_value(item, key=key)
            omitted += extra
            if kept is not _DROP:
                out_list.append(kept)
        return out_list, omitted
    return _DROP, 0


def redact_journal_payload(payload: Any) -> tuple[dict[str, Any], int]:
    """Return ``(redacted_payload, omitted_char_count)``."""
    if not isinstance(payload, dict):
        return {}, _omitted_size(payload)
    kept, omitted = _redact_value(payload, key="")
    if not isinstance(kept, dict):
        return {}, omitted + _omitted_size(payload)
    return kept, omitted


def redact_journal_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project one journal row to the pack/sync-safe shape."""
    payload, omitted = redact_journal_payload(row.get("payload"))
    out: dict[str, Any] = {}
    for key in _ROW_KEEP:
        if key not in row:
            continue
        val = row[key]
        if key == "created_at" and val is not None:
            out[key] = val.isoformat() if isinstance(val, datetime) else str(val)
        else:
            out[key] = val
    out["payload"] = payload
    out["_omitted_chars"] = omitted
    return out


def summarize_redacted_journal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact kind/failure index — no payload text."""
    by_kind: dict[str, int] = {}
    omitted = 0
    failed_runs = 0
    failed_tools = 0
    llm_facts = 0
    tool_facts = 0
    for row in rows:
        kind = str(row.get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        omitted += int(row.get("_omitted_chars") or 0)
        raw_payload = row.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        if kind == "run_failed":
            failed_runs += 1
        if kind in {"tool_use_end", "tool_call"}:
            tool_facts += 1
            if payload.get("status") == "error" or payload.get("success") is False:
                failed_tools += 1
        if kind == "llm_call":
            llm_facts += 1
    return {
        "schema_version": JOURNAL_REDACT_SCHEMA,
        "rows": len(rows),
        "omitted_chars": omitted,
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))),
        "failed_runs": failed_runs,
        "failed_tools": failed_tools,
        "llm_facts": llm_facts,
        "tool_facts": tool_facts,
    }
