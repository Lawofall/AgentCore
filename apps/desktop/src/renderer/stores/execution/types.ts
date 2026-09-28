import type {
  AskQuestion,
  CheckpointDecision,
  ContextBlockWire,
  CostBreakdown,
  DebateNarrativeRound,
  DebateResultPayload,
  EvidenceLedgerEntry,
  PlanRevisionKind,
  ProcessStep,
  RunDebrief,
  RunKind,
  SSEEvent,
  Stance,
  ToolDisplay,
  UsageBreakdown,
  WorkerRunPhase,
} from "@/types/events";

// Re-exported so run-detail components render the「收到的上下文」blocks from the store's
// contract (上下文传递可视化) without reaching into the wire types directly.
export type { ContextBlockWire } from "@/types/events";

// Re-exported so graph/detail components import the debate display contract from
// the store without reaching into the wire types.
export type { Stance } from "@/types/events";

// Re-exported so the graph node renders the「计划已调整」轻痕迹 (设计 §7.2) from the
// store's contract without reaching into the wire types directly.
export type { PlanRevisionKind } from "@/types/events";

export type { WorkerRunPhase } from "@/types/events";

export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped";

export type ExecutionStatus =
  | "planning"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

/** 幕类型 = 能力档取用键（首批 multi_agent / debate）。 */
export type ActKind = "multi_agent" | "debate";

/** 幕授权来源：auto=新开默认。未知/旧戳不认。 */
export type ActAuthorizedBy = "auto";

/** One act in an execution's act sequence (批 A1 幕契约). */
export interface ExecutionAct {
  actId: string;
  kind: ActKind;
  title: string | null;
  /** 本幕从宿主图哪个节点后长出；首幕 / 合成幕为 null。 */
  anchorRunId: string | null;
  /** 辩论幕授权来源；调研幕 / 旧流缺省 null。 */
  authorizedBy: ActAuthorizedBy | null;
}

/** Display labels for a 辩论/审查 side — debate-room copy and custom-name peek.
 * Graph nodes use 阵营色 + 头像字 for the default 正方/反方 names. */
export const STANCE_META: Record<Stance, { label: string; short: string }> = {
  pro: { label: "正方", short: "正" },
  con: { label: "反方", short: "反" },
};

/** Tool name → English action label, shared by the team graph's live composing
 * progress line (AgentNode) and the run-detail tool rows. A label-only twin of
 * MessageBubble's TOOL_META (which also couples a lucide icon, so it can't live
 * in the store); keep the two in sync. An unknown tool falls back to its raw name. */
