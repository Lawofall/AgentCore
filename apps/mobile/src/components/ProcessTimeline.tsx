import { HandoffSuccessRow } from "@/components/DebriefBlock";
import {
  InterjectionBubbles,
  type InterjectionItem,
} from "@/components/InterjectionBubbles";
import { Markdown } from "@/components/Markdown";
import {
  EscalationAnswer,
  TeamView,
  escalationDetail,
} from "@/components/TeamView";
import {
  TOOL_GUIDANCE_LABEL,
  TOOL_STATUS_LABEL,
  toolDetail,
  toolLabel,
  toolPhaseText,
} from "@/components/assistantLabels";
import { isSuccessfulHandoff } from "@/components/handoffBrief";
import {
  type TimelineSlotLookup,
  classifyTimelineInteractionCard,
  timelineEmptyNode,
  timelineIntentionalEmpty,
} from "@/components/timelineCardSlot";
import {
  codeDiagnosticsSummary,
  extractCodeDiagnostics,
} from "@/lib/codeDiagnostics";
import { isFileReadCeilingGuidance } from "@/lib/fileReadCeiling";
import type { SupportDiagnosticIds } from "@/lib/supportDiagnostics";
import type { TurnOutcome } from "@/lib/turnOutcome";
import { isVerifyBudgetExceeded } from "@/lib/verifyBudget";
import type {
  EscalationSlot,
  HotDecisionTrace,
  RunToolCall,
  StageCardTrace,
} from "@/protocol/fold";
import { actAuthorizedByLabel } from "@/protocol/fold";
import type { TeamPreviewTrace } from "@/protocol/teamPreviewTraces";
import type {
  Citation,
  EvidenceLedgerEntry,
  ProcessStep,
  ToolPhase,
} from "@agentcore/contract-types";
import type {
  ProjectedAct,
  ProjectedAgent,
  ProjectedRun,
  ProjectedTeamNote,
  TurnStatus,
} from "@agentcore/protocol-conformance";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import "@/components/ProcessTimeline.css";

type ToolStepData = Extract<ProcessStep, { kind: "tool" }>;

/** 跨回合续接锚点文案：新开一队、接着上一张继续（不是往旧图里加人）。
 * 手机无跨气泡跳转，仅文案行。`addedCount` 仍由旧 journal 传入，不再写入文案。 */
export function graphAppendAnchorLabel(
  _addedCount: number,
  _actKind?: string | null,
  authorizedBy?: string | null,
): string {
  const base = "新开一队、接着上一张继续";
  const auth = actAuthorizedByLabel(authorizedBy);
  return auth ? `${base} · ${auth}` : base;
}

/** 新契约：`prev_execution_id` 链上文案（本回合已有完整 TeamView）。 */
export function prevGraphAnchorLabel(): string {
  return "新开一队、接着上一张继续";
}

/**
 * finish_guard 回炉 chip 文案：流式中且该 rework 后尚无 content 时用人话提示进行中，
 * 否则保持完成时态（复制出口仍用完成时态，见 messageExport）。
 */
export function reworkChipLabel(
  isStreaming: boolean,
  hasContentAfter: boolean,
): string {
  if (isStreaming && !hasContentAfter) return "正在按规则修订…";
  return "引用/格式核验后已重写";
}

/**
 * True once a **worker** left the never-started states (`pending`, or terminal
 * `skipped` from finalize before a start). Captain `run_started` is the CEO
 * turn itself and must not count (journal hydrate restores a frame live SSE drops).
 * Gates TeamView so team_preview hang / stop-before-start stay graph-less;
 * plan_review mid-wave pause (completed worker nodes exist) still shows the graph.
 * Aligns desktop `debatePreviewPlacement.teamHasStartedRuns`.
 */
export function teamHasStartedRuns(
  runs: readonly { status: string; kind?: string | null }[],
): boolean {
  return runs.some(
    (r) =>
      r.kind !== "captain" && r.status !== "pending" && r.status !== "skipped",
  );
}

/** 开工卡「已授权开工」：仅 `continue`。team_preview 上 `adjust` 是回灌 CEO、不开工. */
export function isKickoffGoDecision(decision: unknown): boolean {
  return decision === "continue";
}

/**
 * Hang stays graph-less. After authorize (continue) pending nodes still
 * show. Aligns desktop `shouldShowTeamGraph`.
 */
export function shouldShowTeamGraph(
  runs: readonly { status: string; kind?: string | null }[] | null | undefined,
  kickoffReleased = false,
): boolean {
  const list = runs ?? [];
  if (teamHasStartedRuns(list)) return true;
  return kickoffReleased && list.length > 0;
}

