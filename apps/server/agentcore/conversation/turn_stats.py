"""Turn-level observation helpers for ``chat.turn_complete`` / ``chat.resume_complete``.

``workers`` / ``delegated`` used to be ``len(cost_runs) - 1``. That口径 breaks when:
- checkpoint pause defers member ledger fold (cost_runs is captain-only / empty of members);
- ``role=vision`` rows inflate the count as if they were workers.

Authority: count ``role=member`` ledger rows, and union completed worker
``message_final`` facts from the journal (phase present discriminates workers from the
captain bubble) so a pause/resume segment still reports the workers that already finished.
Local finalize has no ``cost_runs`` on the write-back; it passes journal entries only
(the same half this helper already uses when pause defers ledger fold).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentcore.runtime.costing import ROLE_MEMBER
from agentcore.runtime.facts import FactKind
from agentcore.runtime.runs.types import RunPhase


def turn_worker_stats(result: Mapping[str, Any]) -> tuple[bool, int]:
    """Return ``(delegated, workers)`` for turn_metrics and close-line logs."""
    ids: set[str] = set()
    for row in result.get("cost_runs") or ():
        if not isinstance(row, Mapping):
            continue
        if row.get("role") != ROLE_MEMBER:
            continue
        run_id = row.get("run_id")
        if run_id:
            ids.add(str(run_id))

    entries: Sequence[Any] = result.get("journal_entries") or ()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if (entry.get("kind") or "") != FactKind.MESSAGE_FINAL.value:
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        run_id = payload.get("run_id")
        # Captain message_final has no ``phase``; workers carry the RunState seed.
        if run_id and payload.get("phase") == RunPhase.COMPLETED.value:
            ids.add(str(run_id))

    workers = len(ids)
    return workers > 0, workers
