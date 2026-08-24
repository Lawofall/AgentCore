// Desktop's fold → ProjectedTurn snapshot adapter for the cross-platform protocol
// 巡检 (前端技术与架构 §十 SSE 与协议一致性; protocol-conformance.mdc). The conformance test asserts
// this == the backend-exported golden, the SAME golden the mobile fold is pinned to —
// so desktop and mobile can't diverge on the protocol without the gate going red.
//
// AUTHENTICITY: the team-graph projection reuses desktop's REAL pure fold
// (`projectExecution` + `planFromRunPlan` + `frameFromEvent` from stores/execution.ts)
// — the complex, drift-prone surface is the actual production code, not a copy. The
// process timeline (思考·正文·工具, single-agent AND multi-agent — 统一团队时间线) now ALSO
// reuses production-sourced helpers (`@/lib/foldMessageLane` + `processTimeline`,
// shared with stores/conversation.ts) so live / reload / golden stay aligned.
//
// ProjectedTurn (+ its sub-shapes) is imported from the shared
// @agentcore/protocol-conformance package now that desktop has joined the workspace —
// one judge type for both ends; the committed golden JSON is the real contract checked.

import { assertNever } from "@/lib/assertNever";
import {
  type MessageLaneState,
  foldCitations,
  foldContentDelta,
  foldContentReset,
  foldGraphAppendMarker,
  foldInteractionTimelineMarker,
  foldReasoningDelta,
  foldTeamMarker,
  foldToolUseEnd,
  foldToolUseStart,
  foldUserInterjectionMarker,
} from "@/lib/foldMessageLane";
import {
  type AgentState,
  type ExecutionPlan,
  type ExecutionStatus,
  type RunFrame,
  type RunNode,
  type UserInterjection,
  foldDebatePretrial,
  frameFromEvent,
  mergePlanInto,
  planFromRunPlan,
  projectExecution,
  upsertDebateRound,
  userInterjectionFromPayload,
} from "@/stores/execution";
import type { DebatePretrialState } from "@/stores/execution";
import {
  defFromRequiredEvent,
  defFromResolvedEvent,
  wireFor,
} from "@/stores/interactions";
import type {
  AutoFolderCreatedPayload,
  CitationsPayload,
  ContentDeltaPayload,
  ContentResetPayload,
  ContextBlockWire,
  DebateNarrativeRound,
  DebateResultPayload,
  DebateRoundPayload,
  DebateRoundStartedPayload,
  DeliveryStatusPayload,
  EvidenceLedgerPayload,
  GraphAppendPayload,
  MessageEndPayload,
  ReasoningDeltaPayload,
  RunContextPayload,
  RunPlanPayload,
  RunStartedPayload,
  SSEEvent,
  TeamSynthesisPreviewPayload,
  ToolUseEndPayload,
  ToolUseStartPayload,
  TurnEvidenceLedgerEntry,
  TurnWarningPayload,
} from "@/types/events";
import type {
  CostBreakdown,
  ProjectedAgent,
  ProjectedCitation,
  ProjectedEvidenceLedgerEntry,
  ProjectedRun,
  ProjectedTurn,
  TurnStatus,
} from "@agentcore/protocol-conformance/projectedTurn";
import {
  FINISH_TO_STATUS,
  resolveTurnOutcome,
} from "@agentcore/protocol-fold-kit";
import { foldInteractions, hasGatePending } from "./foldInteractions";

export type { ProjectedTurn };

/** Registry-driven message-lane marker fold for `*_required`. */
function foldLaneFromInteractionEvent(
  lane: MessageLaneState,
  eventType: string,
  payload: Record<string, unknown>,
): MessageLaneState {
  const def = defFromRequiredEvent(eventType);
  if (!def?.timeline) return lane;
  const id = payload[wireFor(def.kind).idField];
  if (typeof id !== "string" || !id) return lane;
  return foldInteractionTimelineMarker(lane, def.timeline, id);
}

