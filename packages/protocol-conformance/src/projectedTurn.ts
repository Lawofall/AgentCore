// ProjectedTurn — the platform-neutral, serializable normalized turn state that is
// the conformance JUDGE (前端技术与架构 §十 SSE 与协议一致性; protocol-conformance.mdc). Each end
// implements `fold(events[]) → ProjectedTurn` and must match the backend-projected
// golden for every vector. Internal store shapes may differ (desktop's Zustand
// `Execution` vs mobile's reducer) — only this snapshot is asserted equal.
//
// Shape mirrors the rule's `{ messages, runs(tree), status, interactions[],
// cost }`, grounded in the two proven projections it must agree with: the desktop
// `projectExecution` fold (runs/agents/progress — stores/execution.ts) and the
// backend `EventSink._accumulate_process` fold (the single-agent process timeline —
// runtime/events.py). The backend oracle (runtime/conformance/projection.py) is the
// single source that emits the golden in exactly this shape.
//
// Hand-written on purpose — generating this from the oracle is REJECTED (rationale in
// 前端技术与架构 §十 SSE 与协议一致性): the oracle returns bare dicts, so generation would first have
// to restructure the one reference implementation everything else is judged against.
//
// Wire-shaped leaves (usage/cost/process step / arguments) are carried VERBATIM from
// the SSE payloads (snake_case kept) so the fold copies them without lossy transforms;
// the structural turn state around them is camelCase.

import type {
  ContextBlockWire,
  CostBreakdown,
  DebateNarrativeRound,
  DebateResultPayload,
  DeliveryStatusPayload,
  PlanRevisionKind,
  ProcessStep,
  RunDebrief,
  RunKind,
  Stance,
  TeamSynthesisPreviewPayload,
  UsageBreakdown,
  WorkerRunPhase,
} from "@agentcore/contract-types";
import {
  INTERACTION_KIND_WIRE,
  USER_INTERACTION_KIND_VALUES,
} from "@agentcore/contract-types";

export type {
  ContextBlockWire,
  CostBreakdown,
  DebateNarrativeRound,
  DebateResultPayload,
  DeliveryStatusPayload,
  PlanRevisionKind,
  ProcessStep,
  RunDebrief,
  TeamSynthesisPreviewPayload,
  UsageBreakdown,
  WorkerRunPhase,
};

/** Turn-level lifecycle, the single fold of desktop's ExecutionStatus + the chat
 * turn's own state. `running` until a gate (→ `paused`) or the terminal event:
 * message_end's finish_reason / an `error` event map to completed/failed/cancelled. */
export type TurnStatus =
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

/** Turn-level result quality, orthogonal to {@link TurnStatus} lifecycle.
 * `paused` is produced when the wire sets ``message_end.outcome=paused``
 * (CEO rate-limit continue). Gate pauses keep outcome null. */
export type TurnOutcome = "ok" | "partial" | "paused" | "error";

export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped";

/** `run_failed.failure_kind` — collaboration-graph face class (additive). */
export type RunFailureKind = "quality" | "format" | "model" | "call";

/** A web source consulted for the assistant message (citations event). */
export interface ProjectedCitation {
  url: string;
  title: string;
  snippet?: string;
  site?: string;
  /** Stable ledger id (`#rN`); P1 required when ledger channel is present. */
  id?: string;
  date?: string;
  /** Credibility tier (official / media / unknown / weak); optional, forward-compatible. */
  tier?: string;
  query?: string;
  deep_read?: boolean;
  registrant?: string;
  citable?: boolean;
}

/** Turn-level research evidence ledger entry (SSE `evidence_ledger` channel). */
export interface ProjectedEvidenceLedgerEntry {
  id: string;
  url?: string;
  title?: string;
  snippet?: string;
  site?: string;
  date?: string;
  tier?: string;
  query?: string;
  deep_read?: boolean;
  registrant?: string;
  citable?: boolean;
}

/** A delegated worker's live state (mirrors desktop AgentState, with the streamed
 * chunk arrays normalized to joined strings for a serializable snapshot). */
export interface ProjectedAgent {
  id: string;
  role: string;
  thinking: boolean;
  status: "idle" | "working" | "completed" | "error" | "cancelled";
  currentRunId: string | null;
  output: string;
  reasoning: string;
  toolProgress: { toolName: string; chars: number } | null;
}

/** A `checkpoint_after` pause on a run (plan_review, 结构化挂起 2a). `orphaned` =
 * 已失效 terminal (提问确认统一重构: the pending gate was invalidated by restart/recover). */
