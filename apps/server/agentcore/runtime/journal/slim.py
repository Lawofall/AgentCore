"""Cold GET list slimming for ``RunsPayload.events`` (read path only).

After fold + stream overlay, the messages list keeps only surface / resolved /
terminal marker events so reload stays thin; full journal is on GET one message.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.events import _JOURNAL_SURFACE_TYPES
from agentcore.runtime.events.types import EventType
from agentcore.runtime.interaction import INTERACTION_KIND_SPECS


def list_retained_event_types() -> frozenset[str]:
    """Events kept on ``GET …/messages`` list replay (derived — do not hand-copy)."""
    retained = set(_JOURNAL_SURFACE_TYPES)
    retained.add(EventType.MESSAGE_END.value)
    retained.add(EventType.DELIVERY_STATUS.value)
    for spec in INTERACTION_KIND_SPECS.values():
        if spec.resolved_event:
            retained.add(spec.resolved_event)
    return frozenset(retained)


def slim_runs_payload(runs: dict[str, Any]) -> dict[str, Any]:
    """Drop non-retained display events; flag incompleteness for the list client."""
    retained = list_retained_event_types()
    events = list(runs.get("events") or [])
    slimmed = [ev for ev in events if (ev.get("type") or "") in retained]
    out = dict(runs)
    out["events"] = slimmed
    if len(slimmed) < len(events):
        out["events_complete"] = False
        out["run_processes"] = None
    else:
        out["events_complete"] = True
    return out
