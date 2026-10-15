"""CEO coordination helpers.

``replan.tell`` answers a segment parked on ``escalate(wait)`` via
:func:`settle_waiting_worker`. A running segment is not a tell target, and the
model has no tool that cancels one segment.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.tools.protocol import ToolResult

logger = get_logger(__name__)


def settle_waiting_worker(
    session: Any,
    *,
    run_id: str,
    answer: str,
    conversation_id: str = "",
) -> ToolResult:
    """Hand ``answer`` to a worker parked on escalate(wait) and let that run continue.

    ``via_user`` is taken from the session: this stretch already received an
    ``ask_user`` answer. The caller clears that flag after the whole tell batch.
    """
    if session is None or not getattr(session, "active", False):
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error="当前没有进行中的团队，没法把话送给停着的人。",
        )
    via_user = bool(getattr(session, "user_consulted", False))
    pending = session.get_arbitration(run_id)
    if pending is None:
        session.stash_resolution(run_id, answer=answer, via_user=via_user)
        from agentcore.runtime.graph.session.journal import record_coordination_snapshot

        record_coordination_snapshot(session)
        logger.info(
            "coordination.escalation_stashed",
            execution_id=session.execution_id,
            run_id=run_id,
            via_user=via_user,
        )
        who = "（经用户）" if via_user else ""
        return ToolResult(
            tool_call_id="",
            success=True,
            output=f"已记下对 {run_id} 的话{who}；他恢复后会按这句继续。",
        )
    escalation_id = str(pending.get("escalation_id") or "")
    conv = str(pending.get("conversation_id") or conversation_id or "")
    registry = default_interaction_registry()
    settled = registry.resolve(
        escalation_id,
        {"answer": answer, "via_user": via_user},
        conversation_id=conv,
    )
    if not settled:
        session.stash_resolution(run_id, answer=answer, via_user=via_user)
        stashed = session.resolved_arbitrations.get(run_id)
        if stashed is not None and escalation_id:
            stashed["escalation_id"] = escalation_id
        from agentcore.runtime.graph.session.journal import record_coordination_snapshot

        record_coordination_snapshot(session)
        logger.info(
            "coordination.escalation_stashed_after_miss",
            execution_id=session.execution_id,
            run_id=run_id,
            via_user=via_user,
        )
        who = "（经用户）" if via_user else ""
        return ToolResult(
            tool_call_id="",
            success=True,
            output=f"已记下对 {run_id} 的话{who}；他恢复后会按这句继续。",
        )
    session.clear_arbitration(run_id)
    from agentcore.runtime.graph.session.journal import record_coordination_snapshot

    record_coordination_snapshot(session)
    logger.info(
        "coordination.escalation_resolved",
        execution_id=session.execution_id,
        run_id=run_id,
        via_user=via_user,
    )
    who = "（经用户）" if via_user else ""
    return ToolResult(
        tool_call_id="",
        success=True,
        output=f"已把话传给 {run_id}{who}，他会按这句继续。",
    )
