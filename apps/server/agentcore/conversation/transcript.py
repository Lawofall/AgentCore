"""Prior-turn transcript for the next CEO window.

Projects one assistant turn's journal into the messages that turn already
showed the captain: completed tool rounds, then the sealed assistant prose.
No system prompt, no worker tools, no engine notes. ``reasoning_content`` is
kept only on assistant rows that carry ``tool_calls`` — thinking-mode relays
400 if that field is dropped from a tool-call turn (平台 LLM · DeepSeek 易错).
The prose row does not carry it.

Unclosed calls (stopped before ``tool_call`` was recorded) get a closed tool
message so the next request is not left with a dangling ``tool_call``.

A completed turn is not a journal dump. The next user turn applies the same
window-compact projection the live ReAct already paid for; when that fact is
missing and tool receipts still exceed the chat recency budget, older results
become a restorable placeholder (call names stay). Canonical journal / UI stay
full. 执行引擎 §三 · 历史重建.
"""

from __future__ import annotations

from typing import Any

from agentcore.config import settings
from agentcore.core.text import estimate_text_tokens

UNCLOSED_TOOL_RESULT = "（此调用在回合结束时未返回。）"
CLEARED_TOOL_RESULT = (
    "（工具回执已从窗口撤下，按路径再读，不要重做已完成的步骤。）"
)


def captain_run_id(entries: list[dict[str, Any]]) -> str | None:
    """First ``role=captain`` round, else the first round boundary.

    Matches ``window_from_journal`` when the caller does not pass ``run_id``:
    a worker's tools stay on that worker's run and are not the CEO window.
    """
    first: str | None = None
    for entry in entries:
        if (entry.get("kind") or "") != "round_boundary":
            continue
        payload = entry.get("payload") or {}
        rid = str(payload.get("run_id") or "")
        if not rid:
            continue
        if first is None:
            first = rid
        if payload.get("role") == "captain":
            return rid
    return first


def _tool_call_row(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    tcid = str(raw.get("id") or "").strip()
    if not tcid:
        return None
    function = raw.get("function")
    fn: dict[str, Any] = function if isinstance(function, dict) else {}
    return {
        "id": tcid,
        "type": "function",
        "function": {
            "name": str(fn.get("name") or ""),
            "arguments": str(fn.get("arguments") or ""),
        },
    }


def _in_run(payload: dict[str, Any], target: str | None) -> bool:
    if target is None:
        return True
    return str(payload.get("run_id") or "") == target


def _captain_tool_rounds(
    facts: list[dict[str, Any]], target: str | None
) -> list[dict[str, Any]]:
    results: dict[str, str] = {}
    for entry in facts:
        if (entry.get("kind") or "") != "tool_call":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict) or not _in_run(payload, target):
            continue
        tcid = str(payload.get("tool_call_id") or "").strip()
        if tcid:
            results[tcid] = payload.get("result") or ""

    rows: list[dict[str, Any]] = []
    for entry in facts:
        if (entry.get("kind") or "") != "llm_call":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict) or not _in_run(payload, target):
            continue
        calls = [
            row
            for raw in (payload.get("tool_calls") or [])
            if (row := _tool_call_row(raw)) is not None
        ]
        if not calls:
            continue
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": payload.get("content") or "",
            "tool_calls": calls,
        }
        reasoning = payload.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            assistant["reasoning_content"] = reasoning
        rows.append(assistant)
        for call in calls:
            tcid = call["id"]
            body = results.get(tcid, UNCLOSED_TOOL_RESULT)
            rows.append(
                {
                    "role": "tool",
                    "content": body if isinstance(body, str) else str(body),
                    "tool_call_id": tcid,
                }
            )
    return rows


def _window_compact_key(
    entries: list[dict[str, Any]], run_id: str | None
) -> tuple[str, int] | None:
    if not run_id:
        return None
    key: tuple[str, int] | None = None
    for entry in entries:
        if (entry.get("kind") or "") != "window_compact":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict) or str(payload.get("run_id") or "") != run_id:
            continue
        summary = payload.get("summary")
        folded = payload.get("folded_rounds")
        if not isinstance(summary, str) or not summary.strip():
            continue
        if not isinstance(folded, int) or isinstance(folded, bool) or folded <= 0:
            continue
        key = (summary, folded)
    return key