export interface ProjectedRunCheckpoint {
  status: "pending" | "resolved";
  decision:
    | "continue"
    | "adjust"
    | "stop"
    | "research_first"
    | "timeout"
    | "orphaned"
    | null;
}

/** 升级实时可见 / 阻塞式求决策: one escalation a worker raised mid-run via `escalate` (its
 * only upward channel). `question` is the self-contained ask; `assumption` is what the worker
 * proceeds on; `blocking` flags that a wrong guess would void its product. Folded onto its
 * {@link ProjectedRun} so every end's node carries the same signal.
 *
 * `status` is the lifecycle (阻塞式求决策): `raised` = non-blocking banner; `pending` =
 * blocking parked; `resolved` = answered; `assumed` = explicit 按假设继续; `timed_out` =
 * wall-clock miss. `assumed` and `timed_out` both leave `answer` null (worker falls
 * back to assumption) but must stay distinct — conflating them made「点了按假设继续」
 * look like system timeout. */
export type EscalationKind = "normal" | "scope" | "dep";

export interface RunEscalation {
  question: string;
  assumption: string;
  blocking: boolean;
  status: "raised" | "pending" | "resolved" | "assumed" | "timed_out";
  answer: string | null;
  /** escalate kind；旧向量缺字段时按 `normal`。 */
  kind?: EscalationKind;
  /** 谁在仲裁：user=经典可答卡；ceo=协调模式等主管。旧向量缺字段按 user。 */
  awaiting?: "user" | "ceo";
  /** 裁决方：user=用户直答；ceo=主管仲裁。旧向量缺字段按 user。 */
  arbitrated_by?: "user" | "ceo";
  /** 仅 arbitrated_by=ceo：是否经 ask_user 转交用户。 */
  via_user?: boolean;
  /**
   * 早停 / 打转收口标记（`validation_thrash` / `ceiling_backstop`）。
   * 缺省 = 真·边干边上报。旧向量无此字段。
   */
  source?: string;
}

/** 幕类型 = 能力档取用键（首批 multi_agent / debate）。 */
export type ActKind = "multi_agent" | "debate";

/** 幕授权来源（批 B）：推进卡 / 自动开辩 / 开工卡确认。 */
export type ActAuthorizedBy = "stage_card" | "auto" | "preview";

/** One act in an execution's act sequence (批 A1 幕契约). */
export interface ProjectedAct {
  actId: string;
  kind: ActKind;
  title: string | null;
  /** 本幕从宿主图哪个节点后长出；首幕 / 合成幕为 null。 */
  anchorRunId: string | null;
  /** 辩论幕授权来源；调研幕 / 旧向量缺省为 null。 */
  authorizedBy: ActAuthorizedBy | null;
}

/** One node in the team graph (mirrors desktop RunNode — stores/execution.ts). The
 * tree is encoded by `parentRunId`; `usage`/`cost` ride verbatim from run_completed. */
export interface ProjectedRun {
  id: string;
  agentId: string;
  task: string;
  status: RunStatus;
  dependsOn: string[];
  /** The worker's authored 结论 (`debrief.summary`) or "" — a scan line, not a truncation;
   * null until run_completed folds in. */
  outputSummary: string | null;
  /** 完工交接简报: the worker's structured wrap-up (结论/关键要点/关键假设/建议下一步), set by
   * run_completed when it authored one; null otherwise (辩手 / trivial worker / captain). */
  debrief: RunDebrief | null;
  durationMs: number | null;
  error: string | null;
  /** `run_failed.failure_kind` — face class; null when absent (old journals). */
  failureKind: RunFailureKind | null;
  /** `run_failed.product_landed` — files already on disk before failure. */
  productLanded: boolean | null;
  parentRunId: string | null;
  kind: RunKind;
  role: string | null;
  model: string | null;
  usage: UsageBreakdown | null;
  cost: CostBreakdown | null;
  stance: Stance | null;
  group: string | null;
  round: number;
  /** 同人续派 / 热修 / 辩论续写：现场根 run id（星型）；null = 冷开局. */
  continuesRunId: string | null;
  /**「计划已调整」轻痕迹 (设计 §7.2): set by `plan_revised` to "bind" (a late-bound
   * placeholder finalised from upstream evidence) or "steer" (a not-yet-run node re-steered
   * after a scope deviation) when the CEO autonomously adjusted this paused node mid-flight;
   * null otherwise. Drives the node's non-interrupting trace label; bind wins over steer. */
  revised: PlanRevisionKind | null;
  /** 回落换人：接手的原 run id；null = 普通委派。 */
  replacesRunId: string | null;
  /** 幕归属：该 run 所属幕的 actId（旧 run_plan 无 act → 合成 act-1）。 */
  actId: string;
  checkpoint: ProjectedRunCheckpoint | null;
  /** 收到的上下文 (上下文传递可视化): the structured context blocks this run was fed at
   * assembly time (from its `run_context` event), carried VERBATIM (wire-shaped
   * snake_case) — the SAME data the LLM saw. Empty until that event folds in (or for a
   * run whose opening was not block-assembled). */
  receivedContext: ContextBlockWire[];
  /** 升级实时可见: escalations this run raised via `escalate`, in fire order (`run_escalation`
   * events). Empty for the common case; non-empty drives every end's node ⚠️ badge + live
   * notice. Transport-only on the wire — the durable copy rides RunState.escalations. */
  escalations: RunEscalation[];
  /** Per-run 思考·正文·工具 timeline (对称 CEO ``process``). Empty until deltas/tools fold. */
  process: ProcessStep[];
  /**
   * Worker mid-flight activity phase (SSE `run_phase` / fold 单一源).
   * Absent on older journals / vectors without `run_phase`.
   * `queued` = `status: pending`; `skipped` = `status: skipped` (not carried here).
   * Cleared on terminal run frames.
   */
  phase?: WorkerRunPhase | null;
  /** When `phase === "tool"`, the tool currently running / being composed. */
  phaseTool?: string | null;
}

