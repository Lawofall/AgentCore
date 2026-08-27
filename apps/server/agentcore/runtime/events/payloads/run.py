"""Multi-agent run SSE payload wire models (factories: ``runtime/events/run.py``)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agentcore.runtime.events.payloads._base import WirePayload, absent
from agentcore.runtime.events.payloads.chat import _REPLACE_DOC, ResetReason
from agentcore.runtime.events.payloads.shared import CostBreakdown, RunDebrief, UsageBreakdown
from agentcore.runtime.runs.types import RunKind

Stance = Literal["pro", "con"]
EscalationKind = Literal["normal", "scope", "dep"]
PlanRevisionKind = Literal["bind", "steer"]
# 幕类型 = 能力档取用键（首批 multi_agent / debate；single_agent 不进幕序列）。
ActKind = Literal["multi_agent", "debate"]
# 幕授权来源（批 B）：推进卡 / 自动开辩 / 存量 leftover（preview 非新开开工卡）。
ActAuthorizedBy = Literal["stage_card", "auto", "preview"]
# run_failed 可机读原因类（additive）：协作图脸优先按此类贴文案。
# quality=内容契约/硬缺口→「未达标」；
# format=结构/格式闸（code_audit·缺章节·JSON）→「格式未过」；
# model=中断/停滞/降级交接→「模型中断」；call=LLM/超时→「调用失败」；
# 缺省→「失败」/空 error「调用失败」。禁前端扫正文猜脸。
RunFailureKind = Literal["quality", "format", "model", "call"]


class PlanRevision(WirePayload):
    run_id: str
    kind: PlanRevisionKind


class PlanRevisedPayload(WirePayload):
    execution_id: str
    revisions: list[PlanRevision]


class PlanAgentPayload(WirePayload):
    id: str
    role: str
    thinking: bool


class RunPlanRunEntry(WirePayload):
    id: str
    agent_id: str
    task: str
    depends_on: list[str]
    parent_run_id: str | None = absent()
    kind: RunKind | None = absent(ts_type="RunKind")
    stance: Stance | None = absent()
    # 呈现分组权威：计划内节点的 ``group`` 以本字段为准（三端 fold 从 run_plan 物化）；
    # 首跑 ``run_started``（``execute_agent_node``）不重复携带。续写身份见 ``RunStartedPayload``。
    group: str | None = absent()
    round: int | None = absent()
    replaces_run_id: str | None = absent()


class RunPlanAct(WirePayload):
    """幕声明（批 A1）：一张 execution 图由 1..N 幕组成；本批仅首幕 act-1。

    runs 归属 = 该 run_plan 声明的幕（``RunPlanRunEntry`` 不加字段）。
    ``authorized_by``（批 B）：辩论幕的授权来源；调研幕缺省。
    """

    act_id: str
    kind: ActKind
    title: str | None = absent()
    # 本幕从宿主图哪个节点后长出；首幕缺省。
    anchor_run_id: str | None = absent()
    authorized_by: ActAuthorizedBy | None = absent()


class RunPlanPayload(WirePayload):
    execution_id: str
    plan_type: Literal["single_agent", "multi_agent", "debate"]
    task_summary: str
    agents: list[PlanAgentPayload]
    runs: list[RunPlanRunEntry]
    # 已退役写入：旧 journal / 旧 divert 生长帧可能仍带；新路径不写。
    # 缺省 = 本回合图（同回合二次 delegate 靠同 execution_id merge）。
    host_message_id: str | None = absent()
    # 图间链（additive）：本回合新图的上一张协作图；旧客户端忽略即降级为独立图。
    # 同回合合入 / adopt 热图 merge 不写此字段。
    prev_execution_id: str | None = absent()
    # 幕声明（additive）：旧客户端 / 旧 journal 忽略；缺省时前端 fold 合成 act-1。
    act: RunPlanAct | None = absent()
    # 便签墙已升（additive）：与 ``setup_note_wall`` 同谓词（≥2 worker 且 coordination=wall）。
    # 真才上线；假 / 旧 journal 缺省 = 无墙。看面据此画空态，避免把「墙已升还没字」当成没开。
    note_wall: bool | None = absent()


class GraphAppendPayload(WirePayload):
    """已停发：旧跨回合同图追加锚点（兼容旧 journal 回放）。新路径用 prev_execution_id。"""

    execution_id: str
    host_message_id: str
    append_message_id: str
    added_count: int
    roles: list[str] = Field(default_factory=list)
    added_run_ids: list[str] = Field(default_factory=list)
    # 幕归属（additive）：文案区分「开新幕」vs「同幕补派」属后续批次；本批只透传字段。
    act_id: str | None = absent()
    act_kind: ActKind | None = absent()
    # 开幕授权来源（批 B，与 RunPlanAct.authorized_by 同形）。
    authorized_by: ActAuthorizedBy | None = absent()


class RunStartedPayload(WirePayload):
    run_id: str
    agent_id: str
    parent_run_id: str | None
    kind: RunKind
    # 同人续派 / 热修 / 辩论续写：恒指现场根（RunSession 键）；星型，前端铺链。
    continues_run_id: str | None = absent()
    stance: Stance | None = absent()
    # 仅续写路径（``continue_run`` / 证人答问）携带：未入 plan 的续写靠本字段出生身份。
    # 计划内首跑（取证员 / 首轮辩手等经 ``execute_agent_node``）不带——
    # ``group`` 权威 = ``run_plan``。
    group: str | None = absent()
    round: int | None = absent()
    # 辩论续写语义方 key（质询 / 结辩 / 续轮）；缺字段（老 journal）→ 前端按 stance / sides 回退。
    side_key: str | None = absent()
    replaces_run_id: str | None = absent()


ContextChannel = Literal[
    "system",
    "history",
    "request",
    "team_position",
    "dependency",
    "workspace",
    "task",
    "deliverable",
    "team_brief",
    "gate_notes",
    "steer",
    "team_result",
    "round_focus",
    "opponent",
    "challenge",
    "interjection",
    "continuation",
    "cross_exam",
    "witness_exam",
    "closing",
    "attack",
    "defense",
    "rebuttal",
    "thread",
    "crux",
]
ContextFidelity = Literal["", "pointer", "summarize", "pass_through"]


class ContextBlockWire(WirePayload):
    channel: ContextChannel
    heading: str
    body: str
    chars: int
    truncated: bool
    source_role: str
    source_run_id: str
    fidelity: ContextFidelity
    files: list[str]


class RunContextPayload(WirePayload):
    run_id: str
    agent_id: str
    blocks: list[ContextBlockWire]


class RunOutputDeltaPayload(WirePayload):
    run_id: str
    agent_id: str
    delta: str
    replace: bool | None = absent(_REPLACE_DOC)


class RunOutputResetPayload(WirePayload):
    run_id: str
    agent_id: str
    # 与 ContentResetPayload.reason 同一枚举：仅 finish_guard 折出 rework 痕迹（didRework）。
    reason: ResetReason = Field(json_schema_extra={"ts_type": "ResetReason"})


class RunReasoningDeltaPayload(WirePayload):
    run_id: str
    agent_id: str
    delta: str
    replace: bool | None = absent(_REPLACE_DOC)


class RunToolProgressPayload(WirePayload):
    run_id: str
    agent_id: str
    tool_name: str
    chars: int


# Mid-flight activity phase for a running worker (桌面/手机 fold 单一源).
# ``queued`` / ``skipped`` are NOT on this wire — they are ``RunStatus``
# (pending / skipped). Terminal runs clear ``phase`` in the projection.
WorkerRunPhase = Literal["thinking", "tool", "waiting_children", "winding_down"]


class RunPhasePayload(WirePayload):
    """Worker 活动相位（``run_phase``）：等 LLM / 跑工具 / 等子 / 超时·token 收尾。

    EPHEMERAL——传输态；``tool_name`` 仅 ``phase=tool`` 时有意义。``winding_down``
    在投影侧粘性覆盖 thinking/tool，直到 run 终态。
    """

    run_id: str
    agent_id: str
    phase: WorkerRunPhase
    tool_name: str | None = absent()


class RunEscalationPayload(WirePayload):
    """升级实时可见 (非阻塞 raised): a worker flagged a decision/blocker and kept working.

    JOURNALED (DURABLE, 统一时间线二期 D6): ``escalation_id`` keys the raised 轻行's
    timeline marker (幂等去重 on attach replay) and lets the raised row + node ⚠️ badge
    reload — the event base is now level with ``escalation_required``.

    ``source`` distinguishes early-stop / thrashing backstops (``validation_thrash`` /
    ``ceiling_backstop``) from genuine mid-work escalate (omit / absent).
    """

    escalation_id: str
    run_id: str
    agent_id: str
    question: str
    assumption: str
    blocking: bool
    kind: EscalationKind | None = absent()
    source: str | None = absent()


class RunEscalationGatePayload(WirePayload):
    run_id: str
    agent_id: str
    layer: Literal["execution", "scheme"]
    action: Literal["continue", "escalate"]
    signals: list[dict[str, Any]]


class TeamNotePostedPayload(WirePayload):
    execution_id: str
    note_id: str
    run_id: str
    agent_id: str
    role: str
    kind: Literal["decision", "heads_up", "claim"]
    text: str
    ts: float
    supersedes: str | None = absent()
    supersede_mode: Literal["update", "void"] | None = absent()
    source: Literal["ceo", "worker", "inherited"] | None = absent()


class TeamSynthesisWorkerPreview(WirePayload):
    run_id: str
    role: str
    status: Literal["pending", "completed", "failed", "cancelled"]
    summary: str


class TeamSynthesisPreviewPayload(WirePayload):
    execution_id: str
    completed: int
    total: int
    headline: str
    text: str
    workers: list[TeamSynthesisWorkerPreview]
    in_progress: bool


class CoordinationWaitPayload(WirePayload):
    """CEO 协调等待（``coordination_wait``）：captain 空等团队事件时的前端 UX 信号。

    ``waiting=true`` 进入/心跳刷新；``waiting=false`` 清除。``completed``/``total`` 为
    session 计数（已完成 worker / 总 worker）。EPHEMERAL——不落 journal。
    """

    execution_id: str
    waiting: bool
    completed: int
    total: int


class WorkspaceLockWaitPayload(WirePayload):
    """同 folder 写锁短等（``workspace_lock_wait``）：跨会话串行时的前端 UX 信号。

    ``waiting=true`` 即将阻塞在 ``workspace_lock``；``waiting=false`` 已拿到锁。
    与同对话 FIFO ``turn_queued`` 正交。EPHEMERAL——不落 journal。
    """

    conversation_id: str
    waiting: bool


DeliveryState = Literal["delivered", "partial", "blocked", "notes"]


class DeliveryGap(WirePayload):
    """One undelivered piece in the wrap-up reconciliation (交付诚实性): the worker
    ``role`` it belongs to (or a batch-level label like「验收」) + a one-line
    ``description`` of what never landed (contract shortfall / degraded handoff /
    completion criteria unmet / failed worker).

    Optional ``reason`` is a machine-readable cutoff / shortfall code when the gap
    comes from a structured engine signal — known:
    ``token_budget`` / ``worker_timeout`` / ``max_rounds`` / ``degraded_handoff`` /
    ``unverified_note`` (soft 示例/虚构自注；不单独把 state 打成 notes) /
    ``files_not_landed`` (零落盘 soft tip：per-worker「本队员本波未交卷」/
    批次「本批未见落盘」；甲⁺ 起不挡收工) /
    ``verify_failed`` (验证形工具失败：browser navigate / test_run /
    verify 形 code_execute·terminal).
    Absent for ordinary contract / criteria prose gaps that have not been projected.
    Clients may badge known codes and ignore unknown ones (forward-compatible).

    Optional ``severity``: ``warning`` = soft reminder (待核实等，不单独撑起
    partial/blocked); absent or ``blocking`` = real shortfall. Old clients that
    ignore unknown fields still see the row; new clients split summary / card state.

    Optional ``paths``: workspace-relative files implicated by a soft note (for
    「打开相关文件」); absent on ordinary blocking gaps."""

    role: str
    description: str
    reason: str | None = absent()
    severity: Literal["blocking", "warning"] | None = absent()
    paths: list[str] | None = absent()


class DeliveryAction(WirePayload):
    """One user action that would close a delivery gap. ``kind`` is a widened string
    on the wire (like ``ToolPhase``) so the backend can add kinds without a client
    bump — known: ``bind_local_folder`` (wire kind；产品文案按会话分流：
    工程尚在本机 → 云协作「导入到云」优先；远程仓进当前云桌走 git clone /
    Composer「从 Git 克隆」；**已是云端会话但沙箱未装配** →
    禁止再导「导入到云」，改稍后重试 / export_to_local / 本机传统；
    本机传统合法非默认，≠离线)；
    ``export_to_local`` (云端已有 delivered_files → 导出到本机文件夹后即可 npm install / 本地运行；
    与 bind_local_folder 可并存但语义不同);
    ``website_verify`` (legacy tape only — runtime 已停发整页 QA 续派按钮);
    ``continue_skipped_runs`` (turn/nested 额度 SKIPPED 未跑节点 → 下一回合续跑);
    unknown kinds render as a plain hint.
    （成篇未写完改由对话框接着说——已撤 ``continue_writing`` 一键按钮。）

    Optional ``prompt`` is the exact user-turn text a client should send for
    kinds that open a new message (e.g. ``continue_skipped_runs``; old
    ``website_verify`` tapes still carry one). Absent for
    non-message actions like ``bind_local_folder`` / ``export_to_local``."""

    kind: str
    description: str
    prompt: str | None = absent()


class DeliveryArtifact(WirePayload):
    """One path-level acceptance row on ``delivery_status`` (主清单数据源).

    ``status=accepted`` → counts toward ``delivered_files`` / CEO「已交付」;
    ``rejected`` carries ``reason`` (e.g. ``citations_unverified`` / ``run_failed``)
    and optional ``detail`` for the file checklist. Undeclared extras are omitted
    (not rejected). Draft is out of scope for block 1.
    ``workspace_id``: landing desk when the plan node set ``target_folder_id``
    (``folder:{id}``); omit → client falls back to the session birth desk.

    ``kind`` / ``derived_from`` are the producing tool's OWN self-report
    (``tools/file_products.py``), carried verbatim from the run-level ledger:
    ``kind`` is the normalized product type (``md`` / ``docx`` / ``pdf`` / ``code``
    / ``image`` / ``file`` …); ``derived_from`` names the source this product was
    EXPORTED from (``md_to_docx``: docx ← 源 md), so a client can demote that source
    to 中间稿 the same way ``fold_exported_sources`` does server-side. Absent when the
    producer did not self-report — clients must NOT guess lineage from extensions or
    tool names, and must keep every accepted path reachable (fold ≠ drop).
    """

    path: str
    status: Literal["accepted", "rejected"]
    reason: str | None = absent()
    detail: str | None = absent()
    workspace_id: str | None = absent()
    kind: str | None = absent()
    derived_from: str | None = absent()


class DeliveryPromotion(WirePayload):
    """Historical ``delivery_status.promoted`` row（AI 工作间 → 用户工作区）.

    ``promote_product`` 已撤销；本结构只兼容旧事件。``from`` 是当时的旧路径，``to``
    是搬走后的位置。空数组是合法状态（字段缺省即空）。
    """

    from_path: str = Field(alias="from", description="AI 工作间旧路径（已不存在）")
    to: str = Field(description="用户工作区新路径（现在的位置）")


class DeliveryStatusPayload(WirePayload):
    """交付状态（能力闸门与交付诚实性）: the structured delivery reconciliation a
    delegate batch emits at wrap-up — 已交付文件 / 缺口 / 待用户操作 — so the client
    renders an honest delivery card instead of mining the CEO's prose.
    Folds keep the LATEST per ``execution_id`` (the event already unions
    declared-and-landed paths across hops of that execution).
    ``state``: delivered = 无 blocking 缺口且有落盘产物; partial = 有产物也有
    blocking 缺口; blocked = 有 blocking 缺口且无落盘产物;
    notes = 仍有 soft 提醒且非「仅 unverified_note」（轻提醒，非「部分未满足」）；
    声明路径未落盘为 path_mismatch blocking gap，不得 delivered；未声明落盘不进
    ``artifacts``。
    ``artifacts``: path-level acceptance (accepted+rejected) for declared landings;
    ``delivered_files`` remains accepted-only for older clients.
    ``promoted``: 历史 ``{from, to}`` 归位行（``promote_product`` 已撤销；新回合不再写入）。
    旧卡 journal 重放仍带此字段；无归位时缺省（= 空数组）。"""

    execution_id: str
    state: DeliveryState
    summary: str
    delivered_files: list[str]
    gaps: list[DeliveryGap]
    actions: list[DeliveryAction]
    artifacts: list[DeliveryArtifact] = []
    promoted: list[DeliveryPromotion] = []


class UserInterjectionAttachment(WirePayload):
    """Attachment metadata on a mid-flight interjection (no inline text body)."""

    name: str
    workspace_path: str | None = absent()
    binary: bool = False


class UserInterjectionAgentMention(WirePayload):
    """Soft @Agent chip on a mid-flight interjection (prompt hint, not a hard route)."""

    agent_id: str
    role: str


class UserInterjectionPayload(WirePayload):
    """Mid-flight user interjection into a live turn (经典 steer + 协调插话共用).

    Lifecycle:
    - 协调: ``received`` → ``injected`` → ``addressed`` / ``queued`` / ``failed``
    - 经典: ``received`` → ``injected`` (终态) / ``queued`` / ``failed``（无 ``addressed``）
    ``injected`` = 内容真正写入模型上下文的那一刻。
    """

    interjection_id: str
    execution_id: str
    content: str
    status: Literal["received", "injected", "addressed", "queued", "failed"]
    note: str | None = absent()
    attachments: list[UserInterjectionAttachment] | None = absent()
    agent_mentions: list[UserInterjectionAgentMention] | None = absent()


class TurnQueuedPayload(WirePayload):
    """FIFO queue ack on the send SSE while another turn is in-flight (D9 · 发送即有流).

    Replaces the retired HTTP 202 ``SendMessageQueuedResponse`` JSON. Same visibility
    fields; the waiting connection later continues with the drained turn on this stream.
    ``degraded_from=steer`` when the client asked for steer but soft-insert was
    unavailable (无 live accepting 窗口 / 回合已收口；协调插话路径不会带此字段).
    """

    queue_id: str
    position: int
    queue_depth: int
    conversation_id: str
    degraded_from: Literal["steer"] | None = absent()


class MessageAttachment(WirePayload):
    """Same fields as REST ``QueuedTurnItem`` / send-turn ``MessageAttachment``.

    Nested on ``turn_queue_started`` so the timeline entrance frame is self-describing
    (not the thinner ``UserInterjectionAttachment``).
    """

    name: str
    path: str
    text: str = ""
    truncated: bool = False
    kind: Literal["file", "dir", "conversation"] = "file"
    conversation_id: str | None = None
    binary: bool = False
    workspace_path: str | None = None


class AgentMention(WirePayload):
    """Same fields as REST ``QueuedTurnItem.agent_mentions`` / ``AgentMention``."""

    agent_id: str
    role: str


class TurnQueueStartedPayload(WirePayload):
    """FIFO dequeue → timeline user-bubble entrance (D9 · 发送即有流).

    Emitted as the **first frame** of the drained turn's EventSink (after ``pop_next``,
    before ``stream_chat`` / ``message_start``). ``content`` is the queued user text
    (on the frame — not persist-first). Empty ``attachments`` / ``agent_mentions`` are
    absent. ``remaining_depth`` is the queue length after this item left the FIFO.
    EPHEMERAL — reload 靠 REST; 不落 journal.
    """

    queue_id: str
    conversation_id: str
    remaining_depth: int
    content: str
    attachments: list[MessageAttachment] | None = absent()
    agent_mentions: list[AgentMention] | None = absent()


class TurnQueueCancelledPayload(WirePayload):
    """Per-item queue cancel ack (同对话再发 · drain 前取消). EPHEMERAL — multi-client UI clear."""

    queue_id: str
    conversation_id: str


class ResumeDeferredPayload(WirePayload):
    """Cold resume deferred while a live turn holds the slot. EPHEMERAL — same-connection wait.

    Settlement is already prewritten; claim + continuation start after the slot frees.
    ``busy_reason=wrap_up`` when the live sink is still this ``message_id`` (host winding
    down); ``live_turn`` when another turn occupies the conversation slot.
    """

    message_id: str
    conversation_id: str
    busy_reason: Literal["wrap_up", "live_turn"]


class ResumeSettledPayload(WirePayload):
    """Cold resume that found its frame already consumed. EPHEMERAL — idempotent success.

    The conclusion is durable in ``paused_turn_outcomes``, stamped by whoever won the
    atomic claim on the paused frame; this frame relays THAT decision — which card
    (``kind`` + ``checkpoint_id``), what was decided (``decision``) and when
    (``decided_at``) — never the decision the caller itself just submitted.

    ``turn_status`` answers a separate question: where the TURN stands (the assistant
    row's ``usage.status``), so a client knows whether to close out its streaming
    bubble. ``running`` means the continuation is still going and the bubble stays
    open; this connection then carries its stream when the run is on this server.
    """

    message_id: str
    conversation_id: str
    kind: Literal["ask_user", "plan_review"]
    checkpoint_id: str
    decision: str
    decided_at: str
    turn_status: Literal["running", "complete", "incomplete", "failed", "unknown"]


class ExecutionDetachedPayload(WirePayload):
    """执行转后台（``execution_detached``）：附着回合已收口，团队继续跑。"""

    execution_id: str
    conversation_id: str
    completed: int
    total: int
    reason: str | None = absent()
    host_turn_id: str | None = absent()


class ExecutionCompletedPayload(WirePayload):
    """后台执行终态（``execution_completed``）：drive 到齐、收割者可发起收口。"""

    execution_id: str
    conversation_id: str
    completed: int
    total: int
    # harvest 收口分类映射：success→completed / failure→failed / cancelled→cancelled。
    # 缺省 completed 兼容旧 fixture；新工厂始终写入。
    status: Literal["completed", "failed", "cancelled"] = "completed"
    host_turn_id: str | None = absent()
    error: str | None = absent()


class RunCompletedPayload(WirePayload):
    run_id: str
    agent_id: str
    output_summary: str
    duration_ms: int
    role: str
    model: str
    usage: UsageBreakdown
    cost: CostBreakdown
    debrief: RunDebrief | None = absent()
    output_files: list[str] | None = absent()
    # Soft-accept / cutoff gaps on a COMPLETED node (additive). Same shape as
    # ``DeliveryGap`` minus the batch-level ``role`` (node role is on the payload).
    gaps: list[DeliveryGap] | None = absent()
    # Host-journal routing after turn teardown (pillar A); absent on old fixtures.
    execution_id: str | None = absent()


class RunFailedPayload(WirePayload):
    run_id: str
    agent_id: str
    error: str
    # Additive：旧客户端 / 旧 journal 忽略；缺省时脸回退「失败」/空 error「调用失败」。
    failure_kind: RunFailureKind | None = absent()
    debrief: RunDebrief | None = absent()
    execution_id: str | None = absent()
    # Additive：失败前已有产物落盘（文件写成功后上游再挂）→ 脸「产出已落盘」。
    product_landed: bool | None = absent()
    # Additive：与 ``AgentCoreError.code`` / ``retryable`` / ``retry_after`` 同语义，
    # 让客户端区分瞬时限流与真终态。缺省 = 旧 journal / 契约硬失败未分类。
    error_code: str | None = absent()
    retryable: bool | None = absent()
    retry_after: float | None = absent()


class RunCancelledPayload(WirePayload):
    run_id: str
    agent_id: str
    reason: Literal["redirect", "stop", "user_stop", "worker_timeout"]
    execution_id: str | None = absent()


class RunSkippedPayload(WirePayload):
    run_id: str
    agent_id: str
    reason: Literal["cascade", "abort"]


class RunProgressPayload(WirePayload):
    completed: int
    total: int


class NodeTimingPayload(WirePayload):
    run_id: str
    start_ms: int
    end_ms: int
    outcome: str


class BatchMetricsPayload(WirePayload):
    execution_id: str
    nodes: int
    width: int
    peak_running: int
    wall_ms: int
    busy_ms: int
    slot_starved: int
    completed: int
    failed: int
    skipped: int
    cancelled: int
    bind_boundaries: int
    scope_boundaries: int
    checkpoint_boundaries: int
    escalations: int
    scope_escalations: int
    timeline: list[NodeTimingPayload]


# Registry alias (events.ts names this inline run-plan row type).
RunPlanNode = RunPlanRunEntry

# Re-export shared leaf types referenced by ``payloads/__init__.py`` TS_EXPORTS.