export const TOOL_LABELS: Record<string, string> = {
  web_search: "Search web",
  web_fetch: "Read page",
  grep: "Grep code",
  code_search: "Search code",
  run: "Run",
  code_execute: "Run code",
  terminal: "Run terminal",
  test_run: "Run tests",
  git: "Git",
  read: "Read file",
  write: "Write file",
  file_list: "List dir",
  glob: "Glob",
  list_folders: "List folders",
  resolve_folder: "Resolve folder",
  folders: "Folders",
  create_folder: "Create folder",
  delete_folder: "Delete folder",
  edit: "Edit file",
  file_delete: "Delete file",
  file_move: "Move file",
  file_copy: "Copy file",
  mkdir: "Make dir",
  file_batch: "Batch files",
  md_to_docx: "Export Word",
  md_to_pdf: "Export PDF",
  md_export: "Export document",
  read_image: "Read image",
  code_diagnostics: "Check types",
  // CEO captain tools (surfaced by the bubble's tool_progress / process timeline).
  delegate: "Delegate",
  replan: "Replan",
  debate: "Debate",
  ask_user: "Ask you",
  consult_skill: "Consult skill",
  consult_memory: "Consult memory",
  consult_rule: "Consult rule",
  consult: "Consult",
  remember: "Remember",
  update_folder_profile: "Update folder profile",
  search_conversations: "Search conversations",
  read_conversation: "Read conversation",
  revise: "Revise",
  // Worker-only upward channel (build_worker_registry); surfaces in run detail.
  escalate: "Escalate",
  // CEO 协调模式原语（波内边跑边调）。
  update_synthesis: "Update synthesis",
  cancel_worker: "Cancel worker",
  queue_user_message: "Queue message",
  // 交接 — keep in sync with TOOL_META English chrome.
  handoff: "Handoff",
  docs_read: "Read doc",
  docs_write: "Write doc",
  external_mount_readonly: "Mount folder",
  // L3 团队浏览器 — keep in sync with TOOL_META（单工具 `browser` + 历史七键）。
  browser: "Browser",
  browser_navigate: "Navigate",
  browser_click: "Click",
  browser_type: "Type",
  browser_scroll: "Scroll",
  browser_snapshot: "Snapshot",
  browser_screenshot: "Screenshot",
  browser_console: "Console",
  // 本机 Host（第三能力面）— keep in sync with TOOL_META.
  host: "Host",
  host_ping: "Host ping",
  host_info: "Host info",
  host_audio_devices: "Audio devices",
  host_storage: "Host storage",
  host_power: "Host power",
  host_network_summary: "Network summary",
  host_apps: "Host apps",
  host_os_log_summary: "OS log summary",
  host_shell: "Host shell",
  host_open_settings: "Open settings",
  host_audio_set_default: "Set default audio",
  host_service_restart: "Restart service",
  host_package_install: "Install package",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name;
}

/** Display label for this run's thinking: vendor effort token when stamped,
 * otherwise thinking on/off. Provider `thinking` plus optional `run_completed.reasoning_effort`. */
export function reasoningMeta(
  thinking: boolean,
  effort?: string | null,
): {
  short: string;
  label: string;
  description: string;
} {
  if (!thinking)
    return {
      short: "非思考",
      label: "非思考",
      description: "不走思考链。",
    };
  const token = effort?.trim() ?? "";
  if (token)
    return {
      short: token,
      label: token,
      description: `思考强度 ${token}（厂商档）。`,
    };
  return {
    short: "思考",
    label: "思考",
    description: "走思考链。",
  };
}

export interface ToolCallState {
  id: string;
  toolName: string;
  arguments: Record<string, unknown>;
  result: string | null;
  /** Rich rendering data resolved on `tool_use_end` (工具结果富渲染); absent for
   * tools whose text `result` is enough. */
  display?: ToolDisplay | null;
  status: "running" | "success" | "error" | "redirect";
}

export interface AgentState {
  id: string;
  role: string;
  /** Provider-level thinking on/off (out of the removed tier system). */
  thinking: boolean;
  status: "idle" | "working" | "completed" | "error" | "cancelled";
  currentRunId: string | null;
  outputChunks: string[];
  /** Streamed thinking chunks (run_reasoning_delta), joined for 思考全文. Empty
   * for non-thinking workers or older journals that never carried reasoning. */
  reasoningChunks: string[];
  toolCalls: ToolCallState[];
  /** The tool call this worker is *currently composing* (run_tool_progress): its
   * name + the chars of arguments streamed so far. Non-null only during active
   * argument assembly — set on each progress tick, cleared once the call starts
   * executing (tool_use_start) or the run ends. Drives the node/detail's live
   * write-family / delegate / debate「{tool} · N 字」heartbeat so a long assemble
   * never looks frozen. */
  toolProgress: { toolName: string; chars: number } | null;
  /** Coarse EXECUTION phase for this worker's currently-running tool (`tool_use_progress`
   * with `run_id`). Transport-only — never folded from frames/journal; overlaid live from
   * {@link ExecutionRuntime.workerToolPhases} keyed by {@link currentRunId}. Cleared when
   * the tool ends. Drives the node/detail honest waiting line (Queued/Searching/…). */
  toolExecutionLive: { toolName: string; phase: string } | null;
}