/** 团队便签墙 (§2.2 通): one note a worker broadcast to its CONCURRENT siblings (`team_note_posted`),
 * folded onto the turn for the team-notes panel. `kind` is `decision` (我定了 — others depend on it:
 * an interface / field name / format / naming), `heads_up` (提个醒 — a pitfall / discovery), or
 * `claim` (我领了 — a piece of work / file this worker is taking, so siblings don't duplicate it);
 * `runId` / `agentId` / `role` are the author (谁贴的); `ts` is epoch seconds. `noteId` is the stable
 * key (dedup). Carried in post order.
 *
 * 便签会过期 → supersession (§2.2): `status` is the lifecycle — `active`, or `superseded` (改写: a
 * later note replaced it) / `voided` (作废: retracted). `supersedes` is set only on an amendment
 * note (the `noteId` it 改写/作废s, else `null`), so the panel can strike a stale note and link an
 * amendment to its origin. */
export interface ProjectedTeamNote {
  noteId: string;
  runId: string;
  agentId: string;
  role: string;
  kind: string;
  text: string;
  ts: number | null;
  status: "active" | "superseded" | "voided";
  supersedes: string | null;
  /** `ceo` when seeded by the host before workers run; `inherited` when replayed from a parent run. */
  source?: "ceo" | "worker" | "inherited";
}

/** Mid-flight user interjection into a live turn (`user_interjection`).
 * Same `interjectionId` keeps latest `status`
 * (协调: received → injected → addressed / queued / failed;
 *  经典: received → injected | queued | failed). */
export interface ProjectedUserInterjectionAttachment {
  name: string;
  workspacePath?: string;
  binary?: boolean;
}

export interface ProjectedUserInterjectionMention {
  agentId: string;
  role: string;
}

export interface ProjectedUserInterjection {
  interjectionId: string;
  executionId: string;
  content: string;
  status: "received" | "injected" | "addressed" | "queued" | "failed" | string;
  note: string | null;
  attachments?: ProjectedUserInterjectionAttachment[];
  agentMentions?: ProjectedUserInterjectionMention[];
}

/** Interaction lifecycle status in the projected turn (提问确认统一重构 P3). */
export type InteractionStatus = "pending" | "resolved" | "orphaned";

/**
 * Kinds that pause the turn when status=pending (gate surface).
 *
 * Derived from the spec's `pausesTurn` so a new gating kind cannot land here
 * while mobile (which already derives) picks it up — that split is exactly the
 * desktop/mobile gate divergence this judge exists to catch.
 */
export const GATE_INTERACTION_KINDS = USER_INTERACTION_KIND_VALUES.filter(
  (kind) => INTERACTION_KIND_WIRE[kind].pausesTurn,
);

/** One user-facing interaction across its lifecycle — replaces the old single-slot
 * `pendingInteraction`. All 7 kinds appear (`client_tool` is fulfill-channel-only, not
 * in this union); status tracks pending|resolved|orphaned so reload after settle never
 * re-renders a false pending card. Multi-approval concurrency is first-class (array, not
 * last-write-wins). */