/**
 * 时间线尾部是否已有活性提示。通用思考尾迹只在「回合在流且尾部无活节点」时出现，
 * 避免与跑着的工具、流式 Thought/正文、wait 空转或协作图重复。
 * `graph_append` 自身只画锚点，活性来自它所属的协作图，故与 `team` 同闸。
 */
export function timelineTailHasLiveCue(
  last: ProcessStep | undefined,
  opts?: { teamGraphVisible?: boolean },
): boolean {
  if (!last) return false;
  if (last.kind === "tool") {
    return last.status === "running" || last.tool_name === "wait";
  }
  if (
    last.kind === "reasoning" ||
    last.kind === "content" ||
    last.kind === "rework"
  ) {
    return true;
  }
  if (last.kind === "team" || last.kind === "graph_append")
    return opts?.teamGraphVisible === true;
  return false;
}

export function kickoffReleasedFromCold(
  entries: Iterable<{
    kind: string;
    messageId: string;
    status: string;
    resolution?: Record<string, unknown>;
  }>,
  messageId: string | null | undefined,
): boolean {
  if (!messageId) return false;
  let released = false;
  let pending = false;
  for (const e of entries) {
    if (e.kind !== "team_preview") continue;
    if (e.messageId !== messageId) continue;
    if (e.status === "pending") {
      pending = true;
      continue;
    }
    if (e.status !== "resolved" && e.status !== "submitting") continue;
    if (isKickoffGoDecision(e.resolution?.decision)) released = true;
  }
  return released && !pending;
}

export interface TeamProjection {
  agents: ProjectedAgent[];
  runs: ProjectedRun[];
  progress: { completed: number; total: number };
  /** 幕序列（批 A4）：透传给 {@link TeamView} 做多幕列表分组。 */
  acts?: ProjectedAct[];
  /** 团队便签墙 (§2.2 通): notes workers broadcast to their concurrent siblings this turn
   *  (`team_note_posted`), in post order — rendered by {@link TeamView}. Optional so the promo
   *  still (which builds team from a truncated vector) and legacy callers keep compiling. */
  teamNotes?: ProjectedTeamNote[];
  /** Turn lifecycle from ProjectedTurn — drives team-notes default expand/collapse. */
  status?: TurnStatus | null;
  /** 阻塞式求决策 (②): forwarded straight to {@link TeamView} via the `{...team}` spread so a
   *  worker's pending escalation can render as an actionable answer card. All optional — a
   *  read-only / history team simply omits them. */
  conversationId?: string | null;
  /** 本图 execution id — 按人干预（只停 / 只改这一个队员）的提交目标。 */
  executionId?: string | null;
  /** runId → pending escalation id from ProjectedTurn.interactions (P3). */
  pendingEscalations?: Map<string, string>;
  /** Live turn → the pending escalation is answerable over the open stream. */
  escalationsInteractive?: boolean;
  /** 队员工具明细 (RunDetail · 工具调用): runId → the worker's tool calls, from the transport-only
   *  sibling {@link import("@/protocol/fold").extractRunToolCalls} (the fold drops run-scoped tool
   *  IO, so the run-detail panel reads it from here). Absent → the panel shows no tool section. */
  runToolCalls?: Map<string, RunToolCall[]>;
  /** Worker `tool_use_progress` (run_id): runId → live EXECUTION phase (transport-only sibling
   *  {@link import("@/protocol/fold").extractWorkerToolPhases}). */
  workerToolPhases?: Map<string, { phase: string; toolName: string }>;
  /** 场级证据台账（`extractEvidenceLedger`）：辩论徽章 `#eN` 解析。 */
  evidenceLedger?: EvidenceLedgerEntry[];
  /** 终态条「用时」= 回合墙钟跨度（`turnElapsedMs(events)`）。缺省 0 = 不显示。 */
  elapsedMs?: number;
  /** 运行态条「用时」墙钟锚点（首条协作事件 epoch ms）。 */
  startedAtMs?: number | null;
  /** Live `coordination_wait` n/m（旁路 extract，不进 ProjectedTurn）。有则盖过 fold progress。 */
  waitProgress?: { completed: number; total: number } | null;
  /** Live `execution_detached`（旁路 extract）。hydrate 后 TeamView 仍可用队员在跑补徽标。 */
  detached?: boolean;
  /** Arbiter verdict when the strip is the primary failure face. */
  outcome?: TurnOutcome | null;
  supportIds?: SupportDiagnosticIds;
  onRetry?: () => void;
}

/** Seconds a tool has been running, ticking client-side from when this row first saw `running`
 *  (≈ the tool_use_start instant) — a liveliness cue for a BLOCKING tool (web_search) whose
 *  execution streams no incremental progress. Resets when not running. Mirrors desktop. */
