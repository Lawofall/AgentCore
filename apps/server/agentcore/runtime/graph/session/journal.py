"""Journal projection for coordination state (ask_user suspend / resume)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from agentcore.runtime.graph.session.session import (
    CoordinationSession,
    CoordinationSnapshot,
)
from agentcore.runtime.facts import Fact, FactKind, record_turn_fact


@dataclass(frozen=True, slots=True)
class CoordinationSnapshotFact:
    """Durable coordination state for resume (draft / completed / budget)."""

    snapshot: dict[str, Any]
    kind: ClassVar[FactKind] = FactKind.COORDINATION_SNAPSHOT

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={"snapshot": dict(self.snapshot)},
            ts=ts,
        )


def record_coordination_snapshot(session: CoordinationSession) -> None:
    """Append the latest coordination snapshot to the turn journal (best-effort)."""
    record_turn_fact(CoordinationSnapshotFact(snapshot=session.snapshot().to_dict()).to_fact())


def coordination_from_journal(entries: list[dict[str, Any]] | None) -> CoordinationSnapshot | None:
    """Fold the last ``coordination_snapshot`` fact from a journal stream."""
    if not entries:
        return None
    last: dict[str, Any] | None = None
    for entry in entries:
        if (entry.get("kind") or "") == FactKind.COORDINATION_SNAPSHOT.value:
            payload = entry.get("payload") or {}
            snap = payload.get("snapshot")
            if isinstance(snap, dict):
                last = snap
    return CoordinationSnapshot.from_dict(last)


def record_plan_snapshot(plan: Any) -> None:
    """Journal the current plan as a ``plan_snapshot`` fact."""
    from agentcore.runtime.runs.serialize import plan_snapshot_fact

    record_turn_fact(plan_snapshot_fact(plan))
