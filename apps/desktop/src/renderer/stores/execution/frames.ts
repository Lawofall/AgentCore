import { resolveToolEndStatus } from "@/lib/channelRedirect";
import type {
  AskQuestion,
  BatchMetricsPayload,
  CheckpointDecision,
  ContextBlockWire,
  EscalationRequiredPayload,
  EscalationResolvedPayload,
  PlanReviewRequiredPayload,
  PlanReviewResolvedPayload,
  PlanRevisedPayload,
  PlanRevisionKind,
  ResetReason,
  RunCancelledPayload,
  RunCompletedPayload,
  RunContextPayload,
  RunEscalationPayload,
  RunFailedPayload,
  RunKind,
  RunOutputDeltaPayload,
  RunOutputResetPayload,
  RunPhasePayload,
  RunProgressPayload,
  RunReasoningDeltaPayload,
  RunSkippedPayload,
  RunStartedPayload,
  RunToolProgressPayload,
  SSEEvent,
  Stance,
  TeamNotePostedPayload,
  ToolDisplay,
  ToolUseEndPayload,
  ToolUseStartPayload,
  WorkerRunPhase,
} from "@/types/events";
import type { BatchMetricsSnapshot } from "./types";

/**
 * One recorded run-level fact. The frame stream is append-only and is the
 * single source of truth for the collaboration graph — mirroring the backend
 * Turn Journal. "Live" is simply playhead = end-of-stream; "replay" is any
 * earlier playhead. Both render through the same {@link projectExecution} fold,
 * so there is no second code path to keep in sync.
 */