function maybeRecordInteractionFrame(
  eventType: string,
  ev: SSEEvent,
  frames: RunFrame[],
): void {
  const required = defFromRequiredEvent(eventType);
  if (required?.sseRequired?.recordExecFrame) {
    const frame = frameFromEvent(ev);
    if (frame) frames.push(frame);
    return;
  }
  const resolved = defFromResolvedEvent(eventType);
  if (resolved?.sseResolved?.recordExecFrame) {
    const frame = frameFromEvent(ev);
    if (frame) frames.push(frame);
  }
}

/** Desktop's fold → ProjectedTurn (the conformance snapshot). */
export function foldToProjectedTurn(events: SSEEvent[]): ProjectedTurn {
  let messageLane: MessageLaneState = {
    content: "",
    reasoning: "",
    process: [],
    citations: [],
  };
  let evidenceLedger: ProjectedEvidenceLedgerEntry[] = [];
  let citedIds: string[] = [];
  let finishReason: string | null = null;
  let explicitOutcome: string | null = null;
  let cost: CostBreakdown | null = null;
  // 跨回合流 vs 同回合 resume：仅 message_id 变化时清空气泡正文（见 message_start）。
  let lastMessageId: string | null = null;
  let debate: DebateResultPayload | null = null;
  let debateRounds: DebateNarrativeRound[] = [];
  let crossExamEnabled = false;
  let debateOpening: string | null = null;
  let debatePretrial: DebatePretrialState | null = null;
  let teamSynthesisPreview: TeamSynthesisPreviewPayload | null = null;
  let deliveryStatus: DeliveryStatusPayload | null = null;
  /** journal 内最后一条 `execution_completed.status`（若有）→ 投影到 execution.status。 */
  let fromExecutionCompleted: ExecutionStatus | null = null;
  let turnWarning: string | null = null;
  let autoFolder: ProjectedTurn["autoFolder"] = null;
  const userInterjections: UserInterjection[] = [];
  const userInterjectionIndex = new Map<string, number>();
  let sawError = false;
  let turnError: { code: string; message: string } | null = null;
  // 收到的上下文 · CEO 侧 (上下文传递可视化): the captain run id (its kind=captain
  // run_started) + the opening context it was fed, routed turn-level — the CEO is the
  // bubble above the graph, not a peer node, so its run_context never becomes a frame.
  let captainRunId: string | null = null;
  let captainContext: ContextBlockWire[] = [];

  // Team graph via the REAL desktop fold: build the plan + frame stream the same way
  // hydrateFromJournal does, then project.
  let plan: ExecutionPlan | null = null;
  const frames: RunFrame[] = [];

  for (const ev of events) {
    const leftoverType = ev.type as string;
    if (
      leftoverType === "team_preview_required" ||
      leftoverType === "team_preview_resolved"
    ) {
      continue;
    }
    switch (ev.type) {
      // `replace`（attach 增量重放的帧级替换）：带标记的帧携带的是末尾那个尚未闭合的
      // 文本块的全文，换块而非追加——与生产 fold 同一实现（`foldContentDelta`），
      // 游标增量段的向量（reload_cursor_incremental）就钉这条。
      case "content_delta": {
        const p = ev.payload as ContentDeltaPayload;
        messageLane = foldContentDelta(messageLane, p.delta, p.replace);
        break;
      }
      case "content_reset":
        messageLane = foldContentReset(
          messageLane,
          (ev.payload as ContentResetPayload).reason,
        );
        break;
      case "reasoning_delta": {
        const p = ev.payload as ReasoningDeltaPayload;
        messageLane = foldReasoningDelta(messageLane, p.delta, p.replace);
        break;
      }
      case "tool_use_start":
        messageLane = foldToolUseStart(
          messageLane,
          ev.payload as ToolUseStartPayload,
        );
        {
          const frame = frameFromEvent(ev);
          if (frame) frames.push(frame);
        }
        break;
      case "tool_use_end":
        messageLane = foldToolUseEnd(
          messageLane,
          ev.payload as ToolUseEndPayload,
        );
        {
          const frame = frameFromEvent(ev);
          if (frame) frames.push(frame);
        }
        break;
      case "citations":
        messageLane = foldCitations(
          messageLane,
          (ev.payload as CitationsPayload).citations ?? [],
        );
        break;
      case "evidence_ledger": {
        const p = ev.payload as EvidenceLedgerPayload;
        if (Array.isArray(p.entries)) {
          evidenceLedger = p.entries as ProjectedEvidenceLedgerEntry[];
        } else if (p.delta?.length) {
          evidenceLedger = mergeTurnLedger(
            evidenceLedger,
            p.delta as TurnEvidenceLedgerEntry[],
          );
        }
        if (Array.isArray(p.cited_ids)) {
          citedIds = p.cited_ids.map(String);
        }
        break;
      }
      case "run_plan": {
        const payload = ev.payload as RunPlanPayload;
        const next = planFromRunPlan(payload);
        plan = plan && plan.id === next.id ? mergePlanInto(plan, next) : next;
        // 旧 journal 跨回合同图追加：带 host_message_id 的生长 run_plan 不插 team
        //（锚点由 graph_append）。新路径无此字段 → 本回合开图。
        if (!payload.host_message_id) {
          // 协作图时间线落点: the first plan of an execution drops a `team` marker fixing
          // the graph's slot in the CEO timeline (later same-id batches no-op).
          messageLane = foldTeamMarker(messageLane, next.id);
        }
        break;
      }
      case "graph_append": {
        const p = ev.payload as GraphAppendPayload;
        messageLane = foldGraphAppendMarker(
          messageLane,
          p.execution_id,
          p.host_message_id,
          p.added_count,
          p.act_id,
          p.act_kind,
          p.authorized_by,
        );
        break;
      }
      case "run_started": {
        // The CEO captain is the turn's root (kind=captain); remember its run id so its
        // run_context routes turn-level (its node still folds via the frame like any run).
        const p = ev.payload as RunStartedPayload;
        if (p.kind === "captain") captainRunId = p.run_id;
        const frame = frameFromEvent(ev);
        if (frame) frames.push(frame);
        break;
      }
      case "run_context": {
        // The CAPTAIN's context routes TURN-LEVEL onto captainContext (the CEO is the
        // bubble above the graph, not a node — shows on every turn, pure chat included),
        // APPENDING across emits so its context GROWS by each post-delegation team readback
        // (通道⑤); a WORKER's stays a frame so projectExecution folds it onto its graph node.
        const p = ev.payload as RunContextPayload;
        if (p.run_id === captainRunId) {
          captainContext = [...captainContext, ...p.blocks];
          break;
        }
        const frame = frameFromEvent(ev);
        if (frame) frames.push(frame);
        break;
      }
      case "run_output_delta":
      // 交付前核验回炉 (finish_guard) 的 worker 对偶: run_output_reset folds via the same frame
      // path — projectExecution clears the agent's outputChunks so the rewrite replaces the
      // discarded draft (content_reset 之于 CEO 气泡). Mirrors the oracle + mobile fold.
      case "run_output_reset":
      case "run_reasoning_delta":
      case "run_tool_progress":
      case "run_phase":
      case "run_completed":
      case "run_failed":
      case "run_cancelled":
      case "run_skipped":
      case "run_progress":
      //「计划已调整」轻痕迹 (设计 §7.2): a NON-interrupting trace — folds onto the runs'
      // `revised` via the same frame path (no gate, like the escalate banner).
      case "plan_revised": {
        const frame = frameFromEvent(ev);
        if (frame) frames.push(frame);
        break;
      }
      case "run_escalation": {
        // Frame → run ⚠️ badge; process marker → CEO timeline slot (二期 D1/D6).
        // Raised is not an interaction required event — stamp via escalation timeline def.
        const frame = frameFromEvent(ev);
        if (frame) frames.push(frame);
        {
          const eid = (ev.payload as { escalation_id?: string })?.escalation_id;
          const timeline = defFromRequiredEvent(
            "escalation_required",
          )?.timeline;
          if (typeof eid === "string" && eid && timeline) {
            messageLane = foldInteractionTimelineMarker(
              messageLane,
              timeline,
              eid,
            );
          }
        }
        break;
      }
      // 阻塞式求决策: frame folds onto run escalations; process marker at required 时刻.
      case "escalation_required": {
        const frame = frameFromEvent(ev);
        if (frame) frames.push(frame);
        messageLane = foldLaneFromInteractionEvent(
          messageLane,
          ev.type,
          (ev.payload ?? {}) as Record<string, unknown>,
        );
        break;
      }
      case "escalation_resolved": {
        const frame = frameFromEvent(ev);
        if (frame) frames.push(frame);
        break;
      }
      // 团队便签墙 (§2.2 通): a worker broadcast a one-line decision / heads-up to its concurrent
      // siblings — folds turn-level onto Execution.teamNotes via the same frame path (post order,
      // deduped by noteId). Mirrors the backend oracle + mobile fold (conformance pins them equal).
      case "team_note_posted": {
        const frame = frameFromEvent(ev);
        if (frame) frames.push(frame);
        break;
      }
      // 辩论收场产物（回合级单事件，非 frame）：verbatim 折入，与 oracle 一致。
      case "debate_result": {
        debate = ev.payload as DebateResultPayload;
        break;
      }
      // 辩论逐轮增量（进行中实时叠加，非 frame）：折叠累积成 debateRounds，与 oracle / 手机
      // fold 一致。round_started 先给焦点（verdict=null=进行中），round 补 summary/verdict/sides。
      case "debate_round_started": {
        const p = ev.payload as DebateRoundStartedPayload;
        if (p.cross_exam_enabled === true) crossExamEnabled = true;
        const rawOpening = (p.opening ?? "").trim();
        if (rawOpening && !debateOpening) debateOpening = rawOpening;
        debateRounds = upsertDebateRound(debateRounds, {
          round_no: p.round_no,
          focus: p.focus,
          summary: "",
          verdict: null,
          sides: [],
          clashes: [],
          cross_exam: [],
          witness_exam: [],
          findings: [],
          thread_turns: [],
        });
        break;
      }
      case "debate_round": {
        const p = ev.payload as DebateRoundPayload;
        debateRounds = upsertDebateRound(debateRounds, {
          round_no: p.round_no,
          focus: p.focus,
          summary: p.summary,
          verdict: p.verdict,
          sides: p.sides,
          clashes: p.clashes,
          cross_exam: p.cross_exam ?? [],
          witness_exam: p.witness_exam ?? [],
          findings: p.findings ?? [],
          thread_turns: p.thread_turns ?? [],
        });
        break;
      }
      case "debate_pretrial_started":
      case "debate_pretrial_orders":
      case "debate_pretrial_completed": {
        debatePretrial = foldDebatePretrial(
          debatePretrial,
          ev.type,
          ev.payload,
        );
        break;
      }
      case "plan_review_required":
      case "checkpoint_required":
      case "approval_required":
      case "stage_card_required": {
        maybeRecordInteractionFrame(ev.type, ev, frames);
        messageLane = foldLaneFromInteractionEvent(
          messageLane,
          ev.type,
          (ev.payload ?? {}) as Record<string, unknown>,
        );
        break;
      }
      case "plan_review_resolved":
      case "checkpoint_resolved":
      case "approval_resolved":
      case "stage_card_resolved": {
        maybeRecordInteractionFrame(ev.type, ev, frames);
        break;
      }
      case "error": {
        sawError = true;
        const p = ev.payload as { code?: string; message?: string };
        turnError = {
          code: (p.code ?? "").trim() || "LLM_ERROR",
          message: (p.message ?? "").trim(),
        };
        break;
      }
      case "message_end": {
        const p = ev.payload as MessageEndPayload;
        finishReason = p.finish_reason;
        cost = p.cost ?? null;
        const raw = p.outcome;
        if (
          raw === "ok" ||
          raw === "partial" ||
          raw === "paused" ||
          raw === "error"
        ) {
          explicitOutcome = raw;
        }
        break;
      }
      case "message_start": {
        // 跨回合流：message_id 变化 = 新助手气泡 → 清空正文/过程时间线；
        // 同 execution 的 plan/frames 保留，使第二回合追加帧继续生长同一张协作图。
        // 同 message_id = 挂起恢复重开同一气泡 → 保留已累积正文（pause→resume）。
        const mid = String(
          (ev.payload as { message_id?: string }).message_id || "",
        );
        if (lastMessageId === null || (mid && mid !== lastMessageId)) {
          messageLane = {
            ...messageLane,
            content: "",
            reasoning: "",
            process: [],
          };
          finishReason = null;
          explicitOutcome = null;
          cost = null;
          turnError = null;
        }
        if (mid) lastMessageId = mid;
        break;
      }
      // Not part of the normalized judge state beyond interactions[] fold (no-op) —
      // enumerated so assertNever stays exhaustive against @agentcore/contract-types.
      case "turn_saved":
      case "title_generated":
      case "board_op_required":
      case "board_read_required":
      case "desktop_notify_required":
      case "external_mount_readonly_required":
      case "host_op_required":
      case "mcp_op_required":
      case "tool_progress":
      case "tool_use_progress":
      case "coordination_wait":
      case "workspace_lock_wait":
      case "turn_queued":
      case "turn_queue_started":
      case "turn_queue_cancelled":
      case "resume_deferred":
      case "resume_settled":
      case "browser_live_frame":
      case "browser_live_status":
      case "batch_metrics":
      case "run_escalation_gate":
      case "interaction_orphaned":
      case "workspace_op_required":
      case "handoff_snapshot_done":
      case "handoff_job_started":
      case "handoff_apply_done":
      case "workspace_snapshot_done":
      case "workspace_snapshot_failed":
      case "execution_detached":
      case "execution_completed": {
        // DURABLE：execution 终态权威 → 投影到 projectExecution 的 status（缺省 completed）。
        // TurnStatus 仍跟 finishReason；此处只校正协作图 execution.status。
        if (ev.type === "execution_completed") {
          const raw = (ev.payload as { status?: string }).status;
          fromExecutionCompleted =
            raw === "cancelled" || raw === "failed" || raw === "completed"
              ? raw
              : "completed";
        }
        break;
      }
      case "sim.agent_action":
      case "sim.agent_state":
      case "sim.interaction":
      case "sim.tick_started":
      case "sim.tick_ended":
      case "sim.tick_frame":
      case "sim.world_event":
        break;
      case "turn_warning": {
        turnWarning = (ev.payload as TurnWarningPayload).message;
        break;
      }
      case "auto_folder_created": {
        const p = ev.payload as AutoFolderCreatedPayload;
        autoFolder = { folderId: p.folder_id, name: p.name };
        break;
      }
      case "team_synthesis_preview": {
        // 同 key 保最新（后写覆盖）——journal append-only，fold 侧去重。
        teamSynthesisPreview = ev.payload as TeamSynthesisPreviewPayload;
        break;
      }
      case "delivery_status": {
        // 交付状态：同 execution_id 保最新（后写覆盖）；载荷 artifacts 已是各波并集。
        deliveryStatus = ev.payload as DeliveryStatusPayload;
        break;
      }
      case "user_interjection": {
        const leaf = userInterjectionFromPayload(ev.payload);
        if (leaf) {
          // 零宽 positional marker：同 id 首次出现钉到 process；后续 status 只改旁路。
          messageLane = foldUserInterjectionMarker(
            messageLane,
            leaf.interjectionId,
          );
          const idx = userInterjectionIndex.get(leaf.interjectionId);
          if (idx === undefined) {
            userInterjectionIndex.set(
              leaf.interjectionId,
              userInterjections.length,
            );
            userInterjections.push(leaf);
          } else {
            userInterjections[idx] = leaf;
          }
        }
        break;
      }
      default:
        assertNever(ev.type);
    }
  }

  const interactions = foldInteractions(events);
  let status: TurnStatus;
  if (finishReason != null) {
    status = FINISH_TO_STATUS[finishReason] ?? "completed";
  } else if (sawError) {
    status = "failed";
  } else if (hasGatePending(interactions)) {
    status = "paused";
  } else {
    status = "running";
  }

  const execStatus: ExecutionStatus =
    fromExecutionCompleted ?? (status === "running" ? "running" : status);
  const execution = plan
    ? projectExecution(
        plan,
        frames,
        execStatus,
        debate,
        debateRounds,
        crossExamEnabled,
        debateOpening,
      )
    : null;

  // Conformance 裁判序 = plan 声明序（与 oracle / 手机 fold 一致）；continue_run 插回父
  // 批次之后。projectExecution 内部仍按 frame 序（直播图无妨），此处只校正 ProjectedTurn。
  const orderedRuns = orderRunsForProjectedTurn(
    plan?.runs.map((r) => r.id) ?? [],
    execution?.runs ?? [],
  );
  const orderedAgents = orderAgentsForProjectedTurn(
    orderedRuns,
    plan?.agents.map((a) => a.id) ?? [],
    execution?.agents ?? [],
  );

  const agents: ProjectedAgent[] = orderedAgents.map((a) => ({
    id: a.id,
    role: a.role,
    thinking: a.thinking,
    status: a.status,
    currentRunId: a.currentRunId,
    output: a.outputChunks.join(""),
    reasoning: a.reasoningChunks.join(""),
    toolProgress: a.toolProgress,
  }));

  const runs: ProjectedRun[] = orderedRuns.map((r) => ({
    id: r.id,
    agentId: r.agentId,
    task: r.task,
    status: r.status,
    dependsOn: r.dependsOn,
    outputSummary: r.outputSummary,
    debrief: r.debrief,
    durationMs: r.durationMs,
    error: r.error,
    failureKind: r.failureKind ?? null,
    productLanded: r.productLanded ?? null,
    parentRunId: r.parentRunId,
    kind: r.kind,
    role: r.role,
    model: r.model,
    usage: r.usage,
    cost: r.cost,
    stance: r.stance,
    group: r.group,
    round: r.round,
    continuesRunId: r.continuesRunId,
    revised: r.revised,
    replacesRunId: r.replacesRunId,
    actId: r.actId || "act-1",
    checkpoint: r.checkpoint,
    receivedContext: r.receivedContext,
    // Strip the desktop-local `id` (the resolve target): the conformance RunEscalation is the
    // golden fields the oracle carries — keeping `id` out here is what lets us thread it
    // through the store without widening the cross-end contract.
    escalations: r.escalations.map((e) => ({
      question: e.question,
      assumption: e.assumption,
      blocking: e.blocking,
      status: e.status,
      answer: e.answer,
      kind: e.kind ?? "normal",
      ...(e.awaiting === "ceo" ? { awaiting: "ceo" as const } : {}),
      ...(e.arbitrated_by === "ceo"
        ? {
            arbitrated_by: "ceo" as const,
            ...(e.via_user != null ? { via_user: e.via_user } : {}),
          }
        : {}),
      // 早停 source 可选；旧 golden 无此字段。桌面本地 id / browserLogin 等仍剥离。
      ...(e.source ? { source: e.source } : {}),
    })),
    process: r.process,
    // Worker mid-flight phase: only emit when set (mirrors oracle — absent on
    // pending/skipped / older vectors without run_phase).
    ...(r.phase != null
      ? { phase: r.phase, phaseTool: r.phaseTool ?? null }
      : {}),
  }));

  return {
    status,
    finishReason,
    outcome: resolveTurnOutcome({
      events,
      finishReason,
      hasError: sawError,
      explicit: explicitOutcome,
      running: status === "running",
    }),
    error: turnError,
    content: messageLane.content,
    reasoning: messageLane.reasoning,
    captainContext,
    // 桌面呈现扩展（act_id/act_kind）不进 ProjectedTurn，避免与 golden 漂移。
    process: messageLane.process.map((step) => {
      if (step.kind !== "graph_append") return step;
      return {
        kind: "graph_append" as const,
        execution_id: step.execution_id,
        host_message_id: step.host_message_id,
        added_count: step.added_count,
      };
    }),
    citations: messageLane.citations as ProjectedCitation[],
    evidenceLedger,
    citedIds,
    agents,
    runs,
    acts: (execution?.acts ?? []).map((a) => ({
      actId: a.actId,
      kind: a.kind,
      title: a.title,
      anchorRunId: a.anchorRunId,
      authorizedBy: a.authorizedBy ?? null,
    })),
    progress: execution ? execution.progress : { completed: 0, total: 0 },
    interactions,
    cost,
    debate,
    debateRounds,
    debatePretrial,
    crossExamEnabled,
    debateOpening,
    teamSynthesisPreview,
    deliveryStatus,
    turnWarning,
    autoFolder,
    // 团队便签墙 (§2.2 通): single source = projectExecution's frame fold (above), mapped to the
    // golden's ProjectedTeamNote shape — the same single-source pattern as `escalations`.
    teamNotes: (execution?.teamNotes ?? []).map((n) => ({
      noteId: n.noteId,
      runId: n.runId,
      agentId: n.agentId,
      role: n.role,
      kind: n.kind,
      text: n.text,
      ts: n.ts,
      status: n.status,
      supersedes: n.supersedes,
      ...(n.source ? { source: n.source } : {}),
    })),
    userInterjections,
  };
}

