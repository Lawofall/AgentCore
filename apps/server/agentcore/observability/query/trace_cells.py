"""Owner-facing model transcript for one turn.

The desktop developer-mode page reads this. Each line is one fact the model
read or wrote, in write order, payload included. Display events that only
paint the chat stay in the journal and are omitted. Dev JSONL is not
consulted. A missing journal is a single gap sentence.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.facts import FactKind
from agentcore.runtime.journal.entries import KIND_TURN_END

_GAP_NONE = "这场回复没有追踪编号"
_GAP_GONE = "这次的记录不在"
# Model transcript only. Resume blobs (plan / pause / format gates) and every
# display kind (tool cards, process steps, run chrome) stay in the journal.
_SHOWN_KINDS = frozenset(
    {
        FactKind.TURN_STARTED.value,
        FactKind.RUN_HEAD.value,
        FactKind.ROUND_BOUNDARY.value,
        FactKind.LLM_CALL.value,
        FactKind.TOOL_CALL.value,
        FactKind.NOTE.value,
        FactKind.MESSAGE_FINAL.value,
        FactKind.WINDOW_COMPACT.value,
        KIND_TURN_END,
    }
)
_TOOL_KINDS = frozenset({FactKind.TOOL_CALL.value})


def build_turn_trace(
    *,
    trace_id: str | None,
    mode: str | None = None,
    duration_ms: int | None = None,
    journal: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One turn as header fields plus the model transcript. No log events."""
    tid = (trace_id or "").strip() or None
    entries = [entry for entry in (journal or []) if isinstance(entry, dict)]
    if not tid:
        return _payload(trace_id=None, gap=_GAP_NONE)
    if not entries:
        return _payload(
            trace_id=tid,
            where=_place_label(mode),
            duration_ms=_positive_int(duration_ms),
            gap=_GAP_GONE,
        )
    return _payload(
        trace_id=tid,
        where=_place_label(mode),
        model=_model_profile(entries),
        duration_ms=_positive_int(duration_ms),
        in_progress=not any((entry.get("kind") or "") == KIND_TURN_END for entry in entries),
        lines=[
            _line(index, entry)
            for index, entry in enumerate(entries)
            if (entry.get("kind") or "") in _SHOWN_KINDS
        ],
    )


def _payload(
    *,
    trace_id: str | None,
    where: str | None = None,
    model: str | None = None,
    duration_ms: int | None = None,
    in_progress: bool = False,
    gap: str | None = None,
    lines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "trace_id": trace_id,
        "in_progress": in_progress,
        "lines": lines or [],
    }
    if where:
        body["where"] = where
    if model:
        body["model"] = model
    if duration_ms is not None:
        body["duration_ms"] = duration_ms
    if gap:
        body["gap"] = gap
    return body


def _line(index: int, entry: dict[str, Any]) -> dict[str, Any]:
    kind = str(entry.get("kind") or "")
    payload = entry.get("payload")
    body = payload if isinstance(payload, dict) else {}
    row: dict[str, Any] = {"id": f"j-{index}", "kind": kind, "payload": body}
    ts = entry.get("ts")
    if isinstance(ts, str) and ts.strip():
        row["ts"] = ts.strip()
    mark = _mark(kind, body)
    if mark:
        row["mark"] = mark
    if kind == "llm_call":
        run_id = _text(body.get("run_id"))
        idx = body.get("round_idx")
        if run_id:
            row["run_id"] = run_id
        if isinstance(idx, int):
            row["round_idx"] = idx
    return row


def _mark(kind: str, payload: dict[str, Any]) -> str | None:
    if kind == "llm_call":
        run_id = _text(payload.get("run_id"))
        idx = payload.get("round_idx")
        if run_id and isinstance(idx, int):
            return f"{run_id} round={idx}"
        if isinstance(idx, int):
            return f"round={idx}"
        return run_id
    if kind == "note":
        return _text(payload.get("reason")) or _text(payload.get("run_id"))
    if kind == "turn_end":
        return _text(payload.get("finish_reason"))
    if kind in _TOOL_KINDS:
        return (
            _text(payload.get("name"))
            or _text(payload.get("tool_name"))
            or _text(payload.get("run_id"))
        )
    return _text(payload.get("run_id"))


def _model_profile(entries: list[dict[str, Any]]) -> str | None:
    for entry in entries:
        if (entry.get("kind") or "") != "turn_started":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        return _text(payload.get("model_profile"))
    return None


def _place_label(mode: str | None) -> str | None:
    if mode == "local":
        return "本机"
    if mode == "cloud":
        return "云端"
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number