/** Live-only worker tool EXECUTION phase keyed by `run_id` (transport-only sibling of CEO
 * `setProcessToolPhase`). Never journaled; merged onto {@link AgentState.toolExecutionLive}
 * at projection time. */
export interface WorkerToolPhaseLive {
  phase: string;
  toolName: string;
}

/** Leftover DAG pause badge on a run. Live plan_review no longer stamps this;
 * leftover frames skip and the field stays null. `decision` is null while
 * unresolved; on resolve it records 继续/停止 (`continue`/`stop`; an
 * engine timeout folds in as `timeout`). */
export interface RunCheckpoint {
  status: "pending" | "resolved";
  decision: CheckpointDecision | null;
}

/** 升级：``escalate`` 工人向上通道。``kind`` = wait 停下等 / adjust 自己这份做完、请看后面。
 * Folded onto its {@link RunNode}. Wait → ``pending`` 请你拍板；adjust → 协作图标记
 * （工人继续干）；引擎早停走 ``source``，不是留言条。
 *
 * `status`: `raised` | `pending` | `resolved` | `assumed` | `timed_out`.
 * `assumed` = explicit 按假设继续; `timed_out` = wall-clock miss. Both leave answer null. */
export type EscalationKind = "wait" | "adjust";

export interface RunEscalation {
  /** Interaction / raised id (`escalation_id` on wire). Wait cards POST to this id;
   * raised banners use it as the timeline marker key. `null` only when the frame
   * omitted it. Desktop-local — STRIPPED from the conformance `ProjectedTurn`. */
  id: string | null;
  question: string;
  assumption: string;
  status: "raised" | "pending" | "resolved" | "assumed" | "timed_out";
  answer: string | null;
  /** wait 停下等 / adjust 请看后面。早停帧可缺。 */
  kind: EscalationKind;
  /** 结构化升级: wait 卡上的 questions。raised 为 `[]`。 */
  questions: AskQuestion[];
  /** 谁在仲裁：user=经典可答卡；ceo=协调模式等主管（初始不可答）。 */
  awaiting?: "user" | "ceo";
  /** 裁决方：user=用户直答；ceo=主管仲裁。 */
  arbitrated_by?: "user" | "ceo";
  /** 仅 arbitrated_by=ceo：是否经 ask_user 转交用户。 */
  via_user?: boolean;
  /**
   * 早停 / 打转收口标记（wire `run_escalation.source`）。
   * `validation_thrash` / `ceiling_backstop` → 卡住早停卡；缺省 = 协作图标记（adjust）。
   * Desktop-local — 不进 conformance ProjectedTurn。
   */
  source?: string;
  /**
   * 写权冲突结构化裁决（wire `ownership_paths`）。有值时呈现「移交写权 / 保持原主」。
   * Desktop-local — 不进 conformance ProjectedTurn。
   */
  ownershipPaths?: string[];
  /** 当前写权持有者 run_id（wire `lock_owner_run_id`）。 */
  lockOwnerRunId?: string;
  /**
   * 这次挂起真实拿到的墙钟上限（秒，wire `timeout_seconds`）——只有运维配了
   * `checkpoint_timeout_seconds` 才有值。缺省 = 默认部署的无限期等待：不答就一直挂着，
   * 所以卡面**不得**无条件写「未答则按假设继续」（见 escalationWaitCopy）。
   * Desktop-local — 不进 conformance ProjectedTurn。
   */
  timeoutSeconds?: number;
}