export type ProjectedInteraction =
  | {
      kind: "approval";
      id: string;
      status: InteractionStatus;
      toolCallId: string;
      toolName: string;
      arguments: Record<string, unknown>;
    }
  | {
      kind: "ask_user";
      id: string;
      status: InteractionStatus;
      question: string;
      context: string;
    }
  | {
      kind: "plan_review";
      id: string;
      status: InteractionStatus;
      runIds: string[];
    }
  | {
      kind: "team_preview";
      id: string;
      status: InteractionStatus;
      workerIds: string[];
      /** Resolved 修正：用户关闭的 run_id；缺省=无排除。 */
      excludedRunIds?: string[];
      /** Resolved 修正：写盘单向收紧（capability 仅 text_only）。 */
      writeCapabilityOverrides?: Array<{
        runId: string;
        capability: "text_only";
      }>;
      /** Resolved 修正：人盖 CEO 的队员模型（run_id → 三元组）。 */
      modelOverrides?: Record<
        string,
        { model: string; origin?: string; provider_id?: string }
      >;
    }
  | {
      kind: "escalation";
      id: string;
      status: InteractionStatus;
      runId: string;
      agentId: string;
      question: string;
      assumption: string;
      awaiting?: "user" | "ceo";
    }
  | {
      kind: "stage_card";
      id: string;
      status: InteractionStatus;
      motion: string;
      sides: Array<{ key: string; name: string; stance: string }>;
      form: string;
      rationale: string;
      factPointers: string[];
      thorough: boolean;
      maxRounds: number;
      note: string | null;
    };

/** 庭前取证投影（`debate_pretrial_*` 折叠；权威=completed）。 */
export interface DebatePretrialProjection {
  status: "running" | "done" | "skipped" | "degraded" | string;
  thorough: boolean;
  skipReason: string | null;
  sides: Array<{ key: string; name: string }>;
  orders: Array<{
    side_key: string;
    tasks: Array<{ query: string; purpose?: string }>;
    source: string;
  }>;
  evidenceLedgerCount: number;
  fallbackSelfSearch: boolean;
  evidenceReady: boolean;
  /**
   * 取证完整度：full / partial / empty（权威=completed）。
   * 缺字段（旧 journal / running）= 未知，勿默认 empty。
   */
  completeness?: "full" | "partial" | "empty" | string;
  /**
   * 明确 incomplete 字段时才有值；缺则未知（勿用 completeness 缺省推 incomplete）。
   */
  incomplete?: boolean;
  /** 外证计划 mode：生产仅 skip。 */
  externalEvidenceMode?: "skip" | string | null;
  /** 外证跳过原因（evidence_pack_full / evidence_pack_partial / no_pack / fast / …）。 */
  externalEvidenceReason?: string | null;
}

/** Turn-level structured error from the transport ``error`` SSE event (reload face
 * authority when content is empty). Null when the turn never emitted ``error``. */
export interface ProjectedTurnError {
  code: string;
  message: string;
}