export type RunFrame =
  | {
      t: number;
      kind: "run_started";
      agentId: string;
      runId: string;
      // `runKind` (not `kind`) because `kind` is this union's discriminant; it
      // carries the wire `kind` (captain/agent).
      parentRunId: string | null;
      runKind: RunKind;
      /** 同人接续现场根；undefined/null = 冷开局。 */
      continuesRunId?: string | null;
      // 乙 wire 携 round/stance/side_key (单一轮次投影): undefined on ordinary / hot-fix starts.
      stance?: Stance;
      group?: string;
      round?: number;
      sideKey?: string;
      // 冷回落接手: mid-flight `_redir` spawn; undefined/null on ordinary / continuation starts.
      replacesRunId?: string | null;
    }
  | {
      t: number;
      kind: "run_context";
      runId: string;
      // 收到的上下文 (上下文传递可视化): the wire ContextBlocks this run was fed.
      blocks: ContextBlockWire[];
    }
  | {
      t: number;
      kind: "run_output_delta";
      runId: string;
      agentId: string;
      delta: string;
      /** attach 增量重放的帧级替换：`delta` 是这一路末尾未闭合块的全文，换块而非追加。
       * 直播帧永不带（缺省 = 追加）。 */
      replace?: boolean;
    }
  // 草稿丢弃的 worker 对偶（content_reset 之于 CEO）：清这个 worker 已累积的草稿产出，
  // 重写版从干净态重累积（reasoning 保留）。reason 决定是否留痕：仅 finish_guard
  // （交付前核验回炉）折 rework chip / didRework，retry / narration 等不留痕。
  | {
      t: number;
      kind: "run_output_reset";
      runId: string;
      agentId: string;
      reason: ResetReason;
    }
  | {
      t: number;
      kind: "run_reasoning_delta";
      runId: string;
      agentId: string;
      delta: string;
      /** 见 `run_output_delta.replace`。 */
      replace?: boolean;
    }
  | {
      t: number;
      kind: "run_tool_progress";
      agentId: string;
      toolName: string;
      chars: number;
    }
  | {
      // Worker mid-flight activity phase (`run_phase`). EPHEMERAL on the wire;
      // folded for live + conformance vectors (reload falls back to status).
      t: number;
      kind: "run_phase";
      runId: string;
      agentId: string;
      phase: WorkerRunPhase;
      toolName?: string;
    }
  | {
      t: number;
      kind: "run_completed";
      runId: string;
      agentId: string;
      outputSummary: string;
      outputFiles?: string[];
      // 完工交接简报: the worker's structured wrap-up; absent when it authored none.
      debrief?: import("@/types/events").RunDebrief;
      durationMs: number;
      // Cost-ledger fields from `run_completed` (§7.3B payroll). Optional so a
      // frame without them (older streams / a journal replay that lacks cost)
      // still projects — the run simply carries no priced cost.
      role?: string;
      model?: string;
      usage?: import("@/types/events").UsageBreakdown;
      cost?: import("@/types/events").CostBreakdown;
    }
  | {
      t: number;
      kind: "run_failed";
      runId: string;
      agentId: string;
      error: string;
      /** Additive face class; absent on old journals. */
      failureKind?: import("@/types/events").RunFailureKind;
      /** Files already on disk before failure; absent on old journals. */
      productLanded?: boolean | null;
      /** `run_failed.error_code` — desktop-local; not projected to golden. */
      errorCode?: string | null;
      /** `run_failed.retryable` — desktop-local; not projected to golden. */
      retryable?: boolean | null;
      /** `run_failed.retry_after` seconds — desktop-local; not projected to golden. */
      retryAfter?: number | null;
      // 完工交接简报: a contract-missing run's authored wrap-up; absent for infra failures.
      debrief?: import("@/types/events").RunDebrief;
    }
  | {
      // 跑一半改方向 / 整轮停止: interrupted mid-flight (orthogonal to run_failed).
      t: number;
      kind: "run_cancelled";
      runId: string;
      agentId: string;
      // 从契约派生：后端新增取值（如 user_stop）时编译期就逼消费方处理，避免帧类型漏跟。
      reason: RunCancelledPayload["reason"];
    }
  | {
      // 级联跳过 / graceful abort: node never ran — materialised SKIPPED.
      t: number;
      kind: "run_skipped";
      runId: string;
      agentId: string;
      reason: "cascade" | "abort";
    }
  | { t: number; kind: "run_progress"; completed: number; total: number }
  | {
      // 调度埋点量化 (深层诊断指标): a WaveScheduler segment's snapshot, folded onto
      // Execution.batches for 诊断模式 (run detail). Carries no run_id — it is execution-level.
      t: number;
      kind: "batch_metrics";
      metrics: BatchMetricsSnapshot;
    }
  | {
      t: number;
      kind: "run_escalation";
      runId: string;
      agentId: string;
      question: string;
      assumption: string;
      blocking: boolean;
      /** 统一时间线二期 D6: raised 轻行幂等键（桌面填入 RunEscalation.id；golden 不加）。 */
      escalationId: string;
      escalationKind: import("./types").EscalationKind;
      /** Wire `source`（桌面本地；ProjectedTurn 不加）。旧流缺字段 → undefined。 */
      source?: string;
    }
  | {
      // 阻塞式求决策: a worker SUSPENDED on a blocking escalate, awaiting the user.
      t: number;
      kind: "escalation_required";
      // The interaction id the EscalationCard resolves against (POST …/interactions/{id}).
      escalationId: string;
      runId: string;
      agentId: string;
      question: string;
      assumption: string;
      escalationKind: import("./types").EscalationKind;
      // 结构化升级: optional structured forks (同 ask_user 的 questions) the card renders. The
      // builder always sets it (`?? []`); optional so hand-built fixtures may omit it.
      questions?: AskQuestion[];
      awaiting?: "user" | "ceo";
      /** Wire `browser_login` — 登录等待 escalate；缺省 false。 */
      browserLogin?: boolean;
      /** Wire `ownership_paths` — 写权冲突结构化裁决。 */
      ownershipPaths?: string[];
      lockOwnerRunId?: string;
      /** Wire `timeout_seconds` — 运维配置的等待上限；缺省 = 无限期等（默认部署）。 */
      timeoutSeconds?: number;
    }
  | {
      // 阻塞式求决策 settlement.
      t: number;
      kind: "escalation_resolved";
      /** Match the pending card by id (not "first pending"). */
      escalationId: string;
      runId: string;
      agentId: string;
      status: "resolved" | "assumed" | "timed_out";
      answer: string;
      arbitrated_by?: "user" | "ceo";
      via_user?: boolean;
    }
  | {
      t: number;
      kind: "tool_use_start";
      toolCallId: string;
      toolName: string;
      arguments: Record<string, unknown>;
      // The delegated worker run this call belongs to; absent/"" for the captain's
      // own calls. Lets the fold file concurrent workers' calls onto the right run
      // instead of the first-running one (workers share the top-level tool stream).
      runId?: string;
    }
  | {
      t: number;
      kind: "tool_use_end";
      toolCallId: string;
      result: string;
      display?: ToolDisplay | null;
      status: "success" | "error" | "redirect";
      /** Product failure face (`tool_use_end.failure`); absent on success / old journals. */
      failure?: import("@/types/events").ToolFailure;
    }
  | {
      t: number;
      kind: "plan_review_required";
      checkpointId: string;
      // The just-completed step run ids this pause gates on (the badge targets).
      runIds: string[];
    }
  | {
      t: number;
      kind: "plan_review_resolved";
      checkpointId: string;
      decision: CheckpointDecision;
    }
  | {
      // 「计划已调整」轻痕迹 (设计 §7.2): the CEO autonomously re-bound / re-steered
      // paused nodes via replan. Each entry tags an affected node's graph trace.
      t: number;
      kind: "plan_revised";
      revisions: { runId: string; revisionKind: PlanRevisionKind }[];
    }
  | {
      // 团队便签墙 (§2.2 通): a worker broadcast a one-line decision / heads-up to its
      // CONCURRENT siblings via post_note. Turn-level (NOT run-scoped onto a node) — folds
      // onto Execution.teamNotes. Journaled, so it replays on reload like any frame.
      // `noteKind` (not `kind`) because `kind` is this union's discriminant; it carries the
      // wire note kind (decision / heads_up / claim). 便签会过期 → supersession (§2.2): an amendment
      // carries `supersedes` (the noteId it 改写/作废s) + `supersedeMode` (update / void).
      t: number;
      kind: "team_note_posted";
      noteId: string;
      runId: string;
      agentId: string;
      role: string;
      noteKind: string;
      text: string;
      ts: number | null;
      supersedes: string | null;
      supersedeMode: "update" | "void" | null;
      source?: "ceo" | "worker" | "inherited";
    };

