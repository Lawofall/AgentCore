"""Coordination-session terminal event helpers for the drive loop."""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.delegate.terminal_output import (
    ALL_COMPLETED_OUTPUT_LIMIT,
    compose_all_completed_output,
)

logger = get_logger(__name__)


def collect_harvest_user_facts(plan: Any, results: dict[str, Any] | None) -> dict[str, Any]:
    """Structured session facts for the user-facing harvest fallback renderer.

    Sibling of ``format_for_ceo``: same plan/results, different audience. The CEO
    text stays on ``ALL_COMPLETED.output``; this dict is what the user bubble may
    read. Does not invent gaps — only accepted files and uncompensated tool failures.
    """
    from agentcore.runtime.delegate.team_synthesis import worker_output_blurb
    from agentcore.runtime.runs.file_acceptance import accepted_paths
    from agentcore.runtime.tool_failures import facts_from_dicts, outstanding_facts

    nodes: list[dict[str, Any]] = []
    files: list[str] = []
    outstanding: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for node in getattr(plan, "nodes", ()) or ():
        rid = str(getattr(node, "run_id", "") or "")
        role = str(getattr(node, "role", None) or getattr(node, "agent_name", None) or "队员")
        state = (results or {}).get(rid)
        if state is not None:
            phase = getattr(state, "phase", None)
            if phase is not None and hasattr(phase, "value"):
                status = phase.value
            else:
                status = str(phase or "pending")
            summary = worker_output_blurb(state)
            node_files = accepted_paths(getattr(state, "file_acceptance", None))
            for fact in outstanding_facts(facts_from_dicts(getattr(state, "tool_failures", None))):
                outstanding.append({"role": role, "tool_name": fact.tool_name})
        else:
            status = "pending"
            summary = ""
            node_files = []
        for path in node_files:
            if path not in seen_files:
                seen_files.add(path)
                files.append(path)
        nodes.append(
            {
                "role": role,
                "status": status,
                "summary": summary,
                "files": list(node_files),
            }
        )
    return {
        "nodes": nodes,
        "files": files,
        "outstanding_tool_failures": outstanding,
    }


def post_session_all_completed(
    session: Any,
    *,
    output: str,
    completed: int | None = None,
    total: int | None = None,
    output_limit: int = ALL_COMPLETED_OUTPUT_LIMIT,
    criteria_met: bool | None = None,
    failed: int | None = None,
    user_facts: dict[str, Any] | None = None,
    roster_text: str = "",
    roster_facts: dict[str, Any] | None = None,
    closing_text: str = "",
) -> None:
    """Post the coordination terminal event (happy path + criteria-gap / partial-fail).

    ``output`` is the lossy synthesis prose (worker bodies + advisory sections).
    The roster and closing are reserved first; long per-worker bodies shrink
    before any tail of the assembled document is touched. Harvest replays this
    payload, so a roster that lost a budget race would order the CEO to
    reconcile against something that is not there.
    """
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    completed_n = completed if completed is not None else len(session.completed_run_ids)
    total_n = total if total is not None else session.total_workers
    raw_join = "\n".join(
        p for p in (output.strip(), roster_text.strip(), closing_text.strip()) if p
    )
    composed = compose_all_completed_output(
        output,
        roster_text,
        closing_text,
        limit=output_limit,
    )
    payload: dict[str, Any] = {
        "completed": completed_n,
        "total": total_n,
        "output": composed,
    }
    from agentcore.runtime.coordination.cancel_close import classify_cancel_close

    if classify_cancel_close(session) is not None:
        payload["cancelled"] = True
    if criteria_met is False:
        payload["criteria_met"] = False
    if failed is not None:
        payload["failed"] = failed
    facts = dict(user_facts) if user_facts else {}
    if roster_facts:
        facts["roster"] = roster_facts
    if facts:
        payload["user_facts"] = facts
        session.harvest_user_facts = facts
    session.post(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload=payload,
        )
    )
    # Drive 终态 ≡ wait 可唤醒：终态投递必须可观测（与 wait_end / coord_inject 对照）。
    logger.info(
        "coordination.terminal_posted",
        execution_id=getattr(session, "execution_id", "") or "",
        completed=completed_n,
        total=total_n,
        failed=failed,
        criteria_met=criteria_met,
        output_chars=len(composed),
        prose_chars=len(output),
        prose_trimmed=len(raw_join) > len(composed),
        roster_attached=bool(roster_text.strip()),
    )
