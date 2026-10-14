"""Drive-loop setup: executor, boundary hook, delegation grant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.graph.drive.boundary import boundary_hook, checkpoint_active
from agentcore.runtime.runs.types import RunSpec, RunState

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

from agentcore.runtime.graph.host import GraphHost

logger = get_logger(__name__)


def resolve_worker_gate(tool: GraphHost) -> Any:
    """Hand workers whatever gate this turn has — never predict away the card.

    Upstream used to guess「这批 worker 用不上逐次卡」from ``backend.location`` (plus a
    patched-in roster scan for desktop-touch / 恒确认 tools) and pass ``None``. That
    guess has to duplicate the sandbox→approval table, and every miss silently
    ungates: 恒确认 died here once, and ``file_write=ask`` on cloud never reached its
    implementation because a worker holding only file tools got ``None`` right here.

    ``None`` now means one thing only — this turn has nobody to ask. Whether a given
    call needs a card is decided at the single chokepoint (``tool_exec_gates``),
    which reads the same ``sandbox_approval`` table with the actual tool name, its
    arguments and the session axes in hand.
    """
    return tool._approval_gate


def build_drive_executor(
    tool: GraphHost,
    plan: RunPlan,
    *,
    execution_id: str,
    worker_gate: Any,
    session: Any,
) -> Callable[[RunSpec, dict], Awaitable[RunState]]:
    """Cold agent executor wrapped with optional coordination timeouts."""
    from agentcore.runtime.runs import build_agent_executor
    from agentcore.runtime.suspension import turn_evidence_ledger as _turn_ledger_var

    cold_executor = build_agent_executor(
        plan=plan,
        llm=tool._llm,
        tools=tool._tools,
        sink=tool._sink,
        base_tool_context=tool._base_tool_context,
        profile_set=tool._profile_set,
        system_prompt=tool._system_prompt,
        user_message=tool._user_message,
        execution_id=execution_id,
        approval_gate=worker_gate,
        delegate_factory=lambda captain_run_id, captain_depth: tool.spawn_lead_subteam(
            captain_run_id, captain_depth
        ),
        interaction_bridge=tool._registry,
        escalation_timeout=tool._checkpoint_timeout_seconds,
        escalation_armed=checkpoint_active(tool),
        team_brief=tool._team_brief,
        # 回合入口绑定的共享台账（与 CEO 同一对象）；辩论 executor 不经此路径。
        turn_evidence_ledger=_turn_ledger_var.get(),
        session_folder_id=getattr(tool, "_folder_id", None),
        local_root_claims=getattr(tool, "_local_root_claims", None),
        permission_axes_obj=getattr(tool, "_permission_axes", None),
        worker_envelope=getattr(tool, "_worker_envelope", "") or "",
    )

    executor: Callable[[RunSpec, dict], Awaitable[RunState]] = cold_executor
    # Hard-timeout chain (warn → TIMEOUT → grace → force cancel). Coordination
    # sessions post CEO TIMEOUT + cancel_ids; nested depth>0 blocking drives use
    # the same registry without a session (timeout_s is no longer a dead field).
    from agentcore.runtime.graph.session.bridge import wrap_executor_with_timeouts

    executor = wrap_executor_with_timeouts(executor, session)
    return executor


def resolve_on_boundary(
    tool: GraphHost,
    plan: RunPlan,
    *,
    complexity_hint: str,
    session: Any,
) -> Any:
    """Wave boundary hook (checkpoint / coordination SCOPE)."""
    # light 与 depends_on 并存时忽略 light：
    # 不得据 light 关掉波边界（否则 SCOPE 无法让出）。
    has_dag_boundary = any(n.depends_on for n in plan.nodes)
    if complexity_hint == "light" and not has_dag_boundary:
        on_boundary = None
    else:
        on_boundary = (
            boundary_hook(tool, plan)
            if (
                checkpoint_active(tool)
                or any(n.depends_on for n in plan.nodes)
            )
            else None
        )
    # Phase 3: under coordination, SCOPE/dep escalations → CEO event queue (PROCEED),
    # not wave-boundary YIELD.
    if session is not None:
        from agentcore.runtime.graph.session.bridge import coordination_boundary_hook

        # Always wire a hook so SCOPE can fire even when the plan has no depends_on /
        # checkpoint markers (parallel fan-out with escalate kind=scope).
        on_boundary = coordination_boundary_hook(session, on_boundary)
    return on_boundary


def apply_delegation_grant(
    tool: GraphHost,
    *,
    execution_id: str,
    worker_gate: Any,
    seed_completed: dict[str, RunState] | None,
) -> bool:
    """Delegation grant from resume / ``command=auto``. Returns whether grant was started this call.

    ``True`` means this drive segment owns revoke-on-exit (unless a live coordination
    session keeps the grant for merge-rearm — see ``drive`` finally).
    """
    # Delegation grant: issued by resume (continue/adjust) or command=auto.
    # Capability auth is silent under command=auto; leftover team_preview continue
    # already granted and is not recovered as a new card.
    if worker_gate is None:
        return False
    # Mid-plan resume already granted; do not treat as a fresh segment owner
    # (avoids double-revoke bookkeeping). Still a no-op apply.
    if seed_completed is not None:
        return False
    from agentcore.core.types import DEFAULT_PERMISSION_AXES

    auto = bool(getattr(tool, "_auto_grant_pending", False))
    already = worker_gate.has_delegation_grant(execution_id)
    axes = getattr(tool, "_permission_axes", None) or DEFAULT_PERMISSION_AXES
    if auto or already or axes.allows_execution:
        if not already:
            worker_gate.grant_delegation(execution_id)
        tool._auto_grant_pending = False  # type: ignore[attr-defined]
        # already=True (e.g. merge-rearm after prior drive kept the grant): not a new
        # segment owner — caller must not revoke on exit.
        return not already
    return False
