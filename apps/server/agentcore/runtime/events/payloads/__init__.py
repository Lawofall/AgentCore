"""SSE payload contract registry — the backend single source for cross-end TS types.

``EVENT_PAYLOAD_MODELS`` maps every :class:`EventType` to the pydantic wire model that
describes its payload; ``TS_EXPORTS`` is the ordered emission plan for
``packages/contract-types/src/events.generated.ts``.

- Regenerate TS: ``pnpm gen:types`` (runs ``apps/server/scripts/dump_sse_payload_types.py``).
- Honesty gates: ``tests/test_sse_payload_models.py`` (conformance vectors validate against
  these models) + ``scripts/validate_sse_contract.py`` (committed artifact vs EventType).

NOT imported by ``runtime.events.__init__`` (keeps the sidecar import closure and the
emit hot path free of this package); factories in ``runtime/events/*.py`` remain the only
payload construction entry.
"""

from __future__ import annotations

from pydantic import BaseModel

from agentcore.runtime.events.payloads import (
    browser,
    chat,
    debate,
    interaction,
    process,
    run,
    shared,
    show,
    sim,
    workspace,
)
from agentcore.runtime.events.payloads._base import (
    TsAlias,
    TsExport,
    TsInlineUnion,
    TsInterface,
    TsRaw,
)
from agentcore.runtime.events.types import EventType

# ── Ordered TS emission plan (mirrors the hand-written events.ts layout) ──────────────