export interface RunNode {
  id: string;
  agentId: string;
  task: string;
  status: RunStatus;
  dependsOn: string[];
  /** The worker's authored 结论 (`debrief.summary`) or "" — a scan line,
   * NOT a truncation; null until `run_completed`. */
  outputSummary: string | null;
  /** Workspace file paths the worker wrote (`run_completed.output_files`); empty until
   * completed. */
  outputFiles: string[];
  /** 完工交接简报 (run_completed): the worker's authored wrap-up — 结论 / 关键要点 / 关键假设 /
   * 建议下一步, each present only when written — rendered structured in the run-detail 摘要.
   * null when the worker authored none (辩手 / trivial worker / the captain). */
  debrief: RunDebrief | null;
  durationMs: number | null;
  /** 真实开始时间（epoch ms，`run_started` 帧的后端墙钟时间戳 `t`）；null 直到该 run 开跑。
   * 进行中的「执行中 · Ns」live 计时锚定于此（而非组件挂载时刻），故对节点重挂载 / 晚看 /
   * 刷新都健壮；完成后由权威 {@link durationMs} 接管。桌面本地——不进 conformance ProjectedTurn
   *（投影忽略时间戳，见 agentcore/conformance/timestamps.py）。 */
  startedAt: number | null;
  /** Failure reason from `run_failed`; null unless this run failed. */
  error: string | null;
  /** `run_failed.failure_kind` — face class; null/absent on old journals. */
  failureKind?: import("@/types/events").RunFailureKind | null;
  /** `run_failed.product_landed` — files already on disk before failure. */
  productLanded?: boolean | null;
  /** `run_failed.error_code` — desktop-local; stripped from ProjectedTurn. */
  errorCode?: string | null;
  /** `run_failed.retryable` — desktop-local; stripped from ProjectedTurn. */
  retryable?: boolean | null;
  /** `run_failed.retry_after` seconds — desktop-local; stripped from ProjectedTurn. */
  retryAfter?: number | null;
  /** Delegating run id (`run_started` slot). 阶段1 always null (flat workers
   * under the CEO); set for 阶段2 nested delegation. */
  parentRunId: string | null;
  /** Node kind from `run_started` / the plan: `captain` is the CEO root 汇聚点,
   * `agent` a delegated worker. Drives how the graph styles the node. */
  kind: RunKind;
  /** Cost-ledger role of the run (member/captain/…) from `run_completed`; null
   * until the run completes. 阶段1 scheduled runs are always "member". */
  role: string | null;
  /** Model id the run billed on (e.g. deepseek-v4-flash); null until completed.
   * Workers may differ in tier, so this is per-run (payroll power detail). */
  model: string | null;
  /** Vendor thinking-effort token actually sent (`run_completed.reasoning_effort`);
   * null until completed, on old journals, or when the leaf does not send the field. */
  reasoningEffort: string | null;
  /** This run's token usage (payroll power detail); null until completed. */
  usage: UsageBreakdown | null;
  /** This run's priced cost in nano-CNY (lights up one payroll row, §7.3B);
   * null until completed / unmetered. All-zero `total` renders as「—」(§7.5). */
  cost: CostBreakdown | null;
  /** 辩论/审查 呈现标记 (前端UX设计.md §四, display-only): this run's side in an
   * opposing batch (`pro`/`con`), the `group` it is paired in, and its `round`
   * (真·多轮辩论 turn, 1-based; 0 = not multi-round); null/0 for ordinary parallel/
   * DAG work. The only client signal that differentiates a debate from普通并行 — the
   * DAG shape + SSE are identical (守住「形状是数据不是模式」). Drives the node side
   * badge, the「辩论」strip title, the graph 分列, and the逐轮 layout. */
  stance: Stance | null;
  group: string | null;
  round: number;
  /** 辩论续写语义方 key（质询 / 结辩 / 续轮）；缺字段（老 journal）→ null，投影回退 stance / sides。 */
  sideKey: string | null;
  /** 同人接续（续派 / 热修 / 辩论续写）：现场根 run id（星型），null = 冷开局。
   * 未进 plan 的续写由 `run_started` 合成；计划内续派节点亦在 started 时写入本字段。 */
  continuesRunId: string | null;
  /** 接续序号（同根链上第几次续写，1-based）；0 = 非接续。座位角标「续 ×N」据此派生。 */
  continuationIndex: number;
  /**「计划已调整」轻痕迹 (设计 §7.2): set by a `plan_revised` frame to "bind" (a late-bound
   * placeholder finalised from upstream evidence) or "steer" (a not-yet-run node re-steered
   * after a 队员 scope deviation) when the CEO autonomously adjusted this paused node
   * mid-flight; null otherwise. Drives the node's non-interrupting「职责已定稿」/「方向已校准」
   * badge — the 自我纠偏 stays visible without ever pausing the run. bind wins over steer. */
  revised: PlanRevisionKind | null;
  /** 回落换人：接手的原 run id；null = 普通委派。 */
  replacesRunId: string | null;
  /** 幕归属：该 run 所属幕的 actId（旧 run_plan 无 act → 合成 act-1）。缺省按 act-1。 */
  actId?: string;
  /**
   * 同回合第几次 delegate 追加（1-based）。从 plan skeleton 投影而来；协作图用来区分
   * 「先后追加的两批任务」与拓扑波次。协议 / ProjectedTurn 不承载此字段。
   */
  delegateBatch?: number;
  /** Leftover DAG pause badge after this run; null when the run never gated.
   * Live plan_review no longer stamps this field. */
  checkpoint: RunCheckpoint | null;
  /** 收到的上下文 (上下文传递可视化): the structured ContextBlocks this run was fed at
   * assembly time, from its `run_context` frame — the SAME data the LLM saw (系统 /
   * 本回合工具 / 原始请求 / 团队位置 / 前置 / 工作区 / 任务…). Empty until that frame folds in (or for a run
   * whose opening wasn't block-assembled). Drives the run detail's「收到的上下文」area. */
  receivedContext: ContextBlockWire[];
  /** 升级实时可见: escalations this run raised via `escalate`, in fire order. Empty for
   * the common case; non-empty drives the node's ⚠️ badge + the card's live notice.
   * Appended on each `run_escalation` frame. */
  escalations: RunEscalation[];
  /** Per-run 思考·正文·工具 timeline (对称 CEO ``message.process``). Live-folded from
   * ``run_reasoning_delta`` / ``run_output_delta`` / worker ``tool_use_*``. Reload may
   * seed from ``runs.run_processes[runId]`` once workers have settled (not while live). */
  process: ProcessStep[];
  /**
   * Worker mid-flight activity phase (`run_phase` SSE). Orthogonal to {@link status}.
   * Absent / null on older journals and after terminal frames. `queued` = status pending;
   * `skipped` = status skipped (not carried here).
   */
  phase?: WorkerRunPhase | null;
  /** When `phase === "tool"`, the tool currently running / being composed. */
  phaseTool?: string | null;
}