export interface ProjectedTurn {
  status: TurnStatus;
  /** message_end.finish_reason (end_turn / max_rounds / degraded / unproductive /
   * error / cancelled), or null while the turn is still streaming. */
  finishReason: string | null;
  /**
   * Turn-level result quality (`message_end.outcome` or aggregated from
   * `delivery_status=partial` / `run_failed.product_landed` /
   * `tool_use_end.partial_failure`). Null while {@link status} is `running`,
   * and on a reserved pause close this wave.
   */
  outcome: TurnOutcome | null;
  /**
   * Latest SSE ``error`` payload for this turn (code + user-facing message).
   * Empty-failure face authority on live/reload when ``content`` is empty —
   * see {@link hasProjectedFailureFace}. Null when no ``error`` event fired.
   */
  error: ProjectedTurnError | null;
  /** The assistant bubble: the CEO captain's reply text + thinking (always, even in
   * a multi-agent turn where the captain speaks above the team graph). */
  content: string;
  reasoning: string;
  /** 收到的上下文 · CEO 侧 (上下文传递可视化, 通道①): the structured context the CEO captain
   * was fed at assembly time — `system` (本回合系统提示，决策②默认隐藏) / `history` / `request`
   * — from its `run_context` event (run_started kind=`captain`). Turn-level, NOT a graph
   * node: the captain is the bubble above the graph, so this shows on EVERY turn (pure chat
   * included), not only when it delegates. Empty until that event folds in. */
  captainContext: ContextBlockWire[];
  /** Single-agent 思考·正文·工具 inline timeline. Empty for a multi-agent turn (the
   * team graph carries the activity instead — parity with EventSink.process_timeline
   * returning None once run_plan fired). */
  process: ProcessStep[];
  citations: ProjectedCitation[];
  /** 回合调研台账（`evidence_ledger` 通道）：delta 累积 / entries 权威覆盖。非调研恒 `[]`。 */
  evidenceLedger: ProjectedEvidenceLedgerEntry[];
  /** 成稿实际引用的台账 id 集（P2 投影钩子；settle 旁路字段）。 */
  citedIds: string[];
  /** Team graph (empty for a single-agent turn). */
  agents: ProjectedAgent[];
  runs: ProjectedRun[];
  /** 幕序列（批 A1）：旧 run_plan 无 act → fold 合成单幕 act-1；无协作图时 `[]`。 */
  acts: ProjectedAct[];
  /** Derived from run states (terminal-completed over total), cumulative across
   * multi-batch delegates — never the per-batch run_progress counters. */
  progress: { completed: number; total: number };
  /** Full interaction inventory (7 kinds × pending|resolved|orphaned). Replaces the
   * legacy single-slot `pendingInteraction` (P3 breaking). */
  interactions: ProjectedInteraction[];
  /** Turn total from message_end.cost (回合总账); null until the turn ends or when no
   * turn ran (error/not-found paths). */
  cost: CostBreakdown | null;
  /** The structured product of a 辩论 that concluded this turn (the `debate_result`
   * event), carried VERBATIM (snake_case kept) — the decision brief + clash
   * narrative the debate view renders, keyed to the graph's debater runs by
   * `run_id`. Null for a turn that ran no debate. */
  debate: DebateResultPayload | null;
  /** 辩论进行中的逐轮叙事（`debate_round_started` / `debate_round` 折叠累积）：让前端进行中
   * 就叠出主持人逐轮焦点 / 小结 / 裁判，而非干等 {@link debate} 收场。P2 DURABLE——落 journal，
   * 刷新后 hydrate/fold 重建；收场后全量叙事线亦在 {@link debate}。非辩论恒 `[]`。 */
  debateRounds: DebateNarrativeRound[];
  /** 庭前取证（`debate_pretrial_*`）：开赛后首轮前；null 当无 / 老 journal。 */
  debatePretrial: DebatePretrialProjection | null;
  /** 本场是否开启质询（`debate_round_started.cross_exam_enabled`）：首轮开场即达。缺字段 /
   * 老 journal → `false`（UI 回退「正在小结…」）。 */
  crossExamEnabled: boolean;
  /** 主持人开场白（`debate_round_started.opening`）：仅首轮携带；sticky 取第一个非空，不被后续
   * 覆盖。收场 {@link debate}.opening 仍是权威。缺字段 / 老 journal → `null`。 */
  debateOpening: string | null;
  /** 协调模式团队进展预览（`team_synthesis_preview`，同 key 保最新）：P2 DURABLE。null 当无。 */
  teamSynthesisPreview: TeamSynthesisPreviewPayload | null;
  /** 交付状态（`delivery_status`，同 execution_id 保最新）：delegate 批次收尾的结构化交付
   * 对账——已交付文件 / 缺口 / 待用户操作（能力闸门与交付诚实性）。DURABLE，刷新后交付状态卡
   * 重建。null 当无（纯 prose 成功批次保持无声）。 */
  deliveryStatus: DeliveryStatusPayload | null;
  /** 预检警告（`turn_warning`）：P2 DURABLE；刷新后横幅重建。null 当无。 */
  turnWarning: string | null;
  /** 裸聊写盘自动建的云文件夹（`auto_folder_created`，双模式工作区 §5.4 裸聊行）：
   * 告知落点的轻提示，DURABLE，刷新后仍在。`name` 是建桌那一刻的名字（用户可当场改名，
   * 客户端按 `folderId` 取现名）。null 当本回合没建。 */
  autoFolder: { folderId: string; name: string } | null;
  /** 团队便签墙 (§2.2 通): the notes workers broadcast to their siblings this turn (`team_note_posted`),
   * in post order. Journaled, so it replays on reload. Empty for a turn with no team notes. */
  teamNotes: ProjectedTeamNote[];
  /** 协调中用户插话（`user_interjection`，同 interjectionId 保最新 status）。Empty when none. */
  userInterjections: ProjectedUserInterjection[];
}
