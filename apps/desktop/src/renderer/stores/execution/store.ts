import { mergeEvidenceLedger } from "@/lib/evidenceLedger";
import { isGraphPerfEnabled, markGraphPerf } from "@/services/graphPerf";
import type {
  CoordinationWaitPayload,
  DebateNarrativeRound,
  DebatePretrialCompletedPayload,
  DebateResultPayload,
  DebateRoundPayload,
  DebateRoundStartedPayload,
  DeliveryStatusPayload,
  EvidenceLedgerEntry,
  ExecutionDetachedPayload,
  ProcessStep,
  RunPlanPayload,
  TeamSynthesisPreviewPayload,
  ToolUseProgressPayload,
} from "@/types/events";
import { turnStatusFromFinish } from "@agentcore/protocol-fold-kit";
import { create } from "zustand";
import { foldDebatePretrial, upsertDebateRound } from "./debate";
import type { DebatePretrialState } from "./debate";
import { type RunFrame, frameFromEvent } from "./frames";
import {
  ensureDelegateBatchStamps,
  mergePlanInto,
  planFromRunPlan,
} from "./plan";
import { projectExecution } from "./project";
import type {
  ExecutionJournal,
  ExecutionPlan,
  ExecutionStatus,
  UserInterjection,
} from "./types";
import { userInterjectionFromPayload } from "./userInterjection";

/**
 * True when every projected **worker** has left pending/running (图收口 by run 终态).
 * Captains are excluded — same rule as {@link hasUnsettledRuns}: ghost / append-turn
 * `kind=captain` rows must not pin the execution at `running`.
 */
function runsAllSettled(
  plan: ExecutionPlan,
  frames: RunFrame[],
  status: ExecutionStatus,
  debate: DebateResultPayload | null,
  debateRounds: DebateNarrativeRound[],
  crossExamEnabled: boolean,
  debateOpening: string | null,
): boolean {
  const exec = projectExecution(
    plan,
    frames,
    status,
    debate,
    debateRounds,
    crossExamEnabled,
    debateOpening,
  );
  const workers = exec.runs.filter((r) => r.kind !== "captain");
  if (workers.length === 0) return false;
  return workers.every((r) => r.status !== "pending" && r.status !== "running");
}

/**
 * The execution state of a single assistant message's turn — plan, frame
 * stream, playhead and cross-view focus. Keyed by assistant message id so every
 * past multi-agent turn in a conversation keeps its own graph: the live turn
 * streams frames into its message's slot, and a reloaded turn hydrates its slot
 * once from the persisted journal.
 */