/** Wall-clock time of a wire event (ms), used to label timeline frames. The
 * journal stores the same ISO timestamp the live stream carried, so replay and
 * live label frames identically. */
function frameTimeOf(event: SSEEvent): number {
  const parsed = Date.parse(event.timestamp);
  return Number.isNaN(parsed) ? Date.now() : parsed;
}

/** Map a journaled run/tool SSE event to a {@link RunFrame}, or null for events
 * that are not frames (e.g. `run_plan`). The single event→frame mapping shared
 * by the live SSE dispatch and journal replay, so there is one fold, not two. */
export function frameFromEvent(event: SSEEvent): RunFrame | null {
  const t = frameTimeOf(event);
  switch (event.type) {
    case "run_started": {
      const p = event.payload as RunStartedPayload;
      return {
        t,
        kind: "run_started",
        agentId: p.agent_id,
        runId: p.run_id,
        parentRunId: p.parent_run_id,
        runKind: p.kind,
        continuesRunId: p.continues_run_id ?? null,
        stance: p.stance,
        group: p.group,
        round: p.round,
        sideKey: p.side_key,
        replacesRunId: p.replaces_run_id ?? null,
      };
    }
    case "run_context": {
      const p = event.payload as RunContextPayload;
      return {
        t,
        kind: "run_context",
        runId: p.run_id,
        blocks: p.blocks,
      };
    }
    case "run_output_delta": {
      const p = event.payload as RunOutputDeltaPayload;
      return {
        t,
        kind: "run_output_delta",
        runId: p.run_id,
        agentId: p.agent_id,
        delta: p.delta,
        ...(p.replace ? { replace: true as const } : {}),
      };
    }
    case "run_output_reset": {
      const p = event.payload as RunOutputResetPayload;
      return {
        t,
        kind: "run_output_reset",
        runId: p.run_id,
        agentId: p.agent_id,
        reason: p.reason,
      };
    }
    case "run_reasoning_delta": {
      const p = event.payload as RunReasoningDeltaPayload;
      return {
        t,
        kind: "run_reasoning_delta",
        runId: p.run_id,
        agentId: p.agent_id,
        delta: p.delta,
        ...(p.replace ? { replace: true as const } : {}),
      };
    }
    case "run_tool_progress": {
      const p = event.payload as RunToolProgressPayload;
      return {
        t,
        kind: "run_tool_progress",
        agentId: p.agent_id,
        toolName: p.tool_name,
        chars: p.chars,
      };
    }
    case "run_phase": {
      const p = event.payload as RunPhasePayload;
      const phase = p.phase;
      if (
        phase !== "thinking" &&
        phase !== "tool" &&
        phase !== "waiting_children" &&
        phase !== "winding_down"
      ) {
        return null;
      }
      return {
        t,
        kind: "run_phase",
        runId: p.run_id,
        agentId: p.agent_id,
        phase,
        toolName: phase === "tool" ? p.tool_name : undefined,
      };
    }
    case "run_completed": {
      const p = event.payload as RunCompletedPayload;
      return {
        t,
        kind: "run_completed",
        runId: p.run_id,
        agentId: p.agent_id,
        outputSummary: p.output_summary,
        outputFiles: p.output_files ?? [],
        debrief: p.debrief,
        durationMs: p.duration_ms,
        role: p.role,
        model: p.model,
        usage: p.usage,
        cost: p.cost,
      };
    }
    case "run_failed": {
      const p = event.payload as RunFailedPayload;
      return {
        t,
        kind: "run_failed",
        runId: p.run_id,
        agentId: p.agent_id,
        error: p.error,
        failureKind: p.failure_kind,
        productLanded: p.product_landed ?? null,
        errorCode: p.error_code ?? null,
        retryable: p.retryable ?? null,
        retryAfter: p.retry_after ?? null,
        debrief: p.debrief,
      };
    }
    case "run_cancelled": {
      const p = event.payload as RunCancelledPayload;
      return {
        t,
        kind: "run_cancelled",
        runId: p.run_id,
        agentId: p.agent_id,
        reason: p.reason,
      };
    }
    case "run_skipped": {
      const p = event.payload as RunSkippedPayload;
      return {
        t,
        kind: "run_skipped",
        runId: p.run_id,
        agentId: p.agent_id,
        reason: p.reason,
      };
    }
    case "run_progress": {
      const p = event.payload as RunProgressPayload;
      return {
        t,
        kind: "run_progress",
        completed: p.completed,
        total: p.total,
      };
    }
    case "batch_metrics": {
      const p = event.payload as BatchMetricsPayload;
      return {
        t,
        kind: "batch_metrics",
        metrics: {
          nodes: p.nodes,
          width: p.width,
          peakRunning: p.peak_running,
          wallMs: p.wall_ms,
          busyMs: p.busy_ms,
          slotStarved: p.slot_starved,
          completed: p.completed,
          failed: p.failed,
          skipped: p.skipped,
          bindBoundaries: p.bind_boundaries,
          scopeBoundaries: p.scope_boundaries,
          checkpointBoundaries: p.checkpoint_boundaries,
          escalations: p.escalations,
          scopeEscalations: p.scope_escalations,
          timeline: (p.timeline ?? []).map((n) => ({
            runId: n.run_id,
            startMs: n.start_ms,
            endMs: n.end_ms,
            outcome: n.outcome,
          })),
        },
      };
    }
    case "run_escalation": {
      const p = event.payload as RunEscalationPayload;
      const source =
        typeof p.source === "string" && p.source.trim()
          ? p.source.trim()
          : undefined;
      return {
        t,
        kind: "run_escalation",
        runId: p.run_id,
        agentId: p.agent_id,
        question: p.question,
        assumption: p.assumption,
        blocking: p.blocking,
        escalationId: p.escalation_id ?? "",
        escalationKind:
          p.kind === "scope" || p.kind === "dep" ? p.kind : "normal",
        ...(source ? { source } : {}),
      };
    }
    case "escalation_required": {
      const p = event.payload as EscalationRequiredPayload;
      const paths = Array.isArray(p.ownership_paths)
        ? p.ownership_paths.filter(
            (x): x is string => typeof x === "string" && x.trim().length > 0,
          )
        : [];
      return {
        t,
        kind: "escalation_required",
        escalationId: p.escalation_id,
        runId: p.run_id,
        agentId: p.agent_id,
        question: p.question,
        assumption: p.assumption,
        escalationKind:
          p.kind === "scope" || p.kind === "dep" ? p.kind : "normal",
        questions: p.questions ?? [],
        awaiting: p.awaiting === "ceo" ? "ceo" : "user",
        ...(p.browser_login === true ? { browserLogin: true as const } : {}),
        ...(paths.length > 0 ? { ownershipPaths: paths } : {}),
        ...(typeof p.lock_owner_run_id === "string" &&
        p.lock_owner_run_id.trim()
          ? { lockOwnerRunId: p.lock_owner_run_id.trim() }
          : {}),
        ...(typeof p.timeout_seconds === "number" && p.timeout_seconds > 0
          ? { timeoutSeconds: p.timeout_seconds }
          : {}),
      };
    }
    case "escalation_resolved": {
      const p = event.payload as EscalationResolvedPayload;
      const raw = p.status as string;
      const status: "resolved" | "assumed" | "timed_out" =
        raw === "resolved" || raw === "assumed" || raw === "timed_out"
          ? raw
          : "timed_out";
      return {
        t,
        kind: "escalation_resolved",
        escalationId: p.escalation_id,
        runId: p.run_id,
        agentId: p.agent_id,
        status,
        answer: p.answer,
        ...(p.arbitrated_by === "user" || p.arbitrated_by === "ceo"
          ? { arbitrated_by: p.arbitrated_by }
          : {}),
        ...(p.arbitrated_by === "ceo" && p.via_user != null
          ? { via_user: p.via_user }
          : {}),
      };
    }
    case "tool_use_start": {
      const p = event.payload as ToolUseStartPayload;
      return {
        t,
        kind: "tool_use_start",
        toolCallId: p.tool_call_id,
        toolName: p.tool_name,
        arguments: p.arguments,
        runId: p.run_id ?? "",
      };
    }
    case "tool_use_end": {
      const p = event.payload as ToolUseEndPayload;
      return {
        t,
        kind: "tool_use_end",
        toolCallId: p.tool_call_id,
        result: p.result,
        display: p.display ?? null,
        status: resolveToolEndStatus(p.status, p.failure),
        ...(p.failure != null ? { failure: p.failure } : {}),
      };
    }
    case "plan_review_required": {
      const p = event.payload as PlanReviewRequiredPayload;
      return {
        t,
        kind: "plan_review_required",
        checkpointId: p.checkpoint_id,
        runIds: (p.steps ?? []).map((s) => s.run_id),
      };
    }
    case "plan_review_resolved": {
      const p = event.payload as PlanReviewResolvedPayload;
      return {
        t,
        kind: "plan_review_resolved",
        checkpointId: p.checkpoint_id,
        decision: p.decision,
      };
    }
    case "plan_revised": {
      const p = event.payload as PlanRevisedPayload;
      return {
        t,
        kind: "plan_revised",
        revisions: p.revisions.map((r) => ({
          runId: r.run_id,
          revisionKind: r.kind,
        })),
      };
    }
    case "team_note_posted": {
      const p = event.payload as TeamNotePostedPayload;
      return {
        t,
        kind: "team_note_posted",
        noteId: p.note_id,
        runId: p.run_id,
        agentId: p.agent_id,
        role: p.role,
        noteKind: p.kind,
        text: p.text,
        ts: p.ts,
        supersedes: p.supersedes ?? null,
        supersedeMode: p.supersede_mode ?? null,
        source: p.source,
      };
    }
    default:
      return null;
  }
}
