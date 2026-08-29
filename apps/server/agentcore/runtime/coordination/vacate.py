"""Vacate a never-started worker seat when no live coordination meeting is open."""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.tools.protocol import ToolContext, ToolResult

logger = get_logger(__name__)


def _vacate_success_message(run_id: str, raw: str, reason: str) -> str:
    msg = f"worker {run_id} 已从队列撤出"
    if run_id != raw:
        msg += f"（由「{raw}」解析）"
    if reason:
        msg += f"（原因：{reason}）"
    msg += "。"
    return msg


def vacate_never_started_seat(
    session: Any,
    context: ToolContext,
    *,
    raw: str,
    reason: str,
) -> ToolResult | None:
    """Vacate a never-started seat when no active coordination meeting is open.

    Empty chairs left after a failed arm (or an already-closed session) must
    still be formally withdrawn — do not answer「当前不在协调模式」for those.
    Running workers still require an active session.
    """
    from agentcore.runtime.coordination.cancel_close import (
        note_cancel_worker_success,
        resolve_unstarted_plan_node,
        worker_was_started,
    )
    from agentcore.runtime.coordination.isomorphic import started_run_ids_from_entries
    from agentcore.runtime.delegate.steer import record_plan_snapshot
    from agentcore.runtime.facts import current_fact_log
    from agentcore.runtime.journal.fold import plan_from_journal

    if session is not None:
        pending = session.resolve_pending_worker(raw)
        if pending.run_id is not None and not worker_was_started(session, pending.run_id):
            pending_id = pending.run_id
            session.vacate_pending_worker(pending_id)
            note_cancel_worker_success(session, pending_id, started=False)
            from agentcore.runtime.coordination.journal import (
                record_coordination_snapshot,
            )

            record_coordination_snapshot(session)
            logger.info(
                "coordination.worker_cancel_pending_withdrawn",
                execution_id=session.execution_id,
                run_id=pending_id,
                raw=raw,
                match=pending.reason,
                reason=reason[:120] if reason else "",
                via="inactive_or_closed",
            )
            return ToolResult(
                tool_call_id="",
                success=True,
                output=_vacate_success_message(pending_id, raw, reason),
            )

    log = current_fact_log.get()
    entries = log.entries() if log is not None else None
    plan = plan_from_journal(entries)
    if plan is None or not plan.nodes:
        return None
    started = started_run_ids_from_entries(entries)
    ended: set[str] = set()
    if session is not None:
        ended = set(session._ended_run_ids())
        started |= {
            rid
            for rid in (getattr(session, "_worker_started_at", None) or {})
        }
    hit, match, _candidates = resolve_unstarted_plan_node(
        plan, raw, started_run_ids=started, ended_run_ids=ended
    )
    if hit is None:
        return None
    plan.nodes[:] = [n for n in plan.nodes if (n.run_id or "") != hit]
    record_plan_snapshot(plan)
    logger.info(
        "coordination.worker_cancel_pending_withdrawn",
        execution_id=getattr(session, "execution_id", None)
        or getattr(context, "execution_id", "")
        or "",
        run_id=hit,
        raw=raw,
        match=match,
        reason=reason[:120] if reason else "",
        via="journal_unstarted",
    )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=_vacate_success_message(hit, raw, reason),
    )