export interface ExecutionRuntime {
  plan: ExecutionPlan | null;
  frames: RunFrame[];
  /** Number of frames to project. `null` = follow the live tail. */
  playhead: number | null;
  status: ExecutionStatus;
  /** 辩论收场产物（`debate_result` —— 回合级单事件，非 plan/非 frame）：到达即存此，
   * {@link projectExecution} 透传到 {@link Execution.debate}。null = 非辩论/未收场。 */
  debate: DebateResultPayload | null;
  /** 辩论逐轮叙事（`debate_round_started` / `debate_round` —— 回合级单事件，非 frame）：
   * 折叠累积于此，{@link projectExecution} 透传到 {@link Execution.debateRounds}。`[]` =
   * 非辩论/无逐轮事件。P2 起事件 DURABLE：重载由 hydrateFromJournal 以同一 fold 重建。 */
  debateRounds: DebateNarrativeRound[];
  /** 本场是否开启质询（`debate_round_started.cross_exam_enabled`，sticky OR）。缺字段 → false。 */
  crossExamEnabled: boolean;
  /** 主持人开场白（`debate_round_started.opening`，sticky 首个非空）。缺字段 → null。 */
  debateOpening: string | null;
  /** 庭前取证（`debate_pretrial_*`）：开赛后首轮前；null = 无 / 老 journal。 */
  debatePretrial: DebatePretrialState | null;
  /** 场级证据台账（`debate_pretrial_completed` / `debate_round` 的
   * `evidence_ledger_delta` 累积；收场由 `debate.evidence_ledger`
   * 权威覆盖）。驱动辩论徽章 `#eN` 溯源；不进 ProjectedTurn。 */
  evidenceLedger: EvidenceLedgerEntry[];
  /** Worker-scoped `tool_use_progress` (run_id present), keyed by run id. Transport-only —
   * merged onto agents at projection time; never journaled or replayed. */
  workerToolPhases: Record<string, { phase: string; toolName: string }>;
  /** Per-run ProcessStep[] from journal reload (`runs.run_processes`). Overlay replaces
   * splice-derived process so reopen matches live interleaving. null while live. */
  runProcesses: Record<string, ProcessStep[]> | null;
  /** CEO 协调模式 Phase 1：`team_synthesis_preview` 最新快照（同 key 保最新）。P2 起
   * DURABLE：重载由 hydrateFromJournal 取 journal 中最后一条重建。状态条已收成工具栏，
   * 不再挂合成草稿行。 */
  teamSynthesisPreview: TeamSynthesisPreviewPayload | null;
  /** CEO 协调等待（`coordination_wait`）：captain 空等团队事件。EPHEMERAL——仅 live
   * stream；waiting=false / 回合结束 / `execution_detached` 清除。状态条只报 n/m；
   * CEO 节点等待句写等谁，不挂已等秒数。 */
  coordinationWait: CoordinationWaitPayload | null;
  /** 执行转后台（`execution_detached`）：附着回合已收口、团队继续跑。EPHEMERAL——仅 live；
   * 驱动 StatusStrip「后台」徽标。n/m 与节点活体跟后续帧/相位，不冻在 stamp 快照。
   * `execution_completed` / 终态清除。 */
  executionDetached: ExecutionDetachedPayload | null;
  /** 交付状态（`delivery_status`，同 execution_id 保最新）：delegate 批次收尾的结构化交付
   * 对账（已交付/缺口/待用户操作）。DURABLE：重载由 hydrateFromJournal 取最后一条重建，
   * 驱动答复下方的交付状态卡。null = 本回合无对账（纯 prose 成功批次无声）。 */
  deliveryStatus: DeliveryStatusPayload | null;
  /** 用户插话（`user_interjection` · 经典/协调；同 interjectionId 保最新）。DURABLE。 */
  userInterjections: UserInterjection[];
  /**
   * Server-attested `message_end.outcome`. Product StatusStrip / arbitrator
   * consume this as `attestedKind`; local delivery bits are fallback only.
   */
  attestedOutcome: "ok" | "partial" | "paused" | "error" | null;
}

/**
 * Every mutator targets one assistant message's {@link ExecutionRuntime} by id.
 * SSE dispatch resolves the live turn's assistant message id and routes frames
 * there; view interactions (focus / playhead) pass the message id of the graph
 * subtree they belong to ({@link useExecutionScope}).
 */
interface ExecutionState {
  byId: Record<string, ExecutionRuntime>;

