"""First-terminal-wins gate for run close frames on one EventSink.

Split from ``sink.py`` — occupancy tracking only. Close-frame membership lives in
``agentcore.runtime.terminal.RUN_CLOSE_EVENT_TYPES``. Duplicate close frames for
the same ``run_id`` are dropped so fold last-write-wins cannot diverge from the
live stream.
"""

from __future__ import annotations


class SinkTerminalMixin:
    """Per-sink ``run_id`` terminal occupancy (executor ``finally`` close check)."""

    _terminal_run_ids: set[str]

    def run_has_terminal(self, run_id: str) -> bool:
        """True when this sink already emitted a close frame for ``run_id``.

        Close = :data:`~agentcore.runtime.terminal.RUN_CLOSE_EVENT_TYPES`
        (completed / failed / cancelled / skipped). Used by the executor
        ``finally`` so a started run cannot leave the journal without a close
        frame (CancelledError bypasses ``except Exception``).
        """
        return bool(run_id) and run_id in self._terminal_run_ids
