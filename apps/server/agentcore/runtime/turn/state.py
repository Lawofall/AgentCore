"""Immutable turn projection from the §8.3 journal (唯一事实源).

``TurnState.from_journal`` is the single projection entry for resume / crash recover.
It folds plan / completed / execution_id / coordination
(and optionally the CEO window) from ordered facts — no second source of truth.
``upto_seq`` supports time-travel / fork (project a prefix); UI for that is out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.runtime.graph.session.journal import coordination_from_journal
from agentcore.runtime.journal.fold import (
    completed_from_journal,
    execution_id_from_journal,
    plan_from_journal,
    window_from_journal,
)

if TYPE_CHECKING:
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.graph.session.session import CoordinationSnapshot
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState


def _entries_upto(
    entries: list[dict[str, Any]] | None,
    upto_seq: int | None,
) -> list[dict[str, Any]] | None:
    """Slice journal facts to ``seq <= upto_seq`` (inclusive).

    DB-loaded rows carry ``seq``; in-memory fact streams are 0-based by list index
    when ``seq`` is absent. ``None`` keeps the full stream.
    """
    if entries is None or upto_seq is None:
        return entries
    out: list[dict[str, Any]] = []
    for i, entry in enumerate(entries):
        seq = entry.get("seq")
        if seq is None:
            seq = i
        try:
            if int(seq) <= upto_seq:
                out.append(entry)
        except (TypeError, ValueError):
            out.append(entry)
    return out


@dataclass(frozen=True, slots=True)
class TurnState:
    """Immutable projection of one turn's journal at a point in the fact stream."""

    plan: RunPlan | None
    completed: dict[str, RunState]
    execution_id: str | None
    coordination: CoordinationSnapshot | None
    # Kept for window / display folds that need the sliced stream (not a second source).
    entries: tuple[dict[str, Any], ...]

    @classmethod
    def from_journal(
        cls,
        entries: list[dict[str, Any]] | None,
        *,
        upto_seq: int | None = None,
        display_journal: list[dict[str, Any]] | None = None,
    ) -> TurnState:
        """Fold journal facts into the resume / recover seed projection.

        ``display_journal`` is only consulted for ``execution_id`` when facts lack a
        ``run_plan`` (same posture as :func:`execution_id_from_journal`).
        """
        sliced = _entries_upto(entries, upto_seq) or []
        return cls(
            plan=plan_from_journal(sliced),
            completed=completed_from_journal(sliced),
            execution_id=execution_id_from_journal(sliced, display_journal),
            coordination=coordination_from_journal(sliced),
            entries=tuple(sliced),
        )

    def window(
        self,
        *,
        run_id: str | None = None,
        history: list[LLMMessage] | None = None,
    ) -> list[LLMMessage] | None:
        """CEO / worker LLM window at this projection point (§8.3)."""
        return window_from_journal(list(self.entries), run_id=run_id, history=history)

    @property
    def unfinished_run_ids(self) -> list[str]:
        """Plan node ids not yet present in the completed seed (crash redrive targets)."""
        if self.plan is None:
            return []
        return [n.run_id for n in self.plan.nodes if n.run_id not in self.completed]