  startExecution: (plan: ExecutionPlan, messageId: string) => void;
  /**
   * Ingest a `run_plan` batch. The first batch of a turn starts a fresh
   * execution; a later batch with the *same* execution id (the adaptive D1′
   * case where the CEO delegates again) is merged in — new agents/runs are
   * appended and the frame stream is kept — so every batch stays on the graph
   * and timeline. A new turn produces a new assistant message (new slot), so
   * cross-turn batches never merge.
   */
  ingestPlan: (plan: ExecutionPlan, messageId: string) => void;
  clearExecution: (messageId: string) => void;
  recordFrame: (frame: RunFrame, messageId: string) => void;
  /** Append a rAF-coalesced batch of frames in ONE update (流式性能): the SSE ingest
   * buffers a frame's worth of `run_*_delta` and flushes them here so a token storm
   * triggers ≤60 store writes/projections per second instead of one per token. */
  recordFrames: (frames: RunFrame[], messageId: string) => void;
  /** Store a turn's debate 收场产物 (`debate_result`) on its slot; a no-plan slot
   * ignores it (stray fact). Sibling of {@link recordFrame} — one accrues the frame
   * stream, the other the debate brief/narrative (a回合级 one-shot, not a frame). */
  recordDebateResult: (debate: DebateResultPayload, messageId: string) => void;
  /** Fold one 逐轮叙事 update (`debate_round_started` → focus only; `debate_round` →
   * full) into the slot's {@link ExecutionRuntime.debateRounds} via {@link
   * upsertDebateRound}; a no-plan slot ignores it. Drives the进行中 per-round overlay
   * before {@link recordDebateResult}'s 收场 product lands. */
  recordDebateRound: (round: DebateNarrativeRound, messageId: string) => void;
  /** Merge one `debate_pretrial_completed` / `debate_round` `evidence_ledger_delta`
   * into the slot's live evidence ledger. */
  recordEvidenceLedgerDelta: (
    delta: EvidenceLedgerEntry[],
    messageId: string,
  ) => void;
  /** Sticky-OR 本场质询开关（`debate_round_started.cross_exam_enabled`）。 */
  recordCrossExamEnabled: (enabled: boolean, messageId: string) => void;
  /** Sticky 首个非空主持人开场白（`debate_round_started.opening`）；后续空串不覆盖。 */
  recordDebateOpening: (opening: string, messageId: string) => void;
  /** 折叠庭前取证事件（权威 = completed）。 */
  recordDebatePretrial: (
    type:
      | "debate_pretrial_started"
      | "debate_pretrial_orders"
      | "debate_pretrial_completed",
    payload: unknown,
    messageId: string,
  ) => void;
  /** Stamp a delegated worker's running-tool EXECUTION phase (`tool_use_progress` with
   * `run_id`). Transport-only — not a frame. */
  setWorkerToolPhase: (
    payload: ToolUseProgressPayload,
    messageId: string,
  ) => void;
  /** Clear a worker's live EXECUTION phase when its tool finishes (`tool_use_end`). */
  clearWorkerToolPhase: (runId: string, messageId: string) => void;
  /** Stamp the latest multi-worker team progress preview (`team_synthesis_preview`).
   * Live stamp (同 key 保最新); journal is DURABLE — hydrateFromJournal rebuilds it. */
  setTeamSynthesisPreview: (
    preview: TeamSynthesisPreviewPayload,
    messageId: string,
  ) => void;
  /** Stamp / clear CEO coordination wait (`coordination_wait`). Transport-only. */
  setCoordinationWait: (
    wait: CoordinationWaitPayload | null,
    messageId: string,
  ) => void;
  /** Stamp / clear background-running chrome (`execution_detached`). Transport-only. */
  setExecutionDetached: (
    detached: ExecutionDetachedPayload | null,
    messageId: string,
  ) => void;
  /** Stamp the latest delivery reconciliation (`delivery_status`, 同 execution_id 保最新).
   * Live stamp; journal is DURABLE — hydrateFromJournal rebuilds it. */
  setDeliveryStatus: (status: DeliveryStatusPayload, messageId: string) => void;
  /** Upsert a mid-flight user interjection (`user_interjection`, same id keeps latest). */
  upsertUserInterjection: (item: UserInterjection, messageId: string) => void;
  setStatus: (status: ExecutionStatus, messageId: string) => void;
  /** Stamp server-attested `message_end.outcome` onto the live / hydrated slot. */
  setAttestedOutcome: (
    outcome: "ok" | "partial" | "paused" | "error" | null,
    messageId: string,
  ) => void;
  setPlayhead: (index: number | null, messageId: string) => void;
  goLive: (messageId: string) => void;
  /**
   * Fold a persisted execution journal (`messages.runs`) into a message's slot,
   * reproducing the team graph a past multi-agent turn had — replayed through
   * the same fold as the live stream. When the slot already holds a plan,
   * applies only if the journal is strictly newer (more runs, else agents,
   * else frames); equal or older journals are left untouched so a re-render
   * or stale history fetch never rolls live state back.
   */
  hydrateFromJournal: (messageId: string, journal: ExecutionJournal) => void;
  /**
   * One-time migrate when `message_start` stamps `serverMessageId`: move the
   * execution slot from the client bubble id to the server turn id so pause and
   * resume share one key. Not a resume remount.
   */
  alignTurnKey: (fromId: string, toId: string) => void;
}

const EMPTY_EXEC: ExecutionRuntime = {
  plan: null,
  frames: [],
  playhead: null,
  status: "planning",
  debate: null,
  debateRounds: [],
  crossExamEnabled: false,
  debateOpening: null,
  debatePretrial: null,
  evidenceLedger: [],
  workerToolPhases: {},
  runProcesses: null,
  teamSynthesisPreview: null,
  coordinationWait: null,
  executionDetached: null,
  deliveryStatus: null,
  userInterjections: [],
  attestedOutcome: null,
};

