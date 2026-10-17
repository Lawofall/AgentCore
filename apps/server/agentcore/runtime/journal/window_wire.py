"""Project a turn journal into the diagnostic LLM-window wire shape."""

from __future__ import annotations

from typing import Any

from agentcore.api.schemas.llm_window import (
    LlmWindowMessageLine,
    LlmWindowToolCall,
    LlmWindowToolCallFunction,
    RunLlmWindowResponse,
)
from agentcore.llm.provider.protocol import LLMMessage, llm_content_text
from agentcore.runtime.facts import FactKind
from agentcore.runtime.journal import window_from_journal
from agentcore.runtime.resolve.prompt.envelope import history_row_to_llm_message


def _history_len(entries: list[dict[str, Any]]) -> int:
    for entry in entries:
        if (entry.get("kind") or "") == FactKind.TURN_STARTED.value:
            return int((entry.get("payload") or {}).get("history_len") or 0)
    return 0


def _run_head_user_origin(entries: list[dict[str, Any]], run_id: str) -> str | None:
    """Return ``user_origin`` from this run's ``run_head`` fact, if any."""
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.RUN_HEAD.value:
            continue
        payload = entry.get("payload") or {}
        if payload.get("run_id") == run_id:
            origin = payload.get("user_origin") or ""
            return str(origin) if origin else None
    return None


def _llm_message_to_wire(
    msg: LLMMessage,
    *,
    origin: str | None = None,
) -> LlmWindowMessageLine:
    tool_calls = None
    if msg.tool_calls:
        tool_calls = [
            LlmWindowToolCall(
                id=tc.id,
                type=tc.type,
                function=LlmWindowToolCallFunction(
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                ),
            )
            for tc in msg.tool_calls
        ]
    return LlmWindowMessageLine(
        role=msg.role,
        content=llm_content_text(msg.content) if msg.content is not None else None,
        tool_calls=tool_calls,
        tool_call_id=msg.tool_call_id,
        reasoning_content=msg.reasoning_content,
        origin=origin,
    )


def project_run_llm_window(
    entries: list[dict[str, Any]] | None,
    *,
    run_id: str,
    history: list[dict] | None = None,
    round_idx: int | None = None,
    history_len: int | None = None,
) -> RunLlmWindowResponse:
    """Fold journal facts into one run's LLM input window for diagnostic replay.

    ``round_idx`` set: the request that call received (facts before it, compact
    watermark already on the journal). Unset: the end-state transcript, compact
    left off, matching resume's fat window.
    """
    if not entries:
        return RunLlmWindowResponse(run_id=run_id, available=False, messages=[])

    history_msgs = (
        [history_row_to_llm_message(h) for h in history] if history else None
    )
    if round_idx is None:
        window = window_from_journal(entries, run_id=run_id, history=history_msgs)
        if not window:
            return RunLlmWindowResponse(run_id=run_id, available=False, messages=[])
        return _wire_response(entries, run_id=run_id, window=window)

    declared = history_len if history_len is not None else _history_len(entries)
    return _project_at_call(
        entries,
        run_id=run_id,
        round_idx=round_idx,
        history_msgs=history_msgs,
        history_len=declared,
        history_rows=len(history or []),
    )