/** 多任务并行调度 (batch_metrics): one dispatched node's occupancy window, folded (snake→camel) from a
 * `batch_metrics` frame's `timeline`. `startMs`/`endMs` are offsets from the scheduler wall start
 * (same t0 as {@link BatchMetricsSnapshot.wallMs}) — overlap = real concurrency, a gap before a
 * window = the `width` cap serialized it, the longest window = the critical path. 采集仍在、
 * 产品不展示. `runId` ties back to a
 * {@link RunNode} for role/label/color. `outcome` is the terminal status (`completed`/`failed`).
 * Dispatched nodes only (cascade-skipped omitted). */
export interface NodeTiming {
  runId: string;
  startMs: number;
  endMs: number;
  outcome: string;
}

/** 调度埋点量化 (深层诊断指标, 前端UX设计.md §四): one WaveScheduler segment's observability
 * snapshot, folded from a `batch_metrics` frame. `busyMs / wallMs ≈` 平均并发; `slotStarved > 0`
 * ⇒ the `width` 并发上限 throttled ready nodes. The boundary tallies count 受监督波循环 yields
 * fired this segment (leftover `bindBoundaries` from historical journals / scope 漂移返工 / checkpoint 用户复核); the escalate tallies
 * are raw (`scopeEscalations ⊆ escalations`). `timeline` carries each dispatched node's occupancy
 * window. A delegate turn accrues one per scheduler segment (a checkpoint / scope yield + resume
 * appends another). 采集仍在、产品不展示. */
