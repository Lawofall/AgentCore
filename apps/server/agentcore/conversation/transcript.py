"""Prior-turn transcript for the next CEO window.

Projects one assistant turn's journal into the messages that turn already
showed the captain: completed tool rounds, then the sealed assistant prose.
No system prompt, no worker tools, no engine notes. ``reasoning_content`` is
kept only on assistant rows that carry ``tool_calls`` — thinking-mode relays
400 if that field is dropped from a tool-call turn (平台 LLM · DeepSeek 易错).
The prose row does not carry it.

Unclosed calls (stopped before ``tool_call`` was recorded) get a closed tool
message so the next request is not left with a dangling ``tool_call``.
"""

from __future__ import annotations

from typing import Any

UNCLOSED_TOOL_RESULT = "（此调用在回合结束时未返回。）"


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


def transcript_rows(
    entries: list[dict[str, Any]] | None,
    assistant_content: str,
    evidence_ledger: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """CEO-visible rows for one prior turn. Empty when there is nothing to replay.

    A ``graph_rewind`` fact replaces this turn's tool trace with the kept
    conclusions and the scar line. The original user message stays the prior row.
    """
    facts = list(entries or [])
    from agentcore.runtime.graph.shape.rewind import history_row, latest_rewind_payload

    rewind = latest_rewind_payload(facts)
    if rewind is not None:
        return [history_row(rewind)]
    target = captain_run_id(facts) if facts else None
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
