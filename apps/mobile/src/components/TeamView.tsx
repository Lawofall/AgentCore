// The multi-agent team view for the mobile client (前端技术与架构 §七 · 多 Agent 团队视图).
//
// A mobile-native, VERTICAL reduction of the desktop React-Flow team graph
// (InlineTeamGraph / GraphView): the same ProjectedTurn.{agents, runs, progress} fields,
// no canvas / ELK / scrubber. Consumed IDENTICALLY by live turns (fold(turn.events)) and
// history replay (fold(MessageDetail.runs.events)) — there is no second data path.
//
// The `captain` run is the chat bubble itself (the CEO's reply streams into the message),
// so it is omitted here; only the delegated `agent` workers are listed. The three desktop
// relations collapse to mobile-appropriate cues: DAG order = list order, the delegate tree
// (`parentRunId`) = indentation, the revision chain (`revision >= 2`) = a badge. Debate
// (`stance`) shows a pill per card rather than pro/con columns (too wide for a phone).
import {
  type EscalationUserDecision,
  decideEscalation,
} from "@/api/interaction";
import { submitRunRedirect, submitRunStop } from "@/api/runControl";
import {
  BrowserLoginDecisionCard,
  type BrowserLoginSubmitKind,
  type OpenBrowserLiveOpts,
} from "@/components/BrowserLoginDecisionCard";
import { DebriefBlock, HandoffSuccessRow } from "@/components/DebriefBlock";
import { EvidenceLedgerProvider } from "@/components/EvidenceLedgerContext";
import { Markdown } from "@/components/Markdown";
import { Modal } from "@/components/Modal";
import { TurnOutcomeActions } from "@/components/TurnOutcomeActions";
import {
  CONTEXT_CHANNEL_LABEL,
  TOOL_GUIDANCE_LABEL,
  TOOL_STATUS_LABEL,
  runPhaseLabel,
  toolDetail,
  toolLabel,
  toolPhaseText,
} from "@/components/assistantLabels";
import {
  hasSuccessfulHandoff,
  isSuccessfulHandoff,
} from "@/components/handoffBrief";
import { escalationWaitNote } from "@/lib/escalationWaitCopy";
import { buildLedgerMap } from "@/lib/evidenceLedger";
import { isFileReadCeilingGuidance } from "@/lib/fileReadCeiling";
import {
  markLocalSettlement,
  noteRemoteSettlementFromReceipt,
  unmarkLocalSettlement,
} from "@/lib/remoteSettlement";
import { markRunStopSent, useRunStopSent } from "@/lib/runStopPending";
import type { SupportDiagnosticIds } from "@/lib/supportDiagnostics";
import { formatDuration } from "@/lib/time";
import {
  PARTIAL_NOTICE,
  type TurnOutcome,
  teamFailureProgressBit,
  teamStripFace,
} from "@/lib/turnOutcome";
import type { EscalationSlotEsc, RunToolCall } from "@/protocol/fold";
import { actAuthorizedByLabel } from "@/protocol/fold";
import type {
  ContextBlockWire,
  EvidenceLedgerEntry,
} from "@agentcore/contract-types";
import type {
  ActKind,
  ProjectedAct,
  ProjectedAgent,
  ProjectedRun,
  ProjectedTeamNote,
  RunEscalation,
  RunStatus,
  TurnStatus,
} from "@agentcore/protocol-conformance";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  type InterveneGate,
  cacheUsageDisplay,
  interveneAckText,
  isLiveRunStatus,
  runRedirectGate,
  runStopGate,
} from "@agentcore/protocol-fold-kit";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import "./TeamView.css";

/** 幕 kind → 列表分组头短标签（无 title 时回落；手机列表语言，非桌面幕分带）。 */
const ACT_KIND_LABEL: Record<ActKind, string> = {
  multi_agent: "调研",
  debate: "辩论",
};

function actSectionLabel(act: ProjectedAct | undefined, actId: string): string {
  if (act?.title?.trim()) return act.title.trim();
  if (act?.kind) return ACT_KIND_LABEL[act.kind] ?? act.kind;
  return actId;
}

/** 这名队员属于哪一幕（缺省 act-1；单幕图退回唯一那幕）——按幕开关「改方向」。 */
function actKindOf(
  actById: ReadonlyMap<string, ProjectedAct>,
  run: ProjectedRun,
): ActKind | null {
  const act = actById.get(run.actId || "act-1");
  if (act) return act.kind;
  return actById.size === 1 ? ([...actById.values()][0]?.kind ?? null) : null;
}

type CheckpointDecision = NonNullable<ProjectedRun["checkpoint"]>["decision"];

/**
 * 团队便签墙默认展开规则（对齐桌面/画布，手机端自写一份，不共享桌面实现）：
 * 便签为空 → 不渲染（调用方短路）；运行中且存在 active 便签 → 默认展开；否则默认收起。
 */
export function teamNotesDefaultExpanded(
  status: TurnStatus | null | undefined,
  notes: readonly ProjectedTeamNote[],
): boolean {
  if (notes.length === 0) return false;
  if (status !== "running") return false;
  return notes.some((n) => n.status === "active");
}

/** 团队便签墙 (§2.2 通) note kind → 中文 label + css class. `decision` (我定了) is a choice others
 * depend on (an interface / field name / format / naming); `heads_up` (提个醒) is a pitfall /
 * discovery; `claim` (我领了) is a piece of work / file this worker is taking, so a sibling doesn't
 * duplicate it. Mirrors the backend NoteWall labels (runtime/runs/notewall.py); an unknown kind
 * falls back to 提个醒. */
const NOTE_STATUS_LABEL: Record<string, string> = {
  superseded: "已更新",
  voided: "已作废",
};

const NOTE_KIND_LABEL: Record<string, string> = {
  decision: "约定",
  heads_up: "提醒",
  claim: "认领",
};

const NOTE_KIND_CLASS: Record<string, string> = {
  decision: "kind-decision",
  heads_up: "kind-headsup",
  claim: "kind-claim",
};

const RUN_STATUS: Record<RunStatus, { label: string; tone: string }> = {
  pending: { label: "排队中", tone: "muted" },
  running: { label: "进行中", tone: "run" },
  completed: { label: "完成", tone: "ok" },
  failed: { label: "失败", tone: "err" },
  cancelled: { label: "已停止", tone: "muted" },
  skipped: { label: "未执行", tone: "muted" },
};

/** Prefer `failureKind`; thin error-text fallback for old journals (align desktop). */
export function failureFaceLabel(
  error: string | null | undefined,
  failureKind: ProjectedRun["failureKind"],
  productLanded?: boolean | null,
): string {
  if (productLanded) return "产出已落盘";
  if (failureKind === "quality") return "未达标";
  if (failureKind === "format") return "格式未过";
  if (failureKind === "model") return "模型中断";
  if (failureKind === "call") return "调用失败";
  const raw = (error ?? "").trim();
  if (!raw) return "调用失败";
  if (
    /中断|abort|断开|停滞|stalled|收尾时中断|degraded_handoff|降级交接/i.test(
      raw,
    )
  ) {
    return "模型中断";
  }
  if (
    /模型|llm|调用|timeout|超时|invalid_request|upstream|网络|provider|api\s*error/i.test(
      raw,
    )
  ) {
    return "调用失败";
  }
  return "失败";
}

/** 证人席位根：开赛挂席、点名才作答 — pending→待命，skipped→未传唤。
 *  running + `run.phase` → 相位文案（不再一律「进行中」/「思考中」）。 */