const RUN_TERMINAL = new Set(["completed", "failed", "cancelled", "skipped"]);

/**
 * Journal settled a worker that the live slot still shows in-flight.
 * Ephemeral live frames (deltas / phases) often outnumber sparse journal
 * frames after detach — frames.length alone would refuse the catch-up and
 * leave nodes stuck on「思考中」while journal already has run_completed.
 */
function journalSettlesLiveWorker(
  cur: ExecutionRuntime,
  journalPlan: ExecutionPlan,
  journalFrames: RunFrame[],
): boolean {
  const curPlan = cur.plan;
  if (!curPlan) return false;
  const live = projectExecution(
    curPlan,
    cur.frames,
    cur.status,
    cur.debate,
    cur.debateRounds,
    cur.crossExamEnabled,
    cur.debateOpening,
  );
  const fromJournal = projectExecution(
    journalPlan,
    journalFrames,
    "running",
    cur.debate,
    cur.debateRounds,
    cur.crossExamEnabled,
    cur.debateOpening,
  );
  const liveById = new Map(live.runs.map((r) => [r.id, r]));
  for (const jr of fromJournal.runs) {
    if (jr.kind === "captain") continue;
    if (!RUN_TERMINAL.has(jr.status)) continue;
    const lr = liveById.get(jr.id);
    if (lr && (lr.status === "pending" || lr.status === "running")) {
      return true;
    }
  }
  return false;
}

/**
 * True when a journal-built plan/frames are strictly ahead of the in-memory
 * slot. Same execution id; lexicographic (runs → agents → frames), with a
 * terminal-lead override so missed live `run_completed` can still heal.
 * Equal or behind keeps memory so hydrate never rolls a live/SSE-ahead slot
 * back via a smaller plan or fewer frames alone.
 */
function journalIsNewerThan(
  cur: ExecutionRuntime,
  journalPlan: ExecutionPlan,
  journalFrames: RunFrame[],
): boolean {
  const curPlan = cur.plan;
  if (!curPlan) return true;
  if (curPlan.id !== journalPlan.id) return false;
  if (journalPlan.runs.length !== curPlan.runs.length) {
    return journalPlan.runs.length > curPlan.runs.length;
  }
  if (journalPlan.agents.length !== curPlan.agents.length) {
    return journalPlan.agents.length > curPlan.agents.length;
  }
  if (journalSettlesLiveWorker(cur, journalPlan, journalFrames)) return true;
  return journalFrames.length > cur.frames.length;
}

/** Map a persisted turn's `finish_reason` to the terminal execution status the
 * fold needs (a journal is only stored for finished turns). SSOT:
 * `@agentcore/protocol-fold-kit` (`turnStatusFromFinish`). */
function statusFromFinish(finishReason: string): ExecutionStatus {
  return turnStatusFromFinish(finishReason);
}

/**
 * The execution runtime of an assistant message, never undefined (empty
 * default). Use this for imperative reads (`getState`, tests); components
 * subscribe via {@link useProjectedExecution} / {@link useActiveExecField}
 * (scoped to the in-context message) so a conversation switch re-renders.
 */
export function execRuntime(
  state: ExecutionState,
  messageId: string | null | undefined,
): ExecutionRuntime {
  return (messageId ? state.byId[messageId] : undefined) ?? EMPTY_EXEC;
}

/**
 * True when the projected graph still has a **non-captain** run in pending/running
 * — the CEO's turn ended (message_end) but its team keeps running detached-hosted
 * in the background (coordination.turn_detached). The live handler holds the graph
 * at `running` instead of collapsing it to `completed`; the run-终态 reconcile in
 * {@link ExecutionState.recordFrame}/{@link ExecutionState.recordFrames} settles
 * it when the last worker's terminal frame lands (delivered via re-attach replay /
 * cross-turn append).
 *
 * Captain is excluded: its early `run_started` is often dropped (no plan yet), so
 * a still-pending captain after `end_turn` must not pin「正在生成汇总」forever when
 * every worker is already terminal. Extra append-turn captains are also ignored.
 * No plan or no worker runs → false (nothing in flight to wait on, so message_end
 * 照常收口). Sibling of the private `runsAllSettled` reconcile check — NOT its
 * exact negation (both are false on a 0-run / captain-only graph); both exclude
 * `kind=captain`.
 */