export interface BatchMetricsSnapshot {
  nodes: number;
  width: number;
  peakRunning: number;
  wallMs: number;
  busyMs: number;
  slotStarved: number;
  completed: number;
  failed: number;
  skipped: number;
  bindBoundaries: number;
  scopeBoundaries: number;
  checkpointBoundaries: number;
  escalations: number;
  scopeEscalations: number;
  timeline: NodeTiming[];
}

export interface Execution {
  id: string;
  planType: "single_agent" | "multi_agent" | "debate";
  taskSummary: string;
  status: ExecutionStatus;
  /**
   * 上一张协作图（`run_plan.prev_execution_id`）。协议链仍在、用户面不画回链；
   * 不进 ProjectedTurn。
   */
  prevExecutionId?: string | null;
  agents: AgentState[];
  runs: RunNode[];
  /** 幕序列（批 A1）：旧 run_plan 无 act → fold 合成单幕 act-1。 */
  acts: ExecutionAct[];
  progress: { completed: number; total: number };
  /** 调度埋点量化 (深层诊断指标, §十): the turn's WaveScheduler snapshots, one per delegate
   * segment, folded from `batch_metrics` frames. Empty for a single-agent turn or before the
   * scheduler reports. 采集仍在、产品不展示 — a desktop-local field, kept out of the
   * conformance ProjectedTurn. */
  batches: BatchMetricsSnapshot[];
  /** 辩论收场产物（`debate_result`）：决策简报 + 交锋叙事线，verbatim 承载；null = 非
   * 辩论回合。与 {@link runs} 互补——辩手发言全文在对应辩手节点，本字段是主持人的逐轮
   * 裁判/小结 + 决策简报（{@link DebateView} 据此渲染）。 */
  debate: DebateResultPayload | null;
  /** 辩论进行中的逐轮叙事（`debate_round_started` / `debate_round` 折叠累积）：让进行中就叠
   * 出主持人逐轮焦点 / 小结 / 裁判，而非干等 {@link debate} 收场。P2 DURABLE——落 journal，
   * 刷新后 hydrateFromJournal 重建；收场后全量叙事线亦在 {@link debate}。非辩论恒 `[]`。 */
  debateRounds: DebateNarrativeRound[];
  /** 本场是否开启质询（`debate_round_started.cross_exam_enabled`）。缺字段 → false。 */
  crossExamEnabled: boolean;
  /** 主持人开场白（`debate_round_started.opening`）：仅首轮携带；sticky 取第一个非空。
   * 收场 {@link debate}.opening 仍是权威。缺字段 / 老 journal → null。 */
  debateOpening: string | null;
  /** 场级证据台账（`debate_round` 的
   * `evidence_ledger_delta` 累积 / `debate_result.evidence_ledger`
   * 权威覆盖）：辩论徽章 `#rN` 溯源。桌面 UI 态——不进 conformance ProjectedTurn（oracle 经
   * `debate.evidence_ledger` 承载收场权威；live delta 同路径累积）。非辩论 / 旧 fixture 可缺省。 */
  evidenceLedger?: EvidenceLedgerEntry[];
}

/** Mid-flight user interjection (`user_interjection` · 经典 steer + 协调共用).
 * Same interjectionId keeps latest status
 * (协调: received → injected → addressed / queued / failed;
 *  经典: received → injected | queued | failed). */
export type UserInterjectionStatus =
  | "received"
  | "injected"
  | "addressed"
  | "queued"
  | "failed"
  | string;

export interface UserInterjectionAttachment {
  name: string;
  workspacePath?: string;
  binary?: boolean;
}

export interface UserInterjectionMention {
  agentId: string;
  role: string;
}

