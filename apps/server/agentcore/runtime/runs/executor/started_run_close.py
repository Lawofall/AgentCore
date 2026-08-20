"""Started-run close guarantee: emit ``run_cancelled`` if the run never got a terminal.

Shared by Wave members, ``continue_run``, and the captain — same
``run_cancelled.reason`` mapping, no per-site cancel catches.
"""

from __future__ import annotations

import sys

from agentcore.runtime.events import EventSink, run_cancelled
from agentcore.runtime.runs.executor.terminal import cancel_reason_from_exc


def _sink_run_has_terminal(sink: object, run_id: str) -> bool:
    """True when ``sink`` already closed this run; unknown sinks → True (don't invent)."""
    probe = getattr(sink, "run_has_terminal", None)
    if callable(probe):
        try:
            return bool(probe(run_id))
        except Exception:  # noqa: BLE001 — a mock/partial sink must not break unwind
            return True
    ids = getattr(sink, "_terminal_run_ids", None)
    if isinstance(ids, (set, frozenset)):
        return run_id in ids
    return True


def emit_run_cancelled_if_unterminated(
    sink: EventSink,
    run_id: str,
    agent_id: str,
    *,
    execution_id: str = "",
) -> None:
    """If this run already ``run_started`` but has no terminal, emit ``run_cancelled``.

    Structured guarantee for executor ``finally``: ``CancelledError`` bypasses
    ``except Exception``, so continuation / captain / debate continue_run used to
    leave journal with only ``run_started``. Never materialises a run that never
    started (caller must invoke only after ``run_started``). Duplicate terminals
    are dropped by the sink. Does not swallow — callers still re-raise.
    """
    if not run_id or _sink_run_has_terminal(sink, run_id):
        return
    sink.emit(
        run_cancelled(
            run_id,
            agent_id,
            reason=cancel_reason_from_exc(sys.exc_info()[1]),
            execution_id=execution_id,
        )
    )