def _apply_window_compact(
    entries: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    run_id: str | None,
) -> list[dict[str, Any]]:
    """Reuse the live ReAct compact prefix; do not un-compact at the turn boundary."""
    key = _window_compact_key(entries, run_id)
    if key is None or not rows:
        return rows
    from agentcore.runtime.engine.window_compact import project_compacted_window
    from agentcore.runtime.resolve.prompt.envelope import (
        history_row_to_llm_message,
        llm_message_to_history_row,
    )

    summary, folded = key
    messages = [history_row_to_llm_message(row) for row in rows]
    projected = project_compacted_window(
        messages, summary=summary, folded_rounds=folded
    )
    if projected is messages:
        return rows
    return [llm_message_to_history_row(message) for message in projected]


def _tool_result_tokens(rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in rows:
        if row.get("role") != "tool":
            continue
        total += estimate_text_tokens(str(row.get("content") or ""))
    return total


def _clear_stale_tool_results(
    rows: list[dict[str, Any]],
    *,
    token_budget: int,
) -> list[dict[str, Any]]:
    """Keep call names; replace older receipts once tool-result tokens exceed recency.

    Newest assistant round stays verbatim even when it alone exceeds the budget.
    Journal stays full — this is a request-window projection, same grain as
    window compact. Anthropic context-editing ``clear_tool_uses`` / Claude Code
    microcompact: results go first, inputs stay.
    """
    if not rows or token_budget < 0:
        return rows
    if _tool_result_tokens(rows) <= token_budget:
        return rows
    from agentcore.runtime.engine.window_compact import assistant_round_spans
    from agentcore.runtime.resolve.prompt.envelope import history_row_to_llm_message

    messages = [history_row_to_llm_message(row) for row in rows]
    spans = assistant_round_spans(messages, start=0)
    if len(spans) <= 1:
        return rows
    out = [dict(row) for row in rows]
    for lo, hi in spans[:-1]:
        if _tool_result_tokens(out) <= token_budget:
            break
        for index in range(lo, hi):
            if out[index].get("role") != "tool":
                continue
            if out[index].get("content") == CLEARED_TOOL_RESULT:
                continue
            out[index] = {**out[index], "content": CLEARED_TOOL_RESULT}
    return out


def _append_sealed_prose(
    rows: list[dict[str, Any]],
    assistant_content: str,
    evidence_ledger: list[Any] | None,
) -> list[dict[str, Any]]:
    prose = (assistant_content or "").strip()
    last_assistant = ""
    for row in reversed(rows):
        if row.get("role") == "assistant":
            last_assistant = (row.get("content") or "").strip()
            break
    ledger = (
        list(evidence_ledger)
        if isinstance(evidence_ledger, list) and evidence_ledger
        else None
    )
    if prose and prose != last_assistant:
        item: dict[str, Any] = {"role": "assistant", "content": prose}
        if ledger is not None:
            item["evidence_ledger"] = ledger
        rows.append(item)
    elif ledger is not None:
        for row in reversed(rows):
            if row.get("role") == "assistant":
                row["evidence_ledger"] = ledger
                break
    return rows


def transcript_rows(
    entries: list[dict[str, Any]] | None,
    assistant_content: str,
    evidence_ledger: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """CEO-visible rows for one prior turn. Empty when there is nothing to replay.

    User utterance is not in this list (the chat loader already has that row).
    Sealed assistant prose is always appended verbatim. Tool process is the
    compact projection, not the fat journal.
    """
    facts = list(entries or [])
    target = captain_run_id(facts) if facts else None
    rows = _captain_tool_rounds(facts, target)
    rows = _apply_window_compact(facts, rows, target)
    rows = _clear_stale_tool_results(
        rows, token_budget=settings.compaction_recency_token_budget
    )
    return _append_sealed_prose(rows, assistant_content, evidence_ledger)
