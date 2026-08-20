"""Turn-level result quality: ``ok | partial | paused | error``.

查询入口（与 RunPhase / FinishReason / 关帧集合对照）→ ``runtime.terminal``。

``finish_reason`` answers how the loop ended; this answers what the turn produced.
Partial is aggregated from batch/node signals already on the wire — no new
heuristics. ``paused`` is produced only when the wire sets it explicitly
(CEO rate-limit continue). Gate pauses (ask_user / plan_review) keep
``outcome=null``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from agentcore.llm.provider.protocol import LLMMessage, llm_content_text
from agentcore.tools.file_products import strip_file_products_markers
from agentcore.tools.protocol import TOOL_AUDIENCE_CEO

TurnOutcome = Literal["ok", "partial", "paused", "error"]

OUTCOMES: tuple[TurnOutcome, ...] = ("ok", "partial", "paused", "error")
PRODUCED_OUTCOMES: tuple[TurnOutcome, ...] = ("ok", "partial", "paused", "error")

_DELEGATE_TOOL = "delegate"
_TOOL_FAILED_MARKER = "<!--agentcore:tool_failed-->"


def coerce_produced_outcome(value: object) -> TurnOutcome | None:
    """Return a produced outcome, or ``None`` if missing / unknown."""
    if not isinstance(value, str):
        return None
    if value in PRODUCED_OUTCOMES:
        return value  # type: ignore[return-value]
    return None


def _event_type_and_payload(ev: object) -> tuple[str, Mapping[str, Any]]:
    if isinstance(ev, Mapping):
        raw = ev.get("type") or ev.get("kind") or ""
        payload = ev.get("payload")
        return (
            str(getattr(raw, "value", raw) or ""),
            payload if isinstance(payload, Mapping) else {},
        )
    raw = getattr(ev, "type", "")
    payload = getattr(ev, "payload", None)
    return (
        str(getattr(raw, "value", raw) or ""),
        payload if isinstance(payload, Mapping) else {},
    )


def events_have_partial_product(events: object) -> bool:
    """True when a batch/node already declared partial product.

    Signals (OR, existing bits only):

    - ``delivery_status.state == "partial"``
    - ``run_failed.product_landed``
    - delegate tool meta ``partial_failure`` (on ``tool_use_end`` when forwarded)
    """
    if isinstance(events, str | bytes) or not isinstance(events, Iterable):
        return False
    for ev in events:
        etype, payload = _event_type_and_payload(ev)
        if etype == "delivery_status" and payload.get("state") == "partial":
            return True
        if etype == "run_failed" and payload.get("product_landed") is True:
            return True
        if etype == "tool_use_end" and payload.get("partial_failure") is True:
            return True
    return False


def resolve_turn_outcome(
    *,
    events: object = (),
    finish_reason: object = None,
    has_error: bool = False,
    explicit: object = None,
    running: bool = False,
) -> TurnOutcome | None:
    """Aggregate turn-level result. ``None`` only while the stream is still running.

    Prefer ``explicit`` (``message_end.outcome`` / ``turn_end.outcome``) when it is a
    produced value. Else OR the batch-level partial bits, else error vs ok.
    ``finish=paused`` without an explicit outcome stays ``None`` (gate cards).
    """
    if running:
        return None
    chosen = coerce_produced_outcome(explicit)
    if chosen is not None:
        return chosen
    if events_have_partial_product(events):
        return "partial"
    finish = getattr(finish_reason, "value", finish_reason)
    # Gate pauses (ask_user / plan_review) keep outcome=null. CEO rate-limit
    # continue stamps explicit ``paused`` on message_end.
    if finish == "paused":
        return None
    if has_error or finish == "error":
        return "error"
    return "ok"


def last_delegate_tool_output(messages: list[LLMMessage] | None) -> str:
    """Last user-facing ``delegate`` tool result in the CEO transcript.

    CEO-audience orchestration (coordination start/merge echo) is skipped: if
    that is the last non-empty delegate output, salvage returns empty rather
    than falling back to an older batch. Blocking synthesis stays unmarked.
    """
    if not messages:
        return ""
    delegate_ids: list[str] = []
    by_id: dict[str, tuple[str, str | None]] = {}
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                name = ""
                fn = getattr(call, "function", None)
                if fn is not None:
                    name = str(getattr(fn, "name", "") or "")
                if name == _DELEGATE_TOOL and call.id:
                    delegate_ids.append(call.id)
        elif message.role == "tool" and message.tool_call_id:
            by_id[message.tool_call_id] = (
                _clean_tool_text(llm_content_text(message.content)),
                getattr(message, "audience", None),
            )
    for call_id in reversed(delegate_ids):
        text, audience = by_id.get(call_id, ("", None))
        text = text.strip()
        if not text:
            continue
        if audience == TOOL_AUDIENCE_CEO:
            return ""
        return text
    return ""


def last_delegate_tool_output_from_events(events: object) -> str:
    """Last user-facing ``tool_use_end.result`` for ``delegate`` (SSE / journal).

    ``audience=ceo`` on the event (coordination host echo) is not salvageable.
    """
    last = ""
    if isinstance(events, str | bytes) or not isinstance(events, Iterable):
        return last
    for ev in events:
        etype, payload = _event_type_and_payload(ev)
        if etype != "tool_use_end":
            continue
        if payload.get("tool_name") != _DELEGATE_TOOL:
            continue
        text = _clean_tool_text(str(payload.get("result") or ""))
        if not text:
            continue
        if payload.get("audience") == TOOL_AUDIENCE_CEO:
            last = ""
            continue
        last = text
    return last


def salvage_captain_delegate_reply(
    *,
    final_content: str,
    messages: list[LLMMessage] | None,
    role: str,
) -> str:
    """When the captain has nothing to say, reuse the last user-facing delegate output.

    Empty string means "do not replace". Workers are never salvaged into the
    user-facing bubble. Coordination host echo is CEO-audience and is refused.
    """
    if role != "captain" or (final_content or "").strip():
        return ""
    return last_delegate_tool_output(messages)


def _clean_tool_text(text: str) -> str:
    body = strip_file_products_markers(text or "")
    if _TOOL_FAILED_MARKER in body:
        body = body.replace(_TOOL_FAILED_MARKER, "")
    return body.strip()
