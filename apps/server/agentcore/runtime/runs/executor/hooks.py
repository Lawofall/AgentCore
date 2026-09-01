"""Domain hooks for AGENT-node execution: retrieval / citation.

Split from ``.node`` — pure move; call only via the node facade or
sibling ``executor.*`` modules (do not grow new external ``_`` importers).
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.runs.types import RunState


def _stamp_retrieval_evidence_gap(
    state: RunState,
    tool_ctx: Any,
    *,
    search_policy: str = "",
) -> RunState:
    """Copy sticky ``RetrievalBudgetState.evidence_gap`` onto RunState for consumers.

    Search stamps the sticky on the budget during academic_literature junk/empty
    injects; delivery / research_quality read ``state.evidence_gap`` or
    ``state.evidence_meta["evidence_gap"]`` without scanning every tool event.
    """
    rb = getattr(tool_ctx, "retrieval_budget", None) if tool_ctx is not None else None
    if rb is None or not getattr(rb, "evidence_gap", False):
        return state
    state.evidence_gap = True
    meta = getattr(state, "evidence_meta", None)
    meta = {} if not isinstance(meta, dict) else dict(meta)
    meta["evidence_gap"] = True
    policy = (search_policy or "").strip()
    if policy:
        meta.setdefault("search_policy", policy)
    state.evidence_meta = meta
    return state


def _two_phase_citation(deliverable: Any) -> bool:
    from agentcore.runtime.runs.research_quality import is_two_phase_citation_deliverable

    return is_two_phase_citation_deliverable(deliverable)