def _project_at_call(
    entries: list[dict[str, Any]],
    *,
    run_id: str,
    round_idx: int,
    history_msgs: list[LLMMessage] | None,
    history_len: int,
    history_rows: int,
) -> RunLlmWindowResponse:
    positions = _call_positions(entries, run_id)
    target_at = next((n for n, (_i, idx) in enumerate(positions) if idx == round_idx), None)
    if target_at is None:
        return RunLlmWindowResponse(run_id=run_id, available=False, messages=[])

    entry_index = positions[target_at][0]
    prev_index = positions[target_at - 1][0] if target_at else None
    window, rewritten = _seen_window(entries[:entry_index], run_id, history_msgs)
    if not window:
        return RunLlmWindowResponse(run_id=run_id, available=False, messages=[])

    current_key = _compact_key(entries[:entry_index], run_id)
    if prev_index is None:
        new_from = 0
        compacted = rewritten
    else:
        prev_key = _compact_key(entries[:prev_index], run_id)
        prev, _prev_rewritten = _seen_window(entries[:prev_index], run_id, history_msgs)
        prev_len = len(prev or [])
        if current_key != prev_key:
            new_from = 0
            compacted = rewritten
        elif len(window) >= prev_len:
            new_from = prev_len
            compacted = False
        else:
            new_from = 0
            compacted = rewritten

    omitted = (
        not _has_run_head(entries, run_id) and history_len > 0 and history_rows < history_len
    )
    return _wire_response(
        entries,
        run_id=run_id,
        window=window,
        new_from=new_from,
        compacted=compacted,
        history_omitted=omitted,
    )


def _wire_response(
    entries: list[dict[str, Any]],
    *,
    run_id: str,
    window: list[LLMMessage],
    new_from: int | None = None,
    compacted: bool = False,
    history_omitted: bool = False,
) -> RunLlmWindowResponse:
    head_origin = _run_head_user_origin(entries, run_id)
    messages: list[LlmWindowMessageLine] = []
    saw_opening_user = False
    for msg in window:
        origin: str | None = None
        if (
            head_origin
            and not saw_opening_user
            and msg.role == "user"
            and not msg.tool_call_id
        ):
            origin = head_origin
            saw_opening_user = True
        messages.append(_llm_message_to_wire(msg, origin=origin))
    return RunLlmWindowResponse(
        run_id=run_id,
        available=True,
        messages=messages,
        new_from=new_from,
        compacted=compacted,
        history_omitted=history_omitted,
    )


def _call_positions(entries: list[dict[str, Any]], run_id: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for index, entry in enumerate(entries):
        if (entry.get("kind") or "") != FactKind.LLM_CALL.value:
            continue
        payload = entry.get("payload") or {}
        if payload.get("run_id") != run_id:
            continue
        round_idx = payload.get("round_idx")
        if isinstance(round_idx, bool) or not isinstance(round_idx, int):
            continue
        found.append((index, round_idx))
    return found


def _has_run_head(entries: list[dict[str, Any]], run_id: str) -> bool:
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.RUN_HEAD.value:
            continue
        if (entry.get("payload") or {}).get("run_id") == run_id:
            return True
    return False


def _compact_key(entries: list[dict[str, Any]], run_id: str) -> tuple[str, int] | None:
    key: tuple[str, int] | None = None
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.WINDOW_COMPACT.value:
            continue
        payload = entry.get("payload") or {}
        if payload.get("run_id") != run_id:
            continue
        raw_summary = payload.get("summary")
        summary = raw_summary if isinstance(raw_summary, str) else ""
        folded = payload.get("folded_rounds")
        rounds = folded if isinstance(folded, int) and not isinstance(folded, bool) else 0
        key = (summary, rounds)
    return key


def _seen_window(
    entries: list[dict[str, Any]],
    run_id: str,
    history: list[LLMMessage] | None,
) -> tuple[list[LLMMessage] | None, bool]:
    """Return the request window and whether compact replaced earlier rounds.

    Identity is checked inside this call: a no-op compact returns the same list.
    """
    raw = window_from_journal(entries, run_id=run_id, history=history)
    if not raw:
        return None, False
    key = _compact_key(entries, run_id)
    if key is None:
        return raw, False
    from agentcore.runtime.engine.window_compact import project_compacted_window

    summary, folded = key
    projected = project_compacted_window(raw, summary=summary, folded_rounds=folded)
    return projected, projected is not raw


def history_len_from_journal(entries: list[dict[str, Any]] | None) -> int:
    """Expose ``turn_started.history_len`` for the route's history loader."""
    if not entries:
        return 0
    return _history_len(entries)
