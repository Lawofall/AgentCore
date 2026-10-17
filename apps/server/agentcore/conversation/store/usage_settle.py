"""Reload-visible settle snapshot for ``Message.usage``.

Cloud ``_usage_metadata`` and sidecar writeback must forward the same keys.
Spend is not in this snapshot (inference proxy). Wire ``prompt_tokens`` becomes
``last_prompt_tokens`` on the usage row.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any, Literal

# Integer fields copied onto the outbox record / RecordTurnRequest.
USAGE_SETTLE_WIRE_INT_KEYS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
    "rounds",
    "prompt_tokens",
)

# Optional clocks: omit when missing / not positive (older clients).
USAGE_SETTLE_POSITIVE_INT_KEYS: tuple[str, ...] = (
    "duration_ms",
    "generation_ms",
    "ttft_ms",
)

USAGE_SETTLE_INT_KEYS: tuple[str, ...] = (
    USAGE_SETTLE_WIRE_INT_KEYS + USAGE_SETTLE_POSITIVE_INT_KEYS
)

# Non-int settle facts. ``finish_reason`` is owned by the finalize caller
# (enum → str); the rest copy when present.
USAGE_SETTLE_PASSTHROUGH_KEYS: tuple[str, ...] = (
    "error_code",
    "collab",
    "outcome",
)

USAGE_SETTLE_KEYS: frozenset[str] = frozenset(
    USAGE_SETTLE_INT_KEYS + ("finish_reason",) + USAGE_SETTLE_PASSTHROUGH_KEYS
)

# Local-only latch / salvage surface — not part of the cloud settle snapshot.
LOCAL_USAGE_EXTRA_KEYS: frozenset[str] = frozenset(
    {"paused", "incomplete", "error"}
)


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            return 0
    return 0


def usage_settle_finalize_kwargs(result: Mapping[str, Any]) -> dict[str, Any]:
    """Kwargs to merge into ``OutboxStore.finalize`` from a pipeline settle result.

    ``finish_reason`` stays with the caller (already stringified).
    """
    out: dict[str, Any] = {}
    for key in USAGE_SETTLE_WIRE_INT_KEYS:
        out[key] = _coerce_int(result.get(key))
    for key in USAGE_SETTLE_POSITIVE_INT_KEYS:
        if key in result and result[key] is not None:
            out[key] = result[key]
    for key in USAGE_SETTLE_PASSTHROUGH_KEYS:
        val = result.get(key)
        if val is None or val == "":
            continue
        out[key] = getattr(val, "value", val)
    return out


def copy_usage_settle_into_record(
    source: Mapping[str, Any], record: MutableMapping[str, Any]
) -> None:
    """Copy settle fields from finalize kwargs onto an outbox record."""
    for key in USAGE_SETTLE_INT_KEYS:
        if key in source and source[key] is not None:
            record[key] = _coerce_int(source[key])
    for key in USAGE_SETTLE_PASSTHROUGH_KEYS:
        val = source.get(key)
        if val is None or val == "":
            continue
        record[key] = val


def apply_usage_settle_to_record_turn_body(
    record: Mapping[str, Any], body: MutableMapping[str, Any]
) -> None:
    """Project outbox settle fields onto the ``RecordTurnRequest`` wire body."""
    for key in USAGE_SETTLE_WIRE_INT_KEYS:
        body[key] = _coerce_int(record.get(key))
    for key in USAGE_SETTLE_POSITIVE_INT_KEYS:
        raw = record.get(key)
        if raw is None:
            continue
        n = _coerce_int(raw)
        if n > 0:
            body[key] = n
    for key in USAGE_SETTLE_PASSTHROUGH_KEYS:
        val = record.get(key)
        if val is None or val == "":
            continue
        body[key] = val


MessageStatusLane = Literal["cloud", "local_writeback", "outbox"]


def project_message_status(
    finish_value: str | None,
    *,
    lane: MessageStatusLane,
    outcome: str | None = None,
    has_reply: bool = False,
    has_error: bool = False,
) -> str:
    """Map a turn finish onto ``Message.usage.status``.

    The three callers disagree, and those disagreements stay here:

    * ``cloud`` — a partial turn with a visible reply is complete; an error
      fails; everything else is complete. A paused cloud turn is written
      earlier and must not reach this lane.
    * ``local_writeback`` — paused stays running; cancelled / interrupted are
      incomplete; an error with a partial reply stays complete.
    * ``outbox`` — the stamp before write-back. Cancelled is incomplete,
      paused is running, error is failed. The partial-reply upgrade happens
      on ``local_writeback``, not here.
    """
    from agentcore.core.message_merge import (
        MESSAGE_STATUS_COMPLETE,
        MESSAGE_STATUS_FAILED,
        MESSAGE_STATUS_INCOMPLETE,
        MESSAGE_STATUS_RUNNING,
    )
    from agentcore.runtime.events.types import FinishReason

    finish = finish_value if isinstance(finish_value, str) else None
    if lane == "outbox":
        if finish == FinishReason.CANCELLED.value:
            return MESSAGE_STATUS_INCOMPLETE
        if finish == FinishReason.ERROR.value:
            return MESSAGE_STATUS_FAILED
        if finish == FinishReason.PAUSED.value:
            return MESSAGE_STATUS_RUNNING
        return MESSAGE_STATUS_COMPLETE
    if lane == "local_writeback":
        if finish == FinishReason.PAUSED.value:
            return MESSAGE_STATUS_RUNNING
        if finish in {
            FinishReason.CANCELLED.value,
            FinishReason.INTERRUPTED.value,
        }:
            return MESSAGE_STATUS_INCOMPLETE
        if finish == FinishReason.ERROR.value:
            if outcome == "partial" and has_reply:
                return MESSAGE_STATUS_COMPLETE
            return MESSAGE_STATUS_FAILED
        return MESSAGE_STATUS_COMPLETE
    if outcome == "partial" and has_reply:
        return MESSAGE_STATUS_COMPLETE
    if has_error or finish == FinishReason.ERROR.value:
        return MESSAGE_STATUS_FAILED
    return MESSAGE_STATUS_COMPLETE