function runStatusLabel(
  status: RunStatus,
  run: {
    group?: string | null;
    continuesRunId?: string | null;
    phase?: ProjectedRun["phase"];
    error?: string | null;
    failureKind?: ProjectedRun["failureKind"];
    productLanded?: boolean | null;
  },
): { label: string; tone: string } {
  const base = RUN_STATUS[status];
  const witnessSeat =
    run.group === "debate:witness" && run.continuesRunId == null;
  if (witnessSeat) {
    if (status === "pending") return { label: "待命", tone: "muted" };
    if (status === "skipped") return { label: "未传唤", tone: "muted" };
    return base;
  }
  if (status === "failed") {
    return {
      label: failureFaceLabel(
        run.error,
        run.failureKind ?? null,
        run.productLanded ?? null,
      ),
      tone: "err",
    };
  }
  if (status === "running") {
    const phase = runPhaseLabel(run.phase);
    if (phase) return { label: phase, tone: "run" };
  }
  return base;
}

/** 单个队员的耗时（卡片脚 / 详情头）——秒以下保留毫秒，看的是这一个人跑了多久。 */
function formatRunDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function checkpointLabel(decision: CheckpointDecision): string {
  switch (decision) {
    case "adjust":
      return "已调整";
    case "stop":
      return "已停止";
    case "timeout":
      return "已超时";
    default:
      return "已放行";
  }
}

function lastLine(text: string | undefined): string | null {
  if (!text) return null;
  const lines = text.trimEnd().split("\n");
  return lines[lines.length - 1] || null;
}

/** Sum worker costs (nano-CNY); mirrors desktop StatusStrip turn money signal. */
function aggregateWorkerCost(runs: readonly ProjectedRun[]): {
  nano: number;
  estimated: boolean;
  unpriced: boolean;
} | null {
  let nano = 0;
  let estimated = false;
  let unpriced = false;
  let any = false;
  for (const r of runs) {
    if (r.kind === "captain") continue;
    const c = r.cost;
    if (!c) continue;
    any = true;
    if (c.pricing_source === "unpriced") unpriced = true;
    if (c.total > 0) {
      nano += c.total;
    } else if (c.estimated_total && c.estimated_total > 0) {
      nano += c.estimated_total;
      estimated = true;
    }
  }
  if (!any) return null;
  return { nano, estimated, unpriced };
}

function maxDebateRound(workers: readonly ProjectedRun[]): number {
  let max = 0;
  for (const r of workers) {
    if (r.round > max) max = r.round;
  }
  return max;
}

function debateEntryText(workers: readonly ProjectedRun[]): string {
  const round = maxDebateRound(workers);
  const pros = workers.filter((r) => r.stance === "pro").length;
  const cons = workers.filter((r) => r.stance === "con").length;
  const parts: string[] = [];
  if (round > 0) parts.push(`第 ${round} 轮`);
  if (pros || cons) parts.push(`正 ${pros} · 反 ${cons}`);
  const running = workers.filter((r) => r.status === "running").length;
  if (running > 0) parts.push(`${running} 人发言中`);
  return parts.length > 0 ? parts.join(" · ") : "辩论协作进行中";
}

/** Live wall-clock seconds since a stable start (first collab event). Same
 *  shape as desktop ToolLine: 1s ticker only re-renders; value is recomputed
 *  from Date.now() so fold/remount does not reset. 36h clamp matches desktop
 *  `runningElapsedSec` (offline preview skew → omit, don't show millions of s). */
const MAX_SANE_RUNNING_ELAPSED_SEC = 36 * 60 * 60;

