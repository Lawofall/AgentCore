"""D7 server-side merge rules for assistant message rows (leaf; db + conversation).

Four invariants under outbox reorder / retry:

1. **content merge** — checkpoints / salvage / incomplete stay length-monotonic; a
   ``complete`` finalize delivery is authoritative and may replace a longer mid-stream body.
2. **status gate** — completion status only advances (never ``complete`` → ``running``).
3. **journal seq idempotent** — duplicate appends dedupe on ``(turn_id, seq)``.
4. **finalize full journal** — finalize upserts the full fact list by seq to fill holes.
5. **terminal ⇒ not paused** — ``merge_usage_status`` is the single authority that clears
   the pause latch whenever merged ``status`` is terminal (prevents ``paused:true``
   resurrecting across a second ``{**existing, **incoming}`` merge).
"""

from __future__ import annotations

from typing import Any

from agentcore.core.errors import UNCLASSIFIED_EXCEPTION_USER_MESSAGE

# Progressive assistant-row lifecycle (Message.usage.status).
MESSAGE_STATUS_RUNNING = "running"
MESSAGE_STATUS_COMPLETE = "complete"
MESSAGE_STATUS_INCOMPLETE = "incomplete"
MESSAGE_STATUS_FAILED = "failed"

# Higher rank wins; equal ranks keep the existing value (no oscillation).
_STATUS_RANK: dict[str, int] = {
    MESSAGE_STATUS_RUNNING: 0,
    MESSAGE_STATUS_INCOMPLETE: 1,
    MESSAGE_STATUS_FAILED: 1,
    MESSAGE_STATUS_COMPLETE: 2,
}

_TERMINAL_STATUSES = frozenset(
    {
        MESSAGE_STATUS_COMPLETE,
        MESSAGE_STATUS_INCOMPLETE,
        MESSAGE_STATUS_FAILED,
    }
)


def status_rank(status: str | None) -> int:
    if not status:
        return -1
    return _STATUS_RANK.get(status, -1)


def is_terminal_status(status: str | None) -> bool:
    return status in _TERMINAL_STATUSES


def should_advance_status(existing: str | None, incoming: str | None) -> bool:
    """True when ``incoming`` is strictly ahead of ``existing`` (D7 status gate)."""
    return status_rank(incoming) > status_rank(existing)


def merge_usage_status(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict:
    """Merge usage metadata with status-only-advances (D7).

    Non-status keys from ``incoming`` win (finalize / checkpoint payload is fresher);
    ``status`` only moves forward.

    **Pause latch invariant** (single authority): a terminal ``status`` is never paused.
    ``paused`` is only meaningful while ``status=running``; once the merged status is
    terminal, the latch is cleared here — callers must not re-implement pop/clear
    around a second ``{**existing, **incoming}`` merge (that resurrects stale
    ``paused:true`` when incoming omits the key).
    """
    base = dict(existing or {})
    nxt = dict(incoming or {})
    existing_status = base.get("status")
    incoming_status = nxt.get("status")
    merged = {**base, **nxt}
    if not should_advance_status(existing_status, incoming_status):
        if existing_status is not None:
            merged["status"] = existing_status
        elif "status" in merged and incoming_status is None:
            merged.pop("status", None)
    # 终态必非暂停：terminal status wins over any residual paused latch.
    if is_terminal_status(merged.get("status")) or nxt.get("paused") is False:
        merged.pop("paused", None)
    return merged


def pick_monotonic_content(existing: str | None, incoming: str | None) -> str:
    """Prefer the longer body (salvage / incomplete monotonic protection)."""
    a = existing or ""
    b = incoming or ""
    return b if len(b) >= len(a) else a


def pick_merged_content(
    existing: str | None,
    incoming: str | None,
    *,
    incoming_status: str | None = None,
) -> str:
    """Merge assistant body for finalize / upsert.

    ``complete`` finalize is the authoritative delivery — it may replace a longer
    mid-stream draft. Salvage / incomplete / failed keep length-monotonic protection
    so a shorter crash salvage cannot erase a fuller partial.
    """
    if incoming_status == MESSAGE_STATUS_COMPLETE:
        return incoming or ""
    return pick_monotonic_content(existing, incoming)


def pick_longest(*candidates: str | None) -> str:
    """Reduce candidates with :func:`pick_monotonic_content` (error/FAILED salvage)."""
    best = ""
    for c in candidates:
        best = pick_monotonic_content(best, c)
    return best


# Default ``error.message`` when a FAILED settle must synthesize structured
# ``{code, message}`` for journal/usage. Never stuffed into ``message.content``.
# Default ``code`` in that synthesizer is ``PIPELINE_ERROR`` — keep this sentence
# identical to ``UNCLASSIFIED_EXCEPTION_USER_MESSAGE``.
DEFAULT_FAILED_ERROR_MESSAGE = UNCLASSIFIED_EXCEPTION_USER_MESSAGE


def visible_failed_assistant_content(
    *,
    content: str | None,
    error: str | None = None,
) -> str:
    """Pick assistant ``content`` for a FAILED / ERROR settle.

    Keeps any partial deliverable prose. When there is no half-finished body,
    returns ``""`` — structured ``error`` on journal/usage is the authority for
    failure copy; callers must not write error/default text into ``message.content``.

    ``error`` is accepted for call-site compatibility and ignored.
    """
    _ = error  # structured error lives on journal/usage, not content
    body = content or ""
    return body if body.strip() else ""
