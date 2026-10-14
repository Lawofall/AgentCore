"""Worker usage / ledger / citations / session roster accumulation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

from agentcore.runtime.graph.host import GraphHost


def accumulate_usage(tool: GraphHost, results: dict) -> dict[str, int]:
    """Sum this call's worker token usage and fold it into the turn total."""
    call = {"input": 0, "output": 0, "reasoning": 0, "cache_hit": 0, "cache_miss": 0}
    for state in results.values():
        for key in call:
            call[key] += state.usage.get(key, 0)
    tool._acc.add_usage(call)
    return call


def collect_ledger(tool: GraphHost, plan: RunPlan, results: dict) -> None:
    """Capture each worker run that metered LLM usage as a per-run cost row."""
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state:
            tool._acc.add_run_cost(node, state, parent_run_id=tool._captain_run_id)


def collect_citations(tool: GraphHost, results: dict) -> None:
    """Fold COMPLETED workers' web sources into this turn's source list."""
    for state in results.values():
        tool._acc.add_citations(state)


def register_sessions(tool: GraphHost, plan: RunPlan, results: dict) -> list:
    """Keep each COMPLETED worker alive as a recoverable RunSession (留人).

    Idempotent with mid-wave :func:`register_completed_session`: if a session was
    already extended by 续派 / redirect, keep its transcript / recall_count.
    """
    if tool._session_store is None:
        return []
    from agentcore.runtime.runs import RunPhase, RunSession

    registered = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state and state.phase is RunPhase.COMPLETED and state.transcript:
            existing = tool._session_store.get(node.run_id)
            if existing is not None and (
                existing.recall_count > 0 or len(existing.transcript) > len(state.transcript)
            ):
                # Already extended by continuation — don't clobber.
                registered.append(existing)
                continue
            session = RunSession(
                run_id=node.run_id,
                spec=node,
                transcript=state.transcript,
                content=state.content,
                recall_count=existing.recall_count if existing is not None else 0,
            )
            tool._session_store.put(session)
            registered.append(session)
    return registered