TS_EXPORTS: tuple[TsExport, ...] = (
    TsInterface(chat.MessageStartPayload),
    TsInterface(chat.ContentDeltaPayload),
    TsAlias(
        "ResetReason",
        chat.ResetReason,
        doc=(
            "Why a `content_reset` / `run_output_reset` fired. Folds render the\n"
            "「已按交付规范重写」rework chip ONLY for `finish_guard`（交付前核验回炉）；\n"
            "every other reason still clears the streamed draft but leaves no trace:\n"
            "`retry`（LLM 流式透明重试）· `soft_gate`（captain 软门控打回）·\n"
            "`narration`（worker 旁白回滚）· `ask_user`（blocking ask_user 吸收）."
        ),
    ),
    TsInterface(chat.ContentResetPayload),
    TsInterface(chat.ReasoningDeltaPayload),
    TsInterface(chat.ToolProgressPayload),
    TsAlias(
        "ToolPhase",
        chat.ToolPhase,
        doc=(
            "A running tool's coarse EXECUTION phase (工具执行阶段进度). Known values:\n"
            "web_search → queued / querying / fallback; read_url → fetching / reading /\n"
            "blocked; code_execute / test_run → executing; git → git_queued (waiting behind\n"
            "another write on the same repo) / git_credentials (PAT / gh token lookup) /\n"
            "git_remote (push·pull·fetch network leg, create_pr's GitHub REST) / executing\n"
            "(local git command). Kept as a widened `string` on the wire so the backend can\n"
            "add phases without a client bump — an unknown value maps to a generic「处理中」."
        ),
    ),
    TsInterface(chat.ToolUseProgressPayload),
    TsInterface(chat.ToolUseStartPayload),
    TsRaw(
        "ToolDisplay",
        "Record<string, unknown>",
        doc=(
            "A tool's OPTIONAL render-oriented payload (工具结果富渲染), distinct from the\n"
            "model-facing `result` text. Opaque on the wire (snake_case)."
        ),
    ),
    TsInterface(
        chat.ToolFailure,
        doc=(
            "User-facing tool failure face on `tool_use_end` when status=error.\n"
            "`message` = Chinese product copy; `code` = stable error code.\n"
            "Model-facing technical detail stays in `result`."
        ),
    ),
    TsInterface(chat.ToolUseEndPayload),
    TsInlineUnion(
        "ProcessStep",
        process.PROCESS_STEP_MEMBERS,
        doc=(
            "One step in a turn's 思考·正文·工具·协作 inline timeline (统一团队时间线).\n"
            "reasoning/content/rework + tool are the CEO bubble's own narrative; the rest\n"
            "are POSITIONAL MARKERS — zero-width anchors fixing WHERE a non-text element\n"
            "renders (payload looked up from the turn's side channels by id)."
        ),
    ),
    TsAlias(
        "ApprovalDecision",
        interaction.ApprovalDecision,
        doc=(
            "The user's settlement of a paused GRANTABLE tool call; mirrors the backend\n"
            "`ApprovalDecision`."
        ),
    ),
    TsInterface(interaction.ApprovalRequiredPayload),
    TsInterface(interaction.ApprovalResolvedPayload),
    TsAlias(
        "DelegationAuthorizationDecision",
        interaction.DelegationAuthorizationDecision,
        doc="The user's settlement of a delegation-level authorization gate (委派级授权).",
    ),
    TsInterface(interaction.DelegationAuthorizationWorker),
    TsInterface(interaction.DelegationAuthorizationRequiredPayload),
    TsInterface(interaction.DelegationAuthorizationResolvedPayload),
    TsAlias(
        "CheckpointDecision",
        interaction.CheckpointDecision,
        doc="The user's settlement of a checkpoint the CEO raised (ask_user).",
    ),
    TsInterface(interaction.AskAssumption),
    TsInterface(interaction.AskOption),
    TsInterface(interaction.AskQuestion),
    TsAlias("CheckpointIntent", interaction.AskCheckpointIntent),
    TsInterface(interaction.CheckpointRequiredPayload),
    TsInterface(interaction.CheckpointResolvedPayload),
    TsInterface(interaction.QuestionPostedPayload),
    TsInterface(interaction.QuestionResolvedPayload),
    TsInterface(interaction.PlanReviewStep),
    TsInterface(interaction.PlanReviewPending),
    TsInterface(interaction.CeoReviewSummary),
    TsInterface(interaction.PlanReviewRequiredPayload),
    TsInterface(interaction.PlanReviewResolvedPayload),
    TsInterface(interaction.TeamPreviewWorker),
    TsInterface(interaction.TeamPreviewSide),
    TsInterface(interaction.ModelCandidate),
    TsInterface(interaction.TeamPreviewRequiredPayload),
    TsInterface(interaction.WriteCapabilityOverride),
    TsInterface(interaction.ModelOverride),
    TsInterface(interaction.TeamPreviewResolvedPayload),
    TsInterface(interaction.StageCardRequiredPayload),
    TsInterface(interaction.StageCardResolvedPayload),
    TsAlias("PlanRevisionKind", run.PlanRevisionKind),
    TsInterface(run.PlanRevision),
    TsInterface(run.PlanRevisedPayload),
    TsInterface(run.PlanAgentPayload),
    TsInterface(run.RunPlanNode),
    TsAlias("ActKind", run.ActKind),
    TsInterface(run.RunPlanAct),
    TsInterface(run.RunPlanPayload),
    TsInterface(run.GraphAppendPayload),
    TsAlias("RunKind", run.RunKind),
    TsAlias("Stance", run.Stance),
    TsInterface(run.RunStartedPayload),
    TsInterface(run.ContextBlockWire),
    TsInterface(run.RunContextPayload),
    TsInterface(run.RunOutputDeltaPayload),
    TsInterface(run.RunOutputResetPayload),
    TsInterface(run.RunReasoningDeltaPayload),
    TsInterface(run.RunToolProgressPayload),
    TsAlias("WorkerRunPhase", run.WorkerRunPhase),
    TsInterface(run.RunPhasePayload),
    TsAlias("EscalationKind", run.EscalationKind),
    TsAlias("RunFailureKind", run.RunFailureKind),
    TsInterface(run.RunEscalationPayload),
    TsInterface(run.RunEscalationGatePayload),
    TsInterface(interaction.EscalationRequiredPayload),
    TsInterface(interaction.EscalationResolvedPayload),
    TsInterface(interaction.InteractionOrphanedPayload),
    TsInterface(run.TeamNotePostedPayload),
    TsInterface(run.TeamSynthesisWorkerPreview),
    TsInterface(run.TeamSynthesisPreviewPayload),
    TsInterface(run.CoordinationWaitPayload),
    TsInterface(run.WorkspaceLockWaitPayload),
    TsAlias(
        "DeliveryState",
        run.DeliveryState,
        doc=(
            "Overall verdict of a delegate batch's delivery reconciliation (交付诚实性):\n"
            "delivered = 无缺口且有落盘产物; partial = 有产物也有缺口; blocked = 有缺口且\n"
            "无落盘产物."
        ),
    ),
    TsInterface(run.DeliveryGap),
    TsInterface(run.DeliveryAction),
    TsInterface(run.DeliveryArtifact),
    TsInterface(run.DeliveryPromotion),
    TsInterface(run.DeliveryStatusPayload),
    TsInterface(run.UserInterjectionAttachment),
    TsInterface(run.UserInterjectionAgentMention),
    TsInterface(run.UserInterjectionPayload),
    TsInterface(run.TurnQueuedPayload),
    TsInterface(run.TurnQueueStartedPayload),
    TsInterface(run.TurnQueueCancelledPayload),
    TsInterface(run.ResumeDeferredPayload),
    TsInterface(run.ResumeSettledPayload),
    TsInterface(run.ExecutionDetachedPayload),
    TsInterface(run.ExecutionCompletedPayload),

    TsInterface(run.UsageBreakdown),
    TsInterface(run.CostBreakdown),
    TsAlias(
        "DebateForm",
        shared.DebateForm,
        doc=(
            "辩论形态成员集单源（``runtime.debate.types.DebateForm``）；"
            "wire / schema / 标签键同集。"
        ),
    ),
    TsInterface(shared.MotionCardSide),
    TsInterface(shared.MotionCard),
    TsInterface(run.RunDebrief),
    TsInterface(run.RunCompletedPayload),
    TsInterface(run.RunFailedPayload),
    TsInterface(run.RunCancelledPayload),
    TsInterface(run.RunSkippedPayload),
    TsInterface(run.RunProgressPayload),
    TsInterface(run.NodeTimingPayload),
    TsInterface(run.BatchMetricsPayload),
    TsInterface(debate.DebateSideInfo),
    TsInterface(debate.DebateSpeechArgument),
    TsInterface(debate.DebateRoundSide),
    TsInterface(debate.DebateFindingInfo),
    TsInterface(debate.DebateThreadTurnInfo),
    TsInterface(debate.DebateConsensusMapItem),
    TsInterface(debate.DebateVerdict),
    TsInterface(debate.DebateClash),
    TsInterface(debate.DebateUserInterjection),
    TsInterface(debate.DebateCrossExamExchange),
    TsInterface(debate.DebateCrossExam),
    TsInterface(debate.DebateWitnessExam),
    TsInterface(debate.DebateWitnessSeat),
    TsInterface(debate.DebateClosing),
    TsInterface(debate.DebateRoundScore),
    TsInterface(debate.EvidenceLedgerEntry),
    TsInterface(shared.TurnEvidenceLedgerEntry),
    TsInterface(shared.EvidenceLedgerPayload),
    TsInterface(debate.DebateRoundInfo),
    TsInterface(debate.DebateNarrativeRound),
    TsInterface(debate.DebateHandoffInfo),
    TsInterface(debate.DebateBriefInfo),
    TsInterface(debate.DebateResultPayload),
    TsInterface(debate.DebateRoundStartedPayload),
    TsInterface(debate.DebateRoundPayload, extends=debate.DebateRoundInfo),
    TsInterface(debate.DebatePretrialSideInfo),
    TsInterface(debate.DebatePretrialTask),
    TsInterface(debate.DebatePretrialOrder),
    TsInterface(debate.DebateEvidencePackSource),
    TsInterface(debate.DebateEvidencePackDispute),
    TsInterface(debate.DebateEvidencePack),
    TsInterface(debate.DebatePretrialStartedPayload),
    TsInterface(debate.DebatePretrialOrdersPayload),
    TsInterface(debate.DebatePretrialCompletedPayload),
    TsInterface(chat.TurnCollabMetrics),
    TsInlineUnion(
        "TeamBatchStatus",
        (chat.TeamBatchNoBatch, chat.TeamBatchInFlight, chat.TeamBatchSettled),
        doc=(
            "本回合团队状态（turn journal 派生）：no_batch / in_flight / settled。\n"
            "没派工是确定态，不是信息缺失。worker_count = 本波 kickoff 编制，不含 captain。"
        ),
    ),
    TsInterface(chat.MessageEndUsage),
    TsInterface(chat.MessageEndPayload),
    TsInterface(chat.ErrorContext),
    TsInterface(chat.ErrorPayload),
    TsInterface(chat.TitleGeneratedPayload),
    TsInterface(chat.TurnWarningPayload),
    TsInterface(sim.Vec3, force_required=frozenset({"x", "y", "z"})),
    TsInterface(
        sim.SimAgentState,
        force_required=frozenset({"activity", "mood", "goal", "last_thought"}),
        doc="Per-agent snapshot on `sim.agent_state` and in tick snapshots.",
    ),
    TsInterface(
        sim.SimAgentAction,
        force_required=frozenset({"thought", "success", "detail"}),
        doc="One agent decision within a tick (`sim.agent_action`).",
    ),
    TsInterface(sim.SimTickStartedPayload),
    TsInterface(
        sim.TickMetrics,
        name="SimTickMetrics",
        force_required=frozenset(
            {
                "avg_mood",
                "trade_count",
                "trade_total_amount",
                "positive_relation_ratio",
                "population_by_region",
            }
        ),
        doc=(
            "Macro indicators for one simulation tick (backend `TickMetrics`), carried on\n"
            "`sim.tick_ended` for the 观测面板. `population_by_region` maps a region name →\n"
            "head count."
        ),
    ),
    TsInterface(sim.SimTickEndedPayload),
    TsInterface(sim.SimTickFramePayload),
    TsInterface(sim.SimAgentStatePayload),
    TsInterface(sim.SimAgentActionPayload),
    TsInterface(sim.InteractionTranscriptLine, force_required=frozenset({"round"})),
    TsInterface(
        sim.InteractionStateChange,
        doc="Summary of world mutations applied by an interaction.",
    ),
    TsInterface(sim.InteractionResult),
    TsInterface(sim.SimInteractionPayload),
    TsInterface(
        sim.WorldModifiersWire,
        force_required=frozenset(
            {
                "market_price_multiplier",
                "storm_active",
                "festival_active",
                "square_attraction_boost",
            }
        ),
        doc="World-level knobs affected by scheduled events.",
    ),
    TsInterface(
        sim.WorldEventWire,
        force_required=frozenset({"duration_ticks", "source"}),
        doc="One active world event in tick snapshots.",
    ),
    TsInterface(sim.SimWorldEventPayload, force_required=frozenset({"modifiers"})),
    TsInterface(
        show.SimShowHeartPickPayload,
        doc="恋综心动选票（密封或已揭晓） on `sim.show.heart_pick`.",
    ),
    TsInterface(show.SimShowPairFormedPayload, doc="恋综互选配对 on `sim.show.pair_formed`."),
    TsInterface(
        show.SimShowAffectionShiftPayload,
        doc="恋综移情标记 on `sim.show.affection_shift`.",
    ),
    TsInterface(
        show.SimShowZeroVoteAlertPayload,
        doc="恋综零票告急 on `sim.show.zero_vote_alert`.",
    ),
    TsInterface(show.SimShowDeparturePayload, doc="恋综角色离场 on `sim.show.departure`."),
    TsInterface(show.SimShowRevealPayload, doc="恋综心动揭晓一步 on `sim.show.reveal`."),
    TsInterface(
        show.SimShowEpisodeGatePayload,
        doc="恋综期分段 / 仪式门 on `sim.show.episode_gate`.",
    ),
    TsAlias(
        "BrowserLiveState",
        browser.BrowserLiveState,
        doc=(
            "团队浏览器直播通道状态（`browser_live_status`）：`started` 有会话且开播；\n"
            "`no_session` 附着但无直播会话；`session_closed` 观看中的会话被回收/关闭。"
        ),
    ),
    TsInterface(
        browser.BrowserLiveFramePayload,
        doc="One live screencast frame on `browser_live_frame`（base64 jpeg + 设备像素尺寸）.",
    ),
    TsInterface(browser.BrowserLiveStatusPayload),
    TsInterface(chat.FollowupsGeneratedPayload),
    TsInterface(chat.FollowupsUnavailablePayload),
    TsInterface(chat.TurnSavedPayload),
    TsInterface(shared.Citation),
    TsInterface(shared.CitationsPayload),
    TsInterface(workspace.WorkspaceOpRequiredPayload),
    TsInterface(workspace.BoardOp),
    TsInterface(workspace.BoardOpRequiredPayload),
    TsInterface(workspace.BoardReadRequiredPayload),
    TsInterface(workspace.DesktopNotifyRequiredPayload),
    TsInterface(workspace.ExternalMountReadonlyRequiredPayload),
    TsInterface(workspace.HostOpRequiredPayload),
    TsInterface(workspace.McpOpRequiredPayload),
    TsInterface(workspace.AutoFolderCreatedPayload),
    TsInterface(workspace.HandoffSnapshotDonePayload),
    TsInterface(workspace.HandoffJobStartedPayload),
    TsInterface(workspace.HandoffApplyResult),
    TsInterface(workspace.HandoffApplyDonePayload),
    TsInterface(workspace.WorkspaceSnapshotDonePayload),
    TsInterface(workspace.WorkspaceSnapshotFailedPayload),
)

