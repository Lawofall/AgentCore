"""Shared wiring for one turn's agent-node executor (no nested closures)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.llm.profiles import TurnProfiles as ProfileSet
from agentcore.llm.provider.protocol import LLMProvider
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import EventSink
from agentcore.runtime.ports import ClientRequestBridge
from agentcore.runtime.runs.executor.identities import DelegateFactory
from agentcore.runtime.runs.plan import RunPlan
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.write_claims import WriteCoordinator

if TYPE_CHECKING:
    from agentcore.runtime.debate.evidence_ledger import EvidenceLedger
    from agentcore.runtime.evidence_ledger import EvidenceLedgerCore


@dataclass(slots=True)
class AgentExecutorEnv:
    """Closed-over turn wiring, lifted out of ``build_agent_executor`` nested defs."""

    plan: RunPlan
    llm: LLMProvider
    tools: ToolRegistry
    sink: EventSink
    base_tool_context: ToolContext
    profiles: ProfileSet
    system_prompt: str
    user_message: str
    execution_id: str
    approval_gate: ApprovalGate | None
    delegate_factory: DelegateFactory | None
    interaction_bridge: ClientRequestBridge | None
    escalation_timeout: float | None
    escalation_armed: bool
    team_brief: str | None
    write_coordinator: WriteCoordinator
    ancestors_by_id: Mapping[str, frozenset[str]]
    conversation_id: str
    # 辩论场级证据台账（可选）；opening 辩手经此登记检索来源并过 id 闸。
    evidence_ledger: EvidenceLedger | Any | None = None
    # 回合共享调研台账（``#r``）；与辩论 ``evidence_ledger``（``#e``）分前缀、分路径。
    turn_evidence_ledger: EvidenceLedgerCore | Any | None = None
    # Structural cost role for log_context + model fallback (member=组队; arena=辩论).
    cost_role: str = "member"
    # 跨文件夹指挥 · 会话出生 folder（不中途改挂）；与 RunSpec.target_folder_id 对照换桌。
    session_folder_id: str | None = None
    # 同回合多 local 根认领簿（C0 登记、不拒）；None = 不登记（测试 / 辩论）。
    local_root_claims: Any | None = None
    # Optional axes object for rebuild_worker_prompt_for_target (PermissionAxes | None).
    permission_axes_obj: Any | None = None
