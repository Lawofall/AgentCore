"""ProcessStep — one step in a turn's 思考·正文·工具·协作 inline timeline (统一团队时间线).

Not an event payload itself: the shared wire-shaped leaf carried verbatim inside
`ProjectedTurn.messages[*].process` and the REST `RunsPayload.process`. reasoning /
content + tool are the CEO bubble's own narrative; the remaining kinds are POSITIONAL MARKERS —
zero-width anchors fixing WHERE a non-text turn element renders (payload looked up from
the turn's side channels by id). Emitted to TS as one inline discriminated union.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentcore.runtime.events.payloads._base import WirePayload, absent


class ProcessReasoningStep(WirePayload):
    kind: Literal["reasoning"]
    text: str


class ProcessContentStep(WirePayload):
    kind: Literal["content"]
    text: str


class ProcessToolStep(WirePayload):
    kind: Literal["tool"]
    id: str
    tool_name: str
    arguments: dict[str, Any]
    result: str | None
    status: Literal["running", "success", "error", "redirect"]
    display: dict[str, Any] | None = Field(
        default=None, json_schema_extra={"ts_type": "ToolDisplay"}
    )
    failure: dict[str, Any] | None = absent(
        "User-facing copy from tool_use_end (status=error or redirect). "
        "Shape mirrors ToolFailure: {message, code}.",
        ts_type="ToolFailure",
    )
    phase: str | None = absent(
        "工具执行阶段进度: the running tool's latest coarse phase from `tool_use_progress`. "
        "LIVE-ONLY ephemeral — never journaled, never in the conformance ProjectedTurn; "
        "meaningful only while status === 'running'.",
        ts_type="ToolPhase",
    )


class ProcessTeamStep(WirePayload):
    """The multi-agent collaboration graph slot (emitted at the turn's first `run_plan`)."""

    kind: Literal["team"]
    execution_id: str


class ProcessGraphAppendStep(WirePayload):
    """跨回合同图追加锚点：新回合过程时间线上的「已往上方协作图追加 N 名成员」。"""

    kind: Literal["graph_append"]
    execution_id: str
    host_message_id: str
    added_count: int


class ProcessCheckpointStep(WirePayload):
    kind: Literal["checkpoint"]
    checkpoint_id: str


class ProcessPlanReviewStep(WirePayload):
    kind: Literal["plan_review"]
    checkpoint_id: str


class ProcessEscalationStep(WirePayload):
    """升级卡时间线落点 (统一时间线二期 D1/D2): one escalation's own slot in the CEO
    timeline — required 系三态落于 ``escalation_required``，非阻塞 raised 落于
    ``run_escalation``（同一 ``escalation_id`` 键，二者互斥）。"""

    kind: Literal["escalation"]
    escalation_id: str


class ProcessApprovalStep(WirePayload):
    """热审批痕迹落点 (统一时间线二期 D3): resolved 后在其 required 时刻显轻状态行；
    pending 期间标记在、行不显（操作面恒在决策区）。"""

    kind: Literal["approval"]
    approval_id: str


class ProcessStageCardStep(WirePayload):
    """阶段推进卡时间线落点：required 时刻落锚；行渲染由 resolved/orphaned 门控
   （pending 操作面在 Dock；历史回看显「已开辩 / 已失效」轻痕迹）。"""

    kind: Literal["stage_card"]
    stage_card_id: str


class ProcessUserInterjectionStep(WirePayload):
    """用户运行中插话的时间线落点：插话真实发生在回合中途，marker 钉住它的发生位置，
    避免气泡统一堆到回合末尾造成因果倒置。同 `interjection_id` 只落一次（首次 received），
    正文与五态由旁路 `userInterjections` 按 id 查。"""

    kind: Literal["user_interjection"]
    interjection_id: str


PROCESS_STEP_MEMBERS: tuple[type[WirePayload], ...] = (
    ProcessReasoningStep,
    ProcessContentStep,
    ProcessToolStep,
    ProcessTeamStep,
    ProcessGraphAppendStep,
    ProcessCheckpointStep,
    ProcessPlanReviewStep,
    ProcessEscalationStep,
    ProcessApprovalStep,
    ProcessStageCardStep,
    ProcessUserInterjectionStep,
)

# Retired process-step discriminants that may still sit on historical process[] rows.
RETIRED_PROCESS_STEP_KINDS: frozenset[str] = frozenset({"ask", "team_preview"})