/** Merge turn-ledger delta by id (append-order; later write wins). */
function mergeTurnLedger(
  existing: ProjectedEvidenceLedgerEntry[],
  delta: TurnEvidenceLedgerEntry[],
): ProjectedEvidenceLedgerEntry[] {
  if (delta.length === 0) return existing;
  const order: string[] = [];
  const byId = new Map<string, ProjectedEvidenceLedgerEntry>();
  for (const e of existing) {
    if (!byId.has(e.id)) order.push(e.id);
    byId.set(e.id, e);
  }
  for (const e of delta) {
    if (!byId.has(e.id)) order.push(e.id);
    byId.set(e.id, e as ProjectedEvidenceLedgerEntry);
  }
  return order
    .map((id) => byId.get(id))
    .filter((e): e is ProjectedEvidenceLedgerEntry => e !== undefined);
}

/**
 * Plan 声明序（对齐 oracle / 手机）。仅在无 continue_run 时重排——有续派时保持
 * frame 序（与直播图一致，避免证人/红队复攻插队）。庭前无 continue：主辩先声明；
 * 旧 journal 若仍有附属 run 先执行，frame 序会插到主辩前，此处校正。
 */
function orderRunsForProjectedTurn(
  planIds: readonly string[],
  folded: readonly RunNode[],
): RunNode[] {
  if (folded.some((r) => r.continuesRunId)) return [...folded];
  const byId = new Map(folded.map((r) => [r.id, r]));
  const out: RunNode[] = [];
  const placed = new Set<string>();
  for (const id of planIds) {
    const r = byId.get(id);
    if (r && !placed.has(r.id)) {
      out.push(r);
      placed.add(r.id);
    }
  }
  for (const r of folded) {
    if (!placed.has(r.id)) out.push(r);
  }
  return out;
}

function orderAgentsForProjectedTurn(
  orderedRuns: readonly RunNode[],
  planAgentIds: readonly string[],
  folded: readonly AgentState[],
): AgentState[] {
  // 有续派时与 runs 同策略：保持 frame 序（folded 原序）。
  if (orderedRuns.some((r) => r.continuesRunId)) return [...folded];
  const byId = new Map(folded.map((a) => [a.id, a]));
  const out: AgentState[] = [];
  const placed = new Set<string>();
  for (const r of orderedRuns) {
    if (placed.has(r.agentId)) continue;
    const a = byId.get(r.agentId);
    if (a) {
      out.push(a);
      placed.add(a.id);
    }
  }
  for (const id of planAgentIds) {
    if (placed.has(id)) continue;
    const a = byId.get(id);
    if (a) {
      out.push(a);
      placed.add(a.id);
    }
  }
  for (const a of folded) {
    if (!placed.has(a.id)) out.push(a);
  }
  return out;
}
