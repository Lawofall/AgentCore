"""AGENT-node entry: ``build_agent_executor`` wires turn bindings.

Node execution and escalate channel live in ``.node`` / ``.escalation``.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentcore.core.log_context import log_context
from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.profiles import default_turn_profiles as default_profile_set
from agentcore.llm.provider.protocol import LLMProvider
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import record_turn_fact
from agentcore.runtime.ports import ClientRequestBridge
from agentcore.runtime.runs.executor.context import _ancestors_by_id
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.identities import DelegateFactory
from agentcore.runtime.runs.executor.node import execute_agent_node
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.scheduler import RunExecutor
from agentcore.runtime.runs.serialize import run_final_fact
from agentcore.runtime.runs.types import RunSpec, RunState
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry


def build_agent_executor(
    *,
    plan: RunPlan,
    llm: LLMProvider,
    tools: ToolRegistry,
    sink: EventSink,
    base_tool_context: ToolContext,
    profile_set: ProfileSet | None = None,
    system_prompt: str,
    user_message: str,
    execution_id: str,
    approval_gate: ApprovalGate | None,
    delegate_factory: DelegateFactory | None = None,
    interaction_bridge: ClientRequestBridge | None = None,
    escalation_timeout: float | None = None,
    escalation_armed: bool = False,
    team_brief: str | None = None,
    evidence_ledger: object | None = None,
    turn_evidence_ledger: object | None = None,
    cost_role: str = "member",
    session_folder_id: str | None = None,
    local_root_claims: object | None = None,
    permission_axes_obj: object | None = None,
) -> RunExecutor:
    """Build a :class:`RunExecutor` bound to one turn's wiring.

    Closes over ``plan`` so a node can resolve a dependency's display role when
    labelling injected upstream context; the scheduler passes only the terminal
    ``completed`` states per call.

    See module history / design docs on ``profile_set``, ``approval_gate``,
    ``delegate_factory``, and escalation wiring.
    """
    profiles = profile_set or default_profile_set()
    # C3: prefer coordination-session ledger (shared with nested via current_execution_id);
    # non-coordination batches keep a fresh batch-local book.
    from agentcore.workspace.write_claims import resolve_write_coordinator

    write_coordinator = resolve_write_coordinator(execution_id=execution_id)
    ancestors_by_id = _ancestors_by_id(plan)

    env = AgentExecutorEnv(
        plan=plan,
        llm=llm,
        tools=tools,
        sink=sink,
        base_tool_context=base_tool_context,
        profiles=profiles,
        system_prompt=system_prompt,
        user_message=user_message,
        execution_id=execution_id,
        approval_gate=approval_gate,
        delegate_factory=delegate_factory,
        interaction_bridge=interaction_bridge,
        escalation_timeout=escalation_timeout,
        escalation_armed=escalation_armed,
        team_brief=team_brief,
        write_coordinator=write_coordinator,
        ancestors_by_id=ancestors_by_id,
        conversation_id=base_tool_context.conversation_id,
        evidence_ledger=evidence_ledger,
        turn_evidence_ledger=turn_evidence_ledger,
        cost_role=cost_role,
        session_folder_id=session_folder_id,
        local_root_claims=local_root_claims,
        permission_axes_obj=permission_axes_obj,
    )

    async def execute(spec: RunSpec, completed: Mapping[str, RunState]) -> RunState:
        agent_id = spec.agent_id or spec.run_id
        with log_context(
            run_id=spec.run_id,
            agent_id=agent_id,
            depth=spec.depth,
            cost_role=cost_role,
            persona=(spec.role or "").strip() or None,
            parent_run_id=spec.parent_run_id or None,
        ):
            state = await execute_agent_node(env, spec, completed, agent_id)
            # Nested terminals may never enter parent completed_run_ids — mark ended
            # on the shared write ledger so declare/claim can hand off (ghost-lock fix).
            try:
                from agentcore.runtime.runs.types import RunPhase

                if state.phase in (
                    RunPhase.COMPLETED,
                    RunPhase.FAILED,
                    RunPhase.CANCELLED,
                    RunPhase.SKIPPED,
                ):
                    env.write_coordinator.mark_ended(spec.run_id)
            except Exception:  # noqa: BLE001 — never break run finalization
                pass
            record_turn_fact(run_final_fact(spec.run_id, state))
            return state

    return execute