export function hasUnsettledRuns(runtime: ExecutionRuntime): boolean {
  if (!runtime.plan) return false;
  const exec = projectExecution(
    runtime.plan,
    runtime.frames,
    runtime.status,
    runtime.debate,
    runtime.debateRounds,
    runtime.crossExamEnabled,
    runtime.debateOpening,
  );
  return exec.runs.some(
    (r) =>
      r.kind !== "captain" &&
      (r.status === "pending" || r.status === "running"),
  );
}

export const useExecutionStore = create<ExecutionState>((set, get) => {
  /** Patch one message's runtime slice, lazily created from empty. */
  const patchExec = (
    messageId: string,
    update: (cur: ExecutionRuntime) => Partial<ExecutionRuntime> | null,
  ) =>
    set((state) => {
      const cur = state.byId[messageId] ?? EMPTY_EXEC;
      const patch = update(cur);
      if (patch === null) return {};
      return { byId: { ...state.byId, [messageId]: { ...cur, ...patch } } };
    });

  return {
    byId: {},

    startExecution: (plan, messageId) =>
      patchExec(messageId, () => ({
        plan: ensureDelegateBatchStamps(plan),
        frames: [],
        playhead: null,
        status: "running",
        debate: null,
        debateRounds: [],
        crossExamEnabled: false,
        debateOpening: null,
        debatePretrial: null,
        evidenceLedger: [],
        workerToolPhases: {},
        runProcesses: null,
        teamSynthesisPreview: null,
        coordinationWait: null,
        executionDetached: null,
        deliveryStatus: null,
        userInterjections: [],
        attestedOutcome: null,
      })),

    ingestPlan: (plan, messageId) => {
      const cur = execRuntime(get(), messageId).plan;
      // Different turn / first batch → fresh start (resets frames).
      if (!cur || cur.id !== plan.id) {
        get().startExecution(plan, messageId);
        return;
      }
      // Same execution → an incremental delegate batch: merge in unseen
      // agents/runs while keeping the existing frame stream and playhead.
      patchExec(messageId, () => ({
        plan: mergePlanInto(cur, plan),
        status: "running",
      }));
    },

    clearExecution: (messageId) =>
      patchExec(messageId, () => ({ ...EMPTY_EXEC })),

    // Frames only carry meaning inside an execution; ignore stray run/tool facts
    // from the single-agent path (no plan declared).
    recordFrame: (frame, messageId) => {
      patchExec(messageId, (cur) =>
        cur.plan ? { frames: [...cur.frames, frame] } : null,
      );
      // 跨回合同图追加：宿主卡不靠追加回合 message_end 收口，按 run 终态 reconcile。
      const rt = execRuntime(get(), messageId);
      if (
        rt.plan &&
        rt.status === "running" &&
        runsAllSettled(
          rt.plan,
          rt.frames,
          rt.status,
          rt.debate,
          rt.debateRounds,
          rt.crossExamEnabled,
          rt.debateOpening,
        )
      ) {
        get().setStatus("completed", messageId);
      }
    },

    recordFrames: (frames, messageId) => {
      const perfOn = isGraphPerfEnabled();
      const t0 = perfOn ? performance.now() : 0;
      patchExec(messageId, (cur) =>
        cur.plan && frames.length
          ? { frames: [...cur.frames, ...frames] }
          : null,
      );
      if (frames.length === 0) return;
      const rt = execRuntime(get(), messageId);
      if (
        rt.plan &&
        rt.status === "running" &&
        runsAllSettled(
          rt.plan,
          rt.frames,
          rt.status,
          rt.debate,
          rt.debateRounds,
          rt.crossExamEnabled,
          rt.debateOpening,
        )
      ) {
        get().setStatus("completed", messageId);
      }
      if (perfOn) {
        markGraphPerf("flush", performance.now() - t0, {
          batch: frames.length,
          frames: rt.frames.length,
          mid: messageId.slice(0, 8),
        });
      }
    },

    recordDebateResult: (debate, messageId) =>
      patchExec(messageId, (cur) =>
        cur.plan
          ? {
              debate,
              // 收场全量权威；缺字段（老 journal）保留 live 累积。
              ...(Array.isArray(debate.evidence_ledger)
                ? { evidenceLedger: debate.evidence_ledger }
                : {}),
            }
          : null,
      ),

    recordDebateRound: (round, messageId) =>
      patchExec(messageId, (cur) =>
        cur.plan
          ? { debateRounds: upsertDebateRound(cur.debateRounds, round) }
          : null,
      ),

    recordEvidenceLedgerDelta: (delta, messageId) =>
      patchExec(messageId, (cur) =>
        cur.plan && delta.length
          ? {
              evidenceLedger: mergeEvidenceLedger(cur.evidenceLedger, delta),
            }
          : null,
      ),

    /** Sticky-OR the场级质询开关（来自 debate_round_started.cross_exam_enabled）。 */
    recordCrossExamEnabled: (enabled, messageId) =>
      patchExec(messageId, (cur) =>
        cur.plan && enabled && !cur.crossExamEnabled
          ? { crossExamEnabled: true }
          : null,
      ),

    /** Sticky 首个非空主持人开场白（来自 debate_round_started.opening）；后续空串不覆盖。 */
    recordDebateOpening: (opening, messageId) =>
      patchExec(messageId, (cur) => {
        const trimmed = opening.trim();
        if (!cur.plan || !trimmed || cur.debateOpening) return null;
        return { debateOpening: trimmed };
      }),

    recordDebatePretrial: (type, payload, messageId) =>
      patchExec(messageId, (cur) => {
        if (!cur.plan) return null;
        const next = foldDebatePretrial(cur.debatePretrial, type, payload);
        return next === cur.debatePretrial ? null : { debatePretrial: next };
      }),

    setWorkerToolPhase: (payload, messageId) => {
      if (!payload.run_id) return;
      patchExec(messageId, (cur) => ({
        workerToolPhases: {
          ...cur.workerToolPhases,
          [payload.run_id as string]: {
            phase: payload.phase,
            toolName: payload.tool_name,
          },
        },
      }));
    },

    clearWorkerToolPhase: (runId, messageId) =>
      patchExec(messageId, (cur) => {
        if (!cur.workerToolPhases[runId]) return null;
        const { [runId]: _, ...rest } = cur.workerToolPhases;
        return { workerToolPhases: rest };
      }),

    setTeamSynthesisPreview: (preview, messageId) =>
      patchExec(messageId, (cur) =>
        cur.plan ? { teamSynthesisPreview: preview } : null,
      ),

    setCoordinationWait: (wait, messageId) =>
      patchExec(messageId, (cur) => {
        if (!cur.plan) return null;
        // Detached: n/m follows live execution.progress (run_*). Wait is
        // CEO-foreground chrome; late heartbeats must not revive a frozen stamp.
        if (cur.executionDetached != null) {
          return cur.coordinationWait == null
            ? null
            : { coordinationWait: null };
        }
        if (wait == null || wait.waiting === false) {
          return cur.coordinationWait == null
            ? null
            : { coordinationWait: null };
        }
        const prev = cur.coordinationWait;
        if (
          prev &&
          prev.waiting === wait.waiting &&
          prev.completed === wait.completed &&
          prev.total === wait.total &&
          prev.execution_id === wait.execution_id
        ) {
          return null;
        }
        return { coordinationWait: wait };
      }),

    setExecutionDetached: (detached, messageId) =>
      patchExec(messageId, (cur) => {
        if (!cur.plan) return null;
        if (detached == null) {
          return cur.executionDetached == null
            ? null
            : { executionDetached: null };
        }
        // Keep live worker tool chrome: the detached window is the longest
        // stretch of worker activity. Clearing phases here froze nodes/strip
        // at the stamp snapshot until execution_completed.
        // Drop coordinationWait: terminal/stopping no longer apply wait
        // heartbeats, so keeping the stamp froze the strip at wait n/m.
        return {
          executionDetached: detached,
          coordinationWait: null,
        };
      }),

    setDeliveryStatus: (status, messageId) =>
      // 可用性短问可在无 plan 的 CEO 回合复用对账发卡——勿再要求 cur.plan。
      patchExec(messageId, () => ({ deliveryStatus: status })),

    upsertUserInterjection: (item, messageId) =>
      // 经典 steer 无 run_plan；协调有 plan。二者都须可写 DURABLE 插话。
      patchExec(messageId, (cur) => {
        const list = [...cur.userInterjections];
        const idx = list.findIndex(
          (x) => x.interjectionId === item.interjectionId,
        );
        if (idx < 0) list.push(item);
        else list[idx] = item;
        return { userInterjections: list };
      }),

    setStatus: (status, messageId) =>
      patchExec(messageId, (cur) => {
        // Terminal / paused turns clear live wait chrome.
        // failed 保留 executionDetached：对话失败收口与「团队后台运行中」并陈。
        if (
          status === "completed" ||
          status === "cancelled" ||
          status === "paused"
        ) {
          return {
            status,
            coordinationWait: null,
            executionDetached: null,
          };
        }
        if (status === "failed") {
          return {
            status,
            coordinationWait: null,
          };
        }
        return cur.status === status ? null : { status };
      }),

    setAttestedOutcome: (outcome, messageId) =>
      patchExec(messageId, (cur) =>
        cur.attestedOutcome === outcome ? null : { attestedOutcome: outcome },
      ),

    setPlayhead: (index, messageId) =>
      patchExec(messageId, () => ({ playhead: index })),

    goLive: (messageId) => patchExec(messageId, () => ({ playhead: null })),

    hydrateFromJournal: (messageId, journal) =>
      set((state) => {
        let plan: ExecutionPlan | null = null;
        const frames: RunFrame[] = [];
        let debate: DebateResultPayload | null = null;
        let debateRounds: DebateNarrativeRound[] = [];
        let crossExamEnabled = false;
        let debateOpening: string | null = null;
        let debatePretrial: DebatePretrialState | null = null;
        let evidenceLedger: EvidenceLedgerEntry[] = [];
        let teamSynthesisPreview: TeamSynthesisPreviewPayload | null = null;
        let deliveryStatus: DeliveryStatusPayload | null = null;
        /** journal 内 `execution_completed.status`（若有）→ 覆盖 finishReason 投影。 */
        let fromExecutionCompleted: ExecutionStatus | null = null;
        let attestedOutcome: ExecutionRuntime["attestedOutcome"] = null;
        const userInterjections: UserInterjection[] = [];
        const interjectionIndex = new Map<string, number>();
        for (const event of journal.events) {
          if (event.type === "run_plan") {
            const next = planFromRunPlan(event.payload as RunPlanPayload);
            plan = plan
              ? mergePlanInto(plan, next)
              : ensureDelegateBatchStamps(next);
          } else if (event.type === "debate_result") {
            // 回合级单事件（非 frame）：直接捕获，回放与直播经同一 slot 渲染辩论视图。
            debate = event.payload as DebateResultPayload;
            if (Array.isArray(debate.evidence_ledger)) {
              evidenceLedger = debate.evidence_ledger;
            }
          } else if (event.type === "user_interjection") {
            const leaf = userInterjectionFromPayload(event.payload);
            if (!leaf) continue;
            const idx = interjectionIndex.get(leaf.interjectionId);
            if (idx === undefined) {
              interjectionIndex.set(
                leaf.interjectionId,
                userInterjections.length,
              );
              userInterjections.push(leaf);
            } else {
              userInterjections[idx] = leaf;
            }
          } else if (
            event.type === "debate_pretrial_started" ||
            event.type === "debate_pretrial_orders" ||
            event.type === "debate_pretrial_completed"
          ) {
            debatePretrial = foldDebatePretrial(
              debatePretrial,
              event.type,
              event.payload,
            );
            // 与 live SSE / debate_round 同路径：pretrial_completed delta 并入场级台账。
            if (event.type === "debate_pretrial_completed") {
              const p = event.payload as DebatePretrialCompletedPayload;
              if (p.evidence_ledger_delta?.length) {
                evidenceLedger = mergeEvidenceLedger(
                  evidenceLedger,
                  p.evidence_ledger_delta,
                );
              }
            }
          } else if (event.type === "debate_round_started") {
            // P2 DURABLE：刷新后从 journal 重建辩论进行态（与 live recordDebateRound 同 fold）。
            const p = event.payload as DebateRoundStartedPayload;
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
          } else if (event.type === "debate_round") {
            const p = event.payload as DebateRoundPayload;
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
            if (p.evidence_ledger_delta?.length) {
              evidenceLedger = mergeEvidenceLedger(
                evidenceLedger,
                p.evidence_ledger_delta,
              );
            }
          } else if (event.type === "team_synthesis_preview") {
            // P2 DURABLE：同 key 保最新（后写覆盖）；刷新后 StatusStrip 可重建。
            teamSynthesisPreview = event.payload as TeamSynthesisPreviewPayload;
          } else if (event.type === "delivery_status") {
            // DURABLE：同 execution_id 保最新（后写覆盖）；刷新后交付卡可读。
            deliveryStatus = event.payload as DeliveryStatusPayload;
          } else if (event.type === "execution_completed") {
            // DURABLE：execution 终态权威；缺省 completed（与 live handler 同口径）。
            const raw = (event.payload as { status?: string }).status;
            fromExecutionCompleted =
              raw === "cancelled" || raw === "failed" || raw === "completed"
                ? raw
                : "completed";
          } else if (event.type === "message_end") {
            const raw = (event.payload as { outcome?: unknown }).outcome;
            if (
              raw === "ok" ||
              raw === "partial" ||
              raw === "paused" ||
              raw === "error"
            ) {
              attestedOutcome = raw;
            }
          } else {
            const frame = frameFromEvent(event);
            if (frame) frames.push(frame);
          }
        }
        // No run_plan：经典单聊 / 可用性短问——无协作图骨架，仍恢复 DURABLE 插话
        // 与 deliveryStatus（刷新后 InterjectionTimeline / 交付卡可读）。
        if (!plan) {
          if (
            !deliveryStatus &&
            userInterjections.length === 0 &&
            attestedOutcome == null
          )
            return {};
          const cur = state.byId[messageId] ?? EMPTY_EXEC;
          return {
            byId: {
              ...state.byId,
              [messageId]: {
                ...cur,
                ...(deliveryStatus ? { deliveryStatus } : {}),
                ...(userInterjections.length > 0 ? { userInterjections } : {}),
                ...(attestedOutcome != null ? { attestedOutcome } : {}),
              },
            },
          };
        }
        const cur = state.byId[messageId] ?? EMPTY_EXEC;
        // Newer-wins: catch up after missed graph_append; never roll live back.
        if (!journalIsNewerThan(cur, plan, frames)) return {};
        // Detached mid-flight: captain may have finish_reason=stop while workers
        // still run — keep graph running + background stamp so soft refresh can
        // heal worker terminals without collapsing the strip to「团队完成」.
        const finishStatus =
          attestedOutcome === "paused"
            ? "paused"
            : (fromExecutionCompleted ??
              statusFromFinish(journal.finishReason));
        const provisional: ExecutionRuntime = {
          ...EMPTY_EXEC,
          plan,
          frames,
          status: "running",
          debate,
          debateRounds,
          crossExamEnabled,
          debateOpening,
        };
        // Only hold running+detached when healing a live background drive.
        // Finished-turn reload (no detached stamp) still follows finishReason /
        // execution_completed even if some journal runs look unsettled.
        const stillUnsettled =
          fromExecutionCompleted == null &&
          cur.executionDetached != null &&
          hasUnsettledRuns(provisional);
        return {
          byId: {
            ...state.byId,
            [messageId]: {
              plan,
              frames,
              playhead: null,
              status: stillUnsettled ? "running" : finishStatus,
              debate,
              debateRounds,
              crossExamEnabled,
              debateOpening,
              debatePretrial,
              evidenceLedger,
              workerToolPhases: {},
              runProcesses: journal.runProcesses ?? null,
              teamSynthesisPreview,
              coordinationWait: null,
              executionDetached: stillUnsettled
                ? (cur.executionDetached ?? null)
                : null,
              deliveryStatus,
              userInterjections,
              attestedOutcome,
            },
          },
        };
      }),

    alignTurnKey: (fromId, toId) =>
      set((state) => {
        if (!fromId || !toId || fromId === toId) return {};
        const from = state.byId[fromId];
        if (!from) return {};
        const to = state.byId[toId];
        // Prefer the destination if it already has a plan; only move when empty.
        if (to?.plan) {
          const { [fromId]: _, ...rest } = state.byId;
          return { byId: rest };
        }
        const { [fromId]: _, ...rest } = state.byId;
        return { byId: { ...rest, [toId]: from } };
      }),
  };
});