# ── EventType → payload wire model (exhaustive; asserted by tests) ─────────────────────

EVENT_PAYLOAD_MODELS: dict[EventType, type[BaseModel]] = {
    EventType.MESSAGE_START: chat.MessageStartPayload,
    EventType.CONTENT_DELTA: chat.ContentDeltaPayload,
    EventType.CONTENT_RESET: chat.ContentResetPayload,
    EventType.REASONING_DELTA: chat.ReasoningDeltaPayload,
    EventType.TOOL_PROGRESS: chat.ToolProgressPayload,
    EventType.TOOL_USE_PROGRESS: chat.ToolUseProgressPayload,
    EventType.TOOL_USE_START: chat.ToolUseStartPayload,
    EventType.TOOL_USE_END: chat.ToolUseEndPayload,
    EventType.APPROVAL_REQUIRED: interaction.ApprovalRequiredPayload,
    EventType.APPROVAL_RESOLVED: interaction.ApprovalResolvedPayload,
    EventType.DELEGATION_AUTHORIZATION_REQUIRED: (
        interaction.DelegationAuthorizationRequiredPayload
    ),
    EventType.DELEGATION_AUTHORIZATION_RESOLVED: (
        interaction.DelegationAuthorizationResolvedPayload
    ),
    EventType.CHECKPOINT_REQUIRED: interaction.CheckpointRequiredPayload,
    EventType.CHECKPOINT_RESOLVED: interaction.CheckpointResolvedPayload,
    EventType.QUESTION_POSTED: interaction.QuestionPostedPayload,
    EventType.QUESTION_RESOLVED: interaction.QuestionResolvedPayload,
    EventType.PLAN_REVIEW_REQUIRED: interaction.PlanReviewRequiredPayload,
    EventType.PLAN_REVIEW_RESOLVED: interaction.PlanReviewResolvedPayload,
    EventType.TEAM_PREVIEW_REQUIRED: interaction.TeamPreviewRequiredPayload,
    EventType.TEAM_PREVIEW_RESOLVED: interaction.TeamPreviewResolvedPayload,
    EventType.STAGE_CARD_REQUIRED: interaction.StageCardRequiredPayload,
    EventType.STAGE_CARD_RESOLVED: interaction.StageCardResolvedPayload,
    EventType.PLAN_REVISED: run.PlanRevisedPayload,
    EventType.RUN_PLAN: run.RunPlanPayload,
    EventType.GRAPH_APPEND: run.GraphAppendPayload,
    EventType.RUN_STARTED: run.RunStartedPayload,
    EventType.RUN_CONTEXT: run.RunContextPayload,
    EventType.RUN_OUTPUT_DELTA: run.RunOutputDeltaPayload,
    EventType.RUN_OUTPUT_RESET: run.RunOutputResetPayload,
    EventType.RUN_REASONING_DELTA: run.RunReasoningDeltaPayload,
    EventType.RUN_TOOL_PROGRESS: run.RunToolProgressPayload,
    EventType.RUN_PHASE: run.RunPhasePayload,
    EventType.RUN_COMPLETED: run.RunCompletedPayload,
    EventType.RUN_FAILED: run.RunFailedPayload,
    EventType.RUN_CANCELLED: run.RunCancelledPayload,
    EventType.RUN_SKIPPED: run.RunSkippedPayload,
    EventType.RUN_PROGRESS: run.RunProgressPayload,
    EventType.BATCH_METRICS: run.BatchMetricsPayload,
    EventType.RUN_ESCALATION: run.RunEscalationPayload,
    EventType.RUN_ESCALATION_GATE: run.RunEscalationGatePayload,
    EventType.ESCALATION_REQUIRED: interaction.EscalationRequiredPayload,
    EventType.ESCALATION_RESOLVED: interaction.EscalationResolvedPayload,
    EventType.INTERACTION_ORPHANED: interaction.InteractionOrphanedPayload,
    EventType.TEAM_NOTE_POSTED: run.TeamNotePostedPayload,
    EventType.TEAM_SYNTHESIS_PREVIEW: run.TeamSynthesisPreviewPayload,
    EventType.COORDINATION_WAIT: run.CoordinationWaitPayload,
    EventType.WORKSPACE_LOCK_WAIT: run.WorkspaceLockWaitPayload,
    EventType.DELIVERY_STATUS: run.DeliveryStatusPayload,
    EventType.USER_INTERJECTION: run.UserInterjectionPayload,
    EventType.TURN_QUEUED: run.TurnQueuedPayload,
    EventType.TURN_QUEUE_STARTED: run.TurnQueueStartedPayload,
    EventType.TURN_QUEUE_CANCELLED: run.TurnQueueCancelledPayload,
    EventType.RESUME_DEFERRED: run.ResumeDeferredPayload,
    EventType.RESUME_SETTLED: run.ResumeSettledPayload,
    EventType.EXECUTION_DETACHED: run.ExecutionDetachedPayload,
    EventType.EXECUTION_COMPLETED: run.ExecutionCompletedPayload,
    EventType.DEBATE_RESULT: debate.DebateResultPayload,
    EventType.DEBATE_ROUND_STARTED: debate.DebateRoundStartedPayload,
    EventType.DEBATE_ROUND: debate.DebateRoundPayload,
    EventType.DEBATE_PRETRIAL_STARTED: debate.DebatePretrialStartedPayload,
    EventType.DEBATE_PRETRIAL_ORDERS: debate.DebatePretrialOrdersPayload,
    EventType.DEBATE_PRETRIAL_COMPLETED: debate.DebatePretrialCompletedPayload,
    EventType.MESSAGE_END: chat.MessageEndPayload,
    EventType.ERROR: chat.ErrorPayload,
    EventType.TITLE_GENERATED: chat.TitleGeneratedPayload,
    EventType.TURN_WARNING: chat.TurnWarningPayload,
    EventType.SIM_TICK_STARTED: sim.SimTickStartedPayload,
    EventType.SIM_TICK_ENDED: sim.SimTickEndedPayload,
    EventType.SIM_TICK_FRAME: sim.SimTickFramePayload,
    EventType.SIM_AGENT_ACTION: sim.SimAgentActionPayload,
    EventType.SIM_AGENT_STATE: sim.SimAgentStatePayload,
    EventType.SIM_INTERACTION: sim.SimInteractionPayload,
    EventType.SIM_WORLD_EVENT: sim.SimWorldEventPayload,
    EventType.SIM_SHOW_HEART_PICK: show.SimShowHeartPickPayload,
    EventType.SIM_SHOW_PAIR_FORMED: show.SimShowPairFormedPayload,
    EventType.SIM_SHOW_AFFECTION_SHIFT: show.SimShowAffectionShiftPayload,
    EventType.SIM_SHOW_ZERO_VOTE_ALERT: show.SimShowZeroVoteAlertPayload,
    EventType.SIM_SHOW_DEPARTURE: show.SimShowDeparturePayload,
    EventType.SIM_SHOW_REVEAL: show.SimShowRevealPayload,
    EventType.SIM_SHOW_EPISODE_GATE: show.SimShowEpisodeGatePayload,
    EventType.BROWSER_LIVE_FRAME: browser.BrowserLiveFramePayload,
    EventType.BROWSER_LIVE_STATUS: browser.BrowserLiveStatusPayload,
    EventType.FOLLOWUPS_GENERATED: chat.FollowupsGeneratedPayload,
    EventType.FOLLOWUPS_UNAVAILABLE: chat.FollowupsUnavailablePayload,
    EventType.TURN_SAVED: chat.TurnSavedPayload,
    EventType.CITATIONS: shared.CitationsPayload,
    EventType.EVIDENCE_LEDGER: shared.EvidenceLedgerPayload,
    EventType.WORKSPACE_OP_REQUIRED: workspace.WorkspaceOpRequiredPayload,
    EventType.BOARD_OP_REQUIRED: workspace.BoardOpRequiredPayload,
    EventType.BOARD_READ_REQUIRED: workspace.BoardReadRequiredPayload,
    EventType.DESKTOP_NOTIFY_REQUIRED: workspace.DesktopNotifyRequiredPayload,
    EventType.EXTERNAL_MOUNT_READONLY_REQUIRED: (
        workspace.ExternalMountReadonlyRequiredPayload
    ),
    EventType.HOST_OP_REQUIRED: workspace.HostOpRequiredPayload,
    EventType.MCP_OP_REQUIRED: workspace.McpOpRequiredPayload,
    EventType.AUTO_FOLDER_CREATED: workspace.AutoFolderCreatedPayload,
    EventType.HANDOFF_SNAPSHOT_DONE: workspace.HandoffSnapshotDonePayload,
    EventType.HANDOFF_JOB_STARTED: workspace.HandoffJobStartedPayload,
    EventType.HANDOFF_APPLY_DONE: workspace.HandoffApplyDonePayload,
    EventType.WORKSPACE_SNAPSHOT_DONE: workspace.WorkspaceSnapshotDonePayload,
    EventType.WORKSPACE_SNAPSHOT_FAILED: workspace.WorkspaceSnapshotFailedPayload,
}

__all__ = [
    "EVENT_PAYLOAD_MODELS",
    "TS_EXPORTS",
    "TsAlias",
    "TsExport",
    "TsInlineUnion",
    "TsInterface",
    "TsRaw",
]