function useRunningElapsed(
  ticking: boolean,
  startedAt: number | null | undefined,
): number {
  const [, force] = useState(0);
  useEffect(() => {
    if (!ticking) return;
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [ticking]);
  if (!ticking || startedAt == null || !Number.isFinite(startedAt)) return 0;
  const sec = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  return sec > MAX_SANE_RUNNING_ELAPSED_SEC ? 0 : sec;
}

/**
 * 一行账：子任务 n/m、用时、花费。运行态用时由调用方传入墙钟值（非冻结跨度）。
 *
 * 并行省时已从产品删掉——不计算、不写进条、不写进 title。
 */
function teamStripMeta(args: {
  workers: readonly ProjectedRun[];
  progress: { completed: number; total: number };
  elapsedMs: number;
  /** Partial verdict already lives in the strip title — don't repeat 部分完成 in meta. */
  skipPartialBit?: boolean;
}): string {
  const { workers, progress, elapsedMs, skipPartialBit } = args;
  const bits: string[] = [];
  // 「N 个 Agent」已删——与图/列表上成员重复；保留子任务 n/m。
  bits.push(`${progress.completed}/${progress.total} 子任务`);
  const failedBit = teamFailureProgressBit(workers);
  if (failedBit && !(skipPartialBit && failedBit === PARTIAL_NOTICE)) {
    bits.push(failedBit);
  }
  // 用时 = 回合墙钟。终态用首末跨度；运行态用 Date.now() − 起点，避免长工具无新帧时冻住。
  if (elapsedMs > 0) {
    bits.push(`用时 ${formatDuration(elapsedMs)}`);
  }
  const money = aggregateWorkerCost(workers);
  if (money) {
    if (money.nano > 0) {
      bits.push(formatCostYuan(money.nano, money.estimated));
    } else if (money.unpriced) {
      bits.push("自带密钥·未计价");
    }
  }
  return bits.join(" · ");
}

/** The read-only one-liner under an escalation's question, by lifecycle. */
export function escalationDetail(esc: RunEscalation): string | null {
  const earlyStop =
    esc.source === "validation_thrash" || esc.source === "ceiling_backstop";
  if (earlyStop) return "卡住早停 · 交付可能不完整";
  if (esc.status === "resolved" && esc.answer) return `已答复：${esc.answer}`;
  if (esc.status === "assumed")
    return esc.assumption ? `按假设继续：${esc.assumption}` : null;
  if (esc.status === "timed_out")
    return esc.assumption ? `超时回落假设：${esc.assumption}` : null;
  return esc.assumption ? `暂定假设：${esc.assumption}` : null;
}

export function TeamView({
  agents,
  runs,
  progress,
  acts = [],
  teamNotes = [],
  status,
  conversationId = null,
  executionId = null,
  pendingEscalations: _pendingEscalations,
  escalationsInteractive: _escalationsInteractive = false,
  runToolCalls,
  workerToolPhases,
  evidenceLedger = [],
  elapsedMs = 0,
  startedAtMs = null,
  waitProgress = null,
  detached = false,
  outcome = null,
  supportIds,
  onRetry,
}: {
  agents: ProjectedAgent[];
  runs: ProjectedRun[];
  progress: { completed: number; total: number };
  /** 幕序列（批 A4）：≥2 幕时列表按幕插分组头；单幕 / 缺省保持既有扁平列表。 */
  acts?: ProjectedAct[];
  /** 团队便签墙 (§2.2 通): notes workers broadcast to their concurrent siblings this turn. */
  teamNotes?: ProjectedTeamNote[];
  /** Turn lifecycle from ProjectedTurn — drives team-notes default expand/collapse. */
  status?: TurnStatus | null;
  /** 阻塞式求决策 (②): present on a live multi-agent turn so a worker's pending escalation
   *  renders as an actionable answer card. 也是按人干预（只停/只改这一个队员）的提交对象。 */
  conversationId?: string | null;
  /** 本图 execution id（`run_plan.execution_id`）——按人干预的提交目标；缺省则不出干预条。 */
  executionId?: string | null;
  /** runId → pending escalation id from ProjectedTurn.interactions (P3 · 按 id 精确提交). */
  pendingEscalations?: Map<string, string>;
  /** Live turn → the pending escalation is answerable over the open stream (else read-only). */
  escalationsInteractive?: boolean;
  /** 队员工具明细 (RunDetail · 工具调用): runId → the worker's tool calls (transport-only sibling
   *  extractRunToolCalls). Fed to the run-detail panel; absent → the panel shows no tool section. */
  runToolCalls?: Map<string, RunToolCall[]>;
  /** Worker `tool_use_progress` (run_id): runId → live EXECUTION phase + tool name (transport-only
   *  sibling {@link import("@/protocol/fold").extractWorkerToolPhases}). */
  workerToolPhases?: Map<string, { phase: string; toolName: string }>;
  /** 场级证据台账（`extractEvidenceLedger`）：辩论发言徽章 `#eN` 解析（O7）。 */
  evidenceLedger?: EvidenceLedgerEntry[];
  /** 终态条「用时」= 回合墙钟跨度（`turnElapsedMs(turn.events)`）。绝不用队员时长求和
   *  顶替，那是工时，并行越多数字越大。缺省 0 = 不显示用时。 */
  elapsedMs?: number;
  /** 运行态条「用时」的墙钟锚点（首条协作事件 epoch ms）。缺省则运行中不显示用时。 */
  startedAtMs?: number | null;
  /** Live `coordination_wait` n/m。有则盖过 fold `progress`（条上只换数字，不写长句）。 */
  waitProgress?: { completed: number; total: number } | null;
  /** Live `execution_detached`。CEO 已收口、队员还在跑时条上挂「后台」。 */
  detached?: boolean;
  /** Arbiter verdict: when `surface==="strip"` this bar is the primary failure face. */
  outcome?: TurnOutcome | null;
  supportIds?: SupportDiagnosticIds;
  onRetry?: () => void;
}) {
  // 深度检视单个队员 (RunDetail): tapping a RunCard opens a detail panel pinned to this run. The
  // panel navigates to another run (修订链切换 / 关系跳转) by swapping the selected id — the run
  // list is the same ProjectedTurn slice whether live or replayed.
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // 默认展开：手机上看团队就是看队员卡；箭头仍可收起，不按对话落盘。
  const [expanded, setExpanded] = useState(true);
  const workers = runs.filter((r) => r.kind !== "captain");
  const showBackground =
    status !== "cancelled" &&
    status !== "paused" &&
    (detached ||
      (status === "completed" && workers.some((r) => r.status === "running")));
  const stripStatus =
    showBackground && (status === "completed" || status === "running")
      ? "running"
      : status;
  const liveSec = useRunningElapsed(stripStatus === "running", startedAtMs);
  const ledgerMap = useMemo(
    () => (evidenceLedger.length ? buildLedgerMap(evidenceLedger) : null),
    [evidenceLedger],
  );
  const actById = useMemo(() => new Map(acts.map((a) => [a.actId, a])), [acts]);
  if (workers.length === 0) return null;
  const selectedRun = selectedRunId
    ? (runs.find((r) => r.id === selectedRunId) ?? null)
    : null;

  const multiAct = acts.length >= 2;
  // 单幕辩论：头部保留「辩论」徽标；多幕时改由分组头表达，避免整队被扁平标成辩论。
  const isDebate =
    !multiAct && (acts[0]?.kind === "debate" || workers.some((r) => r.stance));
  const notesDefaultOpen = teamNotesDefaultExpanded(status, teamNotes);
  const strip = teamStripFace(
    stripStatus,
    showBackground && status === "completed" ? null : outcome,
  );
  const displayProgress =
    waitProgress && waitProgress.total > 0 ? waitProgress : progress;
  const liveMs = liveSec > 0 ? liveSec * 1000 : 0;
  const stripMeta = teamStripMeta({
    workers,
    progress: displayProgress,
    elapsedMs: stripStatus === "running" ? liveMs : elapsedMs,
    skipPartialBit: outcome?.kind === "partial",
  });
  const stripOwnsVerdict = outcome?.surface === "strip";
  const showDebateEntry = isDebate;

  // Indent a nested delegate by how many worker parents it chains through (stage-2 子任务).
  const depthOf = (run: ProjectedRun): number => {
    let depth = 0;
    let parentId = run.parentRunId;
    const seen = new Set<string>([run.id]);
    while (parentId && !seen.has(parentId)) {
      seen.add(parentId);
      const parent = workers.find((r) => r.id === parentId);
      if (!parent) break;
      depth += 1;
      parentId = parent.parentRunId;
    }
    return depth;
  };

  const runCards: ReactNode[] = [];
  let lastActId: string | null = null;
  for (const run of workers) {
    const actId = run.actId || "act-1";
    if (multiAct && actId !== lastActId) {
      lastActId = actId;
      const act = actById.get(actId);
      runCards.push(
        <div key={`act-head-${actId}`} className="team-act-head">
          <span className="team-act-title">{actSectionLabel(act, actId)}</span>
          {act?.kind && (
            <span className="team-act-kind">
              {ACT_KIND_LABEL[act.kind] ?? act.kind}
            </span>
          )}
          {actAuthorizedByLabel(act?.authorizedBy) ? (
            <span className="team-act-auth">
              {actAuthorizedByLabel(act?.authorizedBy)}
            </span>
          ) : null}
        </div>,
      );
    }
    runCards.push(
      <RunCard
        key={run.id}
        run={run}
        agent={agents.find((a) => a.id === run.agentId)}
        depth={depthOf(run)}
        continuationIndex={continuationIndexOf(workers, run)}
        workerToolPhase={workerToolPhases?.get(run.id)}
        onOpen={() => setSelectedRunId(run.id)}
      />,
    );
  }

  return (
    <EvidenceLedgerProvider ledger={ledgerMap}>
      <div className="team" data-testid="team-view">
        <div className="team-strip">
          <div className="team-strip-row">
            <span
              className={`team-strip-mark mark-${strip.mark}`}
              aria-hidden
            />
            <div className="team-strip-body">
              {strip.title || multiAct || isDebate || showBackground ? (
                <div
                  className={`team-strip-title${strip.phase ? " is-phase" : ""}`}
                >
                  {strip.title ? <span>{strip.title}</span> : null}
                  {showBackground ? (
                    <span
                      className="team-tag"
                      data-testid="team-strip-background"
                    >
                      后台
                    </span>
                  ) : null}
                  {multiAct ? (
                    <span className="team-tag">{acts.length} 幕</span>
                  ) : isDebate ? (
                    <span className="team-tag">辩论</span>
                  ) : null}
                </div>
              ) : null}
              {stripMeta ? (
                <div className="team-strip-meta">{stripMeta}</div>
              ) : null}
            </div>
            {!isDebate && (
              <span
                className={`team-strip-progress${strip.phase ? " is-phase" : ""}`}
              >
                {displayProgress.completed}/{displayProgress.total}
              </span>
            )}
            <button
              type="button"
              className="team-strip-toggle"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              aria-label={expanded ? "收起协作列表" : "展开协作列表"}
            >
              {expanded ? "▾" : "▸"}
            </button>
          </div>
          {stripOwnsVerdict && outcome ? (
            <div className="team-strip-verdict">
              <TurnOutcomeActions
                outcome={outcome}
                supportIds={supportIds ?? {}}
                onRetry={onRetry}
                hideNotice={!!outcome.notice && outcome.notice === strip.title}
              />
            </div>
          ) : null}
        </div>
        {expanded && (
          <div className="team-body">
            {showDebateEntry ? (
              <div
                className="team-debate-entry"
                data-testid="team-debate-entry"
              >
                <span className="team-debate-entry-tag">辩论进展</span>
                <span className="team-debate-entry-text">
                  {debateEntryText(workers)}
                </span>
              </div>
            ) : null}
            <div className="team-runs">{runCards}</div>
          </div>
        )}
        {/* 便签在折叠后仍可达（对齐桌面：便签墙不随图折叠卸载）。 */}
        {teamNotes.length > 0 && (
          <div className="team-body team-body-persist">
            <TeamNotesWall
              key={notesDefaultOpen ? "open" : "shut"}
              notes={teamNotes}
              defaultOpen={notesDefaultOpen}
            />
          </div>
        )}
        {selectedRun && (
          <RunDetailPanel
            run={selectedRun}
            agents={agents}
            runs={runs}
            toolCalls={runToolCalls?.get(selectedRun.id) ?? []}
            conversationId={conversationId}
            executionId={executionId}
            redirectCapable={actKindOf(actById, selectedRun) !== "debate"}
            onSelect={setSelectedRunId}
            onClose={() => setSelectedRunId(null)}
          />
        )}
      </div>
    </EvidenceLedgerProvider>
  );
}

/** Collapsible team-notes wall — remounted by the parent when `defaultOpen` flips so the
 *  running→finished transition re-applies the collapsed default (same key trick as desktop). */
function TeamNotesWall({
  notes,
  defaultOpen,
}: {
  notes: ProjectedTeamNote[];
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details
      className="team-notes"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="team-notes-head">团队便签 {notes.length}</summary>
      <div className="team-notes-body">
        {notes.map((note) => {
          // 便签会过期 → supersession (§2.2): a 改写/作废'd note is struck + dimmed with a status
          // pill, so a stale decision can't be mistaken for current truth. `active` → no pill.
          const statusLabel = NOTE_STATUS_LABEL[note.status];
          return (
            <div
              key={note.noteId}
              className={`team-note${statusLabel ? " team-note-stale" : ""}`}
            >
              <div className="team-note-meta">
                <span
                  className={`team-note-kind ${
                    NOTE_KIND_CLASS[note.kind] ?? "kind-headsup"
                  }`}
                >
                  {NOTE_KIND_LABEL[note.kind] ?? "提醒"}
                </span>
                <span className="team-note-author">
                  {note.role || note.agentId}
                </span>
                {statusLabel && (
                  <span
                    className={`team-note-status ${
                      note.status === "voided"
                        ? "status-voided"
                        : "status-superseded"
                    }`}
                  >
                    {statusLabel}
                  </span>
                )}
              </div>
              <div className="team-note-text">{note.text}</div>
            </div>
          );
        })}
      </div>
    </details>
  );
}

/** The status / role pills on a run (stance / 续 ×N / 计划已调整 / 子任务 / checkpoint / 上报),
 *  shared by the {@link RunCard} peek and the {@link RunDetailPanel} header so the two read the
 *  same. `isChild` (a delegated sub-task) is passed in — the card knows it from graph depth, the
 *  panel from the run's parent. Renders nothing when the run has no pill. */
function RunPills({
  run,
  isChild,
  continuationIndex = 0,
}: {
  run: ProjectedRun;
  isChild: boolean;
  continuationIndex?: number;
}) {
  const hasPill =
    run.stance ||
    continuationIndex >= 1 ||
    run.revised ||
    isChild ||
    run.checkpoint ||
    run.escalations.length > 0;
  if (!hasPill) return null;
  return (
    <div className="run-tags">
      {run.stance && (
        <span className="run-pill">
          {run.stance === "pro" ? "正方" : "反方"}
        </span>
      )}
      {continuationIndex >= 1 && (
        <span className="run-pill">续 ×{continuationIndex}</span>
      )}
      {/* 「计划已调整」轻痕迹 (设计 §7.2): the CEO autonomously re-bound (bind) / re-steered
          (steer) this node mid-flight — a non-interrupting cue mirroring the desktop node badge. */}
      {run.revised && <span className="run-pill">计划已调整</span>}
      {isChild && <span className="run-pill">子任务</span>}
      {/* 拍板类不用琥珀（2026-07 拍板）：pending「需要你」= 蓝 pill-act，已决记录 = 中性
          run-pill（对齐桌面 checkpointBadge：pending primary / settled muted）。 */}
      {run.checkpoint && (
        <span
          className={`run-pill${run.checkpoint.status === "pending" ? " pill-act" : ""}`}
        >
          {run.checkpoint.status === "pending"
            ? "待放行"
            : checkpointLabel(run.checkpoint.decision)}
        </span>
      )}
      {/* 升级实时可见: a worker flagged a blocker for the CEO — a 待裁决 cue mirroring the desktop
          node ⚠️ badge; the full ask renders below / in the panel. Pending = 蓝（待你拍板），
          其余（已答复 / 按假设 / 非阻塞上报）= 中性记录. */}
      {run.escalations.length > 0 && (
        <span
          className={`run-pill${run.escalations.some((e) => e.status === "pending") ? " pill-act" : ""}`}
        >
          上报{run.escalations.length > 1 ? ` ${run.escalations.length}` : ""}
        </span>
      )}
    </div>
  );
}

function RunCard({
  run,
  agent,
  depth,
  continuationIndex = 0,
  workerToolPhase,
  onOpen,
}: {
  run: ProjectedRun;
  agent: ProjectedAgent | undefined;
  depth: number;
  continuationIndex?: number;
  /** Live worker tool EXECUTION phase (transport-only `tool_use_progress` with run_id). */
  workerToolPhase?: { phase: string; toolName: string };
  /** 深度检视 (RunDetail): tap the card summary to open this run's detail panel. */
  onOpen: () => void;
}) {
  const st = runStatusLabel(run.status, run);
  const name = run.role ?? agent?.role ?? run.agentId;
  // Align desktop agent-node peek: heading + body (tool / output / thinking / summary).
  let activity: {
    heading: string;
    text: string;
    live?: boolean;
    italic?: boolean;
  } | null = null;
  if (run.status === "running") {
    if (agent?.toolProgress) {
      activity = {
        heading: "正在生成",
        text: `${toolLabel(agent.toolProgress.toolName)}…`,
        live: true,
      };
    } else if (workerToolPhase) {
      activity = {
        heading:
          toolPhaseText(workerToolPhase.phase) ?? TOOL_STATUS_LABEL.running,
        text: toolLabel(workerToolPhase.toolName),
        live: true,
      };
    } else {
      const out = lastLine(agent?.output);
      if (out) {
        activity = { heading: "输出中", text: out, live: true };
      } else if (agent?.reasoning) {
        activity = {
          heading: "思考中",
          text: lastLine(agent.reasoning) ?? agent.reasoning,
          live: true,
          italic: true,
        };
      }
    }
  } else if (run.status === "failed" && run.error) {
    activity = { heading: "失败原因", text: run.error };
  } else if (run.outputSummary) {
    activity = { heading: "产出预览", text: run.outputSummary };
  }

  const footBits: string[] = [];
  if (run.model) footBits.push(run.model);
  if (run.status === "completed" && run.durationMs != null) {
    footBits.push(formatRunDuration(run.durationMs));
  }
  if (run.round > 0) footBits.push(`第 ${run.round} 轮`);

  return (
    <div
      className={`run run-${st.tone}`}
      style={depth > 0 ? { marginInlineStart: depth * 12 } : undefined}
    >
      {/* 深度检视入口: the whole card summary is ONE tap target opening the run detail. The
          escalation answer card (textarea / 提交 / 按假设继续) is a SIBLING below, OUTSIDE this
          button, so its interactions are never hijacked by the open-detail tap (架构约束①). */}
      <button type="button" className="run-open" onClick={onOpen}>
        <div className="run-head">
          <span className="run-name">{name}</span>
          <span className={`run-badge badge-${st.tone}`}>{st.label}</span>
        </div>
        <RunPills
          run={run}
          isChild={depth > 0}
          continuationIndex={continuationIndex}
        />
        {run.task && <div className="run-task">{run.task}</div>}
        {activity && (
          <div className={`run-activity${activity.live ? " is-live" : ""}`}>
            <div className="run-activity-head">{activity.heading}</div>
            <div
              className={`run-preview${activity.italic ? " is-italic" : ""}`}
            >
              {activity.text}
            </div>
          </div>
        )}
        {run.error && run.status !== "failed" && (
          <div className="run-error">{run.error}</div>
        )}
        {footBits.length > 0 && (
          <div className="run-foot">
            {footBits.map((b) => (
              <span key={b} className="run-foot-item">
                {b}
              </span>
            ))}
          </div>
        )}
      </button>
      {/* 升级卡已迁独立时间线标记（统一时间线二期 D2）；节点仍保留 上报 pill。 */}
    </div>
  );
}

/** 阻塞式求决策「待你拍板」(②): a worker SUSPENDED on a blocking escalate is awaiting the user
 *  over the open stream. Free-text answer + 提交 / 按假设继续 (== timeout disposition), mirroring
 *  the mobile PauseCard's reduced surface (structured forks fold to prose). decideEscalation
 *  POSTs to the unified resolve endpoint; the stream's `escalation_resolved` then folds this
 *  run's escalation to resolved/timeout and unmounts the card (so busy stays true on success).
 *  统一时间线二期: rendered at the escalation process marker (not under TeamView run cards).
 *  browser_login → BrowserLoginDecisionCard（主钮一键「已登录，继续」，不因空 textarea 禁用；
 *  可开 BrowserLiveSheet）。 */
export function EscalationAnswer({
  esc,
  escalationId,
  conversationId,
  runId,
  onOpenLive,
  onResolved,
}: {
  esc: EscalationSlotEsc;
  escalationId: string;
  conversationId: string;
  /** Worker run id — forwarded to BrowserLive session pin when opening live. */
  runId?: string;
  onOpenLive?: (opts?: OpenBrowserLiveOpts) => void;
  /** Cold recovery: POST success → parent dismisses card (no SSE unmount). */
  onResolved?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState<BrowserLoginSubmitKind | null>(
    null,
  );

  async function decide(decision: EscalationUserDecision) {
    if (busy) return;
    setBusy(true);
    setErr(null);
    // 登记在 POST 之前：抢先回来的 `escalation_resolved` 才认得出是自己点的（B2 · 验收 5）。
    markLocalSettlement(escalationId);
    try {
      const outcome = await decideEscalation(
        conversationId,
        escalationId,
        decision,
      );
      if (outcome === "already_processed") {
        // 这张已经结了，但回执不说是谁结的——升级卡还能由主管仲裁、按假设推进或超时兜底，
        // 认成「另一端处理」就是替用户认领一个他没做过的动作。撤回本端登记，把归属交回带
        // `status` / `arbitrated_by` 的 `escalation_resolved` 帧；这里只如实说结果未知。
        unmarkLocalSettlement(escalationId);
        const noted = noteRemoteSettlementFromReceipt({
          interactionId: escalationId,
          conversationId,
          kind: "escalation",
        });
        if (!noted) {
          setErr(
            "这条已经结了——可能是另一端拍板，也可能是主管仲裁或按假设继续。",
          );
          setBusy(false);
          setSubmitting(null);
        }
      }
      if (onResolved) {
        onResolved();
        return;
      }
      // Leave busy=true on success: escalation_resolved drops `pending` and unmounts this.
    } catch (e) {
      setErr(e instanceof Error ? e.message : "提交失败");
      setBusy(false);
      setSubmitting(null);
    }
  }

  if (esc.browserLogin) {
    return (
      <div className="run-escalation run-escalation-live">
        <BrowserLoginDecisionCard
          roleLabel="队员"
          question={esc.question}
          assumption={esc.assumption || undefined}
          timeoutSeconds={esc.timeoutSeconds}
          busy={busy}
          submitting={submitting}
          onLoggedIn={() => {
            setSubmitting("logged_in");
            void decide({ kind: "answer", answer: "已登录，继续" });
          }}
          onUseAssumption={
            esc.assumption
              ? () => {
                  setSubmitting("use_assumption");
                  void decide({ kind: "use_assumption" });
                }
              : undefined
          }
          onOpenLive={onOpenLive}
          liveRunId={runId}
        />
        {err && <span className="run-error">{err}</span>}
      </div>
    );
  }

  return (
    <div className="run-escalation run-escalation-live">
      <span className="run-escalation-q">↑ {esc.question}</span>
      {esc.assumption && (
        <span className="run-escalation-a">
          {escalationWaitNote({
            assumption: esc.assumption,
            timeoutSeconds: esc.timeoutSeconds,
            awaiting: esc.awaiting,
          })}
        </span>
      )}
      <textarea
        className="run-escalation-note"
        rows={2}
        value={note}
        disabled={busy}
        placeholder={
          esc.assumption
            ? "输入你的决定（留空则点「按假设继续」）"
            : "输入你的决定"
        }
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="run-escalation-actions">
        <button
          type="button"
          className="esc-btn esc-btn-primary"
          disabled={busy || !note.trim()}
          onClick={() => void decide({ kind: "answer", answer: note.trim() })}
        >
          提交
        </button>
        {esc.assumption ? (
          <button
            type="button"
            className="esc-btn esc-btn-neutral"
            disabled={busy}
            onClick={() => void decide({ kind: "use_assumption" })}
          >
            按假设继续
          </button>
        ) : null}
      </div>
      {busy && <span className="run-escalation-busy">处理中…</span>}
      {err && <span className="run-error">{err}</span>}
    </div>
  );
}

// —— 深度检视单个队员 · RunDetail (对齐桌面 RunDetail 抽屉的信息，手机原生重表达) ——

/** 思考开关 → 中文 label (mirrors desktop reasoningMeta): off / 开启. */
function reasoningLabel(thinking: boolean): string {
  if (!thinking) return "关闭";
  return "开启";
}

/** Compact token count (1.2k / 3.4M) — the run-detail 资源 is a power detail, kept scannable. */
function formatCompact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** Integer nano-CNY (1 元 = 1e9) → ¥ caption; all-zero renders「—」(§7.5), never「¥0.00」.
 *  BYOK estimates use ≈ prefix. */
function formatCostYuan(nanoCny: number, estimated = false): string {
  const yuan = nanoCny / 1e9;
  if (yuan <= 0) return "—";
  const body = yuan < 0.01 ? "<¥0.01" : `¥${yuan.toFixed(yuan < 0.1 ? 4 : 2)}`;
  return estimated ? `≈${body} 自带密钥·估算` : body;
}

interface RevisionVersion {
  version: number;
  run: ProjectedRun;
}

/** 同根接续链上的序号（1-based）；非接续为 0。 */
function continuationIndexOf(runs: ProjectedRun[], run: ProjectedRun): number {
  if (run.continuesRunId == null) return 0;
  const siblings = runs.filter((r) => r.continuesRunId === run.continuesRunId);
  const i = siblings.findIndex((r) => r.id === run.id);
  return i >= 0 ? i + 1 : 1;
}
function revisionChainFor(
  runs: ProjectedRun[],
  runId: string,
): RevisionVersion[] | null {
  const run = runs.find((r) => r.id === runId);
  if (!run) return null;
  const originalId = run.continuesRunId ?? run.id;
  const original = runs.find((r) => r.id === originalId);
  if (!original) return null;
  const continuations = runs.filter((r) => r.continuesRunId === originalId);
  if (continuations.length === 0) return null;
  return [
    { version: 1, run: original },
    ...continuations.map((r, i) => ({ version: i + 2, run: r })),
  ];
}

/**
 * 深度检视单个队员 (RunDetail): a mobile-native bottom-sheet reduction of the desktop RunDetail
 * drawer, pinned to one worker run. Sections mirror the desktop order (头部 → 任务/本轮焦点 →
 * 轮次/修订链 → 升级 → 收到的上下文 → 思考 → 工具明细 → 输出/降级简报 → 资源 → 关系); 诊断/审计
 * are intentionally omitted (power-user desktop surfaces). Reads only the fold's ProjectedTurn
 * fields + the transport-only {@link RunToolCall} side channel — no desktop import, no new fold.
 * Navigates to another run (修订链切换 / 关系跳转) via `onSelect`, which re-pins the panel.
 */
function RunDetailPanel({
  run,
  agents,
  runs,
  toolCalls,
  conversationId,
  executionId,
  redirectCapable,
  onSelect,
  onClose,
}: {
  run: ProjectedRun;
  agents: ProjectedAgent[];
  runs: ProjectedRun[];
  toolCalls: RunToolCall[];
  /** 按人干预的提交对象；任一为空（历史草稿 / 无图）则不出干预条。 */
  conversationId: string | null;
  executionId: string | null;
  /** 本幕是否开放「改方向」（辩论幕不开放：辩手须独立对抗）。 */
  redirectCapable: boolean;
  onSelect: (runId: string) => void;
  onClose: () => void;
}) {
  const agent = agents.find((a) => a.id === run.agentId);
  const st = runStatusLabel(run.status, run);
  const name = run.role ?? agent?.role ?? run.agentId;
  const reasoning = agent?.reasoning ?? "";
  const output = agent?.output ?? "";
  const isChild = run.parentRunId != null && run.continuesRunId == null;

  // 本轮焦点: a 续写 run (辩论逐轮 / 同人接续) was fed round-scoped context — show its 本轮焦点 in
  // place of the (inherited) task, mirroring the desktop RunDetail.
  const roundFocus =
    run.continuesRunId != null
      ? run.receivedContext.find((b) => b.channel === "round_focus")?.body
      : undefined;

  // 收到的上下文: the worker-side blocks it was fed. Hide the verbatim 系统提示 (决策②: mobile has
  // no full-prompt reveal) — a worker's blocks are task / deliverable / dependency context anyway.
  const contextBlocks = run.receivedContext.filter(
    (b) => b.channel !== "system",
  );

  const chain = revisionChainFor(runs, run.id);
  const isDebateChain = chain?.some((v) => v.run.stance != null) ?? false;

  // 关系: 依赖 (upstream) / 后续 (downstream) / 上级 (delegate parent) / 子任务 (children).
  const upstream = run.dependsOn
    .map((id) => runs.find((r) => r.id === id))
    .filter((r): r is ProjectedRun => r != null);
  const downstream = runs.filter((r) => r.dependsOn.includes(run.id));
  const parent =
    run.parentRunId != null && run.continuesRunId == null
      ? (runs.find((r) => r.id === run.parentRunId) ?? null)
      : null;
  const children = runs.filter(
    (r) => r.parentRunId === run.id && r.continuesRunId == null,
  );
  const roleOf = (r: ProjectedRun): string =>
    r.role ?? agents.find((a) => a.id === r.agentId)?.role ?? r.agentId;

  const hasResources = !!(run.usage || run.cost || run.model);
  const hasRelations =
    upstream.length > 0 ||
    downstream.length > 0 ||
    parent != null ||
    children.length > 0;

  return (
    <Modal
      className="run-detail"
      onClose={onClose}
      label={`${name} · 队员详情`}
    >
      <header className="rd-head">
        <span className="rd-title">{name}</span>
        <span className={`run-badge badge-${st.tone}`}>{st.label}</span>
        {run.durationMs != null && (
          <span className="rd-dur">{formatRunDuration(run.durationMs)}</span>
        )}
        <button
          type="button"
          className="rd-close"
          onClick={onClose}
          aria-label="关闭"
        >
          ✕
        </button>
      </header>
      <div className="rd-body">
        <RunPills
          run={run}
          isChild={isChild}
          continuationIndex={continuationIndexOf(runs, run)}
        />

        {run.kind !== "captain" &&
        conversationId &&
        executionId &&
        isLiveRunStatus(run.status) ? (
          <RunInterveneBar
            conversationId={conversationId}
            executionId={executionId}
            run={run}
            role={name}
            redirectCapable={redirectCapable}
          />
        ) : null}

        <RunSection title={roundFocus != null ? "本轮焦点" : "任务"}>
          <Markdown content={roundFocus ?? run.task} evidence />
        </RunSection>

        {chain && (
          <RunSection title={isDebateChain ? "轮次" : "接续"}>
            <div className="rd-chain">
              {chain.map(({ version, run: v }) => {
                const current = v.id === run.id;
                const label = isDebateChain
                  ? `第 ${v.round || version} 轮`
                  : version === 1
                    ? "现场"
                    : `续 ×${version - 1}`;
                return (
                  <button
                    key={v.id}
                    type="button"
                    className={`rd-chip${current ? " rd-chip-current" : ""}`}
                    disabled={current}
                    onClick={() => onSelect(v.id)}
                  >
                    {label}
                    {current ? " · 当前" : ""}
                  </button>
                );
              })}
            </div>
          </RunSection>
        )}

        {run.escalations.length > 0 && (
          <RunSection title={`向上升级 (${run.escalations.length})`}>
            <div className="rd-stack">
              {run.escalations.map((esc, i) => {
                const detail = escalationDetail(esc);
                return (
                  <div
                    // biome-ignore lint/suspicious/noArrayIndexKey: per-run escalations are append-only with stable order
                    key={i}
                    className="run-escalation"
                  >
                    <span className="run-escalation-q">
                      ↑ {esc.question}
                      {esc.blocking ? " · 阻断性" : ""}
                    </span>
                    {detail && (
                      <span className="run-escalation-a">{detail}</span>
                    )}
                  </div>
                );
              })}
            </div>
          </RunSection>
        )}

        {contextBlocks.length > 0 && (
          <RunSection title={`收到的上下文 · ${contextBlocks.length} 段`}>
            <div className="recv-list">
              {contextBlocks.map((b, i) => (
                <ContextBlockRow key={`${b.channel}-${i}`} block={b} />
              ))}
            </div>
          </RunSection>
        )}

        {reasoning && (
          <RunSection title="思考">
            <pre className="rd-reasoning">{reasoning}</pre>
          </RunSection>
        )}

        {run.error && (
          <RunSection title="错误">
            <p className="run-error">{run.error}</p>
          </RunSection>
        )}

        {toolCalls.length > 0 && (
          <RunSection title={`工具明细 (${toolCalls.length})`}>
            <div className="rd-tools">
              {toolCalls.map((c) => (
                <RunToolRow key={c.id} call={c} />
              ))}
            </div>
          </RunSection>
        )}

        {output && (
          <RunSection title="输出">
            <div className="rd-output">
              <Markdown content={output} evidence />
            </div>
          </RunSection>
        )}

        {!hasSuccessfulHandoff(toolCalls) &&
          (run.debrief ? (
            <DebriefBlock debrief={run.debrief} />
          ) : run.outputSummary ? (
            <RunSection title="结论">
              <Markdown content={run.outputSummary} evidence />
            </RunSection>
          ) : null)}

        {hasResources && <ResourceBlock run={run} agent={agent} />}

        {hasRelations && (
          <RunSection title="关系">
            <div className="rd-stack">
              {upstream.length > 0 && (
                <RunRefGroup
                  label="依赖"
                  runs={upstream}
                  roleOf={roleOf}
                  onSelect={onSelect}
                />
              )}
              {downstream.length > 0 && (
                <RunRefGroup
                  label="后续"
                  runs={downstream}
                  roleOf={roleOf}
                  onSelect={onSelect}
                />
              )}
              {parent && (
                <RunRefGroup
                  label="上级"
                  runs={[parent]}
                  roleOf={roleOf}
                  onSelect={onSelect}
                />
              )}
              {children.length > 0 && (
                <RunRefGroup
                  label={`子任务 (${children.length})`}
                  runs={children}
                  roleOf={roleOf}
                  onSelect={onSelect}
                />
              )}
            </div>
          </RunSection>
        )}
      </div>
    </Modal>
  );
}

/**
 * 按人干预条（只改这个人的方向 / 只停这个人）—— 点开队员就在手边，不必再翻到底。
 *
 * 手机上这两件事此前**一处都没有**：列表按人显示每个队员在干什么，能操作的却只有整轮
 * 停止。现在提到队员详情最上方，一次点击（点卡）即可达。
 *
 * 调用方只在 `isLiveRunStatus`（running / pending）时挂上；终局整条不渲染——点不动也改
 * 不了，死按钮没有教学价值，也不再写灰字原因。排队未开工仍画：可停；改方向变灰，原因
 * 写成看得见的一行字（手机没有 hover）。判定与文案与桌面共用
 * `protocol-fold-kit/runIntervene`，两端说同一句。
 */
function RunInterveneBar({
  conversationId,
  executionId,
  run,
  role,
  redirectCapable,
}: {
  conversationId: string;
  executionId: string;
  run: ProjectedRun;
  role: string;
  redirectCapable: boolean;
}) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<"stop" | "redirect" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 停止请求已发出：不假装 run 已停（状态由引擎的后续帧改），只把按钮切成请求中。
  // 存在组件外，关掉详情再打开仍算数——否则同一个 run 上可以反复发同一条请求。
  const stopSent = useRunStopSent(executionId, run.id, run.status);

  const stopGate = runStopGate(run.status);
  const redirectGate = runRedirectGate(run.status);
  const reason = redirectCapable
    ? (redirectGate.reason ?? stopGate.reason)
    : stopGate.reason;
  const stopping = stopSent || busy === "stop";

  async function stopMember() {
    if (!stopGate.enabled || busy) return;
    setBusy("stop");
    setErr(null);
    try {
      const ack = await submitRunStop(conversationId, {
        executionId,
        runId: run.id,
      });
      // 引擎够不着这个 run 时什么都没入队——不许留下「停止请求中…」，那是许一个空愿。
      if (ack.accepted) markRunStopSent(executionId, run.id);
      else setErr(interveneAckText(ack));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "停止失败");
    } finally {
      setBusy(null);
    }
  }

  async function submitRedirect() {
    const feedback = draft.trim();
    if (!feedback || busy) return;
    setBusy("redirect");
    setErr(null);
    try {
      const ack = await submitRunRedirect(conversationId, {
        executionId,
        runId: run.id,
        feedback,
      });
      if (!ack.accepted) {
        setErr(interveneAckText(ack));
        return;
      }
      setComposerOpen(false);
      setDraft("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "提交失败");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rd-intervene">
      <div className="rd-intervene-actions">
        {redirectCapable && (
          <InterveneButton
            gate={redirectGate}
            label="改这个人的方向"
            tone="primary"
            disabled={busy != null}
            onPress={() => {
              setComposerOpen((v) => !v);
              if (!composerOpen && !draft) {
                setDraft(`请按以下方向调整「${role}」的产出：`);
              }
            }}
          />
        )}
        <InterveneButton
          gate={stopGate}
          label={stopping ? "停止请求中…" : "停止这位队员"}
          tone="neutral"
          disabled={busy != null || stopping}
          onPress={() => void stopMember()}
        />
      </div>

      {/* 只在能点的时候才说这两件事分别意味着什么；点不动时上面的原因行才是要读的。 */}
      {stopGate.enabled && !stopping ? (
        <p className="rd-intervene-note">
          只作用于这一位队员，主 Agent 与这轮对话继续（不是「停止整轮」）。
        </p>
      ) : null}
      {stopping ? (
        <p className="rd-intervene-note">
          停止请求已发出，等引擎确认后这位队员的状态才会变。
        </p>
      ) : null}
      {reason ? <p className="rd-intervene-why">{reason}</p> : null}

      {composerOpen && redirectGate.enabled ? (
        <div className="rd-intervene-composer">
          <textarea
            className="run-escalation-note"
            rows={3}
            value={draft}
            disabled={busy === "redirect"}
            placeholder="具体、可执行的修改方向…"
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="run-escalation-actions">
            <button
              type="button"
              className="esc-btn esc-btn-primary"
              disabled={busy === "redirect" || !draft.trim()}
              onClick={() => void submitRedirect()}
            >
              提交改方向
            </button>
            <button
              type="button"
              className="esc-btn esc-btn-neutral"
              disabled={busy === "redirect"}
              onClick={() => setComposerOpen(false)}
            >
              取消
            </button>
          </div>
          <p className="rd-intervene-note">
            提交后这位队员在飞的工作立刻取消，带着你的新方向重跑——接不上现场就从头重做，
            这一段要重新花时间和钱。
          </p>
        </div>
      ) : null}

      {err ? <p className="run-error">{err}</p> : null}
    </div>
  );
}

/**
 * 不可用走 `aria-disabled` 而非原生 `disabled`：真 disabled 的按钮读屏会整枚跳过，用户
 * 连「这里本来有个入口」都不知道。原因写在 `aria-label` 与旁边那行字里，两处都读得到。
 * 原生 `disabled` 只留给「请求已在飞」这类真忙态。
 */
function InterveneButton({
  gate,
  label,
  tone,
  disabled,
  onPress,
}: {
  gate: InterveneGate;
  label: string;
  tone: "primary" | "neutral";
  disabled: boolean;
  onPress: () => void;
}) {
  const unavailable = !gate.enabled;
  return (
    <button
      type="button"
      className={`esc-btn esc-btn-${tone}${unavailable ? " esc-btn-unavailable" : ""}`}
      disabled={disabled && !unavailable}
      aria-disabled={unavailable || undefined}
      aria-label={unavailable ? `${label}（${gate.reason}）` : label}
      onClick={() => {
        if (unavailable) return;
        onPress();
      }}
    >
      {label}
    </button>
  );
}

/** A titled run-detail section (label + body), the mobile mirror of the desktop detail `Section`. */
function RunSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="rd-section">
      <h4 className="rd-section-title">{title}</h4>
      {children}
    </section>
  );
}