export interface UserInterjection {
  interjectionId: string;
  executionId: string;
  content: string;
  status: UserInterjectionStatus;
  note: string | null;
  /** 落库用户行。received 时时间线不画；injected / addressed 时按这个 id 放入。 */
  userMessageId?: string;
  attachments?: UserInterjectionAttachment[];
  /** Soft `@` role chips on the interjection bubble (prompt hint, not a hard route). */
  agentMentions?: UserInterjectionMention[];
}

/**
 * Immutable skeleton declared once when the DAG is planned (`run_plan`).
 * Frames mutate a *projection* of this skeleton — never the skeleton itself.
 */
export interface ExecutionPlan {
  id: string;
  planType: "single_agent" | "multi_agent" | "debate";
  taskSummary: string;
  /**
   * 上一张协作图的 execution_id（`run_plan.prev_execution_id`）。
   * 新回合开新图时的协议前向链（用户面不画回链）；同 execution merge 时保留首值。
   * 旧 journal 无此字段 → null。
   */
  prevExecutionId?: string | null;
  /** 幕序列骨架（批 A1）：旧 run_plan 无 act → 合成单幕 act-1。手写 fixture 可缺省。 */
  acts?: ExecutionAct[];
  agents: {
    id: string;
    role: string;
    thinking?: boolean;
  }[];
  runs: {
    id: string;
    agentId: string;
    task: string;
    dependsOn: string[];
    /** Delegating run id (阶段2 nested delegation). A sub-worker points at its
     * captain worker's run id (a real node) so the graph + detail tree group it
     * under that parent; a top-level worker points at the CEO captain run (no
     * node here) or is null. Declared at plan time so the *structural* graph
     * layout can group without waiting for the run_started frame. */
    parentRunId?: string | null;
    /** Declared node kind (default `agent`). `captain` marks the CEO root 汇聚点;
     * also re-confirmed by the run_started frame. */
    kind?: RunKind;
    /** 辩论/审查 呈现标记 (前端UX设计.md §四, display-only): opposing-side tag,
     * pairing group, and 真·多轮辩论 turn (`round`). Declared at plan time so the
     * strip can show a「辩论」title and the graph can band正/反 + 逐轮 from the plan
     * alone, before any run frame folds in.
     * ``group`` 权威 = ``run_plan``（计划内节点）；续写才从 ``run_started.group`` 出生。 */
    stance?: Stance;
    group?: string;
    round?: number;
    /** 回落换人：接手的原 worker run_id。 */
    replacesRunId?: string | null;
    /** 幕归属：该 run 所属幕的 actId。缺省按 act-1。 */
    actId?: string;
    /**
     * 同回合第几次 `run_plan` / delegate 追加（1-based）。呈现层专用：协议不携带批次元数据，
     * 由 {@link planFromRunPlan} / {@link mergePlanInto} 在 ingest 时盖戳，供协作图画
     * 「第 N 次委派」泳道。不进 ProjectedTurn。
     */
    delegateBatch?: number;
  }[];
}

/**
 * A persisted multi-agent execution journal for one assistant message
 * (`messages.runs`): the turn's ordered run/tool SSE events plus its finish
 * reason. Replayed client-side through the same fold as the live stream to
 * rebuild a past turn's team graph on reload. Carried on {@link Message.runs};
 * absent for user / single-agent messages (no delegation).
 */
export interface ExecutionJournal {
  events: SSEEvent[];
  finishReason: string;
  /** Per-run ProcessStep[] from journal (settled-reload seed). Absent on older journals. */
  runProcesses?: Record<string, ProcessStep[]> | null;
  /**
   * False when REST list dropped bulky journal events; graph / turn-detail
   * fetch `GET …/messages/{id}` before hydrateFromJournal. Absent on live SSE
   * journals and pre-slim opened cache (treat as complete).
   */
  eventsComplete?: boolean;
  /**
   * Structured turn failure from journal ``turn_end.error`` (cold reload / duck
   * path). Live SSE usually lifts this onto ``Message.error``; keep optional here
   * so ``visibleMessageText`` / export can still read runs.error.
   */
  error?: { code?: string; message?: string } | null;
}