function useRunningElapsed(running: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!running) {
      setElapsed(0);
      return;
    }
    const start = Date.now();
    const id = setInterval(
      () => setElapsed(Math.floor((Date.now() - start) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [running]);
  return elapsed;
}

/**
 * 「直播中自动展开、收场后按保存值」——手机端会话态镜像（各端全新建；不落盘、不碰桌面
 * disclosure store）。流式临时收起/展开只在本轮 live 有效；收场回到 settled 选择。
 */
function useStreamAwareDisclosure(
  live: boolean,
  opts?: { liveDefault?: boolean; settledDefault?: boolean },
): readonly [boolean, () => void] {
  const liveDefault = opts?.liveDefault ?? true;
  const settledDefault = opts?.settledDefault ?? false;
  const [stored, setStored] = useState(settledDefault);
  const [liveOverride, setLiveOverride] = useState<boolean | null>(null);
  const prevLive = useRef(live);

  useEffect(() => {
    if (prevLive.current && !live) setLiveOverride(null);
    prevLive.current = live;
  }, [live]);

  const expanded = live ? (liveOverride ?? liveDefault) : stored;

  const toggle = useCallback(() => {
    if (live) setLiveOverride((v) => !(v ?? liveDefault));
    else setStored((v) => !v);
  }, [live, liveDefault]);

  return [expanded, toggle] as const;
}

/** Last path segment of a detail (a file 名 from a path / url); the whole string when it
 *  carries no separator (a query / pattern). Keeps a group summary compact. */
function baseName(detail: string): string {
  if (!detail) return "";
  const segs = detail.split(/[/\\]/);
  return segs[segs.length - 1] || detail;
}

type TimelineNode =
  | Exclude<ProcessStep, { kind: "tool" }>
  | { kind: "tool"; step: ToolStepData }
  | { kind: "tool-group"; tools: ToolStepData[] };

/** Coalesce consecutive tool steps into collapsible groups (前端UX设计.md §一B): a run of
 *  ≥2 adjacent tool steps folds into one `tool-group`, a lone tool stays inline, and
 *  reasoning/content break runs so chronological order is preserved. Mobile keeps its own
 *  copy of this fold — it is chrome, not a protocol fold (no conformance), so the desktop
 *  `groupToolRuns` is intentionally NOT imported (各端全新建 per cross-platform-frontend). */
function groupToolRuns(steps: ProcessStep[]): TimelineNode[] {
  const nodes: TimelineNode[] = [];
  let run: ToolStepData[] = [];
  const flush = () => {
    if (run.length === 0) return;
    nodes.push(
      run.length === 1
        ? { kind: "tool", step: run[0] }
        : { kind: "tool-group", tools: run },
    );
    run = [];
  };
  for (const s of steps) {
    if ((s as { kind: string }).kind === "ask") continue;
    if (s.kind === "tool") run.push(s);
    else {
      flush();
      nodes.push(s);
    }
  }
  flush();
  return nodes;
}

/**
 * 锚 A · ProcessStep kind（编译期响）：漏处理一个 kind，`never` 形参收不下，tsc 失败。
 * 运行期不抛（渲染路径上抛出会白屏）；与 fold `noteUnhandledEvent` 同款。
 */
function noteUnhandledProcessStep(x: never): string {
  return `unknown-${String(x)}`;
}

/** Stable render keys（对称桌面 timelineNodeKeys）：中段 insert 不位移后续行。 */
function timelineNodeKeys(nodes: TimelineNode[]): string[] {
  const ordinals = new Map<string, number>();
  const ordinalKey = (kind: "reasoning" | "content" | "rework") => {
    const n = (ordinals.get(kind) ?? 0) + 1;
    ordinals.set(kind, n);
    return `${kind}-${n}`;
  };
  return nodes.map((node) => {
    switch (node.kind) {
      case "team":
        return `team-${node.execution_id}`;
      case "graph_append":
        return `gappend-${node.execution_id}-${node.host_message_id}`;
      case "team_preview":
        return `tp-${node.checkpoint_id}`;
      case "checkpoint":
        return `cp-${node.checkpoint_id}`;
      case "plan_review":
        return `pr-${node.checkpoint_id}`;
      case "escalation":
        return `esc-${node.escalation_id}`;
      case "approval":
        return `appr-${node.approval_id}`;
      case "stage_card":
        return `sc-${node.stage_card_id}`;
      case "user_interjection":
        return `uinj-${node.interjection_id}`;
      case "tool":
        return `tool-${node.step.id}`;
      case "tool-group":
        return `tgrp-${node.tools[0]?.id ?? "empty"}`;
      case "reasoning":
      case "content":
      case "rework":
        return ordinalKey(node.kind);
      default:
        return noteUnhandledProcessStep(node);
    }
  });
}

/** Header summary for a folded tool group: per-category counts in first-seen order
 *  (「Read file 6 · Edit file 2」), or each call's name/query when a single-category run is ≤3. */
function toolGroupSummary(tools: ToolStepData[]): string {
  const sameKind = tools.every((t) => t.tool_name === tools[0].tool_name);
  if (sameKind && tools.length <= 3) {
    const label = toolLabel(tools[0].tool_name, tools[0].arguments);
    const names = tools.map((t) =>
      baseName(toolDetail(t.arguments, t.tool_name)),
    );
    if (names.every(Boolean)) return `${label} ${names.join(" · ")}`;
  }
  const order: string[] = [];
  const counts = new Map<string, number>();
  for (const t of tools) {
    const label = toolLabel(t.tool_name, t.arguments);
    if (!counts.has(label)) order.push(label);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return order.map((l) => `${l} ${counts.get(l)}`).join(" · ");
}

/** Thought 折叠覆盖面：推理/工具 + 弱式决策痕迹（批准/委派授权/推进卡）。
 *  强交互卡（checkpoint/ask/escalation/…）仍外置可见；手机操作仍走 Sheet。 */
function isProcessNode(node: TimelineNode): boolean {
  return (
    node.kind === "reasoning" ||
    node.kind === "tool" ||
    node.kind === "tool-group" ||
    node.kind === "approval" ||
    node.kind === "stage_card"
  );
}

function countProcessStats(nodes: TimelineNode[]) {
  let reasoningCount = 0;
  let toolCount = 0;
  for (const node of nodes) {
    if (node.kind === "reasoning") reasoningCount++;
    else if (node.kind === "tool") toolCount++;
    else if (node.kind === "tool-group") toolCount += node.tools.length;
  }
  return { reasoningCount, toolCount };
}

function formatProcessSummary(
  reasoningCount: number,
  toolCount: number,
): string {
  const parts: string[] = [];
  if (reasoningCount > 0) {
    parts.push(
      `Thought ${reasoningCount} step${reasoningCount === 1 ? "" : "s"}`,
    );
  }
  if (toolCount > 0) {
    parts.push(`Used ${toolCount} tool${toolCount === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function ThinkingDots() {
  return (
    <span className="thinking-dots" aria-hidden>
      <span className="thinking-dot" style={{ animationDelay: "0ms" }} />
      <span className="thinking-dot" style={{ animationDelay: "150ms" }} />
      <span className="thinking-dot" style={{ animationDelay: "300ms" }} />
    </span>
  );
}

function ThinkingHeader({
  isStreaming,
  expanded,
  streamingLabel,
  doneLabel,
  onToggle,
}: {
  isStreaming: boolean;
  expanded: boolean;
  streamingLabel: string;
  doneLabel: string;
  onToggle: () => void;
}) {
  return (
    <button type="button" className="thinking-header" onClick={onToggle}>
      {isStreaming ? (
        <>
          <ThinkingDots />
          <span>{streamingLabel}</span>
        </>
      ) : (
        <>
          <span>{doneLabel}</span>
          <span className="thinking-chevron" aria-hidden>
            {expanded ? "▾" : "▸"}
          </span>
        </>
      )}
    </button>
  );
}

/** Collapsible thinking block — live auto-expand / settled collapse (对称桌面 InlineReasoning). */
export function Reasoning({
  text,
  isStreaming = false,
}: {
  text: string;
  isStreaming?: boolean;
}) {
  const [expanded, toggle] = useStreamAwareDisclosure(isStreaming, {
    settledDefault: false,
  });

  return (
    <div className="process-thought">
      <ThinkingHeader
        isStreaming={isStreaming}
        expanded={expanded}
        streamingLabel="Thinking…"
        doneLabel="Thought"
        onToggle={toggle}
      />
      {expanded && (
        <div className="process-thought-body">
          <Markdown content={text} isStreaming={isStreaming} muted />
        </div>
      )}
    </div>
  );
}

/** The single-agent inline timeline: content (Markdown), thinking (collapsible), and tool
 *  calls, in the order the model produced them. Consecutive tools coalesce into a
 *  collapsible {@link ToolGroup} (≥2); a lone tool stays an inline {@link ToolStep}.
 *  Settled turns fold process nodes into a Thought/tools summary (对称桌面). */
export function ProcessTimeline({
  steps,
  citations,
  citationToDisplay,
  evidenceLedger,
  isStreaming = false,
  messageId: _messageId,
  fallbackContent,
  team,
  escalationSlots,
  hotTraces,
  stageCardTraces,
  teamPreviewTraces,
  toolPhases,
  graphAppendActKinds,
  graphAppendAuthorizedBy,
  prevExecutionIds,
  userInterjections,
  turnClosed = false,
  onOpenBrowserLive,
  kickoffReleased = false,
}: {
  steps: ProcessStep[];
  citations?: Citation[];
  citationToDisplay?: ReadonlyMap<number, number>;
  evidenceLedger?: EvidenceLedgerEntry[];
  /** Live turn → stream-aware Thought/tool expand + Markdown streaming path. */
  isStreaming?: boolean;
  /** Turn key for disclosure identity (optional; session-scoped when absent). */
  messageId?: string | null;
  /** Scalar content when process has no `content` step yet (slot before team). */
  fallbackContent?: string;
  team?: TeamProjection;
  escalationSlots?: Map<string, EscalationSlot>;
  hotTraces?: Map<string, HotDecisionTrace>;
  stageCardTraces?: Map<string, StageCardTrace>;
  /** 开工卡 / 开赛卡 resolved 痕迹（桌面 TeamPreviewCard 对等）。 */
  teamPreviewTraces?: Map<string, TeamPreviewTrace>;
  toolPhases?: Map<string, ToolPhase>;
  graphAppendActKinds?: Map<string, string>;
  graphAppendAuthorizedBy?: Map<string, string>;
  /** execution_id → prev_execution_id（`extractPrevExecutionIds`）；新契约续自文案。 */
  prevExecutionIds?: Map<string, string>;
  /** 旁路插话叶（fold → userInterjections）；marker 只钉位置，按 id 查正文/五态。 */
  userInterjections?: readonly InterjectionItem[];
  /** 回合已收口 → received 派生态「未被主 Agent 读取」。 */
  turnClosed?: boolean;
  /** 开工卡已授权 continue：pending 编制也出图. */
  kickoffReleased?: boolean;
  onOpenBrowserLive?: (opts?: { runId?: string }) => void;
}) {
  const nodes = groupToolRuns(steps);
  const nodeKeys = timelineNodeKeys(nodes);
  const { reasoningCount, toolCount } = countProcessStats(nodes);
  const shouldCollapseProcess =
    !isStreaming &&
    (reasoningCount > 0 || toolCount > 0) &&
    !(reasoningCount === 1 && toolCount === 0);
  const [processExpanded, toggleProcess] = useStreamAwareDisclosure(
    isStreaming,
    { settledDefault: false },
  );
  const processSummary = formatProcessSummary(reasoningCount, toolCount);

  const hasContentStep = steps.some((s) => s.kind === "content");
  const hasTeamMarker = steps.some((s) => s.kind === "team");
  const fallbackText = fallbackContent ?? "";
  const fallbackBeforeTeamIdx =
    !hasContentStep && fallbackText
      ? nodes.findIndex((n) => n.kind === "team" || n.kind === "graph_append")
      : -1;
  const showFallbackAfter =
    !hasContentStep && Boolean(fallbackText) && fallbackBeforeTeamIdx < 0;

  // 回合在流且尾部无活节点 → 通用思考尾迹（delegate/debate 顶位是 team 标记，不是已完成 tool）。
  const last = steps[steps.length - 1];
  const teamGraphVisible = Boolean(
    team && shouldShowTeamGraph(team.runs, kickoffReleased),
  );
  const showThinkingTail =
    isStreaming && !timelineTailHasLiveCue(last, { teamGraphVisible });

  const slotLookup: TimelineSlotLookup = {
    escalationSlots,
    hotTraces,
    stageCardTraces,
    teamPreviewTraces,
    userInterjections,
  };

  const renderFallback = (key: string) => (
    <div key={key} className="process-narration">
      <Markdown
        content={fallbackText}
        citations={citations}
        citationToDisplay={citationToDisplay}
        evidenceLedger={evidenceLedger}
        isStreaming={isStreaming}
      />
    </div>
  );

  const renderNode = (node: TimelineNode, i: number) => {
    const live = isStreaming && i === nodes.length - 1;
    const nodeKey = nodeKeys[i];

    if (node.kind === "content") {
      return (
        <div key={nodeKey} className="process-narration">
          <Markdown
            content={node.text}
            citations={citations}
            citationToDisplay={citationToDisplay}
            evidenceLedger={evidenceLedger}
            isStreaming={live}
          />
        </div>
      );
    }
    if (node.kind === "reasoning") {
      return <Reasoning key={nodeKey} text={node.text} isStreaming={live} />;
    }
    if (node.kind === "team") {
      if (!team || !shouldShowTeamGraph(team.runs, kickoffReleased))
        return timelineIntentionalEmpty();
      const hasPrev = Boolean(prevExecutionIds?.get(node.execution_id));
      return (
        <Fragment key={nodeKey}>
          {hasPrev ? (
            <div className="graph-append-anchor">{prevGraphAnchorLabel()}</div>
          ) : null}
          <TeamView {...team} />
        </Fragment>
      );
    }
    if (node.kind === "graph_append") {
      const actKind = graphAppendActKinds?.get(node.execution_id);
      const auth = graphAppendAuthorizedBy?.get(node.execution_id);
      return (
        <div key={nodeKey} className="graph-append-anchor">
          {graphAppendAnchorLabel(node.added_count, actKind, auth)}
        </div>
      );
    }
    if (node.kind === "user_interjection") {
      const cardSlot = classifyTimelineInteractionCard(
        "user_interjection",
        node,
        slotLookup,
      );
      const empty = timelineEmptyNode(nodeKey, cardSlot);
      if (empty !== undefined) return empty;
      const item = userInterjections?.find(
        (u) => u.interjectionId === node.interjection_id,
      );
      if (!item) {
        return (
          timelineEmptyNode(nodeKey, {
            kind: "missing",
            processKind: "user_interjection",
            id: node.interjection_id,
          }) ?? null
        );
      }
      return (
        <InterjectionBubbles
          key={nodeKey}
          items={[item]}
          turnClosed={turnClosed}
        />
      );
    }
    if (node.kind === "escalation") {
      const cardSlot = classifyTimelineInteractionCard(
        "escalation",
        node,
        slotLookup,
      );
      const empty = timelineEmptyNode(nodeKey, cardSlot);
      if (empty !== undefined) return empty;
      const escSlot = escalationSlots?.get(node.escalation_id);
      if (!escSlot) {
        return (
          timelineEmptyNode(nodeKey, {
            kind: "missing",
            processKind: "escalation",
            id: node.escalation_id,
          }) ?? null
        );
      }
      const liveEsc =
        escSlot.esc.status === "pending" &&
        team?.escalationsInteractive &&
        team.conversationId
          ? escSlot.id
          : undefined;
      if (liveEsc && team?.conversationId) {
        return (
          <EscalationAnswer
            key={escSlot.id}
            esc={escSlot.esc}
            escalationId={liveEsc}
            conversationId={team.conversationId}
            runId={escSlot.runId}
            onOpenLive={onOpenBrowserLive}
          />
        );
      }
      const detail = escalationDetail(escSlot.esc);
      return (
        <div key={escSlot.id} className="run-escalation">
          <span className="run-escalation-q">↑ {escSlot.esc.question}</span>
          {detail && <span className="run-escalation-a">{detail}</span>}
        </div>
      );
    }
    // 热审批 / 委派授权痕迹 (D3): resolved 后轻行；pending 有意为空（操作面在 Sheet/PauseCard）。
    if (node.kind === "approval") {
      const cardSlot = classifyTimelineInteractionCard(
        "approval",
        node,
        slotLookup,
      );
      const empty = timelineEmptyNode(nodeKey, cardSlot);
      if (empty !== undefined) return empty;
      const t = hotTraces?.get(node.approval_id);
      if (!t?.resolved) {
        return (
          timelineEmptyNode(nodeKey, {
            kind: "missing",
            processKind: "approval",
            id: node.approval_id,
          }) ?? null
        );
      }
      const tool = t.toolName ? toolLabel(t.toolName) : "工具";
      return (
        <div key={nodeKey} className="hot-trace">
          ✓ {t.denied ? `已拒绝 · ${tool}` : `已批准 · ${tool}`}
        </div>
      );
    }
    if (node.kind === "stage_card") {
      const cardSlot = classifyTimelineInteractionCard(
        "stage_card",
        node,
        slotLookup,
      );
      const empty = timelineEmptyNode(nodeKey, cardSlot);
      if (empty !== undefined) return empty;
      const t = stageCardTraces?.get(node.stage_card_id);
      if (!t) {
        return (
          timelineEmptyNode(nodeKey, {
            kind: "missing",
            processKind: "stage_card",
            id: node.stage_card_id,
          }) ?? null
        );
      }
      const label =
        t.outcome === "orphaned"
          ? "推进卡 · 已失效"
          : t.decision === "research_first"
            ? "推进卡 · 已选补充调研"
            : "推进卡 · 已开辩";
      return (
        <div key={nodeKey} className="hot-trace">
          {t.outcome === "orphaned" ? "✕ " : "✓ "}
          {label}
        </div>
      );
    }
    // checkpoint·plan_review：手机阻塞交互走 Sheet，时间线有意为空（不是丢卡）。
    // team_preview pending 仍有意为空；resolved 画「已调整 · 已交回修订」等痕迹。
    if (node.kind === "checkpoint" || node.kind === "plan_review") {
      return timelineIntentionalEmpty();
    }
    if (node.kind === "team_preview") {
      const cardSlot = classifyTimelineInteractionCard(
        "team_preview",
        node,
        slotLookup,
      );
      const empty = timelineEmptyNode(nodeKey, cardSlot);
      if (empty !== undefined) return empty;
      const t = teamPreviewTraces?.get(node.checkpoint_id);
      if (!t) {
        return (
          timelineEmptyNode(nodeKey, {
            kind: "missing",
            processKind: "team_preview",
            id: node.checkpoint_id,
          }) ?? null
        );
      }
      if (t.decision === "continue" && kickoffReleased) {
        return timelineIntentionalEmpty();
      }
      return (
        <div
          key={nodeKey}
          className="hot-trace"
          data-testid="team-preview-trace"
          data-decision={t.decision ?? t.status}
        >
          <div>{t.label}</div>
          {t.note.trim() ? (
            <div className="hot-trace-note">{t.note}</div>
          ) : null}
        </div>
      );
    }
    if (node.kind === "tool-group") {
      return (
        <ToolGroup
          key={nodeKey}
          tools={node.tools}
          toolPhases={toolPhases}
          isStreaming={live}
        />
      );
    }
    if (node.kind === "rework") {
      const hasContentAfter = nodes
        .slice(i + 1)
        .some((n) => n.kind === "content");
      return (
        <span key={nodeKey} className="rework-chip">
          {reworkChipLabel(isStreaming, hasContentAfter)}
        </span>
      );
    }
    if (node.kind !== "tool") return null;
    return (
      <ToolStep
        key={nodeKey}
        step={node.step}
        phase={toolPhases?.get(node.step.id)}
      />
    );
  };

  return (
    <div className="timeline">
      {team &&
      !hasTeamMarker &&
      shouldShowTeamGraph(team.runs, kickoffReleased) ? (
        <TeamView {...team} />
      ) : null}
      {nodes.map((node, i) => {
        const prefix =
          i === fallbackBeforeTeamIdx
            ? renderFallback("fallback-before-team")
            : null;

        if (shouldCollapseProcess) {
          const isFirstProcess =
            isProcessNode(node) && !nodes.slice(0, i).some(isProcessNode);

          if (!processExpanded) {
            if (isProcessNode(node)) {
              if (!isFirstProcess) return null;
              return (
                <Fragment key={`sum-${nodeKeys[i]}`}>
                  {prefix}
                  <button
                    type="button"
                    className="process-summary"
                    onClick={toggleProcess}
                  >
                    <span>{processSummary}</span>
                    <span className="thinking-chevron" aria-hidden>
                      ▸
                    </span>
                  </button>
                </Fragment>
              );
            }
          } else if (isFirstProcess) {
            return (
              <Fragment key="process-expanded">
                {prefix}
                <button
                  type="button"
                  className="process-summary"
                  onClick={toggleProcess}
                >
                  <span>{processSummary}</span>
                  <span className="thinking-chevron" aria-hidden>
                    ▾
                  </span>
                </button>
                {renderNode(node, i)}
              </Fragment>
            );
          }
        }

        if (prefix) {
          return (
            <Fragment key={`wrap-${nodeKeys[i]}`}>
              {prefix}
              {renderNode(node, i)}
            </Fragment>
          );
        }
        return renderNode(node, i);
      })}
      {showFallbackAfter && renderFallback("fallback-after")}
      {showThinkingTail && (
        <span className="thinking-tail" data-testid="thinking-tail">
          <ThinkingDots />
          Thinking…
        </span>
      )}
    </div>
  );
}

/** ≥2 consecutive `web_search` — flatten (no outer shell). Each search row already
 *  shows query + count; mirroring desktop ToolLineGroup's web_search flat path. */
function isWebSearchFlatGroup(tools: ToolStepData[]): boolean {
  return tools.length >= 2 && tools.every((t) => t.tool_name === "web_search");
}

/** Folded run of ≥2 consecutive tools — stream-aware expand (对称桌面 DefaultToolLineGroup). */
function ToolGroup({
  tools,
  toolPhases,
  isStreaming = false,
}: {
  tools: ToolStepData[];
  toolPhases?: Map<string, ToolPhase>;
  isStreaming?: boolean;
}) {
  const [expanded, toggleExpanded] = useStreamAwareDisclosure(isStreaming);
  if (isWebSearchFlatGroup(tools)) {
    return (
      <div className="tool-group-flat">
        {tools.map((t) => (
          <ToolStep key={t.id} step={t} phase={toolPhases?.get(t.id)} />
        ))}
      </div>
    );
  }
  const errorCount = tools.reduce(
    (n, t) =>
      n +
      (t.status === "error" &&
      !isFileReadCeilingGuidance(t.tool_name, t.result) &&
      !isVerifyBudgetExceeded(t.display)
        ? 1
        : 0),
    0,
  );
  const running = tools.some((t) => t.status === "running");
  return (
    <div className="tool-group">
      <button
        type="button"
        className="tool-group-head"
        onClick={toggleExpanded}
      >
        <span className="tool-group-summary-row">
          {running && <ThinkingDots />}
          <span className="tool-group-summary">{toolGroupSummary(tools)}</span>
          {errorCount > 0 && (
            <span className="tool-group-error">{errorCount} 失败</span>
          )}
          {!running && (
            <span className="thinking-chevron" aria-hidden>
              {expanded ? "▾" : "▸"}
            </span>
          )}
        </span>
      </button>
      {expanded && (
        <div className="tool-group-body">
          {tools.map((t) => (
            <ToolStep key={t.id} step={t} phase={toolPhases?.get(t.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

/** A tool call: English name (+ its arg detail) · 中文状态, expandable to its full arguments and
 *  result. While running, the status shows the coarse phase（正在检索 / 排队中 / 改用备用）
 *  from the live `phase` + an elapsed timer — a live waiting cue instead of a static「进行中」.
 *  Successful `wait` is sealed (no expand, no model receipt / reason). Failures still
 *  expand to the product sentence. */
function ToolStep({
  step,
  phase,
}: {
  step: ToolStepData;
  phase?: ToolPhase;
}) {
  if (isSuccessfulHandoff(step.tool_name, step.status)) {
    return <HandoffSuccessRow args={step.arguments} />;
  }
  return <GenericToolStep step={step} phase={phase} />;
}

/** wait 成功/进行中：无用户可见参数与回执，不可展开。失败仍展产品句。 */
function isSealedWait(step: ToolStepData): boolean {
  return step.tool_name === "wait" && step.status !== "error";
}

function GenericToolStep({
  step,
  phase,
}: {
  step: ToolStepData;
  phase?: ToolPhase;
}) {
  const [open, setOpen] = useState(false);
  const sealedWait = isSealedWait(step);
  // wait.reason 用户不可见；展开 JSON 同样禁泄。其它工具仍可展开看参数。
  const args =
    step.tool_name === "wait"
      ? null
      : Object.keys(step.arguments).length > 0
        ? step.arguments
        : null;
  const detail = toolDetail(step.arguments, step.tool_name);
  const running = step.status === "running";
  const ceilingGuidance =
    isFileReadCeilingGuidance(step.tool_name, step.result) ||
    (step.status === "error" && isVerifyBudgetExceeded(step.display));
  const diagnostics = extractCodeDiagnostics(step.display);
  const elapsed = useRunningElapsed(running);
  // Prefer product `failure.message`; fall back to model-facing `result` when absent.
  // Sealed wait: never surface the coordination ack as a result body.
  const faceText = sealedWait ? null : (step.failure?.message ?? step.result);
  const doneStatus = ceilingGuidance
    ? isVerifyBudgetExceeded(step.display)
      ? "验证未完成"
      : TOOL_GUIDANCE_LABEL
    : diagnostics
      ? codeDiagnosticsSummary(diagnostics)
      : TOOL_STATUS_LABEL[step.status];
  const runningStatus = running
    ? [
        toolPhaseText(phase) ?? TOOL_STATUS_LABEL.running,
        elapsed >= 1 ? `${elapsed}s` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : doneStatus;
  const shellClass = ceilingGuidance
    ? "tool tool-guidance"
    : `tool tool-${step.status}`;
  const head = (
    <>
      <span className="tool-name">
        <span className="tool-label">
          {toolLabel(step.tool_name, step.arguments)}
        </span>
        {detail && <span className="tool-detail">{detail}</span>}
      </span>
      <span className="tool-status">{runningStatus}</span>
    </>
  );
  return (
    <div className={shellClass}>
      {sealedWait ? (
        <div className="tool-head">{head}</div>
      ) : (
        <button
          type="button"
          className="tool-head"
          onClick={() => setOpen((o) => !o)}
        >
          {head}
        </button>
      )}
      {open && !sealedWait && (args || faceText != null || diagnostics) && (
        <div className="tool-body">
          {isVerifyBudgetExceeded(step.display) && (
            <div className="tool-incomplete">验证未完成（预算耗尽）</div>
          )}
          {diagnostics && (
            <div className="tool-diagnostics">
              <div className="tool-diagnostics-title">类型诊断</div>
              <div>{codeDiagnosticsSummary(diagnostics)}</div>
              {diagnostics.status === "ok" &&
                diagnostics.diagnostics
                  .filter((d) => d.severity === "error")
                  .slice(0, 8)
                  .map((d, i) => (
                    <div
                      key={`${d.path}:${d.line}:${i}`}
                      className="tool-diagnostics-row"
                    >
                      {d.path}:{d.line} · {d.message}
                      {d.code ? (
                        <span className="tool-diagnostics-code">
                          {" "}
                          ({d.code})
                        </span>
                      ) : null}
                    </div>
                  ))}
            </div>
          )}
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