/** One label→value row in the 资源 metrics block. */
function MetricRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rd-metric">
      <span className="rd-metric-label">{label}</span>
      <span className={`rd-metric-value${mono ? " rd-mono" : ""}`}>
        {value}
      </span>
    </div>
  );
}

/** A labelled list of related runs (依赖 / 后续 / 上级 / 子任务) — each row navigates the panel to
 *  that run (关系跳转), reusing the same status tone dot as the cards. */
function RunRefGroup({
  label,
  runs,
  roleOf,
  onSelect,
}: {
  label: string;
  runs: ProjectedRun[];
  roleOf: (r: ProjectedRun) => string;
  onSelect: (runId: string) => void;
}) {
  return (
    <div className="rd-refs">
      <div className="rd-refs-label">{label}</div>
      {runs.map((r) => {
        const st = runStatusLabel(r.status, r);
        return (
          <button
            key={r.id}
            type="button"
            className="rd-ref"
            onClick={() => onSelect(r.id)}
          >
            <span className={`rd-ref-dot dot-${st.tone}`} />
            <span className="rd-ref-name">{roleOf(r)}</span>
            {r.task && <span className="rd-ref-task">{r.task}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** One worker tool call (RunDetail · 工具明细) — reuses the CEO-side ToolStep visual language
 *  (.tool*): 中文名 + 参数 detail + status, click to expand the raw args / result pre block. The
 *  rich 6-类 tool rendering (desktop) is intentionally NOT ported (架构决策③).
 *  成功 handoff 例外：行即简报卡（与页脚是否跳过 DebriefBlock 同一判定）。 */
function RunToolRow({ call }: { call: RunToolCall }) {
  if (isSuccessfulHandoff(call.toolName, call.status)) {
    return <HandoffSuccessRow args={call.arguments} />;
  }
  return <GenericRunToolRow call={call} />;
}

function GenericRunToolRow({ call }: { call: RunToolCall }) {
  const [open, setOpen] = useState(false);
  const args = Object.keys(call.arguments).length > 0 ? call.arguments : null;
  const detail = toolDetail(call.arguments, call.toolName);
  const ceilingGuidance =
    call.status === "error" &&
    isFileReadCeilingGuidance(call.toolName, call.result);
  const status =
    call.status === "running"
      ? TOOL_STATUS_LABEL.running
      : ceilingGuidance
        ? TOOL_GUIDANCE_LABEL
        : call.status === "error"
          ? TOOL_STATUS_LABEL.error
          : TOOL_STATUS_LABEL.success;
  // Prefer product `failure.message`; fall back to model-facing `result` when absent.
  const faceText = call.failure?.message ?? call.result;
  const hasBody = !!args || (faceText != null && faceText !== "");
  const shellClass = ceilingGuidance
    ? "tool tool-guidance"
    : `tool tool-${call.status}`;
  return (
    <div className={shellClass}>
      <button
        type="button"
        className="tool-head"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="tool-name">
          <span className="tool-label">
            {toolLabel(call.toolName, call.arguments)}
          </span>
          {detail && <span className="tool-detail">{detail}</span>}
        </span>
        <span className="tool-status">{status}</span>
      </button>
      {open && hasBody && (
        <div className="tool-body">
          {args && (
            <pre className="tool-pre">{JSON.stringify(args, null, 2)}</pre>
          )}
          {faceText != null && faceText !== "" && (
            <pre className="tool-pre">{faceText}</pre>
          )}
        </div>
      )}
    </div>
  );
}

/** One 收到的上下文 block (RunDetail) — reuses the CEO-side ReceivedContext visual language
 *  (.recv*): channel origin + heading, then the body the worker read + any files / 已截断 mark. */
function ContextBlockRow({ block }: { block: ContextBlockWire }) {
  return (
    <div className="recv-item">
      <div className="recv-head">
        <span className="recv-channel">
          {CONTEXT_CHANNEL_LABEL[block.channel] ?? block.channel}
        </span>
        {block.heading && <span className="recv-heading">{block.heading}</span>}
      </div>
      {block.body && <pre className="recv-body">{block.body}</pre>}
      {block.files.length > 0 && (
        <div className="recv-files">
          {block.files.map((f) => (
            <span key={f} className="recv-file">
              {f}
            </span>
          ))}
        </div>
      )}
      {block.truncated && (
        <div className="recv-trunc">已截断（完整内容已传给 AI）</div>
      )}
    </div>
  );
}

/** 资源用量 (RunDetail) — 档位 / 思考 (from the agent) + 模型 / 成本 / token (from the run's
 *  run_completed usage+cost). All-zero cost shows「—」. */
function ResourceBlock({
  run,
  agent,
}: {
  run: ProjectedRun;
  agent: ProjectedAgent | undefined;
}) {
  const { usage, cost, model } = run;
  const money =
    cost && cost.total > 0
      ? { nano: cost.total, estimated: false }
      : cost?.estimated_total && cost.estimated_total > 0
        ? { nano: cost.estimated_total, estimated: true }
        : null;
  const tokenTotal = usage ? usage.input + usage.output : 0;
  const byokUnpriced = cost?.pricing_source === "unpriced";
  // 未计价 ≠ 估算：社区价目未命中时连估算值都没有，标注要如实（与桌面同口径）。
  const byokLabel =
    cost?.pricing_source === "unpriced" ? "自带密钥·未计价" : "自带密钥·估算";
  const cache = usage ? cacheUsageDisplay(usage) : null;
  return (
    <RunSection title="资源">
      <div className="rd-metrics">
        {agent && (
          <MetricRow label="思考" value={reasoningLabel(agent.thinking)} />
        )}
        {model && <MetricRow label="模型" value={model} mono />}
        {money && money.nano > 0 && (
          <MetricRow
            label={money.estimated ? "成本（自带密钥·估算）" : "成本"}
            value={formatCostYuan(money.nano, money.estimated)}
          />
        )}
        {!money && usage && tokenTotal > 0 && byokUnpriced && (
          <MetricRow
            label={byokLabel}
            value={`${formatCompact(usage.input)}↑ / ${formatCompact(usage.output)}↓`}
          />
        )}
        {usage && (
          <>
            <MetricRow label="输入 token" value={formatCompact(usage.input)} />
            {cache?.billedAsMiss ? (
              <MetricRow
                label={CACHE_BILLED_AS_MISS_LABEL}
                value={formatCompact(cache.cacheMiss)}
              />
            ) : (
              <MetricRow
                label="缓存命中"
                value={`${formatCompact(usage.cache_hit)} · ${cache?.hitRatePercent ?? 0}%`}
              />
            )}
            <MetricRow label="输出 token" value={formatCompact(usage.output)} />
            <MetricRow
              label="推理 token"
              value={formatCompact(usage.reasoning)}
            />
          </>
        )}
      </div>
    </RunSection>
  );
}
